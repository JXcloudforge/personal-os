"""Trusted local-terminal invitation operations. Never expose these commands over HTTP."""
from __future__ import annotations

import argparse
from datetime import timedelta
import sys

from app.database import SessionLocal
from app.services.invitation_service import (
    create_invitation,
    get_invitation_metadata,
    list_invitation_metadata,
    revoke_invitation,
)


def _format(row) -> str:
    claimed_user = str(row.claimed_by_user_id) if row.claimed_by_user_id is not None else "-"
    return f"id={row.id} state={row.state} created_at={row.created_at.isoformat()} expires_at={row.expires_at.isoformat()} claimed_at={row.claimed_at.isoformat() if row.claimed_at else '-'} claimed_by_user_id={claimed_user} revoked_at={row.revoked_at.isoformat() if row.revoked_at else '-'} issuer={row.issuer_kind}"


def _is_interactive_terminal() -> bool:
    return bool(sys.stdout.isatty())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage registration invitations from a trusted local terminal.")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Create and display a new invitation once")
    create.add_argument("--ttl-hours", type=int, default=168)
    commands.add_parser("list", help="List invitation metadata without token values")
    show = commands.add_parser("show", help="Show one invitation's metadata")
    show.add_argument("invitation_id", type=int)
    revoke = commands.add_parser("revoke", help="Revoke an unused, unexpired invitation")
    revoke.add_argument("invitation_id", type=int)
    args = parser.parse_args(argv)

    if args.command == "create" and not 1 <= args.ttl_hours <= 8760:
        print("TTL must be between 1 and 8760 hours.", file=sys.stderr)
        return 2
    if args.command == "create" and not _is_interactive_terminal():
        print("Invite creation requires an interactive terminal so the one-time code is not redirected to a log.", file=sys.stderr)
        return 2

    with SessionLocal() as db:
        try:
            if args.command == "create":
                token, row = create_invitation(db, ttl=timedelta(hours=args.ttl_hours))
                db.commit()
                print(f"Invitation id: {row.id}")
                print(f"Expires at: {row.expires_at.isoformat()}")
                print("Invite code (shown once): " + token)
                print("Store and share this code securely; it cannot be displayed again.")
                return 0
            if args.command == "list":
                rows = list_invitation_metadata(db)
                for row in rows:
                    print(_format(row))
                if not rows:
                    print("No invitations.")
                return 0
            if args.command == "show":
                row = get_invitation_metadata(db, args.invitation_id)
                if row is None:
                    print("Invitation not found.", file=sys.stderr)
                    return 4
                print(_format(row))
                return 0
            if args.command == "revoke":
                if not revoke_invitation(db, args.invitation_id):
                    db.rollback()
                    print("Invitation is missing or is no longer revocable.", file=sys.stderr)
                    return 4
                db.commit()
                print(f"Invitation {args.invitation_id} revoked.")
                return 0
        except Exception:
            db.rollback()
            print("Invitation operation failed; no sensitive details were displayed.", file=sys.stderr)
            return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
