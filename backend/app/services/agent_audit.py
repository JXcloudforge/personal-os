from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from datetime import datetime

from ..models import AgentAuditEvent, AgentModelUsage, AgentRun, AgentToolCall


def start_run(db: Session, user_id: int, workflow: str, mode: str, provider: str, model: str | None, scope: str) -> AgentRun:
    run = AgentRun(user_id=user_id, workflow=workflow, mode=mode, provider=provider, model=model, authorized_scope=scope, status="started")
    db.add(run)
    db.flush()
    audit_event(db, user_id, "run_started", {"workflow": workflow, "mode": mode}, run_id=run.id)
    return run


def audit_event(db: Session, user_id: int, event_type: str, detail: dict | None = None, *, run_id: int | None = None, proposal_id: int | None = None, actor: str = "agent") -> None:
    db.add(AgentAuditEvent(user_id=user_id, run_id=run_id, proposal_id=proposal_id, actor=actor, event_type=event_type, detail=json.dumps(detail or {}, ensure_ascii=False)))


def tool_call(db: Session, run: AgentRun, user_id: int, tool_name: str, allowed: bool, status: str, arguments: dict | None = None, result: dict | None = None, error_code: str | None = None, duration_ms: int | None = None) -> None:
    db.add(AgentToolCall(run_id=run.id, user_id=user_id, tool_name=tool_name, allowed=allowed, status=status, arguments_summary=json.dumps(arguments or {}, ensure_ascii=False), result_summary=json.dumps(result or {}, ensure_ascii=False), error_code=error_code, duration_ms=duration_ms))
    # The production SessionLocal has autoflush=False. Subsequent tool calls
    # must see this run's just-completed calls for source allowlisting,
    # per-run limits, and the required duplicate-check-before-proposal gate.
    db.flush()


def finish_run(db: Session, run: AgentRun, status: str, *, error_code: str | None = None, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
    run.status = status
    run.error_code = error_code
    run.prompt_tokens = prompt_tokens
    run.completion_tokens = completion_tokens
    run.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
    audit_event(db, run.user_id, "run_finished", {"status": status, "error_code": error_code, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}, run_id=run.id)


def start_model_usage(db: Session, run: AgentRun, request_index: int, *, provider: str, model: str | None, conversation_id: str | None = None, message_id: int | None = None) -> AgentModelUsage:
    record = AgentModelUsage(user_id=run.user_id, run_id=run.id, conversation_id=conversation_id, message_id=message_id, request_index=request_index, provider=provider, model=model, status="started")
    db.add(record)
    db.flush()
    return record


def finish_model_usage(record: AgentModelUsage, usage: dict | None, *, status: str = "completed") -> tuple[int, int]:
    usage = usage if isinstance(usage, dict) else {}
    prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
    record.input_tokens = prompt if isinstance(prompt, int) and not isinstance(prompt, bool) and prompt >= 0 else None
    record.output_tokens = completion if isinstance(completion, int) and not isinstance(completion, bool) and completion >= 0 else None
    record.status = status if record.input_tokens is not None and record.output_tokens is not None else "usage_unknown"
    record.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    return record.input_tokens or 0, record.output_tokens or 0


def fail_model_usage(record: AgentModelUsage, status: str) -> None:
    record.status = status
    record.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
