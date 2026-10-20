from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AIProvider, ChatConversation, ChatMessage
from ..schemas.knowledge import KnowledgeCreate, KnowledgePatch, KnowledgePurgeInput, KnowledgeRefineInput, KnowledgeRetrieveInput
from ..services import knowledge_service as service
from ..services.task_manager import knowledge_task_reference
from ..services.agent_audit import audit_event, fail_model_usage, finish_model_usage, finish_run, start_model_usage, start_run
from ..services.knowledge_refinement_adapter import RefinementError, contains_credential_material, refine_text
from ..services.provider_service import CredentialUnavailable, ProviderCredentialRejected, ensure_provider, require_user_api_key
from .deps import current_user, require_csrf


router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.post("/refine-from-message")
async def refine_from_message(payload: KnowledgeRefineInput, user=Depends(require_csrf), db: Session = Depends(get_db)):
    conversation = db.scalar(select(ChatConversation).where(ChatConversation.id == payload.conversation_id, ChatConversation.user_id == user.id))
    message = db.scalar(select(ChatMessage).where(
        ChatMessage.id == payload.message_id, ChatMessage.user_id == user.id,
        ChatMessage.conversation_id == payload.conversation_id, ChatMessage.role == "assistant",
        ChatMessage.status == "completed",
    ))
    if conversation is None or conversation.deleted_at is not None or message is None:
        raise HTTPException(404, detail={"code": "SOURCE_MESSAGE_NOT_FOUND", "message": "原始回复不存在或无权访问。"})

    source = payload.selected_text if payload.selected_text is not None else message.content
    if payload.selected_text is not None and payload.selected_text not in message.content:
        raise HTTPException(422, detail={"code": "SELECTION_NOT_IN_SOURCE", "message": "所选文本与原始回复不匹配，请重新选择。"})
    if not source.strip() or len(source) > 30000:
        raise HTTPException(422, detail={"code": "SOURCE_TEXT_INVALID", "message": "提炼文本为空或超出长度限制。"})
    sensitive = conversation.domain in {"health", "finance"} or any(term in source.casefold() for term in ("medical record", "diagnosis", "bank account", "credit card", "body weight", "blood pressure", "blood sugar", "sleep log", "income", "salary", "expense", "病历", "诊断", "体重", "血压", "血糖", "饮食", "睡眠", "药物", "症状", "银行卡", "信用卡", "账户余额", "收入", "支出", "工资", "债务"))
    if sensitive and not payload.sensitive_data_consent:
        raise HTTPException(422, detail={"code": "SENSITIVE_CONSENT_REQUIRED", "message": "此内容可能涉及健康或财务信息。请确认仅将当前回复发送给 DeepSeek 后再提炼。"})
    if contains_credential_material(source):
        raise HTTPException(422, detail={"code": "CREDENTIAL_CONTENT_BLOCKED", "message": "检测到疑似密钥、密码或访问令牌；请先从原文中移除敏感凭证。"})

    provider = db.scalar(select(AIProvider).where(AIProvider.user_id == user.id, AIProvider.provider == "deepseek"))
    if provider is None:
        provider = ensure_provider(db, "deepseek", user.id)
    try:
        api_key = require_user_api_key(provider, user.id)
    except ProviderCredentialRejected as exc:
        raise HTTPException(503, detail={"code": exc.code, "message": str(exc)}) from exc
    except CredentialUnavailable as exc:
        raise HTTPException(503, detail={"code": "CREDENTIAL_UNAVAILABLE", "message": "DeepSeek 凭证无法解密，请在设置中重新配置。"}) from exc

    run = start_run(db, user.id, "knowledge_refinement", "deepseek", "deepseek", provider.model, f"single_assistant_message:{message.id}")
    usage_row = start_model_usage(db, run, 1, provider="deepseek", model=provider.model, conversation_id=conversation.id, message_id=message.id)
    audit_event(db, user.id, "knowledge_refinement_requested", {"message_id": message.id, "input_kind": "selection" if payload.selected_text is not None else "full_message", "character_count": len(source), "sensitive_content_detected": sensitive, "sensitive_consent": bool(sensitive and payload.sensitive_data_consent)}, run_id=run.id)
    try:
        candidate, usage = await refine_text(provider, api_key, source)
        prompt_tokens, completion_tokens = finish_model_usage(usage_row, usage)
        provider.total_prompt_tokens += prompt_tokens
        provider.total_completion_tokens += completion_tokens
        provider.status = "connected"
        finish_run(db, run, "completed", prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        audit_event(db, user.id, "knowledge_refinement_candidate_ready", {"message_id": message.id, "input_tokens_known": usage_row.input_tokens is not None, "output_tokens_known": usage_row.output_tokens is not None}, run_id=run.id)
        db.commit()
        return {"candidate": candidate.model_dump(), "run_id": run.id, "usage": {"input_tokens": usage_row.input_tokens, "output_tokens": usage_row.output_tokens, "status": usage_row.status}}
    except RefinementError as exc:
        if exc.usage is not None:
            prompt_tokens, completion_tokens = finish_model_usage(usage_row, exc.usage, status="failed")
            provider.total_prompt_tokens += prompt_tokens
            provider.total_completion_tokens += completion_tokens
        else:
            fail_model_usage(usage_row, "failed")
        finish_run(db, run, "failed", error_code=exc.code)
        audit_event(db, user.id, "knowledge_refinement_failed", {"message_id": message.id, "error_code": exc.code}, run_id=run.id)
        db.commit()
        status = 503 if exc.code in {"UPSTREAM_AUTH_FAILED", "INSUFFICIENT_BALANCE", "RATE_LIMITED", "MODEL_NOT_FOUND", "MODEL_REQUEST_FAILED", "MODEL_TIMEOUT"} else 502
        raise HTTPException(status, detail={"code": exc.code, "message": str(exc), "run_id": run.id}) from exc
    except Exception as exc:
        fail_model_usage(usage_row, "failed")
        finish_run(db, run, "failed", error_code="REFINEMENT_INTERNAL_ERROR")
        audit_event(db, user.id, "knowledge_refinement_failed", {"message_id": message.id, "error_code": "REFINEMENT_INTERNAL_ERROR"}, run_id=run.id)
        db.commit()
        raise HTTPException(500, detail={"code": "REFINEMENT_INTERNAL_ERROR", "message": "提炼失败，原草稿已保留。", "run_id": run.id}) from exc


def _error(exc: Exception):
    if isinstance(exc, service.KnowledgeNotFound):
        raise HTTPException(404, detail={"code": "KNOWLEDGE_NOT_FOUND", "message": "知识卡片不存在或无权访问。"}) from exc
    if isinstance(exc, service.KnowledgeConflict):
        code = str(exc)
        status = 409 if code == "KNOWLEDGE_MUST_BE_TRASHED" else 422
        messages = {"KNOWLEDGE_MUST_BE_TRASHED": "请先将卡片移入最近删除，再执行彻底清理。", "CONFIRM_TITLE_MISMATCH": "确认标题不匹配，未执行清理。"}
        raise HTTPException(status, detail={"code": code, "message": messages.get(code, "知识操作冲突。")}) from exc
    if isinstance(exc, ValueError):
        code = str(exc)
        raise HTTPException(422, detail={"code": code if code.startswith("KNOWLEDGE_") else "KNOWLEDGE_VALIDATION_FAILED", "message": "知识字段无效或关联消息不属于当前用户。"}) from exc
    raise exc


@router.get("")
def list_knowledge(q: str | None = None, domain: str | None = None, knowledge_type: str | None = None,
                   review_status: str | None = None, tag: str | None = None, task_id: int | None = None,
                   include_archived: bool = False, include_deleted: bool = False, limit: int = 100,
                   user=Depends(current_user), db: Session = Depends(get_db)):
    if task_id is not None:
        try:
            knowledge_task_reference(db, user.id, task_id)
        except LookupError as exc:
            raise HTTPException(404, detail={"code": "TASK_NOT_FOUND", "message": "任务不存在或无权访问。"}) from exc
    try:
        cards = service.list_cards(db, user.id, q=q, domain=domain, knowledge_type=knowledge_type, review_status=review_status, tag=tag, task_id=task_id, archived=include_archived, deleted=include_deleted, limit=limit)
        return [service.card_out(db, card, user.id) for card in cards]
    except ValueError as exc:
        _error(exc)


@router.get("/recently-deleted")
def recently_deleted(user=Depends(current_user), db: Session = Depends(get_db)):
    cards = service.list_cards(db, user.id, deleted=True)
    return [service.card_out(db, card, user.id) for card in cards]


@router.post("")
def create_knowledge(payload: KnowledgeCreate, user=Depends(require_csrf), db: Session = Depends(get_db)):
    try:
        card = service.create_card(db, user.id, payload)
        return service.card_out(db, card, user.id)
    except (service.KnowledgeNotFound, ValueError) as exc:
        _error(exc)


@router.get("/{card_id}/permanent-delete-preview")
def permanent_delete_preview(card_id: int, user=Depends(current_user), db: Session = Depends(get_db)):
    try:
        return service.purge_preview(db, user.id, card_id)
    except service.KnowledgeNotFound as exc:
        _error(exc)


@router.post("/{card_id}/permanent-delete")
def permanent_delete(card_id: int, payload: KnowledgePurgeInput, user=Depends(require_csrf), db: Session = Depends(get_db)):
    try:
        return service.purge_card(db, user.id, card_id, payload.confirm_title)
    except (service.KnowledgeNotFound, service.KnowledgeConflict) as exc:
        _error(exc)


@router.get("/{card_id}/export")
def export_knowledge(card_id: int, format: str = "markdown", user=Depends(current_user), db: Session = Depends(get_db)):
    try:
        card = service.export_card(db, user.id, card_id)
    except service.KnowledgeNotFound as exc:
        _error(exc)
    if format == "json":
        body = json.dumps(card, ensure_ascii=False, indent=2, default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value))
        return Response(body, media_type="application/json; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="knowledge-{card_id}.json"'})
    if format != "markdown":
        raise HTTPException(422, detail={"code": "INVALID_EXPORT_FORMAT", "message": "format must be markdown or json"})
    sources = "\n".join(f"- [{item['title'] or item['url']}]({item['url']}) — {item['fetch_status']} ({item['evidence_kind']})" for item in card["sources"] if item.get("url")) or "- 无外部来源"
    links = ", ".join(str(item) for item in card["task_ids"]) or "无"
    body = f"# {card['title']}\n\n- ID: {card['id']}\n- 领域: {card['domain']}\n- 类型: {card['knowledge_type']}\n- 审核: {card['review_status']}\n- 标签: {', '.join(card['tags']) or '无'}\n- 任务 ID: {links}\n\n## 摘要\n\n{card['summary'] or '无'}\n\n## 内容\n\n{card['content_markdown']}\n\n## 来源与证据状态\n\n{sources}\n"
    return Response(body, media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="knowledge-{card_id}.md"'})


@router.post("/retrieve")
def retrieve_knowledge(payload: KnowledgeRetrieveInput, user=Depends(require_csrf), db: Session = Depends(get_db)):
    try:
        items = service.retrieve_cards(db, user.id, payload.query, domains=payload.domains, top_k=payload.top_k)
    except ValueError as exc:
        _error(exc)
    return {"status": "completed" if items else "empty", "query": payload.query, "items": items}


@router.get("/{card_id}")
def get_knowledge(card_id: int, include_deleted: bool = False, user=Depends(current_user), db: Session = Depends(get_db)):
    try:
        card = service.get_card(db, user.id, card_id, include_deleted=include_deleted)
        return service.card_out(db, card, user.id)
    except service.KnowledgeNotFound as exc:
        _error(exc)


@router.patch("/{card_id}")
def update_knowledge(card_id: int, payload: KnowledgePatch, user=Depends(require_csrf), db: Session = Depends(get_db)):
    try:
        card = service.update_card(db, user.id, card_id, payload)
        return service.card_out(db, card, user.id)
    except (service.KnowledgeNotFound, ValueError) as exc:
        _error(exc)


@router.delete("/{card_id}")
def trash_knowledge(card_id: int, user=Depends(require_csrf), db: Session = Depends(get_db)):
    try:
        card = service.trash_card(db, user.id, card_id)
        return {"id": card.id, "deleted": True, "recoverable": True}
    except service.KnowledgeNotFound as exc:
        _error(exc)


@router.post("/{card_id}/restore")
def restore_knowledge(card_id: int, user=Depends(require_csrf), db: Session = Depends(get_db)):
    try:
        card = service.restore_card(db, user.id, card_id)
        return service.card_out(db, card, user.id)
    except service.KnowledgeNotFound as exc:
        _error(exc)


@router.post("/{card_id}/tasks/{task_id}")
def add_task_link(card_id: int, task_id: int, user=Depends(require_csrf), db: Session = Depends(get_db)):
    try:
        link, created = service.link_task(db, user.id, card_id, task_id)
        reference = knowledge_task_reference(db, user.id, task_id)
        return {"id": link.id, "knowledge_id": card_id, "task": reference, "created": created}
    except (service.KnowledgeNotFound, LookupError) as exc:
        _error(service.KnowledgeNotFound() if isinstance(exc, LookupError) else exc)


@router.delete("/{card_id}/tasks/{task_id}")
def remove_task_link(card_id: int, task_id: int, user=Depends(require_csrf), db: Session = Depends(get_db)):
    try:
        service.unlink_task(db, user.id, card_id, task_id)
        return {"ok": True}
    except service.KnowledgeNotFound as exc:
        _error(exc)
