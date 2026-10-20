"""Container registry, against zot v2.1.22 run locally and ghcr.io and Docker Hub on 20.10.2026.

The fixture is what zot answered behind htpasswd with three repositories and
six tags, and the sign-in challenges ghcr.io sent for a tag list and for the
catalog it does not offer.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context
from app.adapters.registry import _newest

ZOT = "http://registry.local:5000"
BASIC = {"url": ZOT, "username": "reader", "password": "reader-pass-123"}
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "registry_answers.json").read_text())


@pytest.fixture
def ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=1, widget_id=1, cache={})


@respx.mock
async def test_repositories_with_basic_sign_in_and_pages(ctx: Context) -> None:
    respx.get(f"{ZOT}/v2/_catalog", params={"n": "100"}).mock(return_value=httpx.Response(
        200, json=FIXTURE["catalog_page1"], headers={"Link": FIXTURE["catalog_page1_link"]}))
    respx.get(f"{ZOT}/v2/_catalog", params={"n": "2", "last": "homelab/hexdeck"}).mock(return_value=httpx.Response(200, json={"repositories": ["tools/curl"]}))
    respx.get(url__regex=rf"{ZOT}/v2/homelab/backup-job/tags/list.*").mock(return_value=httpx.Response(200, json=FIXTURE["tags"]))
    respx.get(url__regex=rf"{ZOT}/v2/homelab/hexdeck/tags/list.*").mock(return_value=httpx.Response(200, json={"name": "homelab/hexdeck", "tags": ["0.30.0", "latest"]}))
    respx.get(url__regex=rf"{ZOT}/v2/tools/curl/tags/list.*").mock(return_value=httpx.Response(200, json={"name": "tools/curl", "tags": ["8.10"]}))
    data = await get_adapter("registry").fetch("repositories", BASIC, {}, ctx)
    assert data.secondary == [{"label": "Repositories", "value": 3}], "the second page followed"
    assert [(row["title"], row["value"], row["subtitle"]) for row in data.items] == [
        ("homelab/backup-job", "3 tags", "1.3.0 · latest"), ("homelab/hexdeck", "2 tags", "0.30.0 · latest"), ("tools/curl", "1 tag", "8.10")]
    assert respx.calls[0].request.headers["authorization"].startswith("Basic ")


@respx.mock
async def test_an_image_through_a_token_service(ctx: Context) -> None:
    """ghcr.io and Docker Hub: 401 with a Bearer challenge, a token from the realm, then the list."""
    tags = respx.get(url__regex=r"https://ghcr\.io/v2/hexlions/hexdeck/tags/list.*").mock(side_effect=[
        httpx.Response(401, headers={"WWW-Authenticate": FIXTURE["ghcr_challenge_tags"]}),
        httpx.Response(200, json={"name": "hexlions/hexdeck", "tags": ["main", "0.29.2", "latest", "0.30.0", "0.30"]}),
    ])
    token = respx.get("https://ghcr.io/token").mock(return_value=httpx.Response(200, json={"token": "djE6aGV4"}))
    data = await get_adapter("registry").fetch("image", {"url": "https://ghcr.io"}, {"repository": "hexlions/hexdeck"}, ctx)
    assert data.primary == {"label": "Tags", "value": 5}
    assert data.secondary == [{"label": "Image", "value": "hexlions/hexdeck"}, {"label": "Newest", "value": "0.30.0"}]
    assert token.calls[0].request.url.params["scope"] == "repository:hexlions/hexdeck:pull"
    assert "authorization" not in token.calls[0].request.headers, "anonymous for a public image"
    assert tags.calls[1].request.headers["authorization"] == "Bearer djE6aGV4"


@respx.mock
async def test_a_registry_without_a_catalog(ctx: Context) -> None:
    respx.get(url__regex=r"https://ghcr\.io/v2/_catalog.*").mock(return_value=httpx.Response(401, headers={"WWW-Authenticate": FIXTURE["ghcr_challenge_catalog"]}))
    with pytest.raises(AdapterError) as failure:
        await get_adapter("registry").fetch("repositories", {"url": "https://ghcr.io"}, {}, ctx)
    assert failure.value.code == "no_catalog"


@respx.mock
async def test_refusals(ctx: Context) -> None:
    respx.get(url__regex=rf"{ZOT}/v2/_catalog.*").mock(return_value=httpx.Response(401, json=FIXTURE["zot_unauthorized"], headers={"WWW-Authenticate": FIXTURE["zot_challenge"]}))
    respx.get(url__regex=rf"{ZOT}/v2/nope/tags/list.*").mock(return_value=httpx.Response(404, json=FIXTURE["name_unknown"]))
    respx.get(url__regex=rf"{ZOT}/v2/broken/tags/list.*").mock(side_effect=[httpx.Response(500), httpx.Response(200, text="<html>")])
    adapter = get_adapter("registry")
    with pytest.raises(AuthFailed):
        await adapter.fetch("repositories", {**BASIC, "password": "wrong"}, {}, ctx)
    for repository, code in (("nope", "not_found"), ("broken", "http_error"), ("broken", "not_registry"), ("", "no_repository")):
        with pytest.raises(AdapterError) as failure:
            await adapter.fetch("image", BASIC, {"repository": repository}, Context(httpx.AsyncClient(), integration_id=1, widget_id=1, cache={}))
        assert failure.value.code == code


@respx.mock
async def test_a_token_service_that_refuses(ctx: Context) -> None:
    respx.get(url__regex=r"https://ghcr\.io/v2/private/app/tags/list.*").mock(return_value=httpx.Response(
        401, headers={"WWW-Authenticate": FIXTURE["ghcr_challenge_tags"].replace("hexlions/hexdeck", "private/app")}))
    respx.get("https://ghcr.io/token").mock(return_value=httpx.Response(403, json={"errors": [{"code": "DENIED"}]}))
    with pytest.raises(AuthFailed):
        await get_adapter("registry").fetch("image", {"url": "https://ghcr.io", "username": "me", "password": "ghp_x"}, {"repository": "private/app"}, ctx)


@respx.mock
async def test_the_connection_test(ctx: Context) -> None:
    respx.get(f"{ZOT}/v2/").mock(return_value=httpx.Response(200, json={}, headers={"Docker-Distribution-Api-Version": "registry/2.0"}))
    assert await get_adapter("registry").test(BASIC, ctx) == "The registry answers (registry/2.0)."


def test_the_newest_version_skips_dates_and_words() -> None:
    assert _newest(["20260805", "3.9", "3.24.2", "edge", "latest", "v3.24.10"]) == "v3.24.10"
    assert _newest(["latest", "main"]) == ""


def test_the_demo() -> None:
    adapter = get_adapter("registry")
    for tick in range(0, 60, 7):
        assert adapter.demo("repositories", {}, tick).items
        assert adapter.demo("image", {}, tick).primary["label"] == "Tags"


@respx.mock
async def test_a_token_realm_without_https_and_bad_token_answers(ctx: Context) -> None:
    challenge = 'Bearer realm="http://registry.example.com/token",service="r",scope="repository:a/b:pull"'
    respx.get(url__regex=r"https://registry\.example\.com/v2/a/b/tags/list.*").mock(return_value=httpx.Response(401, headers={"WWW-Authenticate": challenge}))
    with pytest.raises(AdapterError) as failure:
        await get_adapter("registry").fetch("image", {"url": "https://registry.example.com", "username": "u", "password": "p"}, {"repository": "a/b"}, ctx)
    assert failure.value.code == "bad_challenge", "the password never goes to an http token service"
    respx.get(url__regex=r"https://ghcr\.io/v2/x/y/tags/list.*").mock(return_value=httpx.Response(
        401, headers={"WWW-Authenticate": FIXTURE["ghcr_challenge_tags"].replace("hexlions/hexdeck", "x/y")}))
    respx.get("https://ghcr.io/token").mock(side_effect=[httpx.Response(200, text="<html>"), httpx.Response(200, json={"token": ""})])
    for _ in range(2):
        with pytest.raises(AdapterError) as failure:
            await get_adapter("registry").fetch("image", {"url": "https://ghcr.io"}, {"repository": "x/y"}, Context(httpx.AsyncClient(), integration_id=1, widget_id=1, cache={}))
        assert failure.value.code == "bad_token"


@respx.mock
async def test_a_list_cut_short_names_no_newest_version(ctx: Context) -> None:
    def page(request: httpx.Request) -> httpx.Response:
        last = int(request.url.params.get("last", "0"))
        return httpx.Response(200, json={"tags": [f"1.{last + n}" for n in range(1, 4)]},
                              headers={"Link": f'</v2/big/app/tags/list?n=100&last={last + 3}>; rel="next"'})
    respx.get(url__regex=rf"{ZOT}/v2/big/app/tags/list.*").mock(side_effect=page)
    data = await get_adapter("registry").fetch("image", BASIC, {"repository": "big/app"}, ctx)
    assert data.primary == {"label": "Tags", "value": "30+"}
    assert [one["label"] for one in data.secondary] == ["Image"] and data.metrics == {}


async def test_a_name_that_is_not_a_repository(ctx: Context) -> None:
    for name in ("../etc", "a?b", "UPPER/case"):
        with pytest.raises(AdapterError) as failure:
            await get_adapter("registry").fetch("image", BASIC, {"repository": name}, ctx)
        assert failure.value.code == "no_repository"
