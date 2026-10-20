from contextlib import asynccontextmanager
import asyncio
import logging
from pathlib import Path
from traceback import extract_tb
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .api.routes import router
from .api.knowledge_routes import router as knowledge_router
from .config import settings
from .release_history import current_app_version
from .schema_startup import validate_schema_revision
from .models import entities  # noqa: F401
from .services.backup_service import run_due_local_backups


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_runtime_security()
    # Schema changes are applied explicitly with Alembic before startup.
    if settings.runtime_environment == "production":
        validate_schema_revision()
    backup_task = asyncio.create_task(_local_backup_scheduler())
    yield
    backup_task.cancel()
    try:
        await backup_task
    except asyncio.CancelledError:
        pass


async def _local_backup_scheduler() -> None:
    while True:
        await asyncio.to_thread(run_due_local_backups)
        await asyncio.sleep(3600)


def validate_runtime_security() -> None:
    if settings.runtime_environment == "production":
        secret = settings.secret_key
        if not secret or len(secret) < 32 or secret == "change-me-before-network-use":
            raise RuntimeError("Production startup requires PERSONAL_OS_SECRET_KEY with at least 32 characters")
        if not settings.cookie_secure:
            raise RuntimeError("Production startup requires PERSONAL_OS_COOKIE_SECURE=true")


app = FastAPI(title="Personal OS API", version=current_app_version(), lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[item.strip() for item in settings.trusted_hosts.split(",") if item.strip()] + ["*.ts.net"])
app.add_middleware(CORSMiddleware, allow_origins=[item.strip() for item in settings.allowed_origins.split(",") if item.strip()], allow_credentials=True, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"], allow_headers=["Content-Type", "X-CSRF-Token", "X-Registration-CSRF"])
logger = logging.getLogger("personal_os.errors")


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid4().hex
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", "unknown")
    frames = extract_tb(exc.__traceback__)
    location = " -> ".join(f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}" for frame in frames[-6:])
    # Do not log exception text, locals, prompts, auth headers, or credentials.
    logger.error("Unhandled application error request_id=%s type=%s location=%s", request_id, type(exc).__name__, location)
    return JSONResponse(status_code=500, content={"detail": {"code": "INTERNAL_ERROR", "message": "Internal server error", "request_id": request_id}}, headers={"X-Request-ID": request_id})


app.include_router(router)
app.include_router(knowledge_router)


FRONTEND_DIST = Path(settings.frontend_dist).resolve()
PUBLIC_FILES = {"manifest.webmanifest", "robots.txt", "favicon.ico"}


@app.get("/", include_in_schema=False)
def frontend_root():
    index = FRONTEND_DIST / "index.html"
    if not index.is_file():
        return JSONResponse(status_code=503, content={"detail": "Frontend build is not available. Run pnpm build."})
    return FileResponse(index)


@app.get("/{path:path}", include_in_schema=False)
def frontend_route(path: str):
    if path.startswith("api/") or path.startswith(".") or ".." in Path(path).parts:
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    candidate = FRONTEND_DIST / path
    if path in PUBLIC_FILES and candidate.is_file():
        return FileResponse(candidate)
    if path.startswith("assets/") and candidate.is_file():
        return FileResponse(candidate)
    index = FRONTEND_DIST / "index.html"
    if index.is_file():
        return FileResponse(index)
    return JSONResponse(status_code=503, content={"detail": "Frontend build is not available. Run pnpm build."})
