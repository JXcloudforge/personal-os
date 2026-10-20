"""Local invitation lifecycle contract for the future registration transaction."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import secrets

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models import Invitation


def _now_utc() -> datetime:
    # Existing SQLite DateTime columns are naive; normalize UTC to that established format.
    return datetime.now(UTC).replace(tzinfo=None)


class InvitationUnavailable(ValueError):
    """The invite is invalid or cannot be claimed; details are intentionally hidden."""


@dataclass(frozen=True)
class InvitationMetadata:
    id: int
    state: str
    created_at: datetime
    expires_at: datetime
    claimed_at: datetime | None
    claimed_by_user_id: int | None
    revoked_at: datetime | None
    issuer_kind: str


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_invitation_state(invitation: Invitation, *, now: datetime | None = None) -> str:
    current = now or _now_utc()
    if invitation.claimed_at is not None:
        return "CLAIMED"
    if invitation.revoked_at is not None:
        return "REVOKED"
    if invitation.expires_at <= current:
        return "EXPIRED"
    return "OPEN"


def create_invitation(db: Session, *, ttl: timedelta, now: datetime | None = None) -> tuple[str, Invitation]:
    if ttl <= timedelta(0):
        raise ValueError("Invitation lifetime must be positive")
    created_at = now or _now_utc()
    token = "pos_inv_" + secrets.token_urlsafe(32)
    invitation = Invitation(
        token_hash=_token_digest(token),
        created_at=created_at,
        expires_at=created_at + ttl,
        issuer_kind="local_cli",
    )
    db.add(invitation)
    db.flush()
    return token, invitation


def list_invitation_metadata(db: Session, *, now: datetime | None = None) -> list[InvitationMetadata]:
    current = now or _now_utc()
    rows = db.scalars(select(Invitation).order_by(Invitation.id)).all()
    return [
        InvitationMetadata(
            id=row.id,
            state=get_invitation_state(row, now=current),
            created_at=row.created_at,
            expires_at=row.expires_at,
            claimed_at=row.claimed_at,
            claimed_by_user_id=row.claimed_by_user_id,
            revoked_at=row.revoked_at,
            issuer_kind=row.issuer_kind,
        )
        for row in rows
    ]


def get_invitation_metadata(db: Session, invitation_id: int, *, now: datetime | None = None) -> InvitationMetadata | None:
    row = db.get(Invitation, invitation_id)
    if row is None:
        return None
    return InvitationMetadata(
        id=row.id,
        state=get_invitation_state(row, now=now),
        created_at=row.created_at,
        expires_at=row.expires_at,
        claimed_at=row.claimed_at,
        claimed_by_user_id=row.claimed_by_user_id,
        revoked_at=row.revoked_at,
        issuer_kind=row.issuer_kind,
    )


def revoke_invitation(db: Session, invitation_id: int, *, now: datetime | None = None) -> bool:
    current = now or _now_utc()
    result = db.execute(
        update(Invitation)
        .where(
            Invitation.id == invitation_id,
            Invitation.claimed_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > current,
        )
        .values(revoked_at=current)
    )
    changed = result.rowcount == 1
    if changed:
        db.expire_all()
    return changed


def claim_invitation(db: Session, token: str, user_id: int, *, now: datetime | None = None) -> int:
    """Atomically claim within the caller's transaction; caller commits/rolls back all registration writes."""
    current = now or _now_utc()
    result = db.execute(
        update(Invitation)
        .where(
            Invitation.token_hash == _token_digest(token),
            Invitation.claimed_at.is_(None),
            Invitation.claimed_by_user_id.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > current,
        )
        .values(claimed_at=current, claimed_by_user_id=user_id)
    )
    if result.rowcount != 1:
        raise InvitationUnavailable("Invitation is unavailable")
    return int(result.rowcount)
