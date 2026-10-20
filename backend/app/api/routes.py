from datetime import date, datetime, timedelta, timezone
import io
import hashlib
import hmac
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import time

from fastapi import APIRouter, Cookie, Depends, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import delete, func, inspect, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from ..auth.security import create_session, get_session, hash_password, verify_password
from ..config import settings
from ..release_history import current_app_version, load_release_history
from ..database import get_db
from ..models import AIProvider, AIRequest, AgentAuditEvent, AgentModelUsage, AgentRun, AgentToolCall, ChatConversation, ChatMessage, DailyReview, DailyTaskSnapshot, HealthMetric, SearchProvider, SessionToken, StudyError, StudyRecord, Task, TaskConversationLink, TaskDateHistory, TaskManagerOperation, TaskPurgeReceipt, TaskStatus, TaskStatusHistory, User, UserGoal, Workout
from ..schemas.agent import AgentChatRequest, ChatConversationCreate, ChatConversationPatch, EveningReviewRequest, GoalIn, GoalPatch, PlanRequest, ProposalActionIn, PurgeContentRequest, SearchCredentialIn, TaskManagerChatRequest
from ..schemas.api import AIIn, AIProviderUpdate, AccountProfilePatch, AppearancePatch, ChatTaskProposalIn, EvidenceIn, HealthMetricIn, LoginIn, MigrationExportIn, PasswordChangeIn, RegistrationIn, ReviewIn, SetupIn, StudyErrorIn, StudyIn, TaskConversationLinkIn, TaskIn, TaskPatch, UsageBudgetPatch, UserDataSettingsPatch, WorkoutIn
from ..services.agent_runner import AgentRunError, run_chat, run_evening, run_plan
from ..services import knowledge_service, review_service
from ..services.agent_audit import fail_model_usage, finish_model_usage, finish_run, start_model_usage, start_run
from ..services.proposal_service import apply_proposal_action, get_owned_proposal, list_proposals, proposal_view, save_generated_proposal
from ..services.provider_service import CredentialUnavailable, ProviderCredentialRejected, DEFAULT_PROVIDERS, encrypt_api_key, ensure_provider, provider_view, require_user_api_key, test_connection
from ..services.registration_service import RegistrationRateLimited, RegistrationRejected, RegistrationSecurityUnavailable, issue_registration_challenge, record_registration_attempt, register_with_invitation
from ..services.backup_service import validate_migration_package, validate_recovery_passphrase
from ..services.theme_service import MAX_BACKGROUND_BYTES, background_path, inspect_background_image, remove_background, store_background
from ..services.task_manager import CreateTasksInput, ManualTaskInput, TaskPurgeBlockedError, add_task_evidence, allocate_task_id, create_manual_task as create_manual_task_service, create_tasks, dashboard_task_rows, explicit_task_write_intent, is_task_confirmation, purge_task as purge_task_service, requested_task_count, task_manager_required_tools, task_purge_preview as task_purge_preview_service
from .deps import current_user, require_csrf

router = APIRouter(prefix="/api")
FAILED_LOGINS: dict[str, list[float]] = {}


@router.get("/settings/ai/providers")
def ai_providers(user: User = Depends(current_user), db: Session = Depends(get_db)):
    providers = [ensure_provider(db, name, user.id) for name in DEFAULT_PROVIDERS]
    db.commit()
    return [provider_view(provider) for provider in providers]


@router.get("/settings/search")
def search_settings(user: User = Depends(current_user), db: Session = Depends(get_db)):
    provider = db.query(SearchProvider).filter_by(user_id=user.id, provider="tavily").one_or_none()
    return {"provider": "tavily", "configured": bool(provider and provider.encrypted_api_key), "enabled": bool(provider and provider.enabled), "status": provider.status if provider else "not_configured"}


