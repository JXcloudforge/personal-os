from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import tempfile
import zipfile
import base64
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from ..config import APP_ROOT, settings
from ..database import SessionLocal, engine
from ..release_history import current_app_version
from ..models import AIProvider, ChatMessage, KnowledgeCard, KnowledgeSourceReference, KnowledgeTaskLink, SearchProvider, SessionToken, Task, User


FORMAT_MAGIC = b"PERSONAL-OS-MIGRATION-1\n"
FORMAT_VERSION = 1
KDF_ITERATIONS = 600_000
MAX_PACKAGE_BYTES = 250 * 1024 * 1024
MAX_ARCHIVE_BYTES = 500 * 1024 * 1024
ALLOWED_MEMBERS = {"manifest.json", "database.sqlite3", "credential_master_key.txt", "provider_credentials.json"}
CURRENT_REVISION = "a6c7d8e9f0a1"
SUPPORTED_LOCAL_BACKUP_REVISIONS = {CURRENT_REVISION, "f3a4b5c6d7e8", "e8f9a0b1c2d3"}


def backup_directory() -> Path:
    return Path.home() / "AppData" / "Local" / "PersonalOS" / "backups"


def _db_path() -> Path:
    if engine.url.get_backend_name() != "sqlite" or not engine.url.database or engine.url.database == ":memory:":
        raise RuntimeError("Portable migration currently requires a file-backed SQLite database")
    path = Path(engine.url.database)
    return path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()


def _checked_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite existing file: {destination.name}")
    src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    try:
        with destination.open("xb"):
            pass
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
        check = sqlite3.connect(f"file:{destination.as_posix()}?mode=ro", uri=True)
        try:
            quick = [row[0] for row in check.execute("PRAGMA quick_check")]
            foreign = list(check.execute("PRAGMA foreign_key_check"))
            if quick != ["ok"] or foreign:
                raise RuntimeError(f"SQLite snapshot failed validation: quick_check={quick}, foreign_key_violations={len(foreign)}")
        finally:
            check.close()
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        src.close()


def _db_summary(path: Path, *, require_current: bool = True) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        integrity = [row[0] for row in connection.execute("PRAGMA quick_check")]
        foreign = list(connection.execute("PRAGMA foreign_key_check"))
        version_row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        if integrity != ["ok"] or foreign:
            raise ValueError("Database integrity or foreign-key validation failed")
        if not version_row or (require_current and version_row[0] not in SUPPORTED_LOCAL_BACKUP_REVISIONS):
            raise ValueError("Database migration version is not supported by this application")
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        counts = {table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]) for table in sorted(tables)}
        return {"revision": version_row[0], "schema_compatible": version_row[0] == CURRENT_REVISION, "table_counts": counts, "quick_check": "ok", "foreign_key_violations": 0}
    finally:
        connection.close()


