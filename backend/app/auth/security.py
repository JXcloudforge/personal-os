import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import SessionToken, User


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_text, digest_text = stored.split("$", 2)
        salt = base64.urlsafe_b64decode(salt_text)
        expected = base64.urlsafe_b64decode(digest_text)
        actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def create_session_uncommitted(db: Session, user: User) -> tuple[str, str]:
    """Add a session to the caller's transaction and flush without committing."""
    raw_token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    record = SessionToken(
        user_id=user.id,
        token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        csrf_token=csrf_token,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=settings.session_days),
    )
    db.add(record)
    db.flush()
    return raw_token, csrf_token


def create_session(db: Session, user: User) -> tuple[str, str]:
    """Create a session using the legacy self-committing contract."""
    raw_token, csrf_token = create_session_uncommitted(db, user)
    db.commit()
    return raw_token, csrf_token


def get_session(db: Session, raw_token: str | None) -> SessionToken | None:
    if not raw_token:
        return None
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    record = db.scalar(select(SessionToken).where(SessionToken.token_hash == token_hash))
    if not record or record.expires_at < datetime.now(timezone.utc).replace(tzinfo=None):
        return None
    return record
