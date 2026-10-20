from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path


SERVICE_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "windows_service.py"
SPEC = importlib.util.spec_from_file_location("personal_os_windows_service", SERVICE_SCRIPT)
assert SPEC and SPEC.loader
service = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(service)


def test_service_runner_uses_loopback_single_uvicorn_without_access_logs(tmp_path):
    calls = []

    def fake_run(app, **kwargs):
        calls.append((app, kwargs))

    try:
        result = service.run_server(fake_run, log_path=tmp_path / "service.log")
        assert result == 0
        assert len(calls) == 1
        app, options = calls[0]
        assert app == "app.main:app"
        assert options == {
            "host": "127.0.0.1",
            "port": 8000,
            "reload": False,
            "access_log": False,
            "log_config": None,
        }
        assert (tmp_path / "service.log").exists()
    finally:
        for handler in logging.getLogger().handlers[:]:
            handler.close()
            logging.getLogger().removeHandler(handler)


def test_service_runner_returns_failure_without_logging_exception_message(tmp_path):
    def failing_run(*_args, **_kwargs):
        raise RuntimeError("sensitive detail must not be logged")

    log_path = tmp_path / "service.log"
    try:
        assert service.run_server(failing_run, log_path=log_path) == 1
        logged = log_path.read_text(encoding="utf-8")
        assert "RuntimeError" in logged
        assert "sensitive detail" not in logged
    finally:
        for handler in logging.getLogger().handlers[:]:
            handler.close()
            logging.getLogger().removeHandler(handler)


def test_service_runner_logs_exception_reason_and_exit_code(tmp_path):
    def failing_run(*_args, **_kwargs):
        raise RuntimeError("sensitive detail must not be logged")

    log_path = tmp_path / "service.log"
    try:
        assert service.run_server(failing_run, log_path=log_path) == 1
        logged = log_path.read_text(encoding="utf-8")
        assert "service_start run_id=" in logged
        assert "service_exit run_id=" in logged
        assert "reason=exception" in logged
        assert "exit_code=1" in logged
        assert "sensitive detail" not in logged
    finally:
        for handler in logging.getLogger().handlers[:]:
            handler.close()
            logging.getLogger().removeHandler(handler)


def test_service_runner_logs_keyboard_interrupt_as_controlled_exit(tmp_path):
    def interrupted_run(*_args, **_kwargs):
        raise KeyboardInterrupt

    log_path = tmp_path / "service.log"
    try:
        assert service.run_server(interrupted_run, log_path=log_path) == 130
        logged = log_path.read_text(encoding="utf-8")
        assert "reason=keyboard_interrupt" in logged
        assert "exit_code=130" in logged
    finally:
        for handler in logging.getLogger().handlers[:]:
            handler.close()
            logging.getLogger().removeHandler(handler)


def test_service_runner_logs_normal_return(tmp_path):
    log_path = tmp_path / "service.log"
    try:
        assert service.run_server(lambda *_args, **_kwargs: None, log_path=log_path) == 0
        logged = log_path.read_text(encoding="utf-8")
        assert "reason=runner_returned" in logged
        assert not (tmp_path / "fastapi.run-state").exists()
        assert "exit_code=0" in logged
    finally:
        for handler in logging.getLogger().handlers[:]:
            handler.close()
            logging.getLogger().removeHandler(handler)


def test_service_runner_logs_system_exit_code(tmp_path):
    def exiting_run(*_args, **_kwargs):
        raise SystemExit(3)

    log_path = tmp_path / "service.log"
    try:
        assert service.run_server(exiting_run, log_path=log_path) == 3
        logged = log_path.read_text(encoding="utf-8")
        assert "reason=system_exit" in logged
        assert "exit_code=3" in logged
    finally:
        for handler in logging.getLogger().handlers[:]:
            handler.close()
            logging.getLogger().removeHandler(handler)


def test_service_runner_reports_previous_run_without_exit_as_unknown(tmp_path):
    log_path = tmp_path / "fastapi.log"
    marker_path = service.run_state_path(log_path)
    marker_path.write_text("deadbeef1234", encoding="ascii")
    try:
        assert service.run_server(lambda *_args, **_kwargs: None, log_path=log_path) == 0
        logged = log_path.read_text(encoding="utf-8")
        assert "previous_run_missing_exit run_id=deadbeef1234 reason=unknown" in logged
        assert "correlate_task_scheduler_result_and_manager_lifecycle" in logged
        assert "service_exit run_id=" in logged
        assert not marker_path.exists()
    finally:
        for handler in logging.getLogger().handlers[:]:
            handler.close()
            logging.getLogger().removeHandler(handler)


def test_log_path_is_outside_project_and_uses_personal_local_app_data(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert service.default_log_path() == tmp_path / "PersonalOS" / "logs" / "fastapi.log"


def test_windowless_entry_logs_without_console_streams(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "stdin", None)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    log_path = tmp_path / "windowless.log"
    try:
        assert service.run_server(lambda *_args, **_kwargs: None, log_path=log_path) == 0
        assert "service_start" in log_path.read_text(encoding="utf-8")
        assert "reason=runner_returned" in log_path.read_text(encoding="utf-8")
    finally:
        for handler in logging.getLogger().handlers[:]:
            handler.close()
            logging.getLogger().removeHandler(handler)
