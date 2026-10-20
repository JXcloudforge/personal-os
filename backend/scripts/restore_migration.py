"""Validate or restore a password-encrypted Personal OS migration package.

Restore always writes a new database path. This command never changes the
running service configuration or replaces an existing database.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from app.services.backup_service import restore_migration_package, validate_migration_package


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate or restore an encrypted Personal OS migration package")
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--target", type=Path, help="New, non-existing SQLite path for restore")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if not args.package.is_file() or args.package.stat().st_size > 250 * 1024 * 1024:
        print("Migration package is missing or exceeds 250 MB.", file=sys.stderr)
        return 2
    if not args.validate_only and args.target is None:
        parser.error("--target is required unless --validate-only is used")
    if args.target is not None and args.target.exists():
        print("Restore target already exists; refusing to overwrite.", file=sys.stderr)
        return 2
    password = getpass.getpass("Migration recovery passphrase: ")
    try:
        package = args.package.read_bytes()
        if args.validate_only:
            result = validate_migration_package(package, password)
            print(f"Package validation passed. Revision {result['alembic_revision']}; counts verified; provider credentials decrypt.")
            print("Validation used a temporary isolated directory; no production database was touched.")
        else:
            confirmation = input("Type RESTORE to create a new isolated database copy (production will not be switched): ")
            if confirmation != "RESTORE":
                print("Restore cancelled.")
                return 3
            result = restore_migration_package(package, password, args.target)
            print(f"Restored and validated a new database at {result['restored_to']}.")
            print("Imported sessions were revoked. The production service was not switched.")
            print("Keep the original database and activate only after stopping the service and explicit operator review.")
        return 0
    except (ValueError, OSError, RuntimeError, FileExistsError) as exc:
        print(f"Migration operation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
