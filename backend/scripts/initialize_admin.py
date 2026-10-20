"""Create the initial Personal OS administrator through a local terminal only."""
from __future__ import annotations

import getpass
import sys

from sqlalchemy import func, select

from app.auth.security import hash_password
from app.database import SessionLocal
from app.models import User


def main() -> int:
    username = input("Administrator username (3-80 chars): ").strip()
    if not 3 <= len(username) <= 80:
        print("Username length must be between 3 and 80.", file=sys.stderr)
        return 2
    password = getpass.getpass("Administrator password: ")
    confirmation = getpass.getpass("Confirm password: ")
    categories = sum((any(c.islower() for c in password), any(c.isupper() for c in password), any(c.isdigit() for c in password), any(not c.isalnum() and not c.isspace() for c in password)))
    if len(password) < 12 or categories < 3 or password != confirmation:
        print("Use at least 12 characters and three character types; confirmation must match.", file=sys.stderr)
        return 2
    with SessionLocal() as db:
        if (db.scalar(select(func.count(User.id))) or 0) != 0:
            print("An account already exists; initialization refused.", file=sys.stderr)
            return 3
        user = User(username=username, display_name=username, password_hash=hash_password(password))
        db.add(user)
        db.commit()
        db.refresh(user)
        print(f"Initial administrator created (user id {user.id}). Password was not displayed or stored in plaintext.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
