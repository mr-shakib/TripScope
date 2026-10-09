"""`tripscope-admin` command line: bootstrap users without exposing passwords in shell history.

    tripscope-admin create-user --email ana@example.org --name "Ana" --role analyst
The password is read from TRIPSCOPE_NEW_USER_PASSWORD if set, otherwise prompted for (not echoed).
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from sqlalchemy import func, select

from tripscope.core.passwords import hash_password
from tripscope.core.settings import get_settings
from tripscope.metadata.audit import record_audit
from tripscope.metadata.db import make_engine, make_session_factory
from tripscope.metadata.models import Role, User


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tripscope-admin")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-user")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--role", required=True, choices=[r.value for r in Role])
    create.add_argument("--update", action="store_true", help="reset password/role if the user exists")
    args = parser.parse_args(argv)

    password = os.environ.get("TRIPSCOPE_NEW_USER_PASSWORD") or getpass.getpass("Password (min 12 chars): ")
    try:
        password_hash = hash_password(password)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    email = args.email.strip().lower()
    engine = make_engine(get_settings().database_url)
    factory = make_session_factory(engine)
    with factory() as session, session.begin():
        user = session.scalar(select(User).where(func.lower(User.email) == email))
        if user is not None and not args.update:
            print(f"error: user {email} exists (use --update to reset)", file=sys.stderr)
            return 1
        action = "admin.user_updated" if user else "admin.user_created"
        if user is None:
            user = User(email=email)
            session.add(user)
        user.display_name, user.role, user.password_hash, user.is_active = (
            args.name,
            Role(args.role),
            password_hash,
            True,
        )
        session.flush()
        record_audit(
            session,
            action=action,
            outcome="success",
            actor_label="cli",
            target_type="user",
            target_id=str(user.id),
            details={"role": args.role},
        )
    engine.dispose()
    print(f"{action.split('.')[1].replace('_', ' ')}: {email} ({args.role})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
