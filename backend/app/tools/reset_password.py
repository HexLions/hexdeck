"""Set a new password for an account, from the shell of the machine.

    docker exec -it hexdeck python -m app.tools.reset_password admin
    docker exec -it hexdeck python -m app.tools.reset_password admin --password 'a new one'

Without ``--password`` a random one is made and printed once. Every browser
session of the account is ended, and its second factor is left alone: a
password is what was forgotten, not the phone. Whoever can run this has the
data directory, and with it everything; the tool adds no way in that was
not there.
"""

from __future__ import annotations

import argparse
import secrets
import sys

from sqlalchemy import select

from ..db import db_session
from ..migrations import migrate
from ..models import Session, User
from ..security import hash_password, now_ms


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Set a new password for an account.")
    parser.add_argument("username", help="the account, as it signs in")
    parser.add_argument("--password", help="the new password; made up and printed when left out")
    args = parser.parse_args(argv)
    migrate()
    with db_session() as db:
        user = db.scalar(select(User).where(User.username == args.username))
        if user is None:
            names = ", ".join(db.scalars(select(User.username).order_by(User.username)))
            print(f"There is no account called {args.username!r}. Accounts: {names or 'none'}", file=sys.stderr)
            return 1
        password = args.password or secrets.token_urlsafe(12)
        if len(password) < 8:
            print("A password has at least eight characters.", file=sys.stderr)
            return 1
        user.password_hash = hash_password(password)
        user.password_changed_ms = now_ms()
        user.disabled = False
        for session in db.scalars(select(Session).where(Session.user_id == user.id)):
            session.revoked = True
        db.commit()
    if args.password:
        print(f"The password of {args.username!r} was set. Every browser session of the account was signed out.")
    else:
        print(f"The new password of {args.username!r} is: {password}")
        print("Sign in with it and change it under My settings. Every browser session of the account was signed out.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
