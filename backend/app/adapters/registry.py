"""Container registry: the repositories on a registry, and the tags of one image.

Speaks the OCI Distribution API (``/v2/``), which zot, Docker's own registry
(``distribution``), Harbor, Gitea and Forgejo, GitHub's ghcr.io and Docker
Hub all answer.

Checked against zot v2.1.22 (the minimal build, which is the plain
distribution API) running locally on 20.10.2026 behind htpasswd, with three
repositories pushed and six tags, a wrong password and a repository that is
not there; and on the same day against ghcr.io and Docker Hub, anonymously,
for the token sign-in.

⚠️ Two ways to sign in, and the registry says which on the first 401:
``WWW-Authenticate: Basic realm=...`` (zot, a registry behind htpasswd) wants
the user name and password on every request; ``Bearer realm="...",
service="...", scope="..."`` (ghcr.io, Docker Hub, Harbor) wants them sent to
the realm, which hands back a token for that one scope (``token``, on Docker
Hub also ``access_token``, for ``expires_in`` seconds, 300 there). Anonymous
works for public images on both of those.

⚠️ ``/v2/_catalog`` is the only way to list repositories, and the big public
registries do not offer it: ghcr.io answers 401 with a scope for
``repository:user/image``, which no token turns into a list. There the
repository card has to name its image.

⚠️ Both lists come in pages with a ``Link: </v2/...?last=...&n=...>;
rel="next"`` header; Docker Hub gave fifty tags of alpine per page. Pages are
followed up to a bound, and the count says when it stopped.

⚠️ zot answers a wrong password with ``{"code": "UNAUTHORIZED", ...}`` at the
top; distribution and the rest use ``{"errors": [{"code": ...}]}``. Only the
status is read. A repository that is not there is 404 ``NAME_UNKNOWN``.
"""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urljoin

from . import demo as fake
from .base import (
    Adapter,
    AdapterError,
    AuthFailed,
    Context,
    Field,
    WidgetData,
    WidgetType,
    base_url,
)

#: Pages followed for one list. A hundred names a page, as asked for.
MAX_PAGES = 10
PAGE = 100
#: Repositories whose tags the list card counts; one request each.
MAX_REPOSITORIES = 20
_CHALLENGE = re.compile(r'(\w+)="([^"]*)"')
_NEXT = re.compile(r"<([^>]+)>\s*;\s*rel=\"?next\"?")
#: A version has at least one dot: alpine's tags include dates such as
#: ``20260805``, which as a bare number beat every ``3.x``.
_VERSION = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?$")


def _newest(tags: list[str]) -> str:
    """The highest plain version among the tags, or "" when none is one."""
    best: tuple[int, int, int] | None = None
    found = ""
    for tag in tags:
        match = _VERSION.match(tag)
        if not match:
            continue
        key = tuple(int(part or 0) for part in match.groups())
        if best is None or key > best:
            best, found = key, tag  # type: ignore[assignment]
    return found


