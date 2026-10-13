"""Encryption of secrets stored in the database (API keys, passwords, tokens).

The Fernet key is derived from the installation secret with a purpose prefix,
so it can never coincide with the session signing key in ``security.py``.
The derived key is cached per process; a key file swapped on disk therefore
takes effect after a restart, which is the safer of the two behaviours: a
swapped key makes every stored secret unreadable, and the operator should see
that all at once, not one request at a time.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import JSON, String, Text, cast, or_, select, update
from sqlalchemy.engine import Engine

from .config import get_settings

PREFIX = "enc:"
_cached: MultiFernet | None = None


class SecretUnreadable(Exception):
    """The stored value was encrypted with a different key."""


#: How hard it is to try one guess at a hand-written secret key.
#:
#: ⚠️ The salt is fixed, and that is a deliberate half measure. A per
#: installation salt would need a second file next to ``secret.key``, and
#: losing it would make every stored secret unreadable, which is the failure
#: this whole module exists to avoid. What the fixed salt still buys is the
#: only thing that matters here: an operator who set ``HEXDECK_SECRET_KEY`` to
#: something short now costs an attacker with the database file 210000 rounds
#: per guess instead of one hash. The generated key is 288 bits of randomness
#: and was never at risk either way.
ROUNDS = 210_000
SALT = b"nexdeck-secrets-v2"


def _key_from(secret: bytes) -> bytes:
    derived = hashlib.pbkdf2_hmac("sha256", b"nexdeck-secrets:" + secret, SALT, ROUNDS)
    return base64.urlsafe_b64encode(derived)


def _legacy_key_from(secret: bytes) -> bytes:
    """One SHA-256, as it was until 07.09.2026.

    ⚠️ Still here on purpose, and only for reading. Everything encrypted before
    that day was written with it, and HexDeck 0.1.0 is out in the world. A
    change that silently makes every stored API key unreadable would be worse
    than the weakness it fixes. Values are rewritten with the new key whenever
    they are saved again.
    """
    return base64.urlsafe_b64encode(hashlib.sha256(b"nexdeck-secrets:" + secret).digest())


def _previous() -> bytes | None:
    """The secret before the current one, while the operator still names it."""
    settings = get_settings()
    previous = settings.secret_key_previous.strip()
    if not previous or previous == settings.resolved_secret_key():
        return None
    return previous.encode("utf-8")


#: Derived keys by the secret they came from.
#:
#: ⚠️ Not the same thing as ``_cached``, which one call to ``forget_key``
#: empties. Stretching costs about 0.2 s, and the test suite drops the cached
#: key for every one of its several hundred cases; without this, the price of
#: doing the derivation properly would have been two minutes of every run, and
#: the next person would have lowered the rounds to get them back.
_derived: dict[tuple[bytes, bytes | None], MultiFernet] = {}


def _fernet() -> MultiFernet:
    global _cached
    if _cached is None:
        secret = get_settings().resolved_secret_key().encode("utf-8")
        previous = _previous()
        made = _derived.get((secret, previous))
        if made is None:
            # First key encrypts, every key decrypts.
            keys = [Fernet(_key_from(secret)), Fernet(_legacy_key_from(secret))]
            if previous is not None:
                keys += [Fernet(_key_from(previous)), Fernet(_legacy_key_from(previous))]
            made = MultiFernet(keys)
            if len(_derived) < 8:
                _derived[(secret, previous)] = made
        _cached = made
    return _cached


def forget_key() -> None:
    global _cached
    _cached = None


def encrypt(value: str) -> str:
    if value.startswith(PREFIX):
        return value
    return PREFIX + _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt(value: str) -> str:
    """Decrypt a stored value; plain values are passed through unchanged."""
    if not value.startswith(PREFIX):
        return value
    try:
        return _fernet().decrypt(value[len(PREFIX) :].encode("utf-8")).decode("utf-8")
    except InvalidToken as error:
        raise SecretUnreadable(
            "A stored secret cannot be decrypted. The HEXDECK_SECRET_KEY or the "
            "data/secret.key file no longer matches the database."
        ) from error


def is_encrypted(value: str) -> bool:
    return value.startswith(PREFIX)


def _current_only() -> Fernet:
    return Fernet(_key_from(get_settings().resolved_secret_key().encode("utf-8")))


def _rewrapped(value: Any, current: Fernet, counts: dict[str, int]) -> Any:
    """``value`` with every stored secret in it under the current key."""
    if isinstance(value, str):
        if not value.startswith(PREFIX):
            return value
        token = value[len(PREFIX) :].encode("utf-8")
        try:
            current.decrypt(token)
            return value
        except InvalidToken:
            pass
        try:
            rotated = _fernet().rotate(token)
        except InvalidToken:
            counts["unreadable"] += 1
            return value
        counts["rewritten"] += 1
        return PREFIX + rotated.decode("utf-8")
    if isinstance(value, dict):
        return {key: _rewrapped(item, current, counts) for key, item in value.items()}
    if isinstance(value, list):
        return [_rewrapped(item, current, counts) for item in value]
    return value


def rewrap_previous(engine: Engine) -> dict[str, int] | None:
    """Write every stored secret again under the current key.

    Runs on start while ``HEXDECK_SECRET_KEY_PREVIOUS`` is set, and does
    nothing otherwise. Every text and JSON column of every table is read, so
    a secret kept anywhere (a connection, the mail password, a second factor,
    a sign-in provider, Web Push) moves along without a list to keep up to
    date. A value already under the current key is left alone, so a second
    start with the variable still set rewrites nothing. A value neither key
    opens is left as it is and counted.
    """
    if _previous() is None:
        return None
    from .models import Base

    current = _current_only()
    counts = {"rewritten": 0, "unreadable": 0}
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            keys = list(table.primary_key.columns)
            columns = [c for c in table.columns if isinstance(c.type, (String, Text, JSON)) and not c.primary_key]
            if not keys or not columns:
                continue
            # Only the rows that hold a secret at all; the history of every
            # metric is a large table with nothing in it to move.
            holding = or_(*(cast(column, Text).like(f"%{PREFIX}%") for column in columns))
            for row in connection.execute(select(*keys, *columns).where(holding)):
                changes = {}
                for index, column in enumerate(columns):
                    value = row[len(keys) + index]
                    rewritten = _rewrapped(value, current, counts)
                    if rewritten != value:
                        changes[column.name] = rewritten
                if changes:
                    where = [key == row[position] for position, key in enumerate(keys)]
                    connection.execute(update(table).where(*where).values(**changes))
    return counts
