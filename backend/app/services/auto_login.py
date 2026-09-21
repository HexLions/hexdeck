"""Signing in by itself on a trusted network.

An operator names the networks of the home (``192.168.1.0/24``) and one
account. A browser from one of those addresses that has no session gets one
as that account, without a password. Off by default; the sign-in page stays
the way in from everywhere else.

⚠️ The address is the direct client's, unless the direct client is one of
the trusted proxies (``HEXDECK_TRUSTED_PROXIES``): then it is the last
address in ``X-Forwarded-For`` that is not a trusted proxy. Reading the
header without that rule would let anybody claim any address. And the whole
Internet (``0.0.0.0/0``) is refused as a network, because "everyone is this
account" is not a setting, it is the absence of one.
"""

from __future__ import annotations

import ipaddress
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Setting, User

KEY = "auto_login"
#: The cookie a sign-out on purpose leaves behind, so the browser is not
#: signed in again on its next request; a password sign-in clears it.
MANUAL_COOKIE = "nexdeck_manual"
MANUAL_SECONDS = 12 * 3600

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


class AutoLoginError(Exception):
    def __init__(self, message: str, code: str = "bad_auto_login") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def parse_networks(values: list[str]) -> list[Network]:
    networks: list[Network] = []
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        try:
            network = ipaddress.ip_network(text, strict=False)
        except ValueError as failure:
            raise AutoLoginError(f"{text!r} is not an address or a network like 192.168.1.0/24.", "bad_network") from failure
        if network.prefixlen == 0:
            raise AutoLoginError("The whole Internet is not a trusted network.", "bad_network")
        networks.append(network)
    return networks


def stored(db: Session) -> dict[str, Any]:
    row = db.get(Setting, KEY)
    value = dict(row.value) if row is not None else {}
    return {"enabled": bool(value.get("enabled")), "user_id": value.get("user_id"), "networks": list(value.get("networks") or [])}


def save(db: Session, incoming: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(incoming.get("enabled"))
    user_id = incoming.get("user_id")
    networks = parse_networks(list(incoming.get("networks") or []))
    if enabled:
        if not networks:
            raise AutoLoginError("Name at least one network.", "bad_network")
        user = db.get(User, int(user_id)) if user_id is not None else None
        if user is None or user.disabled:
            raise AutoLoginError("Pick an account that exists and is not disabled.", "bad_user")
    value = {"enabled": enabled, "user_id": int(user_id) if user_id is not None else None, "networks": [str(n) for n in networks]}
    db.merge(Setting(key=KEY, value=value))
    db.commit()
    return stored(db)


def _proxies() -> list[Network]:
    try:
        return parse_networks([part for part in get_settings().trusted_proxies.split(",") if part.strip()])
    except AutoLoginError:
        return []


def client_address(request: Request) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Who is really asking: the direct client, or, through a trusted proxy, who it forwards for."""
    direct = request.client.host if request.client else None
    try:
        address = ipaddress.ip_address(direct) if direct else None
    except ValueError:
        return None
    if address is None:
        return None
    proxies = _proxies()
    if not any(address in proxy for proxy in proxies):
        return address
    hops = [hop.strip() for hop in request.headers.get("x-forwarded-for", "").split(",") if hop.strip()]
    for hop in reversed(hops):
        try:
            candidate = ipaddress.ip_address(hop)
        except ValueError:
            return None
        if not any(candidate in proxy for proxy in proxies):
            return candidate
    return address


def account_for(db: Session, request: Request) -> User | None:
    """The account a browser at this address may be signed in as, or None."""
    config = stored(db)
    if not config["enabled"] or config["user_id"] is None:
        return None
    address = client_address(request)
    if address is None:
        return None
    try:
        networks = parse_networks(config["networks"])
    except AutoLoginError:
        return None
    if not any(address in network for network in networks):
        return None
    user = db.get(User, int(config["user_id"]))
    if user is None or user.disabled:
        return None
    return user
