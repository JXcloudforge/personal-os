"""Controlled local SQLite backup/restore helper."""
import argparse
import sqlite3
from contextlib import closing
from pathlib import Path


def _sqlite_backup_to_new_file(source: Path, destination: Path) -> None:
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"SQLite source does not exist: {source}")
    if source == destination:
        raise ValueError("Source and destination must be different files")
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite existing database: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation closes the check-then-create race and ensures the
    # backup/restore operation can never silently overwrite an existing DB.
    with destination.open("xb"):
        pass
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src, closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)
        with closing(sqlite3.connect(destination.as_uri() + "?mode=ro", uri=True)) as check:
            quick = [row[0] for row in check.execute("PRAGMA quick_check")]
            foreign = list(check.execute("PRAGMA foreign_key_check"))
        if quick != ["ok"] or foreign:
            raise RuntimeError(f"Database copy failed integrity validation: quick_check={quick}, foreign_key_violations={len(foreign)}")
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def backup(source: Path, destination: Path) -> None:
    _sqlite_backup_to_new_file(source, destination)


def restore(source: Path, destination: Path) -> None:
    _sqlite_backup_to_new_file(source, destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["backup", "restore"])
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    (backup if args.operation == "backup" else restore)(args.source, args.destination)
    print(f"{args.operation} complete: {args.destination}")
