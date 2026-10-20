from __future__ import annotations

import json
import re

import httpx

from ..schemas.knowledge import KnowledgeRefinement


class RefinementError(RuntimeError):
    def __init__(self, code: str, message: str, usage: dict | None = None):
        super().__init__(message)
        self.code = code
        self.usage = usage


async def refine_text(provider, api_key: str, source_text: str) -> tuple[KnowledgeRefinement, dict | None]:
    """Make exactly one provider call; this adapter has no database/Knowledge access."""
    system = (
        "你是 Personal OS 的知识提炼器。只处理 user 提供的这一条 AI 回复或选中片段，不调用工具、不联网、不扩展聊天历史。"
        "筛选适合长期保存的概念、原理、方法和结论，删去寒暄与重复；保留必要条件、限制、关键代码。"
        "严格区分来源支持的事实、模型推断和用户判断；无法确认的内容标注不确定。原回复中提及的来源只能称为‘原回复所述来源（未独立核验）’，不得补造事实、实验、执行结果或 URL。"
        "原文中 AI 自称已执行的内容不构成执行证据。若没有独立知识价值，返回 has no content 的简短标题/摘要/正文并说明。"
        "整理为易读中文 Markdown；英语/德语学习材料保留关键原句和术语。输出 JSON 对象，字段仅为 worth_saving(bool)、title、summary、content_markdown、limitations。"
    )
    try:
        async with httpx.AsyncClient(base_url=(provider.base_url or "https://api.deepseek.com").rstrip("/"), timeout=httpx.Timeout(35.0, connect=5.0)) as client:
            response = await client.post(
                "/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": provider.model or "deepseek-chat",
                    "max_tokens": 1800,
                    "thinking": {"type": "disabled"},
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": source_text},
                    ],
                },
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.TimeoutException as exc:
        raise RefinementError("MODEL_TIMEOUT", "DeepSeek 提炼请求超时；原草稿仍保留。") from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            code, message = "UPSTREAM_AUTH_FAILED", "DeepSeek 凭证未通过验证。"
        elif status == 402:
            code, message = "INSUFFICIENT_BALANCE", "DeepSeek 账户额度不足。"
        elif status == 429:
            code, message = "RATE_LIMITED", "DeepSeek 请求频率受限，请稍后再试。"
        elif status == 404:
            code, message = "MODEL_NOT_FOUND", "当前模型或接口不存在。"
        else:
            code, message = "MODEL_REQUEST_FAILED", f"DeepSeek 返回 HTTP {status}。"
        raise RefinementError(code, message) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise RefinementError("MODEL_REQUEST_FAILED", "DeepSeek 请求失败或响应格式异常；原草稿仍保留。") from exc

    usage = payload.get("usage") if isinstance(payload, dict) else None
    usage = usage if isinstance(usage, dict) else None
    try:
        content = payload["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("content is not text")
        candidate = KnowledgeRefinement.model_validate(json.loads(content))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RefinementError("INVALID_MODEL_OUTPUT", "模型没有返回符合知识提炼结构的 JSON；原草稿仍保留。", usage) from exc
    source_urls = {item.rstrip(".,;:!?)])}") for item in re.findall(r"https?://[^\s<>\]\[()]+", source_text, re.IGNORECASE)}
    candidate_text = "\n".join((candidate.title, candidate.summary, candidate.content_markdown, *candidate.limitations))
    generated_urls = {item.rstrip(".,;:!?)])}") for item in re.findall(r"https?://[^\s<>\]\[()]+", candidate_text, re.IGNORECASE)}
    if not generated_urls.issubset(source_urls):
        raise RefinementError("UNSUPPORTED_SOURCE_URL", "模型输出包含原回复未提供的链接；候选已拒绝，原草稿保持不变。", usage)
    return candidate, usage


SECRET_PATTERNS = (
    re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|密码|密钥|令牌)\s*[:=：]\s*\S{6,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b[A-Za-z0-9_-]{32,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b"),
)


def contains_credential_material(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)
