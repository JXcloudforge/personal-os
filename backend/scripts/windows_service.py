"""Long-running Uvicorn entry point for the Personal OS Windows logon task."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import secrets
import sys
from typing import Callable


def default_log_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data)
    else:
        base = Path.home() / "AppData" / "Local"
    return base / "PersonalOS" / "logs" / "fastapi.log"


def run_state_path(log_path: Path) -> Path:
    """Return a small, non-sensitive marker used to detect unclean exits."""
    return log_path.with_name("fastapi.run-state")


def configure_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_path,
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[handler],
        force=True,
    )


def run_server(
    runner: Callable[..., object],
    *,
    log_path: Path | None = None,
) -> int:
    configure_logging(log_path or default_log_path())
    logger = logging.getLogger("personal_os.windows_service")
    run_id = secrets.token_hex(6)
    marker_path = run_state_path(log_path or default_log_path())
    try:
        previous_run_id = marker_path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        previous_run_id = ""
    except OSError as exc:
        previous_run_id = ""
        logger.warning("run_state_read_failed error_type=%s", type(exc).__name__)
    if previous_run_id:
        logger.error(
            "previous_run_missing_exit run_id=%s reason=unknown; "
            "correlate_task_scheduler_result_and_manager_lifecycle",
            previous_run_id,
        )
    try:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(run_id, encoding="ascii")
    except OSError as exc:
        # Service availability is more important than this supplemental marker;
        # the regular start/exit and Task Scheduler logs remain available.
        logger.error("run_state_write_failed error_type=%s", type(exc).__name__)
    logger.info("service_start run_id=%s host=127.0.0.1 port=8000", run_id)
    exit_code = 0
    try:
        runner(
            "app.main:app",
            host="127.0.0.1",
            port=8000,
            reload=False,
            access_log=False,
            log_config=None,
        )
    except KeyboardInterrupt:
        logger.warning("service_exit run_id=%s reason=keyboard_interrupt exit_code=130", run_id)
        exit_code = 130
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        logger.log(
            logging.INFO if exit_code == 0 else logging.ERROR,
            "service_exit run_id=%s reason=system_exit exit_code=%s",
            run_id,
            exit_code,
        )
    except Exception as exc:  # Task Scheduler uses the exit code for restart policy.
        logger.error(
            "service_exit run_id=%s reason=exception error_type=%s exit_code=1",
            run_id,
            type(exc).__name__,
        )
        exit_code = 1
    else:
        logger.info("service_exit run_id=%s reason=runner_returned exit_code=0", run_id)
    finally:
        try:
            if marker_path.read_text(encoding="ascii").strip() == run_id:
                marker_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.error("run_state_clear_failed error_type=%s", type(exc).__name__)
    return exit_code


def main() -> int:
    # When launched as a script, sys.path[0] is backend/scripts. Add the backend
    # root so the existing app package and its absolute .env loading work.
    backend_root = Path(__file__).resolve().parents[1]
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))

    try:
        import uvicorn
    except Exception as exc:
        log_path = default_log_path()
        configure_logging(log_path)
        logging.getLogger("personal_os.windows_service").error(
            "Could not load the configured Python runtime; error_type=%s", type(exc).__name__
        )
        return 1

    return run_server(uvicorn.run)


if __name__ == "__main__":
    raise SystemExit(main())
