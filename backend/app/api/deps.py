from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..auth.security import get_session
from ..config import settings
from ..database import get_db
from ..models import User


def current_user(
    db: Session = Depends(get_db),
    personal_os_session: str | None = Cookie(default=None),
) -> User:
    session = get_session(db, personal_os_session)
    if not session or not session.user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return session.user


def require_csrf(
    request: Request,
    db: Session = Depends(get_db),
    personal_os_session: str | None = Cookie(default=None),
    x_csrf_token: str | None = Header(default=None),
) -> User:
    session = get_session(db, personal_os_session)
    if not session or not session.user.is_active:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not x_csrf_token or not hmac_compare(x_csrf_token, session.csrf_token):
        raise HTTPException(status_code=403, detail="CSRF validation failed")
    origin = request.headers.get("origin")
    if origin:
        allowed = {item.strip().rstrip("/") for item in settings.allowed_origins.split(",") if item.strip()}
        request_host = request.headers.get("host", "")
        forwarded_host = request.headers.get("x-forwarded-host", "")
        if origin.rstrip("/") not in allowed and not any(origin.endswith(f"://{host}") or origin.endswith(f"://{host}/") for host in [request_host, forwarded_host] if host):
            raise HTTPException(status_code=403, detail="Origin validation failed")
    return session.user


def hmac_compare(left: str, right: str) -> bool:
    import hmac
    return hmac.compare_digest(left, right)
