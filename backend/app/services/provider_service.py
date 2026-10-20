from __future__ import annotations

from dataclasses import dataclass

import httpx
from cryptography.fernet import Fernet, InvalidToken

from ..config import settings
from ..models import AIProvider


DEFAULT_PROVIDERS = {
    "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-flash"},
    "glm": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash"},
    "custom": {"base_url": "", "model": ""},
}


class CredentialUnavailable(RuntimeError):
    pass


class ProviderCredentialRejected(CredentialUnavailable):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _fernet() -> Fernet:
    key = settings.credential_master_key
    if not key:
        raise CredentialUnavailable("Credential master key is not configured")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise CredentialUnavailable("Credential master key is invalid") from exc


def encrypt_api_key(api_key: str) -> str:
    return _fernet().encrypt(api_key.encode()).decode()


def decrypt_api_key(provider: AIProvider) -> str:
    if not provider.encrypted_api_key:
        return ""
    try:
        return _fernet().decrypt(provider.encrypted_api_key.encode()).decode()
    except InvalidToken as exc:
        raise CredentialUnavailable("Stored provider credential cannot be decrypted") from exc


def credential_source(provider: AIProvider) -> str:
    return "database" if provider.encrypted_api_key else "none"


def provider_view(provider: AIProvider) -> dict:
    configured = bool(provider.encrypted_api_key)
    return {
        "provider": provider.provider,
        "base_url": provider.base_url,
        "model": provider.model,
        "enabled": provider.enabled,
        "configured": configured,
        "credential_source": credential_source(provider),
        "key_hint": "stored" if provider.encrypted_api_key else None,
        "status": provider.status if configured else "not_configured",
        "last_error": provider.last_error,
        "total_prompt_tokens": provider.total_prompt_tokens,
        "total_completion_tokens": provider.total_completion_tokens,
    }


def require_user_api_key(provider: AIProvider, user_id: int) -> str:
    """Return only an enabled credential owned by the trusted caller identity."""
    if provider.user_id != user_id:
        raise ProviderCredentialRejected("PROVIDER_CREDENTIAL_NOT_OWNED", "Provider credential is not owned by the authenticated user")
    if not provider.encrypted_api_key:
        raise ProviderCredentialRejected("PROVIDER_NOT_CONFIGURED", "请先在设置中配置并启用你自己的 Provider API Key，再使用 AI 功能。")
    if not provider.enabled:
        raise ProviderCredentialRejected("PROVIDER_DISABLED", "你的 Provider 已禁用，请在设置中重新启用。")
    return decrypt_api_key(provider)


def ensure_provider(db, provider_name: str, user_id: int) -> AIProvider:
    if provider_name not in DEFAULT_PROVIDERS:
        raise ValueError("Unsupported provider")
    provider = db.query(AIProvider).filter(AIProvider.user_id == user_id, AIProvider.provider == provider_name).one_or_none()
    if provider is None:
        defaults = DEFAULT_PROVIDERS[provider_name]
        provider = AIProvider(user_id=user_id, provider=provider_name, **defaults)
        db.add(provider)
        db.flush()
    return provider


@dataclass
class ConnectionResult:
    ok: bool
    status: str
    detail: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


async def test_connection(provider: AIProvider, api_key: str) -> ConnectionResult:
    if not api_key:
        return ConnectionResult(False, "not_configured", "No API key configured")
    if not provider.base_url:
        return ConnectionResult(False, "invalid_config", "Provider base URL is required")
    try:
        async with httpx.AsyncClient(base_url=provider.base_url.rstrip("/"), timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            # Validate both the credential and selected model using a one-token
            # request. This check is explicitly user-triggered from Settings.
            response = await client.post(
                "/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": provider.model,
                    "messages": [{"role": "user", "content": "Reply with OK."}],
                    "max_tokens": 1,
                    "stream": False,
                },
            )
            response.raise_for_status()
            result = response.json()
            usage = result.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        return ConnectionResult(
            True,
            "connected",
            "Provider connection succeeded",
            prompt_tokens if isinstance(prompt_tokens, int) and not isinstance(prompt_tokens, bool) and prompt_tokens >= 0 else None,
            completion_tokens if isinstance(completion_tokens, int) and not isinstance(completion_tokens, bool) and completion_tokens >= 0 else None,
        )
    except httpx.TimeoutException:
        return ConnectionResult(False, "timeout", "Provider request timed out")
    except httpx.HTTPStatusError as exc:
        try:
            payload = exc.response.json()
            error = payload.get("error") or payload
            code = str(error.get("code") or "").lower() if isinstance(error, dict) else ""
            message = str(error.get("message") or "").lower() if isinstance(error, dict) else ""
        except (ValueError, TypeError):
            code, message = "", ""
        if exc.response.status_code in (401, 403):
            return ConnectionResult(False, "auth_failed", "API key was rejected (HTTP 401/403)")
        if exc.response.status_code == 402 or "balance" in code or "balance" in message or "quota" in code or "insufficient" in message:
            return ConnectionResult(False, "insufficient_balance", "Provider reports insufficient balance or quota")
        if exc.response.status_code == 404:
            return ConnectionResult(False, "model_not_found", "Selected model or API endpoint was not found (HTTP 404)")
        if exc.response.status_code == 429:
            return ConnectionResult(False, "rate_limited", "Provider rate limit reached (HTTP 429)")
        if "model" in code and ("not_found" in code or "not found" in message):
            return ConnectionResult(False, "model_not_found", "Selected model was not found")
        return ConnectionResult(False, "failed", f"Provider returned HTTP {exc.response.status_code}")
    except httpx.HTTPError:
        return ConnectionResult(False, "failed", "Provider connection failed")