def _validate_credentials(db_path: Path, master_key: str) -> None:
    try:
        cipher = Fernet(master_key.encode("ascii"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Credential encryption key in migration package is invalid") from exc
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        for table in ("ai_providers", "search_providers"):
            for (encrypted,) in connection.execute(f"SELECT encrypted_api_key FROM {table} WHERE encrypted_api_key IS NOT NULL"):
                try:
                    cipher.decrypt(encrypted.encode("ascii"))
                except (InvalidToken, ValueError, UnicodeError) as exc:
                    raise ValueError("Provider credentials could not be verified with the packaged encryption key") from exc
    finally:
        connection.close()


def _collect_provider_credentials(db_path: Path, master_key: str) -> list[dict[str, Any]]:
    cipher = Fernet(master_key.encode("ascii"))
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        credentials: list[dict[str, Any]] = []
        for user_id, provider, encrypted, base_url, model in connection.execute("SELECT user_id, provider, encrypted_api_key, base_url, model FROM ai_providers"):
            value = cipher.decrypt(encrypted.encode("ascii")).decode("utf-8") if encrypted else (settings.deepseek_api_key if provider == "deepseek" else None)
            if value:
                credentials.append({"kind": "ai", "user_id": user_id, "provider": provider, "api_key": value, "base_url": base_url, "model": model})
        for user_id, provider, encrypted in connection.execute("SELECT user_id, provider, encrypted_api_key FROM search_providers"):
            if encrypted:
                credentials.append({"kind": "search", "user_id": user_id, "provider": provider, "api_key": cipher.decrypt(encrypted.encode("ascii")).decode("utf-8")})
        return credentials
    finally:
        connection.close()


def _verify_credential_manifest(db_path: Path, credentials: list[dict[str, Any]], master_key: str) -> None:
    if any(not isinstance(item, dict) or item.get("kind") not in {"ai", "search"} or not item.get("api_key") or not isinstance(item.get("user_id"), int) for item in credentials):
        raise ValueError("Provider credential manifest is malformed")
    identities = [(item["kind"], item["user_id"], item["provider"]) for item in credentials]
    if len(identities) != len(set(identities)):
        raise ValueError("Provider credential manifest contains duplicate entries")
    cipher = Fernet(master_key.encode("ascii"))
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        users = {row[0] for row in connection.execute("SELECT id FROM users")}
        if any(item["user_id"] not in users for item in credentials):
            raise ValueError("Provider credential is associated with an unknown user")
        for table, kind in (("ai_providers", "ai"), ("search_providers", "search")):
            columns = "user_id, provider, encrypted_api_key" + (", base_url, model" if kind == "ai" else "")
            for row in connection.execute(f"SELECT {columns} FROM {table}"):
                user_id, provider, encrypted = row[:3]
                candidate = next((item for item in credentials if item["kind"] == kind and item["user_id"] == user_id and item["provider"] == provider), None)
                if encrypted:
                    try:
                        decrypted = cipher.decrypt(encrypted.encode("ascii")).decode("utf-8")
                    except (InvalidToken, UnicodeError) as exc:
                        raise ValueError("Database provider credential does not match its encryption key") from exc
                    if not candidate or candidate["api_key"] != decrypted:
                        raise ValueError("Provider credential manifest does not match encrypted database credentials")
                elif kind == "ai" and provider == "deepseek" and settings.deepseek_api_key and (not candidate or candidate["api_key"] != settings.deepseek_api_key):
                    raise ValueError("Environment DeepSeek credential was not included in the migration package")
    finally:
        connection.close()


def _orm_read_check(db_path: Path) -> dict[str, int]:
    test_engine = create_engine(f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False})
    try:
        with Session(test_engine) as session:
            return {
                "users": int(session.scalar(select(func.count(User.id))) or 0),
                "tasks": int(session.scalar(select(func.count(Task.id))) or 0),
                "chat_messages": int(session.scalar(select(func.count(ChatMessage.id))) or 0),
                "knowledge_cards": int(session.scalar(select(func.count(KnowledgeCard.id))) or 0),
                "knowledge_source_references": int(session.scalar(select(func.count(KnowledgeSourceReference.id))) or 0),
                "knowledge_task_links": int(session.scalar(select(func.count(KnowledgeTaskLink.id))) or 0),
                "ai_providers": int(session.scalar(select(func.count(AIProvider.id))) or 0),
                "search_providers": int(session.scalar(select(func.count(SearchProvider.id))) or 0),
            }
    finally:
        test_engine.dispose()


def _derive_cipher(passphrase: str, salt: bytes) -> Fernet:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=KDF_ITERATIONS)
    return Fernet(base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8"))))


def validate_recovery_passphrase(passphrase: str) -> None:
    categories = sum((any(c.islower() for c in passphrase), any(c.isupper() for c in passphrase), any(c.isdigit() for c in passphrase), any(not c.isalnum() and not c.isspace() for c in passphrase)))
    if len(passphrase) < 16 or categories < 3:
        raise ValueError("Recovery passphrase must be at least 16 characters and use at least three character types")


def create_local_backup() -> dict[str, Any]:
    directory = backup_directory()
    directory.mkdir(parents=True, exist_ok=True)
    backup_id = f"personal_os_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}"
    target = directory / f"{backup_id}.sqlite3"
    _checked_copy(_db_path(), target)
    summary = _db_summary(target)
    metadata = {"id": backup_id, "kind": "local_database_snapshot", "created_at": datetime.now(timezone.utc).isoformat(), "size_bytes": target.stat().st_size, "status": "verified", **summary}
    metadata_path = directory / f"{backup_id}.json"
    try:
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        target.unlink(missing_ok=True)
        raise
    with SessionLocal() as db:
        user = db.scalar(select(User).order_by(User.id).limit(1))
        if user:
            user.last_local_backup_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
    return metadata


def list_local_backups() -> list[dict[str, Any]]:
    directory = backup_directory()
    if not directory.exists():
        return []
    results = []
    for path in sorted(directory.glob("personal_os_*.sqlite3"), key=lambda item: item.stat().st_mtime, reverse=True)[:50]:
        metadata_path = path.with_suffix(".json")
        try:
            if metadata_path.exists():
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            else:
                metadata = {"id": path.stem, "kind": "local_database_snapshot", "created_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(), "size_bytes": path.stat().st_size, "status": "not_recently_verified"}
            actual = _db_summary(path, require_current=False)
            metadata.update({"size_bytes": path.stat().st_size, "status": "verified" if actual["schema_compatible"] else "verified_schema_upgrade_required", "alembic_revision": actual["revision"], "table_counts": actual["table_counts"]})
            results.append(metadata)
        except (OSError, ValueError, sqlite3.Error):
            results.append({"id": path.stem, "kind": "local_database_snapshot", "size_bytes": path.stat().st_size, "status": "integrity_check_failed"})
    for path in sorted(directory.glob("personal_os_migration_*.posmig"), key=lambda item: item.stat().st_mtime, reverse=True)[:20]:
        results.append({"id": path.stem, "kind": "encrypted_migration_package", "size_bytes": path.stat().st_size, "created_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(), "status": "encrypted_package"})
    return sorted(results, key=lambda item: item.get("created_at", ""), reverse=True)[:50]


def export_migration_package(passphrase: str) -> tuple[str, bytes, dict[str, Any]]:
    validate_recovery_passphrase(passphrase)
    if not settings.credential_master_key:
        raise RuntimeError("Credential master key is not configured; migration export cannot safely include provider credentials")
    directory = backup_directory()
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="personal-os-migration-") as temp_dir:
        db_copy = Path(temp_dir) / "database.sqlite3"
        _checked_copy(_db_path(), db_copy)
        summary = _db_summary(db_copy)
        _validate_credentials(db_copy, settings.credential_master_key)
        provider_credentials = _collect_provider_credentials(db_copy, settings.credential_master_key)
        _verify_credential_manifest(db_copy, provider_credentials, settings.credential_master_key)
        # ORM reads prove that this is usable by the current application model layer, not only valid SQLite bytes.
        orm_counts = _orm_read_check(db_copy)
        manifest = {
            "format": "personal-os-migration",
            "format_version": FORMAT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "app_version": current_app_version(),
            "alembic_revision": summary["revision"],
            "database_integrity": summary,
            "orm_counts": orm_counts,
            "files": {
                "database.sqlite3": hashlib.sha256(db_copy.read_bytes()).hexdigest(),
                "credential_master_key.txt": hashlib.sha256(settings.credential_master_key.encode("utf-8")).hexdigest(),
                "provider_credentials.json": hashlib.sha256(json.dumps(provider_credentials, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
            },
            "media_files": [],
            "media_note": "User-uploaded private theme background images are not included in this migration package; back them up separately before moving servers. Future attachments are not implemented.",
        }
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            bundle.writestr("manifest.json", json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8"))
            bundle.write(db_copy, "database.sqlite3")
            bundle.writestr("credential_master_key.txt", settings.credential_master_key.encode("utf-8"))
            bundle.writestr("provider_credentials.json", json.dumps(provider_credentials, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        salt = os.urandom(16)
        encrypted = FORMAT_MAGIC + salt + _derive_cipher(passphrase, salt).encrypt(archive.getvalue())
    package_id = f"personal_os_migration_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}"
    package_path = directory / f"{package_id}.posmig"
    try:
        with package_path.open("xb") as handle:
            handle.write(encrypted)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        package_path.unlink(missing_ok=True)
        raise
    metadata = {"id": package_id, "kind": "encrypted_migration_package", "created_at": datetime.now(timezone.utc).isoformat(), "size_bytes": len(encrypted), "status": "encrypted_package", "alembic_revision": summary["revision"], "table_counts": summary["table_counts"]}
    return package_path.name, encrypted, metadata


def _open_package(package: bytes, passphrase: str) -> tuple[dict[str, Any], bytes, str]:
    validate_recovery_passphrase(passphrase)
    if len(package) > MAX_PACKAGE_BYTES or not package.startswith(FORMAT_MAGIC) or len(package) <= len(FORMAT_MAGIC) + 16:
        raise ValueError("Unsupported, empty, or oversized migration package")
    start = len(FORMAT_MAGIC)
    salt = package[start:start + 16]
    try:
        plaintext = _derive_cipher(passphrase, salt).decrypt(package[start + 16:])
    except (InvalidToken, ValueError) as exc:
        raise ValueError("Recovery passphrase is incorrect or the encrypted package is damaged") from exc
    try:
        with zipfile.ZipFile(io.BytesIO(plaintext), "r") as bundle:
            infos = bundle.infolist()
            names = [info.filename for info in infos]
            if len(infos) != len(ALLOWED_MEMBERS) or set(names) != ALLOWED_MEMBERS or sum(item.file_size for item in infos) > MAX_ARCHIVE_BYTES:
                raise ValueError("Migration package file list or expanded size is invalid")
            for info in infos:
                mode = (info.external_attr >> 16) & 0o170000
                if info.is_dir() or mode == 0o120000 or ".." in Path(info.filename).parts or Path(info.filename).is_absolute():
                    raise ValueError("Migration package contains an unsafe path or file type")
            manifest = json.loads(bundle.read("manifest.json"))
            if manifest.get("format") != "personal-os-migration" or manifest.get("format_version") != FORMAT_VERSION:
                raise ValueError("Migration package format version is unsupported")
            db_bytes = bundle.read("database.sqlite3")
            master_key = bundle.read("credential_master_key.txt").decode("utf-8")
            provider_credentials = json.loads(bundle.read("provider_credentials.json"))
            expected = manifest.get("files", {})
            if hashlib.sha256(db_bytes).hexdigest() != expected.get("database.sqlite3") or hashlib.sha256(master_key.encode("utf-8")).hexdigest() != expected.get("credential_master_key.txt"):
                raise ValueError("Migration package checksum verification failed")
            if hashlib.sha256(json.dumps(provider_credentials, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest() != expected.get("provider_credentials.json"):
                raise ValueError("Provider credential manifest checksum verification failed")
            return manifest, db_bytes, master_key, provider_credentials
    except (zipfile.BadZipFile, KeyError, UnicodeError, json.JSONDecodeError) as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError("Migration package is malformed") from exc


def validate_migration_package(package: bytes, passphrase: str) -> dict[str, Any]:
    manifest, db_bytes, master_key, provider_credentials = _open_package(package, passphrase)
    with tempfile.TemporaryDirectory(prefix="personal-os-restore-check-") as temp_dir:
        db_path = Path(temp_dir) / "restored.sqlite3"
        with db_path.open("xb") as handle:
            handle.write(db_bytes)
        summary = _db_summary(db_path)
        if summary["revision"] != manifest.get("alembic_revision"):
            raise ValueError("Manifest and database migration versions do not match")
        _validate_credentials(db_path, master_key)
        _verify_credential_manifest(db_path, provider_credentials, master_key)
        orm_counts = _orm_read_check(db_path)
        if orm_counts != manifest.get("orm_counts"):
            raise ValueError("Restored ORM record counts differ from the package manifest")
    return {"valid": True, "format_version": FORMAT_VERSION, "created_at": manifest.get("created_at"), "app_version": manifest.get("app_version"), "alembic_revision": summary["revision"], "table_counts": summary["table_counts"], "provider_credentials_verified": True, "provider_credential_count": len(provider_credentials), "media_files": manifest.get("media_files", []), "recovery_note": "Validation ran in a temporary directory; production database was not modified."}


def restore_migration_package(package: bytes, passphrase: str, target: Path) -> dict[str, Any]:
    target = target.resolve()
    if target.exists():
        raise FileExistsError("Restore target already exists; refusing to overwrite")
    result = validate_migration_package(package, passphrase)
    _, db_bytes, master_key, provider_credentials = _open_package(package, passphrase)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as handle:
        handle.write(db_bytes)
    try:
        restored_engine = create_engine(f"sqlite:///{target.as_posix()}", connect_args={"check_same_thread": False})
        try:
            with Session(restored_engine) as session:
                cipher = Fernet(master_key.encode("ascii"))
                for credential in provider_credentials:
                    if credential["kind"] == "ai":
                        provider = session.scalar(select(AIProvider).where(AIProvider.user_id == credential["user_id"], AIProvider.provider == credential["provider"]))
                        if provider:
                            provider.encrypted_api_key = cipher.encrypt(credential["api_key"].encode("utf-8")).decode("ascii")
                            provider.status = "configured"
                    else:
                        provider = session.scalar(select(SearchProvider).where(SearchProvider.user_id == credential["user_id"], SearchProvider.provider == credential["provider"]))
                        if provider:
                            provider.encrypted_api_key = cipher.encrypt(credential["api_key"].encode("utf-8")).decode("ascii")
                            provider.status = "configured"
                session.execute(delete(SessionToken))
                session.commit()
            with sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True) as check:
                if check.execute("PRAGMA quick_check").fetchone()[0] != "ok" or check.execute("PRAGMA foreign_key_check").fetchall():
                    raise RuntimeError("Restored database failed post-restore integrity checks")
        finally:
            restored_engine.dispose()
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return {**result, "restored_to": str(target), "sessions_revoked": True, "requires_manual_activation": True}


def run_due_local_backups() -> None:
    from datetime import timedelta

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as db:
        users = db.scalars(select(User).where(User.auto_backup_enabled.is_(True))).all()
        due_ids = [user.id for user in users if user.last_local_backup_at is None or now - user.last_local_backup_at >= timedelta(days=user.backup_interval_days)]
    for user_id in due_ids:
        try:
            metadata = create_local_backup()
            # The application is single-user today; keep the update user scoped for forward compatibility.
            with SessionLocal() as db:
                user = db.get(User, user_id)
                if user:
                    user.last_local_backup_at = datetime.fromisoformat(metadata["created_at"]).astimezone(timezone.utc).replace(tzinfo=None)
                    db.commit()
        except Exception:
            # Scheduled backup errors are surfaced in the backup status endpoint; never log data or secrets.
            continue