class RegistryAdapter(Adapter):
    kind = "registry"
    label = "Container registry"
    category = "hosts"
    description = "Any OCI or Docker registry (zot, Distribution, Harbor, ghcr.io, Docker Hub): its repositories, and the tags of one image."
    icon = "lucide:package"
    beta = True
    docs_url = "https://github.com/opencontainers/distribution-spec/blob/main/spec.md"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="https://registry.example.com",
              help="The registry's address, without /v2. For Docker Hub: https://registry-1.docker.io."),
        Field("username", "User name", help="Empty for public images."),
        Field("password", "Password or token", type="password", secret=True, help="Empty for public images."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="repositories", label="Repositories", description="Every repository with its number of tags. Needs a registry that lists its catalog.",
                   renderer="list", default_size=(3, 3), refresh_seconds=900,
                   options=(Field("limit", "Entries", type="number", default=10),)),
        WidgetType(kind="image", label="Image", description="How many tags one image has, and its highest version.",
                   renderer="value", default_size=(3, 2), refresh_seconds=900, metrics=("tags",),
                   options=(Field("repository", "Image", required=True, placeholder="library/alpine",
                                  help="The repository path, without the registry's address."),)),
    )

    # -- signing in ----------------------------------------------------------

    def _basic(self, config: dict[str, Any]) -> tuple[str, str] | None:
        user = str(config.get("username") or "")
        return (user, str(config.get("password") or "")) if user else None

    async def _token(self, config: dict[str, Any], ctx: Context, challenge: str) -> str:
        fields = dict(_CHALLENGE.findall(challenge))
        realm = fields.get("realm", "")
        if not realm.startswith(("http://", "https://")):
            raise AdapterError("The registry asks for a token but names no address to get one.", code="bad_challenge",
                               hint="Check the URL; it is the registry itself, without /v2.")
        key = f"registry:token:{fields.get('scope', '')}"
        kept = ctx.cache.get(key)
        if kept and kept[0] > time.monotonic():
            return kept[1]
        params = {name: fields[name] for name in ("service", "scope") if fields.get(name)}
        response = await ctx.request("GET", realm, params=params, auth=self._basic(config),
                                     verify=not config.get("insecure"), auth_errors=False)
        if response.status_code in (401, 403):
            raise AuthFailed("The registry refused the user name or password.", hint="For ghcr.io and Docker Hub a personal access token goes in the password.")
        if response.status_code >= 400:
            raise AdapterError(f"The registry's token service answered with HTTP {response.status_code}.", code="http_error",
                               hint="Check the URL; it is the registry itself, without /v2.")
        try:
            answer = response.json()
            token = str(answer.get("token") or answer.get("access_token") or "")
            lasts = float(answer.get("expires_in") or 60)
        except (ValueError, AttributeError, TypeError) as error:
            raise AdapterError("The registry's token service did not answer with JSON.", code="bad_token") from error
        if not token:
            raise AdapterError("The registry's token service handed over no token.", code="bad_token")
        # A little before it runs out, so a token is never used on its last second.
        ctx.cache[key] = (time.monotonic() + max(10.0, lasts - 10), token)
        return token

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float = 300) -> Any:
        """GET under /v2, signing in as the registry asks. Returns the response."""
        url = path if path.startswith(("http://", "https://")) else urljoin(f"{base_url(config)}/", path.lstrip("/"))
        key = f"registry:bearer:{path.split('?', 1)[0]}"
        headers = {"Authorization": f"Bearer {ctx.cache[key]}"} if key in ctx.cache else None
        response = await ctx.request("GET", url, headers=headers, auth=None if headers else self._basic(config),
                                     verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False)
        challenge = response.headers.get("www-authenticate", "")
        if response.status_code == 401 and path.startswith("/v2/_catalog") and challenge.lower().startswith("bearer") \
                and "registry:catalog" not in challenge:
            # ghcr.io asks for a repository scope here, which no token turns into a list.
            raise AdapterError("This registry does not list its repositories.", code="no_catalog",
                               hint="Use the Image card and name the image instead.")
        if response.status_code == 401 and challenge.lower().startswith("bearer"):
            token = await self._token(config, ctx, challenge)
            ctx.cache[key] = token
            response = await ctx.request("GET", url, headers={"Authorization": f"Bearer {token}"},
                                         verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False)
        if response.status_code in (401, 403):
            raise AuthFailed("The registry refused the user name or password, or does not let them see this.",
                             hint="Public images need neither; a private one needs an account that may pull it.")
        if response.status_code == 404:
            raise AdapterError("The registry has no such repository, or lists none.", code="not_found",
                               hint="Check the image path, such as library/alpine on Docker Hub.")
        if response.status_code >= 400:
            raise AdapterError(f"The registry answered with HTTP {response.status_code}.", code="http_error",
                               hint="Check the URL; it is the registry itself, without /v2.")
        return response

    async def _pages(self, config: dict[str, Any], ctx: Context, path: str, field: str) -> tuple[list[str], bool]:
        """Every name of a paged list, and whether the bound stopped it short."""
        names: list[str] = []
        url: str | None = f"{path}?n={PAGE}"
        for _ in range(MAX_PAGES):
            response = await self._get(config, ctx, url)  # type: ignore[arg-type]
            try:
                part = response.json().get(field) or []
            except (ValueError, AttributeError) as error:
                raise AdapterError("This address answers, but not the way a registry does.", code="not_registry",
                                   hint="Check the URL; it is the registry itself, without /v2.") from error
            names += [str(one) for one in part]
            link = _NEXT.search(response.headers.get("link", ""))
            url = link.group(1) if link else None
            if not url:
                return names, False
        return names, True

    # -- the cards -----------------------------------------------------------

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        response = await self._get(config, ctx, "/v2/", cache=0)
        version = response.headers.get("docker-distribution-api-version", "")
        return f"The registry answers ({version})." if version else "The registry answers."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "image":
            repository = str(options.get("repository") or "").strip().strip("/")
            if not repository:
                raise AdapterError("No image is named.", code="no_repository", hint="Name it in the card's settings, such as library/alpine.")
            tags, cut = await self._pages(config, ctx, f"/v2/{repository}/tags/list", "tags")
            return self._image(repository, tags, cut)
        repositories, cut = await self._pages(config, ctx, "/v2/_catalog", "repositories")
        limit = max(1, min(MAX_REPOSITORIES, int(options.get("limit") or 10)))
        counted = []
        for name in sorted(repositories)[:limit]:
            tags, more = await self._pages(config, ctx, f"/v2/{name}/tags/list", "tags")
            counted.append((name, tags, more))
        return self._repositories(counted, len(repositories), cut)

    @staticmethod
    def _image(repository: str, tags: list[str], cut: bool) -> WidgetData:
        newest = _newest(tags)
        secondary: list[dict[str, Any]] = [{"label": "Image", "value": repository}]
        if newest:
            secondary.append({"label": "Newest", "value": newest})
        return WidgetData(status="ok", primary={"label": "Tags", "value": f"{len(tags)}+" if cut else len(tags)},
                          secondary=secondary, metrics={} if cut else {"tags": float(len(tags))})

    @staticmethod
    def _repositories(counted: list[tuple[str, list[str], bool]], total: int, cut: bool) -> WidgetData:
        items = [{
            "title": name,
            "subtitle": " · ".join(part for part in (_newest(tags), ", ".join(tag for tag in tags if tag == "latest")) if part),
            "status": "ok" if tags else "unknown",
            "value": f"{len(tags)}{'+' if more else ''} tag{'' if len(tags) == 1 and not more else 's'}",
        } for name, tags, more in counted]
        return WidgetData(status="ok", items=items, secondary=[{"label": "Repositories", "value": f"{total}+" if cut else total}],
                          meta={"empty": "The registry has no repository yet."})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        builds = 41 + int(fake.walk("registry-builds", tick, 0, 3.9))
        hexdeck = [f"0.{minor}.0" for minor in range(17, 31)] + ["latest", "main"]
        if widget_kind == "image":
            return self._image(str(options.get("repository") or "homelab/hexdeck"), hexdeck, False)
        counted = [
            ("homelab/backup-job", ["1.2.0", "1.3.0", "latest"], False),
            ("homelab/hexdeck", hexdeck, False),
            ("homelab/website", [f"build-{number}" for number in range(builds)] + ["latest"], False),
            ("tools/curl", ["8.10"], False),
        ]
        return self._repositories(counted, len(counted), False)


ADAPTER = RegistryAdapter()
