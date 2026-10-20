"""Initialize local production runtime settings without displaying secrets."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from app.config import BACKEND_ROOT


def _setting_lines(content: str, key: str, value: str) -> tuple[str, bool]:
    lines = content.splitlines(keepends=True)
    found = [index for index, line in enumerate(lines) if line.partition("=")[0].strip() == key]
    if len(found) > 1:
        raise RuntimeError(f"Duplicate {key} entries in backend environment file; resolve manually")
    existed = bool(found)
    replacement = f"{key}={value}\n"
    if found:
        old = lines[found[0]]
        newline = "\r\n" if old.endswith("\r\n") else "\n"
        lines[found[0]] = replacement.replace("\n", newline)
    else:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += "\n"
        lines.append(replacement)
    return "".join(lines), existed


def initialize(path: Path | None = None) -> None:
    target = path or (BACKEND_ROOT / ".env")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        with target.open("r", encoding="utf-8", newline="") as handle:
            original = handle.read()
    else:
        original = ""

    secret_line = next((line.partition("=")[2].strip() for line in original.splitlines() if line.partition("=")[0].strip() == "PERSONAL_OS_SECRET_KEY"), "")
    if len(secret_line) < 32 or secret_line == "change-me-before-network-use":
        secret_line = secrets.token_urlsafe(48)
    updated = original
    for key, value in (
        ("PERSONAL_OS_SECRET_KEY", secret_line),
        ("PERSONAL_OS_RUNTIME_ENVIRONMENT", "production"),
        ("PERSONAL_OS_COOKIE_SECURE", "true"),
    ):
        updated, _ = _setting_lines(updated, key, value)

    existed = target.exists()
    if existed:
        # Rewrite the existing inode to preserve its restrictive Windows ACL.
        with target.open("r+", encoding="utf-8", newline="") as handle:
            try:
                handle.seek(0)
                handle.write(updated)
                handle.truncate()
                handle.flush()
                os.fsync(handle.fileno())
            except Exception:
                handle.seek(0)
                handle.write(original)
                handle.truncate()
                handle.flush()
                os.fsync(handle.fileno())
                raise
    else:
        with target.open("x", encoding="utf-8", newline="") as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())


if __name__ == "__main__":
    initialize()
    print("Production runtime security settings initialized; secret material was not displayed.")
