"""Create a persistent local Fernet key only when no stored ciphertext exists."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import dotenv_values

BACKEND_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = BACKEND_ROOT / ".env"
KEY_NAME = "PERSONAL_OS_CREDENTIAL_MASTER_KEY"


def _valid_key(candidate: str | None) -> bool:
    if not candidate:
        return False
    try:
        Fernet(candidate.encode("ascii"))
        return True
    except (ValueError, TypeError, UnicodeEncodeError):
        return False


def main() -> int:
    env_values = dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}
    environment_key = os.environ.get(KEY_NAME)
    file_key = env_values.get(KEY_NAME)
    active_key = environment_key if environment_key is not None else file_key
    if _valid_key(active_key):
        print("Credential master key is already configured and valid; no changes made.")
        return 0
    if active_key:
        print("A credential master key value exists but is invalid. No key was generated or replaced.", file=sys.stderr)
        return 2

    # Import after the environment file has been inspected; use the app's
    # configured database and refuse to break any existing encrypted value.
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import AIProvider

    with SessionLocal() as db:
        ciphertext_exists = db.scalar(select(AIProvider.id).where(AIProvider.encrypted_api_key.is_not(None)).limit(1))
    if ciphertext_exists:
        print("Encrypted provider credentials exist but the master key is missing. Recovery is required; no key was generated.", file=sys.stderr)
        return 3

    generated = Fernet.generate_key().decode("ascii")
    existing_text = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    key_line = f"{KEY_NAME}={generated}"
    line_pattern = re.compile(rf"^\s*{re.escape(KEY_NAME)}\s*=.*$", re.MULTILINE)
    if line_pattern.search(existing_text):
        print("An invalid or empty master-key setting already exists in backend/.env. No value was overwritten.", file=sys.stderr)
        return 2
    separator = "" if not existing_text or existing_text.endswith(("\n", "\r")) else "\n"
    updated = existing_text + separator + key_line + "\n"
    # Exclusive creation prevents concurrent first-run processes from
    # replacing each other's key. Existing unrelated settings are preserved.
    if not ENV_PATH.exists():
        try:
            with ENV_PATH.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(updated)
        except FileExistsError:
            print("backend/.env appeared concurrently; no file was overwritten. Re-run to validate it.", file=sys.stderr)
            return 4
    else:
        ENV_PATH.write_text(updated, encoding="utf-8", newline="\n")
    print("Generated and persisted a new Fernet master key in backend/.env; key value was not displayed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
