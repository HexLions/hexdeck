"""A key or a token is stored without the space a copy picked up beside it.

⚠️ The field is masked, so a space at the end of a pasted token shows nowhere,
and the service answers that the credentials are wrong. A password is stored
as typed, because a space can be part of one, and so is a secret of several
lines.
"""

from __future__ import annotations

from app.crypto import decrypt
from app.services.integrations import store_config


def _stored(kind: str, incoming: dict, name: str) -> str:
    return decrypt(store_config(kind, incoming)[name])


def test_a_pasted_key_loses_the_space_around_it() -> None:
    assert _stored("radarr", {"url": "http://radarr.example.com", "api_key": " made-up\t"}, "api_key") == "made-up"


def test_a_password_is_stored_as_typed() -> None:
    config = {"url": "http://camera.example.com", "username": "admin", "password": " with a space "}
    assert _stored("dahua", config, "password") == " with a space "


def test_a_secret_of_several_lines_is_stored_as_entered() -> None:
    assert _stored("jsonapi", {"url": "http://api.example.com", "headers": "X-Api-Key: abc \n"}, "headers") == "X-Api-Key: abc \n"


def test_nothing_but_spaces_keeps_the_stored_key() -> None:
    before = store_config("radarr", {"url": "http://radarr.example.com", "api_key": "made-up"})
    after = store_config("radarr", {"api_key": "   "}, before)
    assert after["api_key"] == before["api_key"]
