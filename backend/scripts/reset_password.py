"""Interactively reset one local Personal OS account's password.

The new password is read with getpass, never printed or persisted by this
script, and hashed with the same scrypt implementation used by the app.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import delete, select

from app.auth.security import hash_password, verify_password
from app.database import SessionLocal
from app.models import SessionToken, Task, User


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset a local Personal OS password")
    parser.add_argument("--username", default="stage1-local")
    args = parser.parse_args()

    password = getpass.getpass(f"New password for {args.username}: ")
    confirmation = getpass.getpass("Confirm new password: ")
    categories = sum((any(c.islower() for c in password), any(c.isupper() for c in password), any(c.isdigit() for c in password), any(not c.isalnum() and not c.isspace() for c in password)))
    if len(password) < 12 or categories < 3:
        print("Password must be at least 12 characters and use at least three character types.", file=sys.stderr)
        return 2
    if password != confirmation:
        print("Passwords do not match.", file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.username == args.username))
        if user is None:
            print(f"User not found: {args.username}", file=sys.stderr)
            return 1
        user_id = user.id
        task_ids_before = set(db.scalars(select(Task.id)).all())

        user.password_hash = hash_password(password)
        db.execute(delete(SessionToken).where(SessionToken.user_id == user_id))
        db.commit()

        db.expire_all()
        updated_user = db.scalar(select(User).where(User.id == user_id))
        task_ids_after = set(db.scalars(select(Task.id)).all())
        if updated_user is None or updated_user.id != user_id:
            raise RuntimeError("User identity verification failed after reset")
        if not verify_password(password, updated_user.password_hash):
            raise RuntimeError("New password verification failed after reset")
        if task_ids_after != task_ids_before:
            raise RuntimeError("Task records changed during password reset")

        print(f"Password reset completed for {args.username} (user id {user_id}).")
        print(f"Existing task records preserved: {len(task_ids_after)}")
        print("All existing sessions were revoked; log in again on each device.")
        return 0
    except Exception as exc:
        db.rollback()
        print(f"Password reset failed; transaction rolled back: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
