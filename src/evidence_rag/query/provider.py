from __future__ import annotations

import re
from dataclasses import dataclass
from threading import RLock
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field, field_validator

from ..config import Settings

PROVIDER_CONTRACT_VERSION = "llm-provider-runtime-v1"
PROVIDER_HEALTH_MAX_TOKENS = 128
PROVIDER_PLANNING_MAX_TOKENS = 1_200
PROVIDER_GENERATION_MAX_TOKENS = 1_600
_PROJECT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}")
_MODEL_ID = re.compile(r"[^\x00-\x1f\x7f]{1,200}")


class LLMProviderConfiguration(BaseModel):
    project_id: str = Field(min_length=1, max_length=160)
    provider: Literal["openai_compatible"] = "openai_compatible"
    base_url: str = Field(min_length=1, max_length=2_048)
    model: str = Field(min_length=1, max_length=200)
    api_key: str = Field(min_length=1, max_length=8_192, repr=False)

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, value: str) -> str:
        value = value.strip()
        if _PROJECT_ID.fullmatch(value) is None:
            raise ValueError("project_id must be a portable identifier")
        return value

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        value = value.strip()
        if _MODEL_ID.fullmatch(value) is None:
            raise ValueError("model must be a bounded printable identifier")
        return value

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("api_key must be a non-empty printable secret")
        return value

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return validate_provider_base_url(value)


class LLMProviderProjectRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=160)

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, value: str) -> str:
        value = value.strip()
        if _PROJECT_ID.fullmatch(value) is None:
            raise ValueError("project_id must be a portable identifier")
        return value


@dataclass(frozen=True, slots=True)
class ResolvedLLMProvider:
    provider: Literal["openai_compatible"]
    base_url: str
    model: str
    api_key: str
    source: Literal["desktop_runtime", "environment"]

    def public(self) -> dict[str, Any]:
        return {
            "contract_version": PROVIDER_CONTRACT_VERSION,
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "configured": True,
            "api_key_present": True,
            "secret_storage": (
                "os_keychain_ephemeral_backend"
                if self.source == "desktop_runtime"
                else "process_environment"
            ),
            "source": self.source,
            "capabilities": [
                "structured_query_understanding",
                "grounded_generation",
            ],
        }


def provider_chat_options(
    provider: ResolvedLLMProvider,
    *,
    purpose: Literal["health", "planning", "generation"],
) -> dict[str, Any]:
    """Return bounded vendor options without weakening generic compatibility.

    DeepSeek V4 enables thinking by default. These operations already have
    deterministic validation around them, so its documented non-thinking mode
    avoids spending the interactive deadline before emitting usable content.
    Other OpenAI-compatible providers receive only portable token budgets.
    """

    max_tokens = {
        "health": PROVIDER_HEALTH_MAX_TOKENS,
        "planning": PROVIDER_PLANNING_MAX_TOKENS,
        "generation": PROVIDER_GENERATION_MAX_TOKENS,
    }[purpose]
    options: dict[str, Any] = {"max_tokens": max_tokens}
    hostname = (urlsplit(provider.base_url).hostname or "").casefold()
    if hostname == "api.deepseek.com" and provider.model.casefold().startswith(
        "deepseek-v4-"
    ):
        options["thinking"] = {"type": "disabled"}
    return options


def validate_provider_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("provider base_url must be a canonical HTTP endpoint")
    loopback = parsed.hostname.casefold() in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not loopback:
        raise ValueError("remote provider base_url must use HTTPS")
    return value


class RuntimeLLMProviderRegistry:
    """Process-local provider secrets supplied by the native desktop application.

    The registry deliberately has no persistence adapter.  The desktop host owns the
    durable secret in the operating-system credential vault and re-registers it after
    process startup.  Public methods never return the secret.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = RLock()
        self._providers: dict[str, ResolvedLLMProvider] = {}

    def configure(self, configuration: LLMProviderConfiguration) -> dict[str, Any]:
        resolved = ResolvedLLMProvider(
            provider=configuration.provider,
            base_url=configuration.base_url,
            model=configuration.model,
            api_key=configuration.api_key,
            source="desktop_runtime",
        )
        with self._lock:
            self._providers[configuration.project_id] = resolved
        return self.status(configuration.project_id)

    def clear(self, project_id: str) -> dict[str, Any]:
        if _PROJECT_ID.fullmatch(project_id) is None:
            raise ValueError("project_id must be a portable identifier")
        with self._lock:
            self._providers.pop(project_id, None)
        return self.status(project_id)

    def resolve(self, project_id: str) -> ResolvedLLMProvider | None:
        with self._lock:
            runtime = self._providers.get(project_id)
        if runtime is not None:
            return runtime
        if self._settings.llm_base_url and self._settings.llm_api_key and self._settings.llm_model:
            return ResolvedLLMProvider(
                provider="openai_compatible",
                base_url=validate_provider_base_url(self._settings.llm_base_url),
                model=self._settings.llm_model,
                api_key=self._settings.llm_api_key,
                source="environment",
            )
        return None

    def status(self, project_id: str) -> dict[str, Any]:
        resolved = self.resolve(project_id)
        if resolved is None:
            return {
                "contract_version": PROVIDER_CONTRACT_VERSION,
                "project_id": project_id,
                "provider": "openai_compatible",
                "base_url": None,
                "model": None,
                "configured": False,
                "api_key_present": False,
                "secret_storage": "os_keychain_required",
                "source": None,
                "capabilities": [],
            }
        return {"project_id": project_id, **resolved.public()}

    def test(self, project_id: str, *, timeout_seconds: float = 15.0) -> dict[str, Any]:
        resolved = self.resolve(project_id)
        if resolved is None:
            raise ValueError("llm_provider_not_configured")
        payload = {
            "model": resolved.model,
            "temperature": 0,
            # Reasoning-capable OpenAI-compatible models may spend an initial
            # token budget on hidden reasoning before producing the tiny health
            # response.  Eight tokens caused valid DeepSeek connections to end
            # with finish_reason=length and an empty content field.
            **provider_chat_options(resolved, purpose="health"),
            "messages": [
                {
                    "role": "system",
                    "content": "Return exactly the word READY. Do not include any other text.",
                },
                {"role": "user", "content": "health check"},
            ],
        }
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.post(
                resolved.base_url + "/chat/completions",
                headers={"Authorization": f"Bearer {resolved.api_key}"},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        content = str(body.get("choices", [{}])[0].get("message", {}).get("content", ""))
        if "READY" not in content.upper():
            raise ValueError("llm_provider_health_contract_mismatch")
        return {
            **resolved.public(),
            "project_id": project_id,
            "health": "ready",
            "tested": True,
        }