@router.put("/settings/search/tavily")
def save_search_settings(payload: SearchCredentialIn, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    provider = db.query(SearchProvider).filter_by(user_id=user.id, provider="tavily").one_or_none()
    if provider is None:
        provider = SearchProvider(user_id=user.id, provider="tavily")
        db.add(provider)
    try:
        provider.encrypted_api_key = encrypt_api_key(payload.api_key)
    except CredentialUnavailable as exc:
        raise HTTPException(503, detail={"code": "CREDENTIAL_SERVICE_NOT_CONFIGURED", "message": str(exc)}) from exc
    provider.enabled = True
    provider.status = "configured"
    db.commit()
    return {"provider": "tavily", "configured": True, "enabled": True, "status": "configured"}


@router.delete("/settings/search/tavily")
def delete_search_settings(user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    provider = db.query(SearchProvider).filter_by(user_id=user.id, provider="tavily").one_or_none()
    if provider:
        provider.encrypted_api_key = None
        provider.enabled = False
        provider.status = "not_configured"
        db.commit()
    return {"provider": "tavily", "configured": False, "enabled": False, "status": "not_configured"}


@router.put("/settings/ai/providers/{provider_name}")
def update_ai_provider(provider_name: str, payload: AIProviderUpdate, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    if provider_name not in DEFAULT_PROVIDERS:
        raise HTTPException(404, "Provider not found")
    provider = ensure_provider(db, provider_name, user.id)
    changes = payload.model_dump(exclude_unset=True)
    if "api_key" in changes:
        try:
            provider.encrypted_api_key = encrypt_api_key(changes.pop("api_key"))
        except CredentialUnavailable as exc:
            raise HTTPException(503, detail={"code": "CREDENTIAL_SERVICE_NOT_CONFIGURED", "message": str(exc)}) from exc
        provider.status = "configured"
        provider.last_error = None
    for key, value in changes.items():
        setattr(provider, key, value)
    db.commit()
    return provider_view(provider)


@router.delete("/settings/ai/providers/{provider_name}/credential")
def delete_ai_credential(provider_name: str, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    if provider_name not in DEFAULT_PROVIDERS:
        raise HTTPException(404, "Provider not found")
    provider = ensure_provider(db, provider_name, user.id)
    provider.encrypted_api_key = None
    provider.enabled = False
    provider.status = "not_configured"
    provider.last_error = None
    db.commit()
    return provider_view(provider)


@router.post("/settings/ai/providers/{provider_name}/test")
async def test_ai_provider(provider_name: str, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    if provider_name not in DEFAULT_PROVIDERS:
        raise HTTPException(404, "Provider not found")
    provider = ensure_provider(db, provider_name, user.id)
    try:
        api_key = require_user_api_key(provider, user.id)
    except ProviderCredentialRejected as exc:
        raise HTTPException(503, detail={"code": exc.code, "message": str(exc)}) from exc
    except CredentialUnavailable as exc:
        raise HTTPException(503, detail={"code": "CREDENTIAL_SERVICE_NOT_CONFIGURED", "message": str(exc)}) from exc
    run = start_run(db, user.id, "provider_test", provider_name, provider_name, provider.model, "provider_connection_test")
    usage_row = start_model_usage(db, run, 1, provider=provider_name, model=provider.model)
    result = await test_connection(provider, api_key)
    provider.status = result.status
    provider.last_error = None if result.ok else result.detail
    if result.ok:
        prompt_tokens, completion_tokens = finish_model_usage(usage_row, {"prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens})
        provider.total_prompt_tokens += prompt_tokens
        provider.total_completion_tokens += completion_tokens
        finish_run(db, run, "completed", prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    else:
        fail_model_usage(usage_row, result.status)
        finish_run(db, run, "failed", error_code=result.status)
    db.commit()
    return {"ok": result.ok, "status": result.status, "detail": result.detail, "run_id": run.id, "usage": {"prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens}, "provider": provider_view(provider)}


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(select(1))
    return {"status": "ok", "database": "ok"}


@router.get("/auth/status")
def auth_status(db: Session = Depends(get_db)):
    setup_required = db.scalar(select(func.count(User.id))) == 0
    return {"setup_required": setup_required, "setup_method": "local_cli" if setup_required and settings.runtime_environment == "production" else "web_local_only"}


def _origin_is_allowed(request: Request, *, require_origin: bool) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return not require_origin
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        return False
    normalized_origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}".rstrip("/")
    allowed = {item.strip().rstrip("/").lower() for item in settings.allowed_origins.split(",") if item.strip()}
    same_origin = f"{request.url.scheme.lower()}://{request.url.netloc.lower()}".rstrip("/")
    return normalized_origin == same_origin or normalized_origin in allowed


def _request_source_ip(request: Request) -> str:
    # Forwarding headers are deliberately ignored: only the ASGI peer address is trusted.
    return request.client.host if request.client else "unknown"


@router.get("/auth/registration/challenge")
def registration_challenge(response: Response, request: Request):
    if not _origin_is_allowed(request, require_origin=False):
        raise HTTPException(403, "Origin validation failed")
    try:
        challenge = issue_registration_challenge(source_ip=_request_source_ip(request))
    except RegistrationRateLimited as exc:
        raise HTTPException(429, "Too many registration requests; try again later") from exc
    except RegistrationSecurityUnavailable as exc:
        raise HTTPException(503, "Registration security is unavailable") from exc
    secure = _request_uses_secure_cookies(request)
    response.set_cookie("personal_os_registration_csrf", challenge, httponly=True, samesite="strict", secure=secure, max_age=600, path="/api/auth")
    response.headers["Cache-Control"] = "no-store"
    return {"challenge": challenge, "expires_in": 600}


@router.post("/auth/register", status_code=201)
def register(payload: RegistrationIn, response: Response, request: Request, db: Session = Depends(get_db), x_registration_csrf: str | None = Header(default=None, alias="X-Registration-CSRF"), registration_cookie: str | None = Cookie(default=None, alias="personal_os_registration_csrf")):
    if not _origin_is_allowed(request, require_origin=True):
        raise HTTPException(403, "Origin validation failed")
    challenge_matches = bool(x_registration_csrf and registration_cookie and hmac.compare_digest(x_registration_csrf, registration_cookie))
    attempt = record_registration_attempt(
        source_ip=_request_source_ip(request),
        username=payload.username,
        challenge=x_registration_csrf if challenge_matches else None,
    )
    if attempt.rate_limited:
        response.delete_cookie("personal_os_registration_csrf", path="/api/auth")
        raise HTTPException(429, "Too many registration attempts; try again later")
    if not challenge_matches or not attempt.csrf_valid:
        response.delete_cookie("personal_os_registration_csrf", path="/api/auth")
        raise HTTPException(403, "Registration CSRF validation failed")
    try:
        result = register_with_invitation(
            db,
            invitation_code=payload.invitation_code,
            username=payload.username,
            password=payload.password,
        )
    except RegistrationRejected as exc:
        response.delete_cookie("personal_os_registration_csrf", path="/api/auth")
        raise HTTPException(400, "Registration could not be completed") from exc
    except RegistrationSecurityUnavailable as exc:
        response.delete_cookie("personal_os_registration_csrf", path="/api/auth")
        raise HTTPException(503, "Registration security is unavailable") from exc
    set_auth_cookies(response, result.session_token, result.csrf_token, request)
    response.delete_cookie("personal_os_registration_csrf", path="/api/auth")
    response.headers["Cache-Control"] = "no-store"
    return {"username": result.user.username}


def _request_uses_secure_cookies(request: Request) -> bool:
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    forwarded_host = request.headers.get("x-forwarded-host", "").split(",", 1)[0].strip().lower()
    host = (forwarded_host or request.url.hostname or "").split(":", 1)[0].rstrip(".").lower()
    return bool(
        settings.cookie_secure
        or request.url.scheme.lower() == "https"
        or forwarded_proto == "https"
        or host.endswith(".ts.net")
    )


def set_auth_cookies(response: Response, raw: str, csrf: str, request: Request) -> None:
    secure = _request_uses_secure_cookies(request)
    response.set_cookie("personal_os_session", raw, httponly=True, samesite="lax", secure=secure, max_age=settings.session_days * 86400, path="/")
    response.set_cookie("personal_os_csrf", csrf, httponly=False, samesite="lax", secure=secure, max_age=settings.session_days * 86400, path="/")


@router.post("/auth/setup")
def setup(payload: SetupIn, response: Response, request: Request, db: Session = Depends(get_db)):
    if settings.runtime_environment == "production":
        raise HTTPException(403, "Production account initialization must use the protected local CLI")
    if db.scalar(select(func.count(User.id))) != 0:
        raise HTTPException(409, "Initial setup is already complete")
    user = User(username=payload.username, password_hash=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    user.last_login_at = datetime.now()
    db.commit()
    raw, csrf = create_session(db, user)
    set_auth_cookies(response, raw, csrf, request)
    return {"username": user.username, "setup_required": False}


@router.post("/auth/login")
def login(payload: LoginIn, response: Response, request: Request, db: Session = Depends(get_db)):
    key = f"{request.client.host if request.client else 'local'}:{payload.username.lower()}"
    now = time.time()
    FAILED_LOGINS[key] = [stamp for stamp in FAILED_LOGINS.get(key, []) if now - stamp < 60]
    if len(FAILED_LOGINS[key]) >= 5:
        raise HTTPException(429, "Too many failed login attempts; try again later")
    user = db.scalar(select(User).where(User.username == payload.username))
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        FAILED_LOGINS.setdefault(key, []).append(now)
        raise HTTPException(401, "Invalid username or password")
    FAILED_LOGINS.pop(key, None)
    user.last_login_at = datetime.now()
    db.commit()
    raw, csrf = create_session(db, user)
    set_auth_cookies(response, raw, csrf, request)
    return {"username": user.username}


@router.post("/auth/logout")
def logout(response: Response, user: User = Depends(require_csrf), db: Session = Depends(get_db), personal_os_session: str | None = Cookie(default=None)):
    session = get_session(db, personal_os_session)
    if session:
        db.delete(session)
        db.commit()
    response.delete_cookie("personal_os_session")
    response.delete_cookie("personal_os_csrf")
    return {"ok": True}


@router.get("/auth/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "username": user.username, "display_name": user.display_name}


@router.get("/account/profile")
def account_profile(user: User = Depends(current_user), db: Session = Depends(get_db), personal_os_session: str | None = Cookie(default=None)):
    current = get_session(db, personal_os_session)
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
        "current_session_created_at": current.created_at if current else None,
        "active_sessions": db.scalar(select(func.count(SessionToken.id)).where(SessionToken.user_id == user.id)) or 0,
    }


@router.patch("/account/profile")
def update_account_profile(payload: AccountProfilePatch, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    user.username = payload.username
    user.display_name = payload.display_name.strip() if payload.display_name and payload.display_name.strip() else None
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "That username is already in use") from exc
    return {"id": user.id, "username": user.username, "display_name": user.display_name}


@router.post("/account/password")
def change_account_password(payload: PasswordChangeIn, user: User = Depends(require_csrf), db: Session = Depends(get_db), personal_os_session: str | None = Cookie(default=None)):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(403, "Current password is incorrect")
    new_password = payload.new_password
    categories = sum((any(c.islower() for c in new_password), any(c.isupper() for c in new_password), any(c.isdigit() for c in new_password), any(not c.isalnum() and not c.isspace() for c in new_password)))
    if len(new_password) < 12 or categories < 3:
        raise HTTPException(422, "Use at least 12 characters and three character types")
    if verify_password(new_password, user.password_hash):
        raise HTTPException(422, "New password must differ from the current password")
    user.password_hash = hash_password(new_password)
    revoked = 0
    if payload.revoke_other_sessions:
        current = get_session(db, personal_os_session)
        if current is None:
            raise HTTPException(401, "Current session has expired; sign in again")
        sessions = db.scalars(select(SessionToken).where(SessionToken.user_id == user.id, SessionToken.id != current.id)).all()
        revoked = len(sessions)
        for session in sessions:
            db.delete(session)
    db.commit()
    return {"password_changed": True, "other_sessions_revoked": revoked}


@router.get("/account/data-settings")
def get_account_data_settings(user: User = Depends(current_user)):
    return {"chat_history_enabled": user.chat_history_enabled, "auto_backup_enabled": user.auto_backup_enabled, "backup_interval_days": user.backup_interval_days, "last_local_backup_at": user.last_local_backup_at, "last_recovery_validation_at": user.last_recovery_validation_at, "last_offsite_backup_at": None}


@router.patch("/account/data-settings")
def update_account_data_settings(payload: UserDataSettingsPatch, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    values = payload.model_dump(exclude_unset=True)
    if {"auto_backup_enabled", "backup_interval_days"} & values.keys():
        raise HTTPException(status_code=403, detail={"code": "INSTANCE_BACKUP_WEB_DISABLED", "message": "Web instance backup controls are disabled until an administrator permission model is available."})
    for key, value in values.items():
        setattr(user, key, value)
    if values.get("chat_history_enabled") is False:
        db.execute(delete(ChatMessage).where(ChatMessage.user_id == user.id))
        db.execute(delete(ChatConversation).where(ChatConversation.user_id == user.id))
    db.commit()
    return get_account_data_settings(user)


def appearance_view(user: User) -> dict:
    has_background = background_path(user) is not None
    return {
        "theme": user.theme if user.theme in {"classic", "cyberpunk-night"} else "classic",
        "has_background": has_background,
        "background_url": "/api/account/appearance/background" if has_background else None,
        "background_darkness": user.theme_background_darkness,
        "background_blur": user.theme_background_blur,
        "background_opacity": user.theme_background_opacity,
    }


@router.get("/account/appearance")
def get_appearance(user: User = Depends(current_user)):
    return appearance_view(user)


@router.patch("/account/appearance")
def update_appearance(payload: AppearancePatch, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    fields = {
        "theme": "theme",
        "background_darkness": "theme_background_darkness",
        "background_blur": "theme_background_blur",
        "background_opacity": "theme_background_opacity",
    }
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, fields[key], value)
    db.commit()
    return appearance_view(user)


@router.put("/account/appearance/background")
async def upload_appearance_background(
    background: UploadFile = File(...),
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    data = await background.read(MAX_BACKGROUND_BYTES + 1)
    await background.close()
    if not data or len(data) > MAX_BACKGROUND_BYTES:
        raise HTTPException(413, "Background image must be between 1 byte and 5 MB")
    try:
        extension, _media_type, _width, _height = inspect_background_image(data)
    except ValueError as exc:
        raise HTTPException(415, str(exc)) from exc
    old_name = user.theme_background_filename
    new_name = store_background(user.id, data, extension)
    user.theme_background_filename = new_name
    try:
        db.commit()
    except Exception:
        db.rollback()
        remove_background(user.id, new_name)
        raise
    remove_background(user.id, old_name)
    return appearance_view(user)


@router.get("/account/appearance/background")
def get_appearance_background(user: User = Depends(current_user)):
    path = background_path(user)
    if path is None:
        raise HTTPException(404, "No custom theme background is configured")
    media_type = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}.get(path.suffix.lower())
    if not media_type:
        raise HTTPException(404, "No custom theme background is configured")
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox"},
    )


@router.delete("/account/appearance/background")
def delete_appearance_background(user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    old_name = user.theme_background_filename
    user.theme_background_filename = None
    db.commit()
    remove_background(user.id, old_name)
    return appearance_view(user)


@router.get("/data/backups")
def get_backup_status(user: User = Depends(current_user)):
    raise HTTPException(status_code=403, detail={"code": "INSTANCE_BACKUP_WEB_DISABLED", "message": "Web instance backup management is disabled until an administrator permission model is available."})


@router.post("/data/backups")
def create_backup(user: User = Depends(require_csrf)):
    raise HTTPException(status_code=403, detail={"code": "INSTANCE_BACKUP_WEB_DISABLED", "message": "Web instance backup management is disabled until an administrator permission model is available."})


@router.post("/data/migration/export")
def download_migration_package(payload: MigrationExportIn, user: User = Depends(require_csrf)):
    raise HTTPException(status_code=403, detail={"code": "INSTANCE_BACKUP_WEB_DISABLED", "message": "Web full-database migration export is disabled until an administrator permission model is available."})


@router.post("/data/migration/validate")
async def validate_uploaded_migration(request: Request, user: User = Depends(require_csrf), db: Session = Depends(get_db), migration_file: UploadFile = File(...), recovery_password: str = Form(...)):
    try:
        validate_recovery_passphrase(recovery_password)
        package = await migration_file.read(250 * 1024 * 1024 + 1)
        if len(package) > 250 * 1024 * 1024:
            raise ValueError("Migration package exceeds the 250 MB limit")
        result = validate_migration_package(package, recovery_password)
        user.last_recovery_validation_at = datetime.now()
        # Persist only the validation timestamp, never the passphrase, package, key, or file contents.
        db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        await migration_file.close()


@router.get("/data/recovery-instructions")
def recovery_instructions(user: User = Depends(current_user)):
    return {
        "current_mode": "local_sqlite",
        "restore_command": "python -m scripts.restore_migration --package <path-to-posmig> --target <new-empty-db-path>",
        "rules": ["Stop the Personal OS service before any activation.", "Restore only to a new path; existing targets are refused.", "Validate the package in the web UI or with the CLI first.", "The restore copy revokes all imported sessions; sign in again after activation.", "Keep the original database and a separate verified backup until the new service has been checked."],
        "credential_note": "The migration package contains the Fernet master key only inside the password-encrypted archive; do not share the recovery password with the package.",
        "activation": "The restore command creates a separate database and never switches or overwrites the running production database. Update the local backend configuration only after explicit operator approval and a stopped service.",
    }


@router.get("/system/info")
def system_info(user: User = Depends(current_user), db: Session = Depends(get_db)):
    revision = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none() if inspect(db.bind).has_table("alembic_version") else None
    history = load_release_history()
    return {"app_version": current_app_version(), "runtime_environment": settings.runtime_environment, "database": "sqlite" if settings.database_url.startswith("sqlite") else "other", "database_path": Path(settings.database_url.replace("sqlite:///", "")).name if settings.database_url.startswith("sqlite:///") else "configured", "alembic_revision": revision, "service_online": True, "offsite_backup": "not_configured", "releases": history["releases"], "roadmap": history["roadmap"]}


def task_out(task: Task):
    return {"id": task.id, "task_code": task.task_code, "title": task.title, "description": task.description, "module": task.module, "status": task.status.value, "priority": task.priority, "estimated_minutes": task.estimated_minutes, "start_date": task.start_date, "due_date": task.due_date, "expected_duration_days": task.expected_duration_days, "completed_at": task.completed_at, "is_pinned": task.is_pinned, "archived": task.archived_at is not None, "deleted": task.deleted_at is not None, "planned_due": task.planned_due, "acceptance_criteria": task.acceptance_criteria, "created_at": task.created_at, "updated_at": task.updated_at, "evidence_count": len(task.evidence)}


def _local_today() -> date:
    try:
        return datetime.now(ZoneInfo(settings.app_timezone)).date()
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(503, detail={"code": "TIMEZONE_NOT_CONFIGURED", "message": "Configured application timezone is unavailable"}) from exc


@router.get("/tasks")
def list_tasks(q: str | None = None, module: str | None = None, status: TaskStatus | None = None, timeline: str | None = None, include_archived: bool = False, include_deleted: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    conditions = [Task.user_id == user.id]
    conditions.append(Task.deleted_at.is_not(None) if include_deleted else Task.deleted_at.is_(None))
    if not include_deleted:
        conditions.append(Task.archived_at.is_not(None) if include_archived else Task.archived_at.is_(None))
    if q and q.strip():
        needle = f"%{q.strip()[:120]}%"
        conditions.append(or_(Task.title.ilike(needle), Task.description.ilike(needle), Task.acceptance_criteria.ilike(needle)))
    if module:
        conditions.append(Task.module == module[:40])
    if status:
        conditions.append(Task.status == status)
    today = _local_today()
    if timeline == "today":
        conditions.extend([Task.status.notin_([TaskStatus.DONE, TaskStatus.CANCELLED]), Task.start_date <= today, Task.due_date >= today])
    elif timeline == "future":
        conditions.extend([Task.status.notin_([TaskStatus.DONE, TaskStatus.CANCELLED]), Task.start_date > today])
    elif timeline == "overdue":
        conditions.extend([Task.status.notin_([TaskStatus.DONE, TaskStatus.CANCELLED]), Task.due_date < today])
    elif timeline not in {None, ""}:
        raise HTTPException(422, detail={"code": "INVALID_TASK_TIMELINE", "message": "timeline must be today, future, or overdue"})
    tasks = db.scalars(select(Task).where(*conditions).options(selectinload(Task.evidence)).order_by(Task.is_pinned.desc(), Task.due_date.asc().nullslast(), Task.start_date.asc().nullslast(), Task.updated_at.desc())).all()
    return [task_out(task) for task in tasks]


def _owned_task(db: Session, user_id: int, task_id: int, *, include_deleted: bool = False) -> Task:
    task = db.get(Task, task_id)
    if not task or task.user_id != user_id or (task.deleted_at is not None and not include_deleted):
        raise HTTPException(404, detail={"code": "TASK_NOT_FOUND", "message": "Task not found"})
    return task


def _task_link(db: Session, user_id: int, task_id: int, conversation_id: str, message_id: int | None):
    if message_id is not None:
        message = db.get(ChatMessage, message_id)
        if not message or message.user_id != user_id or message.conversation_id != conversation_id:
            raise HTTPException(404, detail={"code": "MESSAGE_NOT_FOUND", "message": "Message not found in this conversation"})
    existing = db.scalar(select(TaskConversationLink).where(TaskConversationLink.user_id == user_id, TaskConversationLink.task_id == task_id, TaskConversationLink.conversation_id == conversation_id, TaskConversationLink.message_id == message_id))
    if existing:
        return existing, False
    link = TaskConversationLink(user_id=user_id, task_id=task_id, conversation_id=conversation_id, message_id=message_id)
    db.add(link)
    db.flush()
    return link, True


def _task_detail(db: Session, task: Task, user_id: int):
    links = db.execute(select(TaskConversationLink, ChatConversation, ChatMessage).join(ChatConversation, ChatConversation.id == TaskConversationLink.conversation_id).outerjoin(ChatMessage, ChatMessage.id == TaskConversationLink.message_id).where(TaskConversationLink.user_id == user_id, TaskConversationLink.task_id == task.id).order_by(TaskConversationLink.id)).all()
    return {**task_out(task), "description": task.description, "acceptance_criteria": task.acceptance_criteria,
        "evidence": [{"id": item.id, "evidence_type": item.evidence_type, "verification_level": item.verification_level, "content": item.content, "source": item.source, "created_at": item.created_at} for item in sorted(task.evidence, key=lambda item: item.id)],
        "history": [{"id": item.id, "from_status": item.from_status, "to_status": item.to_status, "reason": item.reason, "created_at": item.created_at} for item in sorted(task.history, key=lambda item: item.id)],
        "date_history": [{"id": item.id, "old_start_date": item.old_start_date, "new_start_date": item.new_start_date, "old_due_date": item.old_due_date, "new_due_date": item.new_due_date, "reason": item.reason, "changed_at": item.changed_at} for item in sorted(task.date_history, key=lambda item: item.id)],
        "links": [{"id": link.id, "conversation_id": conversation.id, "conversation_title": conversation.title, "domain": conversation.domain, "message_id": message.id if message else None, "message_preview": message.content[:180] if message else None, "created_at": link.created_at} for link, conversation, message in links]}


@router.get("/tasks/{task_id}")
def get_task(task_id: int, include_deleted: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    purged = db.scalar(select(TaskPurgeReceipt.id).where(TaskPurgeReceipt.user_id == user.id, TaskPurgeReceipt.task_id == task_id))
    if purged:
        raise HTTPException(410, detail={"code": "TASK_PURGED", "message": "该任务已彻底删除，不能恢复。"})
    task = _owned_task(db, user.id, task_id, include_deleted=include_deleted)
    return _task_detail(db, task, user.id)


@router.post("/tasks")
def create_task(payload: TaskIn, user: User = Depends(require_csrf), db: Session = Depends(get_db), idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
    normalized_key = None
    if idempotency_key is not None:
        try:
            normalized_key = str(UUID(idempotency_key))
        except (ValueError, AttributeError) as exc:
            raise HTTPException(422, detail={"code": "INVALID_IDEMPOTENCY_KEY", "message": "Idempotency-Key must be a UUID"}) from exc
    try:
        task = create_manual_task_service(db, user.id, ManualTaskInput(**payload.model_dump()), idempotency_key=normalized_key)
    except ValueError as exc:
        if str(exc).startswith("A new task cannot start as DONE"):
            raise HTTPException(422, str(exc)) from exc
        if str(exc) == "TASK_DATE_RANGE_INVALID":
            raise HTTPException(422, detail={"code": "TASK_DATE_RANGE_INVALID", "message": "预计天数必须与起止日期范围一致"}) from exc
        if str(exc) == "TASK_IDEMPOTENCY_KEY_CONFLICT":
            raise HTTPException(409, detail={"code": str(exc), "message": "This idempotency key was already used for a different request"}) from exc
        if str(exc) == "TASK_IDEMPOTENCY_RESULT_UNAVAILABLE":
            raise HTTPException(409, detail={"code": str(exc), "message": "The original task for this request is no longer available"}) from exc
        raise
    return task_out(task)


@router.patch("/tasks/{task_id}")
def patch_task(task_id: int, payload: TaskPatch, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    task = _owned_task(db, user.id, task_id)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("status") == TaskStatus.DONE and not task.evidence:
        raise HTTPException(422, "DONE requires at least one evidence record")
    old = task.status.value
    date_keys = {"start_date", "due_date", "expected_duration_days"}
    if date_keys.intersection(changes):
        start = changes.get("start_date", task.start_date)
        due = changes.get("due_date", task.due_date)
        duration = changes.get("expected_duration_days", task.expected_duration_days)
        if "expected_duration_days" in changes and duration is None:
            raise HTTPException(422, detail={"code": "TASK_DURATION_REQUIRED", "message": "预计持续天数必须为正整数"})
        if "expected_duration_days" in changes:
            if start is not None:
                due = start + timedelta(days=duration - 1)
            elif due is not None:
                start = due - timedelta(days=duration - 1)
            else:
                start = _local_today()
                due = start + timedelta(days=duration - 1)
        elif "start_date" in changes and "due_date" not in changes:
            if start is not None:
                duration = duration or 1
                due = start + timedelta(days=duration - 1)
            else:
                duration = None
        elif "due_date" in changes and "start_date" not in changes:
            duration = (due - start).days + 1 if start is not None and due is not None else None
        elif start is not None and due is not None:
            actual_duration = (due - start).days + 1
            if actual_duration < 1 or ("expected_duration_days" in changes and duration is not None and duration != actual_duration):
                raise HTTPException(422, detail={"code": "TASK_DATE_RANGE_INVALID", "message": "预计天数必须与起止日期范围一致"})
            duration = actual_duration
        else:
            duration = None
        if start is not None and due is not None and due < start:
            raise HTTPException(422, detail={"code": "TASK_DATE_RANGE_INVALID", "message": "截止日期不能早于开始日期"})
        changes.update(start_date=start, due_date=due, expected_duration_days=duration)
        if start != task.start_date or due != task.due_date:
            db.add(TaskDateHistory(user_id=user.id, task_id=task.id, old_start_date=task.start_date, new_start_date=start, old_due_date=task.due_date, new_due_date=due, reason="user_rescheduled"))
    for key, value in changes.items():
        if key == "archived":
            task.archived_at = datetime.now(timezone.utc).replace(tzinfo=None) if value else None
        else:
            if key == "status" and value == TaskStatus.DONE and task.completed_at is None:
                task.completed_at = datetime.now(ZoneInfo(settings.app_timezone)).replace(tzinfo=None)
            elif key == "status" and value != TaskStatus.DONE and task.status == TaskStatus.DONE:
                task.completed_at = None
            setattr(task, key, value)
    if "status" in changes and changes["status"].value != old:
        db.add(TaskStatusHistory(task_id=task.id, from_status=old, to_status=changes["status"].value, reason="user update"))
    db.commit()
    db.refresh(task)
    return task_out(task)


@router.delete("/tasks/{task_id}")
def delete_task(task_id: int, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    if db.scalar(select(TaskPurgeReceipt.id).where(TaskPurgeReceipt.user_id == user.id, TaskPurgeReceipt.task_id == task_id)):
        raise HTTPException(410, detail={"code": "TASK_PURGED", "message": "该任务已彻底删除。"})
    task = _owned_task(db, user.id, task_id, include_deleted=True)
    if task.deleted_at is not None:
        return {"ok": True, "deleted": True, "idempotent_replay": True, "retention": "soft_delete; recoverable_until_permanent_delete"}
    task.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
    task.is_pinned = False
    db.add(AgentAuditEvent(user_id=user.id, actor="user", event_type="task_trashed", detail=f"task_id={task.id}"))
    db.commit()
    return {"ok": True, "task_id": task.id, "deleted": True, "idempotent_replay": False, "retention": "soft_delete; recoverable_until_permanent_delete"}


@router.post("/tasks/{task_id}/restore")
def restore_task(task_id: int, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    if db.scalar(select(TaskPurgeReceipt.id).where(TaskPurgeReceipt.user_id == user.id, TaskPurgeReceipt.task_id == task_id)):
        raise HTTPException(410, detail={"code": "TASK_PURGED", "message": "该任务已彻底删除，不能恢复。"})
    task = _owned_task(db, user.id, task_id, include_deleted=True)
    if task.deleted_at is None:
        return task_out(task)
    task.deleted_at = None
    db.add(AgentAuditEvent(user_id=user.id, actor="user", event_type="task_restored", detail=f"task_id={task.id}"))
    db.commit()
    return task_out(task)


@router.get("/tasks/{task_id}/permanent-delete-preview")
def preview_task_permanent_delete(task_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        return task_purge_preview_service(db, user.id, task_id)
    except LookupError as exc:
        raise HTTPException(404, detail={"code": "TASK_NOT_FOUND", "message": "任务不存在或无权访问。"}) from exc


@router.post("/tasks/{task_id}/permanent-delete")
def permanently_delete_task(task_id: int, payload: PurgeContentRequest, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    try:
        return purge_task_service(db, user.id, task_id, payload.confirm_title)
    except LookupError as exc:
        if db.scalar(select(TaskPurgeReceipt.id).where(TaskPurgeReceipt.user_id == user.id, TaskPurgeReceipt.task_id == task_id)):
            raise HTTPException(410, detail={"code": "TASK_PURGED", "message": "该任务已彻底删除，不能再次恢复。"}) from exc
        raise HTTPException(404, detail={"code": "TASK_NOT_FOUND", "message": "任务不存在或无权访问。"}) from exc
    except TaskPurgeBlockedError as exc:
        raise HTTPException(409, detail={"code": "TASK_PURGE_BLOCKED", "message": str(exc), "blockers": exc.blockers}) from exc
    except ValueError as exc:
        code = str(exc)
        status = 409 if code == "TASK_MUST_BE_TRASHED" else 422
        messages = {"TASK_MUST_BE_TRASHED": "请先将任务移入最近删除。", "CONFIRM_TITLE_MISMATCH": "确认标题与任务名称不一致，未执行删除。"}
        raise HTTPException(status, detail={"code": code, "message": messages.get(code, "永久删除未执行。")}) from exc


@router.post("/tasks/{task_id}/links")
def link_task_conversation(task_id: int, payload: TaskConversationLinkIn, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    task = _owned_task(db, user.id, task_id)
    conversation = _owned_conversation(db, user.id, payload.conversation_id)
    if conversation.deleted_at is not None:
        raise HTTPException(410, "Restore the conversation before linking it")
    link, created = _task_link(db, user.id, task.id, conversation.id, payload.message_id)
    db.commit()
    return {"id": link.id, "task_id": task.id, "conversation_id": conversation.id, "message_id": link.message_id, "created": created}


@router.get("/tasks/{task_id}/links")
def task_links(task_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    task = _owned_task(db, user.id, task_id)
    return _task_detail(db, task, user.id)["links"]


@router.delete("/tasks/{task_id}/links/{link_id}")
def unlink_task_conversation(task_id: int, link_id: int, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    _owned_task(db, user.id, task_id)
    link = db.scalar(select(TaskConversationLink).where(TaskConversationLink.id == link_id, TaskConversationLink.user_id == user.id, TaskConversationLink.task_id == task_id))
    if not link:
        raise HTTPException(404, "Task link not found")
    db.delete(link)
    db.commit()
    return {"ok": True}


@router.post("/tasks/{task_id}/evidence")
def add_evidence(task_id: int, payload: EvidenceIn, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    try:
        evidence = add_task_evidence(db, user.id, task_id, **payload.model_dump())
    except ValueError as exc:
        if str(exc) == "TASK_NOT_FOUND":
            raise HTTPException(404, detail={"code": "TASK_NOT_FOUND", "message": "Task not found"}) from exc
        raise
    db.commit()
    return {"id": evidence.id, "verification_level": evidence.verification_level}


@router.get("/study/records")
def study_records(user: User = Depends(current_user), db: Session = Depends(get_db)):
    records = db.scalars(select(StudyRecord).where(StudyRecord.user_id == user.id).options(selectinload(StudyRecord.errors)).order_by(StudyRecord.created_at.desc())).all()
    return [{"id": r.id, "language": r.language, "topic": r.topic, "activity_type": r.activity_type, "resource_url": r.resource_url, "source_name": r.source_name, "planned_content": r.planned_content, "actual_response": r.actual_response, "assessment_status": r.assessment_status, "errors": len(r.errors), "created_at": r.created_at} for r in records]


@router.post("/study/records")
def create_study(payload: StudyIn, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    record = StudyRecord(user_id=user.id, **payload.model_dump())
    db.add(record)
    db.commit()
    db.refresh(record)
    return {"id": record.id, "language": record.language, "topic": record.topic, "assessment_status": record.assessment_status}


@router.post("/study/records/{record_id}/errors")
def add_study_error(record_id: int, payload: StudyErrorIn, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    record = db.get(StudyRecord, record_id)
    if not record or record.user_id != user.id:
        raise HTTPException(404, "Study record not found")
    error = StudyError(study_record_id=record_id, **payload.model_dump())
    record.assessment_status = "VERIFY"
    db.add(error)
    db.commit()
    return {"id": error.id, "assessment_status": record.assessment_status}


@router.get("/health/workouts")
def workouts(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(select(Workout).where(Workout.user_id == user.id).order_by(Workout.date.desc())).all()


@router.post("/health/workouts")
def create_workout(payload: WorkoutIn, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    workout = Workout(user_id=user.id, **payload.model_dump(exclude_none=True))
    db.add(workout)
    db.commit()
    db.refresh(workout)
    return workout


@router.get("/health/metrics")
def metrics(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(select(HealthMetric).where(HealthMetric.user_id == user.id).order_by(HealthMetric.recorded_at.desc())).all()


@router.post("/health/metrics")
def create_metric(payload: HealthMetricIn, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    metric = HealthMetric(user_id=user.id, **payload.model_dump(exclude_none=True))
    db.add(metric)
    db.commit()
    db.refresh(metric)
    return metric


@router.get("/reviews/daily")
def daily_reviews(user: User = Depends(current_user), db: Session = Depends(get_db)):
    reviews = db.scalars(select(DailyReview).where(DailyReview.user_id == user.id).options(selectinload(DailyReview.task_entries)).order_by(DailyReview.review_date.desc())).all()
    return [_review_out(db, review, user.id) for review in reviews]


@router.get("/reviews/weekly")
def weekly_review(week_start: date | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    today = _local_today()
    start = week_start or (today - timedelta(days=today.weekday()))
    try:
        return review_service.weekly_summary(db, user.id, start)
    except ValueError as exc:
        raise HTTPException(422, detail={"code": str(exc), "message": "周起始日期必须为周一。"}) from exc


def _review_out(db: Session, review: DailyReview, user_id: int):
    entries = db.execute(select(DailyTaskSnapshot, Task).join(Task, Task.id == DailyTaskSnapshot.task_id).where(DailyTaskSnapshot.review_id == review.id, DailyTaskSnapshot.user_id == user_id, Task.user_id == user_id).order_by(DailyTaskSnapshot.id)).all()
    return {"id": review.id, "review_date": review.review_date, "planned_summary": review.planned_summary, "actual_summary": review.actual_summary, "blockers": review.blockers, "next_actions": review.next_actions, "created_at": review.created_at, "updated_at": review.updated_at,
        "task_entries": [{"id": entry.id, "task_id": task.id, "task_title": task.title, "status_at_settlement": entry.status_at_settlement, "due_date_at_settlement": entry.due_date_at_settlement, "report_category": entry.report_category, "actual_progress": entry.actual_progress, "evidence_note": entry.evidence_note, "revision": entry.revision} for entry, task in entries]}


@router.get("/reviews/daily/{review_date}")
def get_daily_review(review_date: date, user: User = Depends(current_user), db: Session = Depends(get_db)):
    review = db.scalar(select(DailyReview).where(DailyReview.user_id == user.id, DailyReview.review_date == review_date))
    return _review_out(db, review, user.id) if review else {"review_date": review_date, "task_entries": []}


@router.post("/reviews/daily")
def create_review(payload: ReviewIn, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    review_date = payload.review_date or _local_today()
    review = db.scalar(select(DailyReview).where(DailyReview.user_id == user.id, DailyReview.review_date == review_date))
    values = payload.model_dump(exclude_unset=True)
    task_entries = values.pop("task_entries", None)
    values["review_date"] = review_date
    if not review:
        review = DailyReview(user_id=user.id, **values)
        db.add(review)
        db.flush()
    else:
        for key, value in values.items():
            setattr(review, key, value)
    if task_entries is not None:
        seen: set[int] = set()
        for item in task_entries:
            if item["task_id"] in seen:
                raise HTTPException(422, detail={"code": "DUPLICATE_REVIEW_TASK", "message": "同一结算不能重复添加同一任务"})
            seen.add(item["task_id"])
            task = db.scalar(select(Task).where(Task.id == item["task_id"], Task.user_id == user.id, Task.deleted_at.is_(None)))
            if task is None:
                raise HTTPException(404, detail={"code": "TASK_NOT_FOUND", "message": "结算任务不存在或无权访问"})
            if item["report_category"] == "overdue" and (task.due_date is None or task.due_date > review_date or task.status in {TaskStatus.DONE, TaskStatus.CANCELLED}):
                raise HTTPException(422, detail={"code": "TASK_NOT_OVERDUE", "message": "只能将已到期且未完成的任务记录为逾期"})
            snapshot = db.scalar(select(DailyTaskSnapshot).where(DailyTaskSnapshot.user_id == user.id, DailyTaskSnapshot.review_date == review_date, DailyTaskSnapshot.task_id == task.id))
            if snapshot is None:
                snapshot = DailyTaskSnapshot(user_id=user.id, review_id=review.id, task_id=task.id, review_date=review_date, revision=1)
                db.add(snapshot)
            else:
                snapshot.revision += 1
            snapshot.status_at_settlement = task.status.value
            snapshot.due_date_at_settlement = task.due_date
            snapshot.report_category = item["report_category"]
            snapshot.actual_progress = item.get("actual_progress")
            snapshot.evidence_note = item.get("evidence_note")
    db.commit()
    db.refresh(review)
    return _review_out(db, review, user.id)


@router.get("/dashboard")
def dashboard(user: User = Depends(current_user), db: Session = Depends(get_db)):
    today = _local_today()
    task_summary = dashboard_task_rows(db, user.id, today)
    review = db.scalar(select(DailyReview).where(DailyReview.user_id == user.id, DailyReview.review_date == today).options(selectinload(DailyReview.task_entries)))
    return {**_task_summary_out(task_summary, today),
            "today_review": _review_out(db, review, user.id) if review else None,
            "recent_tasks": [task_out(t) for t in db.scalars(select(Task).where(Task.user_id == user.id, Task.deleted_at.is_(None), Task.archived_at.is_(None)).options(selectinload(Task.evidence)).order_by(Task.updated_at.desc()).limit(5)).all()],
            "study_count": db.scalar(select(func.count(StudyRecord.id)).where(StudyRecord.user_id == user.id)), "workout_count": db.scalar(select(func.count(Workout.id)).where(Workout.user_id == user.id)), "review_count": db.scalar(select(func.count(DailyReview.id)).where(DailyReview.user_id == user.id))}


def _task_summary_out(summary: dict, today: date) -> dict:
    return {"today_date": today, "task_counts": summary["counts"],
            "due_today_count": len(summary["today"]), "today_tasks": [task_out(task) for task in summary["today"]],
            "completed_today_count": len(summary["completed_today"]), "completed_today_tasks": [task_out(task) for task in summary["completed_today"]],
            "overdue_count": summary["overdue_count"], "overdue_tasks": [task_out(task) for task in summary["overdue"]],
            "unscheduled_count": summary["unscheduled_count"], "unscheduled_tasks": [task_out(task) for task in summary["unscheduled"]]}


@router.get("/task-manager/summary")
def task_manager_summary(user: User = Depends(current_user), db: Session = Depends(get_db)):
    today = _local_today()
    return _task_summary_out(dashboard_task_rows(db, user.id, today), today)


def goal_out(goal: UserGoal) -> dict:
    return {"id": goal.id, "domain": goal.domain, "title": goal.title, "priority": goal.priority, "enabled": goal.enabled, "notes": goal.notes, "created_at": goal.created_at, "updated_at": goal.updated_at}


@router.get("/agent/goals")
def agent_goals(user: User = Depends(current_user), db: Session = Depends(get_db)):
    goals = db.scalars(select(UserGoal).where(UserGoal.user_id == user.id).order_by(UserGoal.priority, UserGoal.domain)).all()
    return [goal_out(goal) for goal in goals]


@router.post("/agent/goals")
def create_agent_goal(payload: GoalIn, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    goal = UserGoal(user_id=user.id, **payload.model_dump())
    db.add(goal)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(409, "A goal for this domain already exists") from exc
    db.refresh(goal)
    return goal_out(goal)


@router.patch("/agent/goals/{goal_id}")
def patch_agent_goal(goal_id: int, payload: GoalPatch, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    goal = db.get(UserGoal, goal_id)
    if not goal or goal.user_id != user.id:
        raise HTTPException(404, "Goal not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(goal, key, value)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(409, "A goal for this domain already exists") from exc
    db.refresh(goal)
    return goal_out(goal)


@router.get("/agent/proposals")
def agent_proposals(status: str | None = None, q: str | None = None, include_deleted: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if status and status not in {"PENDING", "APPROVING", "APPROVED", "REJECTED", "PURGED"}:
        raise HTTPException(422, detail={"code": "INVALID_PROPOSAL_STATUS", "message": "Unknown proposal status"})
    return [proposal_view(proposal) for proposal in list_proposals(db, user.id, include_deleted=include_deleted, status=status, query=q)]


def _agent_failure(exc: AgentRunError, request: Request) -> HTTPException:
    status_code = 504 if exc.code in {"MODEL_TIMEOUT", "TOOL_TIMEOUT", "AGENT_TIMEOUT"} else 502 if exc.code in {"INVALID_MODEL_OUTPUT", "MODEL_REQUEST_FAILED", "DUPLICATE_PLAN_ITEMS", "UPSTREAM_AUTH_FAILED", "INSUFFICIENT_BALANCE", "MODEL_NOT_FOUND", "RATE_LIMITED", "UPSTREAM_SERVER_ERROR", "MALFORMED_UPSTREAM_RESPONSE", "TASK_PARSE_FAILED", "TASK_DOMAIN_INVALID", "TASK_DATE_RANGE_INVALID", "TASK_COUNT_MISMATCH", "TASK_VALIDATION_FAILED"} else 503
    return HTTPException(status_code, detail={"code": exc.code, "message": str(exc), "request_id": getattr(request.state, "request_id", None)})


@router.post("/agent/plan")
async def generate_agent_plan(payload: PlanRequest, request: Request, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    if not payload.consent_granted:
        raise HTTPException(422, "Explicit AI data consent is required")
    try:
        run, items, rationale, _context = await run_plan(db, user.id, payload.available_minutes, payload.priority_note, payload.mode)
    except AgentRunError as exc:
        raise _agent_failure(exc, request) from exc
    try:
        proposal = save_generated_proposal(db, user.id, run_id=run.id, proposal_key=f"daily-plan:{date.today().isoformat()}", proposal_type="daily_plan", plan_date=date.today(), title="AI 今日计划", items=items, rationale=rationale)
    except ValueError as exc:
        raise HTTPException(502, detail={"code": "INVALID_AGENT_PROPOSAL", "message": str(exc), "request_id": getattr(request.state, "request_id", None)}) from exc
    return proposal_view(proposal)


@router.patch("/agent/proposals/{proposal_id}")
def decide_agent_proposal(proposal_id: int, payload: ProposalActionIn, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    proposal = get_owned_proposal(db, user.id, proposal_id)
    if not proposal:
        raise HTTPException(404, "Proposal not found")
    if proposal.deleted_at is not None or proposal.purged_at is not None:
        raise HTTPException(410, detail={"code": "PROPOSAL_DELETED", "message": "Restore this proposal before editing or deciding it."})
    try:
        updated, created, skipped = apply_proposal_action(db, user, proposal, action=payload.action, items=[item.model_dump() for item in payload.items] if payload.items is not None else None, note=payload.note)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {**proposal_view(updated), "created_task_ids": created, "skipped_task_ids": skipped}


@router.delete("/agent/proposals/{proposal_id}")
def trash_agent_proposal(proposal_id: int, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    proposal = get_owned_proposal(db, user.id, proposal_id)
    if proposal is None:
        raise HTTPException(404, "Proposal not found")
    if proposal.purged_at is not None:
        raise HTTPException(410, detail={"code": "PROPOSAL_CONTENT_PURGED", "message": "This proposal's content has already been permanently cleared."})
    if proposal.status == "APPROVING":
        raise HTTPException(409, detail={"code": "PROPOSAL_DECISION_IN_PROGRESS", "message": "Wait for the approval transaction to finish."})
    if proposal.deleted_at is None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if proposal.status == "PENDING":
            proposal.status = "REJECTED"
            proposal.decision_note = "User cancelled before moving to Recently deleted. No task was created."
            proposal.decided_at = now
            db.add(AgentAuditEvent(user_id=user.id, run_id=proposal.run_id, proposal_id=proposal.id, actor="user", event_type="proposal_cancelled_and_trashed", detail="Pending proposal cancelled; no task write."))
        else:
            db.add(AgentAuditEvent(user_id=user.id, run_id=proposal.run_id, proposal_id=proposal.id, actor="user", event_type="proposal_trashed", detail=f"status={proposal.status}"))
        proposal.deleted_at = now
        db.commit()
    return proposal_view(proposal)


@router.post("/agent/proposals/{proposal_id}/restore")
def restore_agent_proposal(proposal_id: int, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    proposal = get_owned_proposal(db, user.id, proposal_id)
    if proposal is None:
        raise HTTPException(404, "Proposal not found")
    if proposal.purged_at is not None:
        raise HTTPException(410, detail={"code": "PROPOSAL_CONTENT_PURGED", "message": "Permanently cleared proposal content cannot be restored from the application database."})
    if proposal.deleted_at is not None:
        proposal.deleted_at = None
        db.add(AgentAuditEvent(user_id=user.id, run_id=proposal.run_id, proposal_id=proposal.id, actor="user", event_type="proposal_restored", detail=f"status={proposal.status}"))
        db.commit()
    return proposal_view(proposal)


@router.post("/agent/proposals/{proposal_id}/permanent-delete")
def purge_agent_proposal(proposal_id: int, payload: PurgeContentRequest, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    proposal = get_owned_proposal(db, user.id, proposal_id)
    if proposal is None:
        raise HTTPException(404, "Proposal not found")
    if proposal.purged_at is not None:
        return {"id": proposal.id, "status": "PURGED", "purged": True, "idempotent_replay": True}
    if proposal.deleted_at is None:
        raise HTTPException(409, detail={"code": "PROPOSAL_MUST_BE_TRASHED", "message": "Move the proposal to Recently deleted first."})
    if proposal.status in {"PENDING", "APPROVING"}:
        raise HTTPException(409, detail={"code": "PROPOSAL_NOT_DECIDED", "message": "Pending proposals must be rejected before permanent content cleanup."})
    if payload.confirm_title.strip() != proposal.title:
        raise HTTPException(422, detail={"code": "CONFIRM_TITLE_MISMATCH", "message": "The confirmation title does not match."})
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    proposal.status = "PURGED"
    proposal.title = "[已永久清除]"
    proposal.items = []
    proposal.rationale = None
    proposal.decision_note = None
    proposal.purged_at = now
    db.add(AgentAuditEvent(user_id=user.id, run_id=proposal.run_id, proposal_id=proposal.id, actor="user", event_type="proposal_content_purged", detail="Proposal body cleared; historical backups may still contain pre-delete data."))
    db.commit()
    return {"id": proposal.id, "status": "PURGED", "purged": True, "backup_notice": "Independent historical backups are not altered and may retain pre-delete content."}


@router.post("/agent/review/evening")
async def generate_evening_review(payload: EveningReviewRequest, request: Request, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    if not payload.consent_granted:
        raise HTTPException(422, "Explicit AI data consent is required")
    try:
        run, item, _context = await run_evening(db, user.id, payload.mode)
    except AgentRunError as exc:
        raise _agent_failure(exc, request) from exc
    proposal = save_generated_proposal(db, user.id, run_id=run.id, proposal_key=f"evening-review:{date.today().isoformat()}", proposal_type="evening_review", plan_date=date.today(), title="AI 晚间复盘草案", items=[item], rationale="仅根据当前用户授权的目标、任务、学习反馈和复盘记录生成；未记录内容保持 UNKNOWN。")
    return proposal_view(proposal)


@router.get("/agent/audit")
def agent_audit(user: User = Depends(current_user), db: Session = Depends(get_db)):
    events = db.scalars(select(AgentAuditEvent).where(AgentAuditEvent.user_id == user.id).order_by(AgentAuditEvent.created_at.desc()).limit(100)).all()
    return [{"id": event.id, "run_id": event.run_id, "proposal_id": event.proposal_id, "actor": event.actor, "event_type": event.event_type, "detail": event.detail, "created_at": event.created_at} for event in events]


@router.get("/agent/runs/{run_id}/tools")
def agent_run_tools(run_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    run = db.get(AgentRun, run_id)
    if not run or run.user_id != user.id:
        raise HTTPException(404, "Agent run not found")
    calls = db.scalars(select(AgentToolCall).where(AgentToolCall.run_id == run_id, AgentToolCall.user_id == user.id).order_by(AgentToolCall.created_at)).all()
    return [{"tool": item.tool_name, "allowed": item.allowed, "status": item.status, "arguments": item.arguments_summary, "result": item.result_summary, "error_code": item.error_code, "duration_ms": item.duration_ms} for item in calls]


@router.get("/agent/runs")
def agent_runs(user: User = Depends(current_user), db: Session = Depends(get_db)):
    runs = db.scalars(select(AgentRun).where(AgentRun.user_id == user.id).order_by(AgentRun.id.desc()).limit(20)).all()
    return [{"id": run.id, "model": run.model, "provider": run.provider, "status": run.status, "authorized_scope": run.authorized_scope, "prompt_tokens": run.prompt_tokens, "completion_tokens": run.completion_tokens, "error_code": run.error_code} for run in runs]


@router.get("/settings/ai/usage")
def ai_usage(conversation_id: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        # Asia/Taipei is fixed UTC+08:00 and remains available on Windows
        # without relying on a separately-installed IANA timezone database.
        zone = timezone(timedelta(hours=8), "Asia/Taipei") if settings.app_timezone == "Asia/Taipei" else ZoneInfo(settings.app_timezone)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(500, detail={"code": "INVALID_APP_TIMEZONE", "message": "Configured application timezone is invalid"}) from exc
    now = datetime.now(zone)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
    week_start = (now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
    session_id = None
    if conversation_id:
        conversation = _owned_conversation(db, user.id, conversation_id)
        session_id = conversation.id
    usage_rows = db.scalars(select(AgentModelUsage).where(AgentModelUsage.user_id == user.id).order_by(AgentModelUsage.requested_at.desc(), AgentModelUsage.id.desc())).all()
    runs = db.scalars(select(AgentRun).where(AgentRun.user_id == user.id).order_by(AgentRun.started_at.desc())).all()
    run_ids_with_detail = {row.run_id for row in usage_rows}
    legacy_runs = [run for run in runs if run.id not in run_ids_with_detail]
    def within(value, start):
        return bool(value and value >= start)
    def aggregate(rows, legacy):
        input_sum = sum(row.input_tokens or 0 for row in rows) + sum(run.prompt_tokens or 0 for run in legacy)
        output_sum = sum(row.output_tokens or 0 for row in rows) + sum(run.completion_tokens or 0 for run in legacy)
        unknown = sum(row.input_tokens is None or row.output_tokens is None for row in rows)
        return {"input_tokens": input_sum, "output_tokens": output_sum, "total_tokens": input_sum + output_sum, "known_request_records": sum(row.input_tokens is not None and row.output_tokens is not None for row in rows), "unknown_usage_records": unknown, "legacy_aggregated_runs": len(legacy)}
    periods = {}
    for name, start in (("today", today_start), ("last_7_days", week_start), ("month_to_date", month_start)):
        periods[name] = aggregate([row for row in usage_rows if within(row.requested_at, start)], [run for run in legacy_runs if within(run.started_at, start)])
    session_rows = [row for row in usage_rows if session_id and row.conversation_id == session_id]
    session_total = aggregate(session_rows, []) if session_id else None
    grouped = {}
    for row in usage_rows:
        key = (row.provider, row.model or "UNKNOWN")
        bucket = grouped.setdefault(key, {"provider": key[0], "model": key[1], "input_tokens": 0, "output_tokens": 0, "requests": 0, "unknown_usage": 0})
        bucket["input_tokens"] += row.input_tokens or 0
        bucket["output_tokens"] += row.output_tokens or 0
        bucket["requests"] += 1
        bucket["unknown_usage"] += int(row.input_tokens is None or row.output_tokens is None)
    for run in legacy_runs:
        key = (run.provider, run.model or "UNKNOWN")
        bucket = grouped.setdefault(key, {"provider": key[0], "model": key[1], "input_tokens": 0, "output_tokens": 0, "requests": 0, "unknown_usage": 0})
        bucket["input_tokens"] += run.prompt_tokens or 0
        bucket["output_tokens"] += run.completion_tokens or 0
        bucket["requests"] += 1
    search_calls = db.scalars(select(AgentToolCall).where(AgentToolCall.user_id == user.id, AgentToolCall.tool_name == "web_search").order_by(AgentToolCall.created_at.desc())).all()
    def search_count(start):
        return sum(within(call.created_at, start) for call in search_calls)
    recent = [{"id": row.id, "run_id": row.run_id, "conversation_id": row.conversation_id, "message_id": row.message_id, "request_index": row.request_index, "provider": row.provider, "model": row.model, "input_tokens": row.input_tokens, "output_tokens": row.output_tokens, "total_tokens": row.input_tokens + row.output_tokens if row.input_tokens is not None and row.output_tokens is not None else None, "status": row.status, "requested_at": row.requested_at, "completed_at": row.completed_at} for row in usage_rows[:50]]
    return {"timezone": settings.app_timezone, "refreshed_at": now.isoformat(), "periods": periods, "selected_conversation_id": session_id, "conversation_usage": session_total, "by_model": list(grouped.values()), "recent_requests": recent, "legacy_detail_note": "迁移前 Agent Run 仅保留运行级聚合 Token；旧普通 AI 请求仅保留状态与时间、无 Token 用量，均未伪造成逐次精确明细。", "budget": {"max_tool_calls": user.agent_max_tool_calls, "max_output_tokens": user.agent_max_output_tokens}, "search": {"provider": "tavily", "today_calls": search_count(today_start), "last_7_days_calls": search_count(week_start), "month_to_date_calls": search_count(month_start), "credits": None, "credits_note": "未从 Provider 获取可核验 Credits 用量；这里只统计本应用记录的搜索调用。"}}


@router.put("/settings/ai/usage/budget")
def update_ai_usage_budget(payload: UsageBudgetPatch, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    user.agent_max_tool_calls = payload.max_tool_calls
    user.agent_max_output_tokens = payload.max_output_tokens
    db.commit()
    return {"max_tool_calls": user.agent_max_tool_calls, "max_output_tokens": user.agent_max_output_tokens}


@router.post("/agent/chat")
async def agent_chat(payload: AgentChatRequest, request: Request, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    if (payload.include_personal_context or payload.enable_web_tools or payload.use_knowledge_base) and not payload.consent_granted:
        raise HTTPException(422, detail={"code": "EXPLICIT_CONSENT_REQUIRED", "message": "Confirm the selected data scope before the agent can use personal or web tools."})
    knowledge_context = knowledge_service.retrieve_cards(db, user.id, payload.message, domains=payload.knowledge_domains, top_k=5) if payload.use_knowledge_base else []
    conversation = None
    conversation_id = None
    user_message = None
    if user.chat_history_enabled:
        if payload.conversation_id:
            try:
                conversation_id = str(UUID(payload.conversation_id))
            except ValueError as exc:
                raise HTTPException(422, detail={"code": "INVALID_CONVERSATION_ID", "message": "Conversation ID is invalid"}) from exc
            conversation = db.get(ChatConversation, conversation_id)
            if conversation is None or conversation.user_id != user.id:
                raise HTTPException(404, detail={"code": "CONVERSATION_NOT_FOUND", "message": "Conversation not found"})
        else:
            conversation_id = str(uuid4())
            conversation = ChatConversation(id=conversation_id, user_id=user.id, title=payload.message.strip().replace("\n", " ")[:120] or "新会话", domain=payload.domain)
            db.add(conversation)
        if conversation.deleted_at is not None:
            raise HTTPException(410, detail={"code": "CONVERSATION_DELETED", "message": "This conversation is in Recently deleted; restore it before continuing."})
        stored = db.scalars(select(ChatMessage).where(
            ChatMessage.user_id == user.id,
            ChatMessage.conversation_id == conversation_id,
            ChatMessage.status == "completed",
            ChatMessage.role.in_(["user", "assistant"]),
        ).order_by(ChatMessage.id.desc()).limit(12)).all()
        history = _bounded_chat_history([{"role": item.role, "content": item.content} for item in reversed(stored)])
        if conversation.title == "新会话" and not stored:
            conversation.title = payload.message.strip().replace("\n", " ")[:120] or "新会话"
        conversation.domain = payload.domain if payload.domain != "general" else conversation.domain
        user_message = ChatMessage(user_id=user.id, conversation_id=conversation_id, role="user", content=payload.message, status="pending")
        db.add(user_message)
        db.commit()
        db.refresh(user_message)
    else:
        # Without persistence, only the current browser's bounded transcript can supply context.
        history = _bounded_chat_history([item.model_dump() for item in payload.history])
    try:
        result = await run_chat(db, user.id, payload.message, history, include_personal=payload.include_personal_context, enable_web=payload.enable_web_tools, conversation_id=conversation_id if user_message is not None else None, message_id=user_message.id if user_message is not None else None, knowledge_context=knowledge_context, knowledge_enabled=payload.use_knowledge_base)
        result["knowledge_status"] = "completed" if knowledge_context else ("empty" if payload.use_knowledge_base else "disabled")
        result["knowledge_sources"] = [{key: item[key] for key in ("id", "title", "summary", "domain", "sources")} for item in knowledge_context]
        if user.chat_history_enabled and result.get("content") and user_message is not None:
            user_message.run_id = result.get("run_id")
            user_message.status = "completed"
            conversation.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            assistant_message = ChatMessage(user_id=user.id, conversation_id=conversation_id, run_id=result.get("run_id"), role="assistant", content=result["content"][:12000], status="completed")
            db.add(assistant_message)
            db.commit()
            db.refresh(assistant_message)
            result.update({"conversation_id": conversation_id, "user_message_id": user_message.id, "assistant_message_id": assistant_message.id})
        else:
            result.update({"conversation_id": None, "user_message_id": None, "assistant_message_id": None})
        return result
    except AgentRunError as exc:
        if user_message is not None:
            user_message.status = "failed"
            user_message.run_id = getattr(exc, "run_id", None)
            conversation.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
        raise _agent_failure(exc, request) from exc
    except Exception:
        if user_message is not None:
            user_message.status = "failed"
            conversation.updated_at = datetime.utcnow()
            db.commit()
        raise


def _task_operation_result(db: Session, operation: TaskManagerOperation) -> dict:
    task_ids = sorted({int(item["task_id"]) for item in operation.action_details if item.get("kind") in {"create", "update", "duplicate", "delete_pending", "delete"}})
    tasks = db.scalars(select(Task).where(Task.user_id == operation.user_id, Task.id.in_(task_ids))).all() if task_ids else []
    candidates = next((item.get("items", []) for item in reversed(operation.action_details or []) if item.get("kind") == "prepared_tasks"), [])
    is_expired = bool(operation.expires_at and operation.expires_at <= datetime.now(timezone.utc).replace(tzinfo=None))
    run = db.get(AgentRun, operation.run_id) if operation.run_id else None
    actions = operation.action_details or []
    created_ids = {int(item["task_id"]) for item in actions if item.get("kind") == "create"}
    updated_ids = {int(item["task_id"]) for item in actions if item.get("kind") == "update"}
    task_views = [task_out(task) for task in tasks]
    created_tasks = [task for task in task_views if task["id"] in created_ids]
    updated_tasks = [task for task in task_views if task["id"] in updated_ids]
    return {"operation_id": operation.client_operation_id, "status": operation.status, "mode": operation.mode,
            "failure_code": operation.failure_code or (run.error_code if run else None),
            "error_code": operation.failure_code or (run.error_code if run else None),
            "undone": operation.undone_at is not None,
            "tasks": task_views, "created_tasks": created_tasks, "updated_tasks": updated_tasks,
            "task_ids": sorted(created_ids | updated_ids), "actions": actions, "run_id": operation.run_id,
            "audit_run_id": operation.run_id,
            "conversation_id": operation.conversation_id, "confirmation_required": operation.status in {"awaiting_confirmation", "awaiting_user_confirmation"},
            "candidate_tasks": candidates, "expires_at": operation.expires_at, "preview_expired": is_expired}


def _recover_stale_task_operation(db: Session, operation: TaskManagerOperation) -> None:
    """Recover an abandoned RUNNING row only after the Agent's hard deadline plus grace."""
    if operation.status != "started":
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    age = (now - (operation.updated_at or operation.created_at)).total_seconds()
    if age < 120:
        return
    actions = operation.action_details or []
    kinds = {item.get("kind") for item in actions}
    if kinds.intersection({"create", "update", "duplicate"}):
        operation.status = "completed"
        operation.failure_code = None
    elif "delete_pending" in kinds:
        operation.status = "awaiting_confirmation"
    elif "prepared_tasks" in kinds:
        operation.status = "awaiting_user_confirmation"
        operation.expires_at = now + timedelta(hours=24)
    else:
        operation.status = "failed"
        operation.failure_code = "OPERATION_TIMED_OUT_NO_WRITE"
        if operation.user_message_id:
            message = db.scalar(select(ChatMessage).where(ChatMessage.id == operation.user_message_id, ChatMessage.user_id == operation.user_id))
            if message and message.status == "pending":
                message.status = "failed"
    db.add(AgentAuditEvent(user_id=operation.user_id, run_id=operation.run_id, actor="system", event_type="task_operation_recovered", detail=f"operation_id={operation.client_operation_id}; status={operation.status}"))
    db.commit()


@router.get("/task-manager/pending-confirmations")
def task_manager_pending_confirmations(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(TaskManagerOperation).where(TaskManagerOperation.user_id == user.id, TaskManagerOperation.status == "awaiting_confirmation").order_by(TaskManagerOperation.created_at.desc())).all()
    return [_task_operation_result(db, row) for row in rows]


@router.post("/task-manager/operations/{operation_id}/confirm")
def confirm_task_manager_operation(operation_id: str, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    try:
        normalized = str(UUID(operation_id))
    except ValueError as exc:
        raise HTTPException(422, "Invalid operation ID") from exc
    operation = db.scalar(select(TaskManagerOperation).where(TaskManagerOperation.user_id == user.id, TaskManagerOperation.client_operation_id == normalized))
    if operation is None:
        raise HTTPException(404, "Task operation not found")
    if operation.status == "completed" and any(item.get("kind") == "delete" for item in operation.action_details):
        return {"idempotent_replay": True, **_task_operation_result(db, operation)}
    if operation.status != "awaiting_confirmation":
        raise HTTPException(409, detail={"code": "CONFIRMATION_NOT_PENDING", "message": "No task deletion is awaiting confirmation."})
    pending = [item for item in operation.action_details if item.get("kind") == "delete_pending"]
    if len(pending) != 1:
        raise HTTPException(409, detail={"code": "INVALID_PENDING_DELETE", "message": "The pending deletion record is not valid."})
    task = _owned_task(db, user.id, int(pending[0]["task_id"]))
    was_pinned = bool(task.is_pinned)
    task.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
    task.is_pinned = False
    operation.action_details = [*operation.action_details, {"kind": "delete", "task_id": task.id, "before": {"deleted_at": None, "is_pinned": was_pinned}}]
    operation.status = "completed"
    db.add(AgentAuditEvent(user_id=user.id, run_id=operation.run_id, actor="user", event_type="task_manager_delete_confirmed", detail=f"task_id={task.id}"))
    db.commit()
    return _task_operation_result(db, operation)


@router.post("/task-manager/chat")
async def task_manager_chat(payload: TaskManagerChatRequest, request: Request, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    try:
        operation_id = str(UUID(payload.operation_id))
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "INVALID_OPERATION_ID", "message": "Operation ID must be a UUID"}) from exc
    request_details = {"knowledge_card_ids": payload.knowledge_card_ids, "candidate_tasks": payload.candidate_tasks}
    fingerprint = hashlib.sha256(f"{payload.mode}\0{payload.conversation_id or ''}\0{payload.message}\0{json.dumps(request_details, sort_keys=True, ensure_ascii=False)}".encode("utf-8")).hexdigest()
    existing = db.scalar(select(TaskManagerOperation).where(TaskManagerOperation.user_id == user.id, TaskManagerOperation.client_operation_id == operation_id))
    if existing:
        _recover_stale_task_operation(db, existing)
        if not existing.request_fingerprint:
            raise HTTPException(409, detail={"code": "LEGACY_OPERATION_STATUS_ONLY", "message": "这是旧版操作记录，不能安全重放；请检查其状态后再创建新操作。"})
        if existing.request_fingerprint != fingerprint:
            raise HTTPException(409, detail={"code": "OPERATION_REQUEST_MISMATCH", "message": "此操作 ID 已属于另一条指令或会话；请提交当前指令。"})
        if existing.status in {"applied", "completed", "failed", "no_write", "undone", "awaiting_confirmation", "awaiting_user_confirmation", "expired"}:
            return {"idempotent_replay": True, **_task_operation_result(db, existing)}
        return {"processing": True, **_task_operation_result(db, existing)}
    knowledge_context = []
    if payload.knowledge_card_ids:
        if payload.mode != "auto" or not payload.knowledge_consent_granted:
            raise HTTPException(422, detail={"code": "KNOWLEDGE_CONSENT_REQUIRED", "message": "请主动授权使用所选知识卡片生成任务草案。"})
        if not user.chat_history_enabled:
            raise HTTPException(409, detail={"code": "PERSISTENT_TASK_PREVIEW_REQUIRED", "message": "知识任务草案需要已启用的聊天记录，以便安全保存和确认草案。"})
        try:
            knowledge_context = knowledge_service.get_active_cards(db, user.id, payload.knowledge_card_ids)
        except (ValueError, knowledge_service.KnowledgeNotFound) as exc:
            raise HTTPException(404, detail={"code": "KNOWLEDGE_SOURCE_NOT_FOUND", "message": "一个或多个知识来源已不可用或无权访问；未生成任务草案。"}) from exc
    elif payload.knowledge_consent_granted or payload.candidate_tasks is not None:
        if payload.candidate_tasks is not None:
            pass
        else:
            raise HTTPException(422, detail={"code": "KNOWLEDGE_SOURCE_IDS_REQUIRED", "message": "知识授权需要至少一个知识卡片来源。"})
    conversation = None
    if user.chat_history_enabled:
        if payload.conversation_id:
            conversation = _owned_conversation(db, user.id, payload.conversation_id)
            if conversation.domain != "tasks":
                raise HTTPException(422, detail={"code": "TASK_MANAGER_CONVERSATION_REQUIRED", "message": "Task manager conversations must use the tasks domain."})
            if conversation.deleted_at or conversation.archived_at:
                raise HTTPException(410, detail={"code": "CONVERSATION_NOT_ACTIVE", "message": "Restore this task-manager conversation before continuing."})
        else:
            conversation = ChatConversation(id=str(uuid4()), user_id=user.id, title="任务管理员", domain="tasks")
            db.add(conversation)
        conversation_id = conversation.id
        stored = db.scalars(select(ChatMessage).where(ChatMessage.user_id == user.id, ChatMessage.conversation_id == conversation_id, ChatMessage.status == "completed", ChatMessage.role.in_(["user", "assistant"])).order_by(ChatMessage.id.desc()).limit(12)).all()
        history = _bounded_chat_history([{"role": row.role, "content": row.content} for row in reversed(stored)])
        user_message = ChatMessage(user_id=user.id, conversation_id=conversation_id, role="user", content=payload.message, status="pending")
        db.add(user_message)
        db.flush()
    else:
        conversation_id = None
        history = []
        user_message = None
    confirmation = is_task_confirmation(payload.message)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    pending_candidate = None
    if confirmation and conversation_id:
        expired_rows = db.scalars(select(TaskManagerOperation).where(
            TaskManagerOperation.user_id == user.id,
            TaskManagerOperation.conversation_id == conversation_id,
            TaskManagerOperation.status == "awaiting_user_confirmation",
            TaskManagerOperation.expires_at <= now,
        )).all()
        for expired in expired_rows:
            expired.status = "expired"
            db.add(AgentAuditEvent(user_id=user.id, run_id=expired.run_id, actor="system", event_type="task_candidate_expired", detail=f"operation_id={expired.client_operation_id}"))
        if expired_rows:
            db.commit()
        pending_rows = db.scalars(select(TaskManagerOperation).where(
            TaskManagerOperation.user_id == user.id,
            TaskManagerOperation.conversation_id == conversation_id,
            TaskManagerOperation.status == "awaiting_user_confirmation",
            TaskManagerOperation.expires_at > now,
        ).order_by(TaskManagerOperation.created_at.desc()).limit(10)).all()
        pending_candidate = next((row for row in pending_rows if any(item.get("kind") == "prepared_tasks" for item in (row.action_details or []))), None)
    operation = TaskManagerOperation(user_id=user.id, client_operation_id=operation_id, conversation_id=conversation_id,
                                     user_message_id=user_message.id if user_message else None, status="started",
                                     action_details=[{"kind": "request_constraints", "expected_task_count": requested_task_count(payload.message)}] + ([{"kind": "knowledge_task_proposal", "source_card_ids": payload.knowledge_card_ids, "preserve_undated": True}] if knowledge_context else []))
    operation.mode = payload.mode
    operation.request_fingerprint = fingerprint
    db.add(operation)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        duplicate = db.scalar(select(TaskManagerOperation).where(TaskManagerOperation.user_id == user.id, TaskManagerOperation.client_operation_id == operation_id))
        if duplicate and duplicate.request_fingerprint == fingerprint and duplicate.status in {"applied", "completed", "failed", "no_write", "undone", "awaiting_confirmation", "awaiting_user_confirmation", "expired"}:
            return {"idempotent_replay": True, **_task_operation_result(db, duplicate)}
        raise HTTPException(409, detail={"code": "DUPLICATE_OPERATION", "message": "This task instruction is already being processed."})
    delete_request = bool(re.search(r"(删除|删掉|移除).{0,20}任务", payload.message))
    write_authorized = (payload.mode == "create" and not delete_request) or payload.mode == "modify" or (payload.mode == "auto" and explicit_task_write_intent(payload.message) and not knowledge_context) or bool(confirmation and pending_candidate)
    if confirmation and pending_candidate is None:
        operation.status = "no_write"
        content = "没有找到当前会话中仍有效的待确认任务预览；本次没有修改正式任务。请先描述任务，再确认预览。"
        if user_message:
            user_message.status = "completed"
            db.add(ChatMessage(user_id=user.id, conversation_id=conversation_id, role="assistant", content=content, status="completed"))
            conversation.updated_at = now
        db.commit()
        return {"content": content, **_task_operation_result(db, operation), "write_authorized": False, "tools": []}
    if confirmation and pending_candidate is not None:
        prepared = next(item for item in pending_candidate.action_details if item.get("kind") == "prepared_tasks")
        knowledge_action = next((item for item in pending_candidate.action_details if item.get("kind") == "knowledge_task_proposal"), None)
        try:
            candidate_items = payload.candidate_tasks if payload.candidate_tasks is not None else prepared.get("items", [])
            candidate = CreateTasksInput.model_validate({"items": candidate_items})
            if not pending_candidate.run_id or pending_candidate.expires_at is None or pending_candidate.expires_at <= now:
                raise ValueError("TASK_PREVIEW_EXPIRED")
            if knowledge_action:
                knowledge_service.get_active_cards(db, user.id, knowledge_action.get("source_card_ids", []))
                pending_candidate.action_details = [({**item, "items": [value.model_dump(mode="json") for value in candidate.items]} if item.get("kind") == "prepared_tasks" else item) for item in pending_candidate.action_details]
                operation.action_details = [*operation.action_details, {"kind": "knowledge_task_proposal", "source_card_ids": knowledge_action["source_card_ids"], "preserve_undated": True}]
            result = create_tasks(db, user.id, pending_candidate.run_id, operation_id, candidate.items)
            operation.run_id = pending_candidate.run_id
            operation.status = "completed"
            pending_candidate.status = "completed"
            pending_candidate.expires_at = None
            pending_candidate.action_details = [*pending_candidate.action_details, {"kind": "confirmed_by", "operation_id": operation_id}]
            db.add(AgentAuditEvent(user_id=user.id, run_id=pending_candidate.run_id, actor="user", event_type="task_candidates_confirmed", detail=f"operation_id={operation_id}"))
            result_tasks = _task_operation_result(db, operation)
            created = [task for task in result_tasks["tasks"] if any(action.get("kind") == "create" and action.get("task_id") == task["id"] for action in operation.action_details)]
            created_task_ids = [int(item["id"]) for item in result.get("created", [])]
            if knowledge_action and created_task_ids:
                knowledge_service.link_tasks_to_cards(db, user.id, knowledge_action["source_card_ids"], created_task_ids)
            content = "已按你对待确认预览的确认写入正式任务：\n" + "\n".join(f"#{task['id']} {task['title']} · {task['module']} · {task['start_date']} 至 {task['due_date']}" for task in created) if created else "确认完成，但所有候选均与现有任务重复；未创建重复任务。"
            if user_message:
                user_message.status = "completed"
                db.add(ChatMessage(user_id=user.id, conversation_id=conversation_id, role="assistant", content=content, status="completed"))
                conversation.updated_at = now
            db.commit()
            return {"content": content, **_task_operation_result(db, operation), "write_authorized": True,
                    "tools": [{"name": "confirm_prepared_tasks", "status": "completed"}], "created": result.get("created", []), "duplicates": result.get("duplicates", [])}
        except (ValueError, IntegrityError) as exc:
            db.rollback()
            operation = db.scalar(select(TaskManagerOperation).where(TaskManagerOperation.user_id == user.id, TaskManagerOperation.client_operation_id == operation_id))
            pending_candidate = db.scalar(select(TaskManagerOperation).where(TaskManagerOperation.user_id == user.id, TaskManagerOperation.client_operation_id == pending_candidate.client_operation_id))
            operation.status = "failed"
            operation.run_id = pending_candidate.run_id
            user_message = db.get(ChatMessage, user_message.id) if user_message else None
            if user_message is not None:
                user_message.status = "failed"
                user_message.run_id = pending_candidate.run_id
            db.add(AgentAuditEvent(user_id=user.id, run_id=pending_candidate.run_id, actor="user", event_type="task_candidate_confirmation_failed", detail=f"operation_id={operation_id}; code={type(exc).__name__}"))
            db.commit()
            raise HTTPException(409, detail={"code": "TASK_CONFIRMATION_FAILED", "message": str(exc)[:200]}) from exc
    required_tools = (["get_active_tasks", "prepare_tasks"] if knowledge_context else task_manager_required_tools(payload.message, payload.mode))
    commit_phase = False
    try:
        result = await run_chat(db, user.id, payload.message, history, include_personal=True, enable_web=False,
                                conversation_id=conversation_id, message_id=user_message.id if user_message else None,
                                task_manager=True, task_write_authorized=write_authorized, operation_id=operation_id,
                                task_prepare_authorized=payload.mode == "auto" and not write_authorized,
                                required_task_tools=required_tools, knowledge_context=knowledge_context)
        db.refresh(operation)
        operation.run_id = result.get("run_id")
        if operation.status == "started":
            operation.status = "no_write"
        task_result = _task_operation_result(db, operation)
        if operation.status == "awaiting_user_confirmation":
            prepared_action = next((item for item in operation.action_details if item.get("kind") == "prepared_tasks"), None)
            if prepared_action:
                content = "任务预览已保存，尚未写入正式任务。请回复“确认”或“创建吧”在 24 小时内执行：\n" + "\n".join(f"- {item['title']} · {item['module']} · {item.get('start_date') or '未排期'} 至 {item.get('due_date') or item.get('start_date') or '未排期'}" for item in prepared_action.get("items", []))
                result["content"] = content
            else:
                operation.status = "no_write"
                result["content"] = "没有生成可确认的结构化任务预览；正式任务未改变。请补充任务细节后重试。"
        elif write_authorized:
            if operation.status != "applied":
                duplicates = [task for task in task_result["tasks"] if any(a.get("kind") == "duplicate" and a.get("task_id") == task["id"] for a in operation.action_details)]
                pending = [task for task in task_result["tasks"] if any(a.get("kind") == "delete_pending" and a.get("task_id") == task["id"] for a in operation.action_details)]
                result["content"] = ("删除请求已准备，尚未删除；请在页面再次确认：\n" + "\n".join(f"#{t['id']} {t['title']}" for t in pending)) if pending else (("发现同领域已有同名任务，本次没有重复添加：\n" + "\n".join(f"#{t['id']} {t['title']}" for t in duplicates)) if duplicates else "我没有执行任务写入：本轮没有通过工具校验并提交任何任务。请检查指令后重新提交。")
            else:
                created = [task for task in task_result["tasks"] if any(a.get("kind") == "create" and a.get("task_id") == task["id"] for a in operation.action_details)]
                updated = [task for task in task_result["tasks"] if any(a.get("kind") == "update" and a.get("task_id") == task["id"] for a in operation.action_details)]
                result["content"] = ("已按你的明确指令写入正式任务：\n" + "\n".join(f"#{t['id']} {t['title']} · {t['module']} · {t['start_date']} 至 {t['due_date']}" for t in created)) if created else ("已修改任务：\n" + "\n".join(f"#{t['id']} {t['title']} · {t['module']} · {t['start_date']} 至 {t['due_date']}" for t in updated))
        else:
            result["content"] = "本轮未获任务写入授权，正式任务没有被修改。\n\n" + result["content"]
        if user_message:
            user_message.status = "completed"
            user_message.run_id = result.get("run_id")
            assistant = ChatMessage(user_id=user.id, conversation_id=conversation_id, run_id=result.get("run_id"), role="assistant", content=result["content"][:12000], status="completed")
            db.add(assistant)
            conversation.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        if operation.status == "applied":
            operation.status = "completed"
        elif operation.status == "started":
            operation.status = "no_write"
        commit_phase = True
        db.commit()
        commit_phase = False
        task_result = _task_operation_result(db, operation)
        return {**result, **task_result, "operation_id": operation_id, "write_authorized": write_authorized}
    except AgentRunError as exc:
        db.refresh(operation)
        operation.run_id = getattr(exc, "run_id", None)
        preserve_pending = operation.status in {"applied", "awaiting_confirmation", "awaiting_user_confirmation"}
        if not preserve_pending:
            operation.status = "failed"
            operation.failure_code = exc.code
        if user_message:
            user_message.status = "completed" if operation.status in {"awaiting_confirmation", "awaiting_user_confirmation"} else "failed"
            user_message.run_id = getattr(exc, "run_id", None)
        db.commit()
        if operation.status in {"awaiting_confirmation", "awaiting_user_confirmation"}:
            if user_message:
                content = "任务预览已保存，模型最终回复失败；回复“确认”或“创建吧”仍可执行。" if operation.status == "awaiting_user_confirmation" else "删除请求已准备，但尚未执行。请在任务页再次确认。"
                assistant = ChatMessage(user_id=user.id, conversation_id=conversation_id, run_id=operation.run_id, role="assistant", content=content, status="completed")
                db.add(assistant)
                db.commit()
            return {"content": content if user_message else "任务操作已保存，尚未完成。", "run_id": operation.run_id, **_task_operation_result(db, operation), "operation_id": operation_id, "write_authorized": write_authorized}
        if operation.status == "applied":
            return {"content": "任务已经由后端工具成功提交；模型最终润色失败。以下状态来自数据库实际记录。", "run_id": operation.run_id, **_task_operation_result(db, operation), "operation_id": operation_id, "write_authorized": write_authorized}
        raise _agent_failure(exc, request) from exc
    except Exception as exc:
        db.rollback()
        db.refresh(operation)
        if operation.status == "started":
            operation.status = "failed"
            operation.failure_code = "COMMIT_FAILED" if commit_phase else "TASK_OPERATION_INTERNAL_ERROR"
            if user_message:
                user_message.status = "failed"
            db.commit()
        if commit_phase:
            raise HTTPException(503, detail={"code": "COMMIT_FAILED", "message": "任务事务未能提交；系统已回滚并核对操作记录。", "request_id": getattr(request.state, "request_id", None)}) from exc
        raise


@router.get("/task-manager/operations/{operation_id}")
def get_task_operation(operation_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        normalized = str(UUID(operation_id))
    except ValueError as exc:
        raise HTTPException(422, "Invalid operation ID") from exc
    operation = db.scalar(select(TaskManagerOperation).where(TaskManagerOperation.user_id == user.id, TaskManagerOperation.client_operation_id == normalized))
    if operation is None:
        raise HTTPException(404, "Task operation not found")
    _recover_stale_task_operation(db, operation)
    return _task_operation_result(db, operation)


@router.post("/task-manager/operations/{operation_id}/undo")
def undo_task_operation(operation_id: str, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    try:
        normalized = str(UUID(operation_id))
    except ValueError as exc:
        raise HTTPException(422, "Invalid operation ID") from exc
    operation = db.scalar(select(TaskManagerOperation).where(TaskManagerOperation.user_id == user.id, TaskManagerOperation.client_operation_id == normalized))
    if operation is None or operation.status != "completed" or operation.undone_at is not None:
        raise HTTPException(409, detail={"code": "OPERATION_NOT_UNDOABLE", "message": "Operation is missing, not committed, or already undone."})
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for item in reversed(operation.action_details):
        task = db.scalar(select(Task).where(Task.id == item.get("task_id"), Task.user_id == user.id))
        if task is None:
            continue
        if item.get("kind") == "create":
            task.deleted_at = now
            task.is_pinned = False
        elif item.get("kind") == "delete":
            task.deleted_at = None
            task.is_pinned = bool(item.get("before", {}).get("is_pinned", False))
        elif item.get("kind") == "update":
            before = item.get("before", {})
            old_start, old_due = task.start_date, task.due_date
            for key, value in before.items():
                if key in {"start_date", "due_date"} and value is not None:
                    value = date.fromisoformat(value)
                setattr(task, key, value)
            if (old_start, old_due) != (task.start_date, task.due_date):
                db.add(TaskDateHistory(user_id=user.id, task_id=task.id, old_start_date=old_start, new_start_date=task.start_date, old_due_date=old_due, new_due_date=task.due_date, reason="task_manager_undo"))
    operation.undone_at = now
    operation.status = "undone"
    db.add(AgentAuditEvent(user_id=user.id, run_id=operation.run_id, actor="user", event_type="task_manager_undone", detail=f"operation_id={normalized}"))
    db.commit()
    return _task_operation_result(db, operation)


def _bounded_chat_history(history: list[dict], *, max_chars: int = 24000) -> list[dict]:
    selected = []
    remaining = max_chars
    for item in reversed(history[-12:]):
        role, content = item.get("role"), item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        content = content[:min(6000, remaining)]
        if not content:
            break
        selected.append({"role": role, "content": content})
        remaining -= len(content)
        if remaining <= 0:
            break
    return list(reversed(selected))


def _conversation_out(conversation: ChatConversation, last_message: ChatMessage | None = None):
    first = conversation.title or "新会话"
    return {"id": conversation.id, "title": first, "domain": conversation.domain or "general", "is_pinned": bool(conversation.is_pinned), "archived": conversation.archived_at is not None, "deleted": conversation.deleted_at is not None, "deleted_at": conversation.deleted_at, "created_at": conversation.created_at, "updated_at": conversation.updated_at, "last_message_preview": (last_message.content[:160] if last_message else "")}


def _owned_conversation(db: Session, user_id: int, conversation_id: str) -> ChatConversation:
    try:
        normalized_id = str(UUID(conversation_id))
    except ValueError as exc:
        raise HTTPException(404, detail={"code": "CONVERSATION_NOT_FOUND", "message": "Conversation not found"}) from exc
    conversation = db.get(ChatConversation, normalized_id)
    if conversation is None or conversation.user_id != user_id:
        raise HTTPException(404, detail={"code": "CONVERSATION_NOT_FOUND", "message": "Conversation not found"})
    return conversation


@router.get("/agent/chat/conversations")
def list_chat_conversations(domain: str | None = None, q: str | None = None, include_archived: bool = False, include_deleted: bool = False, limit: int = 100, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not user.chat_history_enabled:
        return {"enabled": False, "conversations": []}
    allowed_domains = {"general", "bilingual", "global", "health", "stem", "projects", "finance", "reviews", "tasks"}
    if domain and domain not in allowed_domains:
        raise HTTPException(422, detail={"code": "INVALID_CHAT_DOMAIN", "message": "Unknown conversation domain"})
    conditions = [ChatConversation.user_id == user.id]
    if domain:
        conditions.append(ChatConversation.domain == domain)
    if include_deleted:
        conditions.append(ChatConversation.deleted_at.is_not(None))
    else:
        conditions.append(ChatConversation.deleted_at.is_(None))
        conditions.append(ChatConversation.archived_at.is_not(None) if include_archived else ChatConversation.archived_at.is_(None))
    if q and q.strip():
        needle = f"%{q.strip()[:120]}%"
        matching_messages = select(ChatMessage.conversation_id).where(ChatMessage.user_id == user.id, ChatMessage.content.ilike(needle))
        conditions.append(or_(ChatConversation.title.ilike(needle), ChatConversation.id.in_(matching_messages)))
    conversations = db.scalars(select(ChatConversation).where(*conditions).order_by(ChatConversation.is_pinned.desc(), ChatConversation.updated_at.desc(), ChatConversation.created_at.desc()).limit(max(1, min(limit, 100)))).all()
    result = []
    for conversation in conversations:
        last_message = db.scalar(select(ChatMessage).where(ChatMessage.user_id == user.id, ChatMessage.conversation_id == conversation.id).order_by(ChatMessage.id.desc()).limit(1))
        result.append(_conversation_out(conversation, last_message))
    return {"enabled": True, "conversations": result}


@router.post("/agent/chat/conversations")
def create_chat_conversation(payload: ChatConversationCreate = ChatConversationCreate(), user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    if not user.chat_history_enabled:
        raise HTTPException(409, detail={"code": "CHAT_HISTORY_DISABLED", "message": "Conversation persistence is disabled"})
    conversation = ChatConversation(id=str(uuid4()), user_id=user.id, title="新会话", domain=payload.domain)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return _conversation_out(conversation)


@router.get("/agent/chat/conversations/{conversation_id}")
def get_chat_conversation(conversation_id: str, before_id: int | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not user.chat_history_enabled:
        raise HTTPException(409, detail={"code": "CHAT_HISTORY_DISABLED", "message": "Conversation persistence is disabled"})
    conversation = _owned_conversation(db, user.id, conversation_id)
    if conversation.deleted_at is not None:
        raise HTTPException(410, detail={"code": "CONVERSATION_DELETED", "message": "Restore this conversation before opening it."})
    base = [ChatMessage.user_id == user.id, ChatMessage.conversation_id == conversation.id]
    if before_id is not None:
        base.append(ChatMessage.id < before_id)
    assistant_ids = db.scalars(select(ChatMessage.id).where(*base, ChatMessage.role == "assistant").order_by(ChatMessage.id.desc()).limit(10)).all()
    if assistant_ids:
        oldest_assistant = min(assistant_ids)
        previous_assistant = db.scalar(select(ChatMessage.id).where(ChatMessage.user_id == user.id, ChatMessage.conversation_id == conversation.id, ChatMessage.role == "assistant", ChatMessage.id < oldest_assistant).order_by(ChatMessage.id.desc()).limit(1))
        first_question = db.scalar(select(ChatMessage.id).where(ChatMessage.user_id == user.id, ChatMessage.conversation_id == conversation.id, ChatMessage.role == "user", ChatMessage.id > (previous_assistant or 0), ChatMessage.id < oldest_assistant).order_by(ChatMessage.id).limit(1))
        start_id = first_question or oldest_assistant
        messages = db.scalars(select(ChatMessage).where(*base, ChatMessage.id >= start_id).order_by(ChatMessage.id)).all()
    else:
        messages = list(reversed(db.scalars(select(ChatMessage).where(*base).order_by(ChatMessage.id.desc()).limit(10)).all()))
        start_id = messages[0].id if messages else None
    has_more = bool(start_id and db.scalar(select(ChatMessage.id).where(ChatMessage.user_id == user.id, ChatMessage.conversation_id == conversation.id, ChatMessage.id < start_id).limit(1)))
    return {**_conversation_out(conversation), "messages": [{"id": item.id, "role": item.role, "content": item.content, "status": item.status, "run_id": item.run_id, "created_at": item.created_at, **_message_trace(db, item, user.id)} for item in messages], "has_more": has_more, "next_before_id": start_id if has_more else None, "linked_tasks": conversation_tasks(conversation.id, user, db)}


@router.patch("/agent/chat/conversations/{conversation_id}")
def update_chat_conversation(conversation_id: str, payload: ChatConversationPatch, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    if not user.chat_history_enabled:
        raise HTTPException(409, detail={"code": "CHAT_HISTORY_DISABLED", "message": "Conversation persistence is disabled"})
    conversation = _owned_conversation(db, user.id, conversation_id)
    if conversation.deleted_at is not None:
        raise HTTPException(410, detail={"code": "CONVERSATION_DELETED", "message": "Restore this conversation before editing it."})
    changes = payload.model_dump(exclude_unset=True)
    if "title" in changes:
        conversation.title = changes["title"].strip()
    if changes.get("domain") is not None:
        conversation.domain = changes["domain"]
    if changes.get("is_pinned") is not None:
        conversation.is_pinned = changes["is_pinned"]
    if changes.get("archived") is not None:
        conversation.archived_at = datetime.now(timezone.utc).replace(tzinfo=None) if changes["archived"] else None
    db.commit()
    db.refresh(conversation)
    return _conversation_out(conversation)


@router.delete("/agent/chat/conversations/{conversation_id}")
def delete_chat_conversation(conversation_id: str, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    if not user.chat_history_enabled:
        raise HTTPException(409, detail={"code": "CHAT_HISTORY_DISABLED", "message": "Conversation persistence is disabled"})
    conversation = _owned_conversation(db, user.id, conversation_id)
    pending = db.scalar(select(ChatMessage.id).where(ChatMessage.user_id == user.id, ChatMessage.conversation_id == conversation.id, ChatMessage.status == "pending").limit(1))
    if pending is not None:
        raise HTTPException(409, detail={"code": "CONVERSATION_RUN_IN_PROGRESS", "message": "Wait for the current Agent request to finish before deleting this conversation."})
    if conversation.deleted_at is None:
        conversation.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        conversation.is_pinned = False
        db.add(AgentAuditEvent(user_id=user.id, actor="user", event_type="conversation_trashed", detail=f"conversation_id={conversation.id}"))
        db.commit()
    return {"id": conversation.id, "deleted": True, "deleted_at": conversation.deleted_at, "retention": "not_automatically_purged; permanent_delete_not_available_in_this_release"}


@router.post("/agent/chat/conversations/{conversation_id}/restore")
def restore_chat_conversation(conversation_id: str, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    if not user.chat_history_enabled:
        raise HTTPException(409, detail={"code": "CHAT_HISTORY_DISABLED", "message": "Conversation persistence is disabled"})
    conversation = _owned_conversation(db, user.id, conversation_id)
    if conversation.deleted_at is None:
        return _conversation_out(conversation)
    conversation.deleted_at = None
    db.add(AgentAuditEvent(user_id=user.id, actor="user", event_type="conversation_restored", detail=f"conversation_id={conversation.id}"))
    db.commit()
    db.refresh(conversation)
    return _conversation_out(conversation)


@router.post("/agent/chat/conversations/{conversation_id}/permanent-delete")
def permanently_delete_chat_conversation(conversation_id: str, payload: PurgeContentRequest, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    if not user.chat_history_enabled:
        raise HTTPException(409, detail={"code": "CHAT_HISTORY_DISABLED", "message": "Conversation persistence is disabled"})
    conversation = _owned_conversation(db, user.id, conversation_id)
    if conversation.deleted_at is None:
        raise HTTPException(409, detail={"code": "CONVERSATION_MUST_BE_TRASHED", "message": "Move the conversation to Recently deleted first."})
    if payload.confirm_title.strip() != conversation.title:
        raise HTTPException(422, detail={"code": "CONFIRM_TITLE_MISMATCH", "message": "The confirmation title does not match."})
    messages = db.scalars(select(ChatMessage).where(ChatMessage.user_id == user.id, ChatMessage.conversation_id == conversation.id)).all()
    if any(message.status == "pending" for message in messages):
        raise HTTPException(409, detail={"code": "CONVERSATION_RUN_IN_PROGRESS", "message": "An Agent request is still in progress; try again when it finishes."})
    run_ids = {message.run_id for message in messages if message.run_id is not None}
    if run_ids:
        active_run = db.scalar(select(AgentRun.id).where(AgentRun.user_id == user.id, AgentRun.id.in_(run_ids), AgentRun.status.in_(["started", "running"])).limit(1))
        if active_run is not None:
            raise HTTPException(409, detail={"code": "CONVERSATION_RUN_IN_PROGRESS", "message": "An Agent request is still in progress; try again when it finishes."})
        # Keep minimal execution metadata for safety audit, but remove tool payloads that
        # could otherwise preserve a deleted conversation's private text in a hidden copy.
        db.execute(update(AgentToolCall).where(AgentToolCall.user_id == user.id, AgentToolCall.run_id.in_(run_ids)).values(arguments_summary=None, result_summary=None))
    operation_rows = db.scalars(select(TaskManagerOperation).where(TaskManagerOperation.user_id == user.id, TaskManagerOperation.conversation_id == conversation.id)).all()
    for operation in operation_rows:
        cleaned = []
        for action in operation.action_details or []:
            if action.get("kind") == "prepared_tasks":
                cleaned.append({"kind": "prepared_tasks_purged"})
            else:
                cleaned.append(action)
        operation.action_details = cleaned
        if operation.status == "awaiting_user_confirmation":
            operation.status = "cancelled"
            operation.expires_at = None
    db.add(AgentAuditEvent(user_id=user.id, actor="user", event_type="conversation_content_purged", detail=f"conversation_id={conversation.id}; messages={len(messages)}; runs={len(run_ids)}; independent backups unchanged"))
    db.delete(conversation)
    db.commit()
    return {"id": conversation_id, "purged": True, "messages_purged": len(messages), "tool_payloads_cleared": len(run_ids),
            "formal_tasks_preserved": True, "safety_audit_metadata_preserved": True,
            "backup_notice": "Independent historical backups are not altered and may retain pre-delete content."}


def _conversation_export_data(db: Session, conversation: ChatConversation, user_id: int):
    messages = db.scalars(select(ChatMessage).where(ChatMessage.user_id == user_id, ChatMessage.conversation_id == conversation.id).order_by(ChatMessage.id)).all()
    exported_messages = []
    for message in messages:
        exported_messages.append({"id": message.id, "role": message.role, "content": message.content, "status": message.status, "created_at": message.created_at.isoformat() if message.created_at else None, "run_id": message.run_id, **_message_trace(db, message, user_id)})
    return {"export_scope": "conversation_messages_only; linked_tasks_knowledge_audit_records_and_database_backups_are_not_deleted_or_exported", "conversation": {"id": conversation.id, "title": conversation.title, "domain": conversation.domain, "created_at": conversation.created_at.isoformat() if conversation.created_at else None, "updated_at": conversation.updated_at.isoformat() if conversation.updated_at else None}, "messages": exported_messages}


def _message_trace(db: Session, message: ChatMessage, user_id: int) -> dict:
    if message.role != "assistant" or message.run_id is None:
        return {"question": None, "run": None, "tools": [], "sources": [], "source_note": "此消息没有可验证的 Agent Run 关联。"}
    run = db.scalar(select(AgentRun).where(AgentRun.id == message.run_id, AgentRun.user_id == user_id))
    if run is None:
        return {"question": None, "run": None, "tools": [], "sources": [], "source_note": "关联运行记录不可用；未推断外部来源。"}
    question = db.scalar(select(ChatMessage.content).where(ChatMessage.user_id == user_id, ChatMessage.conversation_id == message.conversation_id, ChatMessage.run_id == run.id, ChatMessage.role == "user").order_by(ChatMessage.id.desc()).limit(1))
    calls = db.scalars(select(AgentToolCall).where(AgentToolCall.user_id == user_id, AgentToolCall.run_id == run.id).order_by(AgentToolCall.id)).all()
    tools = [{"name": call.tool_name, "status": call.status, "error_code": call.error_code, "duration_ms": call.duration_ms} for call in calls]
    sources: dict[str, dict] = {}
    fetch_calls = []
    for call in calls:
        try:
            summary = __import__("json").loads(call.result_summary or "{}")
        except (TypeError, ValueError):
            summary = {}
        if not isinstance(summary, dict):
            summary = {}
        if call.tool_name == "web_search":
            for source in summary.get("sources", []) if isinstance(summary.get("sources"), list) else []:
                if isinstance(source, dict) and source.get("url", "").startswith("https://"):
                    sources.setdefault(source["url"], {"url": source["url"], "title": source.get("title"), "published_date": source.get("published_date"), "retrieved_at": source.get("retrieved_at"), "search_status": call.status, "fetch_status": "search_summary_only", "evidence_level": "search_summary_only"})
        elif call.tool_name == "web_fetch":
            fetch_calls.append((call, summary))
            url = summary.get("url") if call.status in {"completed", "success"} else None
            if isinstance(url, str) and url.startswith("https://"):
                item = sources.setdefault(url, {"url": url, "title": summary.get("title"), "retrieved_at": summary.get("retrieved_at"), "search_status": "not_in_search_audit", "fetch_status": call.status, "evidence_level": "fetched_body"})
                item.update({"title": summary.get("title") or item.get("title"), "retrieved_at": summary.get("retrieved_at") or item.get("retrieved_at"), "fetch_status": call.status, "evidence_level": "fetched_body"})
            elif call.status not in {"completed", "success"}:
                try:
                    args = __import__("json").loads(call.arguments_summary or "{}")
                except (TypeError, ValueError):
                    args = {}
                for source in sources.values():
                    if args.get("url_sha256") == hashlib.sha256(source["url"].encode("utf-8")).hexdigest():
                        source.update({"fetch_status": call.status, "fetch_error_code": call.error_code, "evidence_level": "search_summary_only_fetch_failed"})
    run_view = {"id": run.id, "provider": run.provider, "model": run.model, "status": run.status, "started_at": run.started_at.isoformat() if run.started_at else None, "finished_at": run.finished_at.isoformat() if run.finished_at else None}
    knowledge_ids = []
    knowledge_status = "disabled"
    for call in calls:
        if call.tool_name == "knowledge_retrieval":
            knowledge_status = "completed" if call.status == "completed" and call.result_summary != "{}" else ("empty" if call.status == "empty" else "failed")
        if call.tool_name == "knowledge_retrieval" and call.status == "completed":
            try:
                summary = __import__("json").loads(call.result_summary or "{}")
                knowledge_ids.extend(int(value) for value in summary.get("card_ids", []) if isinstance(value, int))
            except (TypeError, ValueError, AttributeError):
                pass
    try:
        knowledge_sources = knowledge_service.get_active_cards(db, user_id, list(dict.fromkeys(knowledge_ids))[:5]) if knowledge_ids else []
    except (ValueError, knowledge_service.KnowledgeNotFound):
        knowledge_sources = []
    return {"question": question, "run": run_view, "tools": tools, "sources": list(sources.values()), "knowledge_sources": knowledge_sources, "knowledge_status": knowledge_status, "source_note": "本次回答没有关联外部核验证据。" if not sources else None}


@router.get("/agent/chat/conversations/{conversation_id}/tasks")
def conversation_tasks(conversation_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    conversation = _owned_conversation(db, user.id, conversation_id)
    if conversation.deleted_at is not None:
        raise HTTPException(410, "Restore the conversation before opening linked tasks")
    rows = db.execute(select(TaskConversationLink, Task).join(Task, Task.id == TaskConversationLink.task_id).where(TaskConversationLink.user_id == user.id, TaskConversationLink.conversation_id == conversation.id, Task.user_id == user.id, Task.deleted_at.is_(None)).order_by(Task.is_pinned.desc(), Task.updated_at.desc(), TaskConversationLink.id)).all()
    unique = {}
    for link, task in rows:
        unique.setdefault(task.id, {"link_id": link.id, **task_out(task), "message_id": link.message_id})
    return list(unique.values())


@router.post("/agent/chat/conversations/{conversation_id}/tasks/{task_id}")
def add_conversation_task(conversation_id: str, task_id: int, message_id: int | None = None, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    conversation = _owned_conversation(db, user.id, conversation_id)
    if conversation.deleted_at is not None:
        raise HTTPException(410, "Restore the conversation before linking tasks")
    task = _owned_task(db, user.id, task_id)
    link, created = _task_link(db, user.id, task.id, conversation.id, message_id)
    db.commit()
    return {"id": link.id, "task_id": task.id, "conversation_id": conversation.id, "message_id": link.message_id, "created": created}


@router.post("/agent/chat/messages/{message_id}/task-proposal")
def propose_task_from_message(message_id: int, payload: ChatTaskProposalIn, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    message = db.get(ChatMessage, message_id)
    if not message or message.user_id != user.id or message.role != "assistant" or not message.run_id or not message.conversation_id:
        raise HTTPException(404, detail={"code": "AI_MESSAGE_NOT_FOUND", "message": "No persisted AI reply with a real Agent Run was found"})
    conversation = _owned_conversation(db, user.id, message.conversation_id)
    if conversation.deleted_at is not None:
        raise HTTPException(410, "Restore the conversation before proposing a task")
    item = {"domain": payload.module, "goal_id": None, "existing_task_id": None, "title": payload.title, "action": payload.description, "estimated_minutes": payload.estimated_minutes, "why_today": "由用户从已保存的 AI 回复主动发起；草案仍需人工批准。", "acceptance_criteria": payload.acceptance_criteria, "evidence_required": "保存可复核的实际执行证据。", "source_task_ids": [], "sources": [], "source_conversation_id": conversation.id, "source_message_id": message.id}
    proposal = save_generated_proposal(db, user.id, run_id=message.run_id, proposal_key=f"chat-task:{message.id}", proposal_type="chat_task", plan_date=date.today(), title=payload.title, items=[item], rationale="由用户明确从该 AI 回复请求创建的待审批草案；批准前不写入正式任务。")
    return proposal_view(proposal)


@router.get("/agent/chat/conversations/{conversation_id}/export")
def export_chat_conversation(conversation_id: str, format: str = "markdown", user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not user.chat_history_enabled:
        raise HTTPException(409, detail={"code": "CHAT_HISTORY_DISABLED", "message": "Conversation persistence is disabled"})
    conversation = _owned_conversation(db, user.id, conversation_id)
    if conversation.deleted_at is not None:
        raise HTTPException(410, detail={"code": "CONVERSATION_DELETED", "message": "Restore this conversation before exporting it."})
    data = _conversation_export_data(db, conversation, user.id)
    if format == "json":
        body = __import__("json").dumps(data, ensure_ascii=False, indent=2)
        media_type, extension = "application/json; charset=utf-8", "json"
    elif format in {"markdown", "md"}:
        lines = [f"# {conversation.title}", "", f"- Domain: {conversation.domain}", f"- Conversation ID: `{conversation.id}`", "", "> Scope: messages and linked source evidence only. Tasks, knowledge records, full audit logs and database backups are not included or changed.", ""]
        for message in data["messages"]:
            label = {"user": "用户", "assistant": "Agent"}.get(message["role"], message["role"])
            lines.extend([f"## {label} · {message['created_at'] or '时间未知'} · Message `{message['id']}`", "", message["content"], ""])
            if message["run_id"]:
                lines.append(f"Agent Run: `{message['run_id']}` · 状态：{message['status']}")
            for source in message["sources"]:
                lines.append(f"- 来源（{source.get('evidence_level', source.get('tool', 'unknown'))} / {source.get('fetch_status', source.get('status', 'unknown'))}）：[{source.get('title') or source['url']}]({source['url']})" + (f" · 获取时间：{source['retrieved_at']}" if source.get("retrieved_at") else ""))
            lines.append("")
        body, media_type, extension = "\n".join(lines), "text/markdown; charset=utf-8", "md"
    else:
        raise HTTPException(422, detail={"code": "INVALID_EXPORT_FORMAT", "message": "Use format=markdown or format=json"})
    filename = f"conversation-{conversation.id}.{extension}"
    return StreamingResponse(iter([body.encode("utf-8")]), media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/agent/chat/history")
def agent_chat_history(user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not user.chat_history_enabled:
        return {"enabled": False, "messages": []}
    messages = db.scalars(select(ChatMessage).where(ChatMessage.user_id == user.id, ChatMessage.conversation_id == select(ChatConversation.id).where(ChatConversation.user_id == user.id, ChatConversation.deleted_at.is_(None)).order_by(ChatConversation.updated_at.desc()).limit(1).scalar_subquery()).order_by(ChatMessage.id.desc()).limit(100)).all()
    return {"enabled": True, "messages": [{"id": item.id, "role": item.role, "content": item.content, "created_at": item.created_at} for item in reversed(messages)]}


@router.post("/ai/chat")
async def ai_chat(payload: AIIn, user: User = Depends(require_csrf), db: Session = Depends(get_db)):
    if not payload.consent_granted:
        raise HTTPException(422, "Explicit AI data consent is required")
    provider = ensure_provider(db, "deepseek", user.id)
    request = AIRequest(user_id=user.id, purpose=payload.purpose, provider="deepseek", input_scope=payload.consent_scope, consent_granted=True, status="started")
    db.add(request)
    try:
        api_key = require_user_api_key(provider, user.id)
    except ProviderCredentialRejected as exc:
        request.status = exc.code.lower()
        db.commit()
        raise HTTPException(503, detail={"code": exc.code, "message": str(exc)}) from exc
    except CredentialUnavailable as exc:
        request.status = "credential_unavailable"
        db.commit()
        raise HTTPException(503, str(exc)) from exc
    run = start_run(db, user.id, "simple_chat", "deepseek", "deepseek", provider.model, "user_prompt_only")
    usage_row = start_model_usage(db, run, 1, provider="deepseek", model=provider.model)
    try:
        base_url = provider.base_url or settings.deepseek_base_url
        async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=30) as client:
            result = await client.post("/chat/completions", headers={"Authorization": f"Bearer {api_key}"}, json={"model": provider.model or "deepseek-chat", "messages": [{"role": "user", "content": payload.prompt}]})
            result.raise_for_status()
            data = result.json()
        prompt_tokens, completion_tokens = finish_model_usage(usage_row, data.get("usage") if isinstance(data, dict) else None)
        try:
            content = data["choices"][0]["message"].get("content", "")
            if not isinstance(content, str):
                raise TypeError("non-text model content")
        except (KeyError, IndexError, TypeError) as exc:
            provider.status = "failed"
            provider.last_error = "DeepSeek returned an unexpected response structure"
            request.status = "failed"
            finish_run(db, run, "failed", error_code="MALFORMED_UPSTREAM_RESPONSE", prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
            db.commit()
            raise HTTPException(502, "DeepSeek returned an unexpected response structure") from exc
        provider.total_prompt_tokens += prompt_tokens
        provider.total_completion_tokens += completion_tokens
        provider.status = "connected"
        provider.last_error = None
        request.status = "completed"
        finish_run(db, run, "completed", prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        db.commit()
        return {"configured": True, "content": content, "run_id": run.id, "usage": {"prompt_tokens": usage_row.input_tokens, "completion_tokens": usage_row.output_tokens}}
    except httpx.TimeoutException:
        fail_model_usage(usage_row, "timeout")
        finish_run(db, run, "failed", error_code="MODEL_TIMEOUT")
        provider.status = "timeout"
        provider.last_error = "DeepSeek request timed out"
        request.status = "timeout"
        db.commit()
        raise HTTPException(504, "DeepSeek request timed out")
    except httpx.HTTPStatusError as exc:
        fail_model_usage(usage_row, "failed")
        finish_run(db, run, "failed", error_code=f"HTTP_{exc.response.status_code}")
        provider.status = "rate_limited" if exc.response.status_code == 429 else "failed"
        provider.last_error = f"DeepSeek returned HTTP {exc.response.status_code}"
        request.status = "failed"
        db.commit()
        raise HTTPException(502, "DeepSeek request failed") from exc
    except httpx.HTTPError:
        fail_model_usage(usage_row, "failed")
        finish_run(db, run, "failed", error_code="MODEL_REQUEST_FAILED")
        provider.status = "failed"
        provider.last_error = "DeepSeek request failed"
        request.status = "failed"
        db.commit()
        raise HTTPException(502, "DeepSeek request failed")
    except ValueError as exc:
        fail_model_usage(usage_row, "failed")
        finish_run(db, run, "failed", error_code="MALFORMED_UPSTREAM_RESPONSE")
        provider.status = "failed"
        provider.last_error = "DeepSeek returned an invalid response"
        request.status = "failed"
        db.commit()
        raise HTTPException(502, "DeepSeek returned an invalid response") from exc
