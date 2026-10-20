from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from ..models import (
    AgentRun,
    AgentToolCall,
    ChatConversation,
    ChatMessage,
    KnowledgeAuditEvent,
    KnowledgeCard,
    KnowledgeCardTag,
    KnowledgeSourceReference,
    KnowledgeTag,
    KnowledgeTaskLink,
)
from ..schemas.knowledge import KnowledgeCreate, KnowledgePatch
from . import task_manager


DOMAINS = {"general", "bilingual", "global", "health", "stem", "projects", "finance", "reviews"}
KNOWLEDGE_TYPES = {"Concept", "Procedure", "Practice", "Question", "Insight"}
REVIEW_STATUSES = {"DRAFT", "REVIEWED"}
EVIDENCE_KINDS = {"USER_NOTE", "MODEL_GENERATED", "SEARCH_SNIPPET", "SOURCE_FETCHED", "USER_VERIFIED", "PRACTICE_VERIFIED"}


class KnowledgeNotFound(LookupError):
    pass


class KnowledgeConflict(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalized_tags(tags: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        name = " ".join(raw.strip().split())[:40]
        key = name.casefold()
        if name and key not in seen:
            cleaned.append(name)
            seen.add(key)
    if len(cleaned) > 20:
        raise ValueError("At most 20 unique tags are allowed")
    return cleaned


def _audit(db: Session, user_id: int, card_id: int, event_type: str) -> None:
    db.add(KnowledgeAuditEvent(user_id=user_id, card_id=card_id, event_type=event_type))


def _replace_tags(db: Session, user_id: int, card_id: int, values: list[str]) -> None:
    names = _normalized_tags(values)
    db.execute(delete(KnowledgeCardTag).where(KnowledgeCardTag.card_id == card_id))
    for name in names:
        tag = db.scalar(select(KnowledgeTag).where(KnowledgeTag.user_id == user_id, func.lower(KnowledgeTag.name) == name.casefold()))
        if tag is None:
            tag = KnowledgeTag(user_id=user_id, name=name)
            db.add(tag)
            db.flush()
        db.add(KnowledgeCardTag(card_id=card_id, tag_id=tag.id))


def _owned_card(db: Session, user_id: int, card_id: int, *, include_deleted: bool = False) -> KnowledgeCard:
    card = db.scalar(select(KnowledgeCard).where(KnowledgeCard.id == card_id, KnowledgeCard.user_id == user_id))
    if card is None or card.purged_at is not None:
        raise KnowledgeNotFound
    if card.deleted_at is not None and not include_deleted:
        raise KnowledgeNotFound
    return card


def get_card(db: Session, user_id: int, card_id: int, *, include_deleted: bool = False) -> KnowledgeCard:
    return _owned_card(db, user_id, card_id, include_deleted=include_deleted)


def _owned_message(db: Session, user_id: int, conversation_id: str | None, message_id: int | None):
    if message_id is None:
        if conversation_id is not None:
            raise ValueError("conversation_id requires an owned assistant message")
        return None
    if not conversation_id:
        raise ValueError("message_id requires conversation_id")
    conversation = db.scalar(select(ChatConversation).where(ChatConversation.id == conversation_id, ChatConversation.user_id == user_id))
    message = db.scalar(select(ChatMessage).where(ChatMessage.id == message_id, ChatMessage.user_id == user_id, ChatMessage.conversation_id == conversation_id, ChatMessage.role == "assistant", ChatMessage.status == "completed"))
    if conversation is None or conversation.deleted_at is not None or message is None:
        raise KnowledgeNotFound
    run = db.scalar(select(AgentRun).where(AgentRun.id == message.run_id, AgentRun.user_id == user_id)) if message.run_id else None
    return conversation, message, run


def _source_evidence(db: Session, user_id: int, run_id: int | None) -> list[dict]:
    if run_id is None:
        return []
    calls = db.scalars(select(AgentToolCall).where(AgentToolCall.run_id == run_id, AgentToolCall.user_id == user_id).order_by(AgentToolCall.id)).all()
    sources: dict[str, dict] = {}
    for call in calls:
        try:
            result = json.loads(call.result_summary or "{}")
        except (TypeError, ValueError):
            result = {}
        if not isinstance(result, dict):
            continue
        if call.tool_name == "web_search":
            for item in result.get("sources", []) if isinstance(result.get("sources"), list) else []:
                url = item.get("url") if isinstance(item, dict) else None
                if isinstance(url, str) and _safe_https(url):
                    sources.setdefault(url, {
                        "url": url,
                        "title": str(item.get("title") or "")[:300] or None,
                        "retrieved_at": item.get("retrieved_at"),
                        "fetch_status": "search_summary_only",
                        "evidence_kind": "SEARCH_SNIPPET",
                    })
        elif call.tool_name == "web_fetch" and call.status in {"completed", "success"}:
            url = result.get("url")
            if isinstance(url, str) and _safe_https(url):
                sources[url] = {
                    "url": url,
                    "title": str(result.get("title") or "")[:300] or None,
                    "retrieved_at": result.get("retrieved_at"),
                    "fetch_status": "fetched_body",
                    "evidence_kind": "SOURCE_FETCHED",
                }
    for call in calls:
        if call.tool_name != "web_fetch" or call.status in {"completed", "success"}:
            continue
        try:
            args = json.loads(call.arguments_summary or "{}")
        except (TypeError, ValueError):
            args = {}
        attempted_hash = args.get("url_sha256") if isinstance(args, dict) else None
        for url, source in sources.items():
            if attempted_hash and attempted_hash == hashlib.sha256(url.encode("utf-8")).hexdigest():
                source["fetch_status"] = "search_summary_only_fetch_failed"
                source["fetch_error_code"] = call.error_code or call.status
    return list(sources.values())


def _safe_https(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password


def _source_out(source: KnowledgeSourceReference) -> dict:
    return {
        "id": source.id,
        "evidence_kind": source.evidence_kind,
        "conversation_id": source.conversation_id,
        "message_id": source.message_id,
        "agent_run_id": source.agent_run_id,
        "url": source.source_url,
        "title": source.source_title,
        "fetch_status": source.fetch_status,
        "retrieved_at": source.retrieved_at,
    }


def card_out(db: Session, card: KnowledgeCard, user_id: int) -> dict:
    tags = db.execute(select(KnowledgeTag.name).join(KnowledgeCardTag, KnowledgeCardTag.tag_id == KnowledgeTag.id).where(KnowledgeCardTag.card_id == card.id, KnowledgeTag.user_id == user_id).order_by(KnowledgeTag.name)).scalars().all()
    sources = db.scalars(select(KnowledgeSourceReference).where(KnowledgeSourceReference.card_id == card.id, KnowledgeSourceReference.user_id == user_id).order_by(KnowledgeSourceReference.id)).all()
    task_ids = db.scalars(select(KnowledgeTaskLink.task_id).where(KnowledgeTaskLink.card_id == card.id, KnowledgeTaskLink.user_id == user_id).order_by(KnowledgeTaskLink.id)).all()
    history = db.execute(select(KnowledgeAuditEvent.event_type, KnowledgeAuditEvent.created_at).where(KnowledgeAuditEvent.card_id == card.id, KnowledgeAuditEvent.user_id == user_id).order_by(KnowledgeAuditEvent.id)).all()
    return {
        "id": card.id,
        "title": card.title,
        "summary": card.summary,
        "content_markdown": card.content_markdown,
        "domain": card.domain,
        "knowledge_type": card.knowledge_type,
        "review_status": card.review_status,
        "is_pinned": card.is_pinned,
        "archived": card.archived_at is not None,
        "deleted": card.deleted_at is not None,
        "purged": card.purged_at is not None,
        "created_at": card.created_at,
        "updated_at": card.updated_at,
        "tags": tags,
        "sources": [_source_out(source) for source in sources],
        "task_ids": task_ids,
        "history": [{"event": event, "created_at": created_at} for event, created_at in history],
        "origin_note": "无外部来源" if not any(source.source_url for source in sources) else None,
    }


def _add_source_refs(db: Session, user_id: int, card_id: int, domain: str, origin) -> None:
    if origin is None:
        db.add(KnowledgeSourceReference(card_id=card_id, user_id=user_id, evidence_kind="USER_NOTE", fetch_status="no_external_source"))
        return
    conversation, message, run = origin
    db.add(KnowledgeSourceReference(card_id=card_id, user_id=user_id, evidence_kind="MODEL_GENERATED", conversation_id=conversation.id, message_id=message.id, agent_run_id=run.id if run else None, fetch_status="model_message"))
    for item in _source_evidence(db, user_id, run.id if run else None):
        db.add(KnowledgeSourceReference(card_id=card_id, user_id=user_id, evidence_kind=item["evidence_kind"], conversation_id=conversation.id, message_id=message.id, agent_run_id=run.id if run else None, source_url=item["url"], source_title=item["title"], fetch_status=item["fetch_status"], retrieved_at=item["retrieved_at"]))


def create_card(db: Session, user_id: int, payload: KnowledgeCreate) -> KnowledgeCard:
    origin = _owned_message(db, user_id, payload.conversation_id, payload.message_id)
    card = KnowledgeCard(user_id=user_id, title=payload.title, summary=payload.summary, content_markdown=payload.content_markdown, domain=payload.domain, knowledge_type=payload.knowledge_type, review_status=payload.review_status)
    db.add(card)
    db.flush()
    _replace_tags(db, user_id, card.id, payload.tags)
    _add_source_refs(db, user_id, card.id, payload.domain, origin)
    _audit(db, user_id, card.id, "created")
    db.commit()
    db.refresh(card)
    return card


def list_cards(db: Session, user_id: int, *, q: str | None = None, domain: str | None = None, knowledge_type: str | None = None, review_status: str | None = None, tag: str | None = None, task_id: int | None = None, archived: bool = False, deleted: bool = False, limit: int = 100) -> list[KnowledgeCard]:
    conditions = [KnowledgeCard.user_id == user_id, KnowledgeCard.purged_at.is_(None)]
    conditions.append(KnowledgeCard.deleted_at.is_not(None) if deleted else KnowledgeCard.deleted_at.is_(None))
    if not deleted:
        conditions.append(KnowledgeCard.archived_at.is_not(None) if archived else KnowledgeCard.archived_at.is_(None))
    if q and q.strip():
        needle = f"%{q.strip()[:120]}%"
        conditions.append(or_(KnowledgeCard.title.ilike(needle), KnowledgeCard.summary.ilike(needle), KnowledgeCard.content_markdown.ilike(needle)))
    if domain:
        if domain not in DOMAINS: raise ValueError("KNOWLEDGE_DOMAIN_INVALID")
        conditions.append(KnowledgeCard.domain == domain)
    if knowledge_type:
        if knowledge_type not in KNOWLEDGE_TYPES: raise ValueError("KNOWLEDGE_TYPE_INVALID")
        conditions.append(KnowledgeCard.knowledge_type == knowledge_type)
    if review_status:
        if review_status not in REVIEW_STATUSES: raise ValueError("KNOWLEDGE_REVIEW_STATUS_INVALID")
        conditions.append(KnowledgeCard.review_status == review_status)
    if tag:
        conditions.append(KnowledgeCard.id.in_(select(KnowledgeCardTag.card_id).join(KnowledgeTag, KnowledgeTag.id == KnowledgeCardTag.tag_id).where(KnowledgeTag.user_id == user_id, func.lower(KnowledgeTag.name) == tag.strip().casefold())))
    if task_id is not None:
        conditions.append(KnowledgeCard.id.in_(select(KnowledgeTaskLink.card_id).where(KnowledgeTaskLink.user_id == user_id, KnowledgeTaskLink.task_id == task_id)))
    return db.scalars(select(KnowledgeCard).where(*conditions).order_by(KnowledgeCard.is_pinned.desc(), KnowledgeCard.updated_at.desc(), KnowledgeCard.id.desc()).limit(max(1, min(limit, 100)))).all()


def retrieve_cards(db: Session, user_id: int, query: str, *, domains: list[str] | None = None, top_k: int = 5) -> list[dict]:
    """Return bounded, explainably ranked excerpts from active cards owned by this user."""
    normalized = query.casefold()
    latin_terms = re.findall(r"[a-z0-9_]{2,}", normalized)
    cjk_terms = [
        run[index:index + 2]
        for run in re.findall(r"[\u3400-\u9fff]+", normalized)
        for index in range(max(0, len(run) - 1))
    ]
    terms = list(dict.fromkeys([*latin_terms, *cjk_terms]))[:24]
    if not terms:
        return []
    conditions = [KnowledgeCard.user_id == user_id, KnowledgeCard.deleted_at.is_(None),
                  KnowledgeCard.purged_at.is_(None), KnowledgeCard.archived_at.is_(None)]
    if domains:
        if any(domain not in DOMAINS for domain in domains):
            raise ValueError("KNOWLEDGE_DOMAIN_INVALID")
        conditions.append(KnowledgeCard.domain.in_(set(domains)))
    cards = db.scalars(select(KnowledgeCard).where(*conditions).order_by(KnowledgeCard.is_pinned.desc(), KnowledgeCard.updated_at.desc(), KnowledgeCard.id.desc()).limit(500)).all()
    ranked = []
    for card in cards:
        tags = db.execute(select(KnowledgeTag.name).join(KnowledgeCardTag, KnowledgeCardTag.tag_id == KnowledgeTag.id).where(KnowledgeCardTag.card_id == card.id, KnowledgeTag.user_id == user_id)).scalars().all()
        title = card.title.casefold()
        summary = (card.summary or "").casefold()
        content = card.content_markdown.casefold()
        tag_text = " ".join(tags).casefold()
        score = 0
        matched = []
        for term in terms:
            weight = (6 if term in title else 0) + (4 if term in summary else 0) + (4 if term in tag_text else 0) + (1 if term in content else 0)
            if weight:
                score += weight
                matched.append(term)
        if not score:
            continue
        content_pos = next((content.find(term) for term in matched if content.find(term) >= 0), -1)
        if content_pos >= 0:
            start = max(0, content_pos - 260)
            end = min(len(card.content_markdown), start + 640)
            excerpt = ("…" if start else "") + card.content_markdown[start:end] + ("…" if end < len(card.content_markdown) else "")
        else:
            excerpt = (card.summary or card.content_markdown)[:640]
        dto = card_out(db, card, user_id)
        ranked.append({"id": card.id, "title": card.title, "summary": card.summary, "domain": card.domain,
                       "tags": tags, "score": score, "matched_terms": matched, "excerpt": excerpt,
                       "sources": dto["sources"]})
    ranked.sort(key=lambda item: (-item["score"], -int(next((c.is_pinned for c in cards if c.id == item["id"]), False)), item["id"]))
    return ranked[:max(1, min(top_k, 5))]


def get_active_cards(db: Session, user_id: int, card_ids: list[int]) -> list[dict]:
    """Resolve explicit knowledge IDs, enforcing ownership and active-use policy."""
    if not card_ids or len(card_ids) > 5 or len(set(card_ids)) != len(card_ids):
        raise ValueError("KNOWLEDGE_SOURCE_IDS_INVALID")
    cards = db.scalars(select(KnowledgeCard).where(KnowledgeCard.id.in_(card_ids), KnowledgeCard.user_id == user_id,
        KnowledgeCard.deleted_at.is_(None), KnowledgeCard.purged_at.is_(None), KnowledgeCard.archived_at.is_(None))).all()
    by_id = {card.id: card for card in cards}
    if set(by_id) != set(card_ids):
        raise KnowledgeNotFound
    results = []
    for card_id in card_ids:
        card = by_id[card_id]
        dto = card_out(db, card, user_id)
        results.append({"id": card.id, "title": card.title, "summary": card.summary, "domain": card.domain,
                        "excerpt": card.content_markdown[:640], "sources": dto["sources"]})
    return results


def link_tasks_to_cards(db: Session, user_id: int, card_ids: list[int], task_ids: list[int]) -> None:
    """Stage approved task-source links in the caller's transaction."""
    if len(card_ids) > 5 or len(task_ids) > 10:
        raise ValueError("KNOWLEDGE_LINK_LIMIT_EXCEEDED")
    for card_id in card_ids:
        _owned_card(db, user_id, card_id)
    task_refs = {int(item["id"]) for item in (task_manager.knowledge_task_reference(db, user_id, task_id) for task_id in task_ids)}
    if task_refs != set(task_ids):
        raise KnowledgeNotFound
    for card_id in card_ids:
        for task_id in task_ids:
            exists = db.scalar(select(KnowledgeTaskLink.id).where(KnowledgeTaskLink.user_id == user_id, KnowledgeTaskLink.card_id == card_id, KnowledgeTaskLink.task_id == task_id))
            if exists is None:
                db.add(KnowledgeTaskLink(user_id=user_id, card_id=card_id, task_id=task_id))
                _audit(db, user_id, card_id, "task_linked")
    db.flush()


def update_card(db: Session, user_id: int, card_id: int, payload: KnowledgePatch) -> KnowledgeCard:
    card = _owned_card(db, user_id, card_id)
    values = payload.model_dump(exclude_unset=True)
    tags = values.pop("tags", None)
    archived = values.pop("archived", None)
    for key, value in values.items():
        if value is not None or key == "summary":
            setattr(card, key, value)
    if archived is not None:
        card.archived_at = _now() if archived else None
    if tags is not None:
        _replace_tags(db, user_id, card.id, tags)
    card.updated_at = _now()
    _audit(db, user_id, card.id, "updated")
    db.commit()
    db.refresh(card)
    return card


def trash_card(db: Session, user_id: int, card_id: int) -> KnowledgeCard:
    card = _owned_card(db, user_id, card_id, include_deleted=True)
    if card.deleted_at is None:
        card.deleted_at = _now()
        _audit(db, user_id, card.id, "trashed")
        db.commit()
    return card


def restore_card(db: Session, user_id: int, card_id: int) -> KnowledgeCard:
    card = _owned_card(db, user_id, card_id, include_deleted=True)
    if card.deleted_at is None:
        return card
    card.deleted_at = None
    card.updated_at = _now()
    _audit(db, user_id, card.id, "restored")
    db.commit()
    return card


def link_task(db: Session, user_id: int, card_id: int, task_id: int) -> tuple[KnowledgeTaskLink, bool]:
    card = _owned_card(db, user_id, card_id)
    try:
        reference = task_manager.knowledge_task_reference(db, user_id, task_id)
    except LookupError as exc:
        raise KnowledgeNotFound from exc
    link = db.scalar(select(KnowledgeTaskLink).where(KnowledgeTaskLink.user_id == user_id, KnowledgeTaskLink.card_id == card.id, KnowledgeTaskLink.task_id == reference["id"]))
    if link:
        return link, False
    link = KnowledgeTaskLink(user_id=user_id, card_id=card.id, task_id=reference["id"])
    db.add(link)
    _audit(db, user_id, card.id, "task_linked")
    db.commit()
    db.refresh(link)
    return link, True


def unlink_task(db: Session, user_id: int, card_id: int, task_id: int) -> None:
    _owned_card(db, user_id, card_id)
    link = db.scalar(select(KnowledgeTaskLink).where(KnowledgeTaskLink.user_id == user_id, KnowledgeTaskLink.card_id == card_id, KnowledgeTaskLink.task_id == task_id))
    if link is None:
        raise KnowledgeNotFound
    db.delete(link)
    _audit(db, user_id, card_id, "task_unlinked")
    db.commit()


def purge_preview(db: Session, user_id: int, card_id: int) -> dict:
    card = _owned_card(db, user_id, card_id, include_deleted=True)
    links = db.scalar(select(func.count(KnowledgeTaskLink.id)).where(KnowledgeTaskLink.user_id == user_id, KnowledgeTaskLink.card_id == card.id)) or 0
    sources = db.scalar(select(func.count(KnowledgeSourceReference.id)).where(KnowledgeSourceReference.user_id == user_id, KnowledgeSourceReference.card_id == card.id)) or 0
    return {"card_id": card.id, "title": card.title, "can_purge": card.deleted_at is not None, "task_links_to_detach": links, "source_references_to_clear": sources, "notice": "仅清空当前知识卡片正文、标签及本卡任务/来源关联；原任务、会话、消息、Agent 审计和历史备份不删除。历史备份可能仍保留清理前内容。"}


def purge_card(db: Session, user_id: int, card_id: int, confirm_title: str) -> dict:
    card = _owned_card(db, user_id, card_id, include_deleted=True)
    if card.deleted_at is None:
        raise KnowledgeConflict("KNOWLEDGE_MUST_BE_TRASHED")
    if confirm_title != card.title:
        raise KnowledgeConflict("CONFIRM_TITLE_MISMATCH")
    if card.purged_at is not None:
        raise KnowledgeNotFound
    db.execute(delete(KnowledgeCardTag).where(KnowledgeCardTag.card_id == card.id))
    db.execute(delete(KnowledgeTag).where(KnowledgeTag.user_id == user_id, ~KnowledgeTag.id.in_(select(KnowledgeCardTag.tag_id))))
    db.execute(delete(KnowledgeSourceReference).where(KnowledgeSourceReference.card_id == card.id, KnowledgeSourceReference.user_id == user_id))
    db.execute(delete(KnowledgeTaskLink).where(KnowledgeTaskLink.card_id == card.id, KnowledgeTaskLink.user_id == user_id))
    card.title = "[已永久清理]"
    card.summary = None
    card.content_markdown = ""
    card.domain = "general"
    card.knowledge_type = "Question"
    card.review_status = "DRAFT"
    card.is_pinned = False
    card.archived_at = None
    card.purged_at = _now()
    card.updated_at = card.purged_at
    _audit(db, user_id, card.id, "purged")
    db.commit()
    return {"card_id": card.id, "purged": True, "tombstone_retained": True, "notice": "知识正文及本卡标签/关联已从当前数据库清除；历史备份可能仍保留旧内容。"}


def export_card(db: Session, user_id: int, card_id: int) -> dict:
    card = _owned_card(db, user_id, card_id, include_deleted=True)
    if card.deleted_at is not None:
        raise KnowledgeNotFound
    result = card_out(db, card, user_id)
    result["export_scope"] = "this_knowledge_card_and_its_source_references_and_task_ids_only"
    return result
