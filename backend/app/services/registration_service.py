"""Invitation-backed registration and persistent anonymous request protection."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
import hashlib
import hmac
import secrets

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from ..auth.security import create_session_uncommitted, hash_password
from ..config import settings
from ..database import SessionLocal
from ..models import AuthSecurityState, User
from .invitation_service import InvitationUnavailable, claim_invitation


CHALLENGE_TTL = timedelta(minutes=10)
REGISTRATION_WINDOW = timedelta(minutes=15)
CHALLENGE_WINDOW = timedelta(hours=1)
REGISTRATION_IP_LIMIT = 30
REGISTRATION_IDENTITY_LIMIT = 5
CHALLENGE_IP_LIMIT = 60


class RegistrationRejected(ValueError):
    """A registration could not be completed; public details stay uniform."""


class RegistrationRateLimited(ValueError):
    pass


class RegistrationSecurityUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class RegistrationResult:
    user: User
    session_token: str
    csrf_token: str


@dataclass(frozen=True)
class AttemptResult:
    csrf_valid: bool
    rate_limited: bool


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _secret() -> bytes:
    value = settings.secret_key
    if not value or len(value) < 32:
        raise RegistrationSecurityUnavailable("Registration security is not configured")
    return value.encode("utf-8")


def _state_hash(kind: str, value: str) -> str:
    material = f"personal-os:{kind}:{value}".encode("utf-8")
    return hmac.new(_secret(), material, hashlib.sha256).hexdigest()


def _record_attempt(db: Session, *, kind: str, value: str, now: datetime, window: timedelta) -> int:
    digest = _state_hash(kind, value)
    expired = AuthSecurityState.expires_at <= now
    statement = (
        sqlite_insert(AuthSecurityState)
        .values(
            state_hash=digest,
            state_kind="rate_limit",
            window_started_at=now,
            attempt_count=1,
            expires_at=now + window,
        )
        .on_conflict_do_update(
            index_elements=[AuthSecurityState.state_hash],
            set_={
                "attempt_count": case((expired, 1), else_=AuthSecurityState.attempt_count + 1),
                "window_started_at": case((expired, now), else_=AuthSecurityState.window_started_at),
                "expires_at": case((expired, now + window), else_=AuthSecurityState.expires_at),
            },
        )
        .returning(AuthSecurityState.attempt_count)
    )
    return int(db.scalar(statement) or 0)


def issue_registration_challenge(*, source_ip: str) -> str:
    """Persist a short-lived one-time challenge and independently charge its IP bucket."""
    token = secrets.token_urlsafe(32)
    now = _now()
    try:
        with SessionLocal() as db, db.begin():
            db.execute(delete(AuthSecurityState).where(AuthSecurityState.expires_at <= now))
            attempts = _record_attempt(db, kind="challenge-ip", value=source_ip, now=now, window=CHALLENGE_WINDOW)
            if attempts <= CHALLENGE_IP_LIMIT:
                db.add(AuthSecurityState(
                    state_hash=_state_hash("csrf", token),
                    state_kind="csrf_challenge",
                    window_started_at=now,
                    attempt_count=0,
                    expires_at=now + CHALLENGE_TTL,
                ))
        if attempts > CHALLENGE_IP_LIMIT:
            raise RegistrationRateLimited("Registration rate limit reached")
        return token
    except RegistrationRateLimited:
        raise
    except (SQLAlchemyError, OSError) as exc:
        raise RegistrationSecurityUnavailable("Registration security storage is unavailable") from exc


def record_registration_attempt(*, source_ip: str, username: str, challenge: str | None) -> AttemptResult:
    """Commit throttling and single-use challenge state outside the user transaction."""
    now = _now()
    normalized_username = username.strip().lower()
    try:
        with SessionLocal() as db, db.begin():
            db.execute(delete(AuthSecurityState).where(AuthSecurityState.expires_at <= now))
            ip_attempts = _record_attempt(db, kind="registration-ip", value=source_ip, now=now, window=REGISTRATION_WINDOW)
            # Once the shared IP bucket is over limit, do not create rows for
            # attacker-chosen usernames; this bounds state growth per source.
            identity_attempts = 0
            if ip_attempts <= REGISTRATION_IP_LIMIT:
                identity_key = f"{source_ip}\0{normalized_username}"
                identity_attempts = _record_attempt(db, kind="registration-identity", value=identity_key, now=now, window=REGISTRATION_WINDOW)
            csrf_valid = False
            if challenge:
                result = db.execute(
                    update(AuthSecurityState)
                    .where(
                        AuthSecurityState.state_hash == _state_hash("csrf", challenge),
                        AuthSecurityState.state_kind == "csrf_challenge",
                        AuthSecurityState.expires_at > now,
                        AuthSecurityState.consumed_at.is_(None),
                    )
                    .values(consumed_at=now)
                )
                csrf_valid = result.rowcount == 1
            limited = ip_attempts > REGISTRATION_IP_LIMIT or identity_attempts > REGISTRATION_IDENTITY_LIMIT
        return AttemptResult(csrf_valid=csrf_valid, rate_limited=limited)
    except (SQLAlchemyError, OSError) as exc:
        raise RegistrationSecurityUnavailable("Registration security storage is unavailable") from exc


def register_with_invitation(
    db: Session,
    *,
    invitation_code: str,
    username: str,
    password: str,
) -> RegistrationResult:
    """Create invite claim, User and initial Session in one caller DB transaction."""
    password_hash = hash_password(password)
    normalized_username = username.strip().lower()
    try:
        with db.begin():
            existing = db.scalar(select(User.id).where(func.lower(User.username) == normalized_username))
            if existing is not None:
                raise RegistrationRejected("Registration could not be completed")

            user = User(username=normalized_username, password_hash=password_hash)
            db.add(user)
            db.flush()
            claim_invitation(db, invitation_code, user.id)
            user.last_login_at = _now()
            session_token, csrf_token = create_session_uncommitted(db, user)
        return RegistrationResult(user=user, session_token=session_token, csrf_token=csrf_token)
    except RegistrationRejected:
        raise
    except (InvitationUnavailable, IntegrityError) as exc:
        db.rollback()
        raise RegistrationRejected("Registration could not be completed") from exc
