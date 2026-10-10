"""Language-model providers (ADR-11, spec §10.4).

One OpenAI-compatible adapter covers the DeepSeek API and local servers (Ollama, vLLM, llama.cpp). It speaks
plain HTTP through httpx, retries transient failures once, never logs or returns the API key, and turns every
failure into a short, user-safe `AIProviderError`. `DisabledProvider` keeps the rest of the platform working
without a key.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from tripscope.core.errors import AIProviderError, AIUnavailableError
from tripscope.core.settings import Settings

log = logging.getLogger(__name__)

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
LOCAL_HOSTNAMES = frozenset({"localhost", "host.docker.internal"})


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text exactly as the model produced it; validated by the tool registry


@dataclass(frozen=True)
class ChatResult:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    latency_ms: float = 0.0


class LLMProvider(Protocol):
    name: str
    model: str
    supports_tools: bool
    local: bool
    enabled: bool
    reason: str | None

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
        max_tokens: int | None = None,
    ) -> ChatResult: ...


class DisabledProvider:
    name, model, supports_tools, local, enabled = "disabled", "", False, True, False

    def __init__(self, reason: str) -> None:
        self.reason: str | None = reason

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
        max_tokens: int | None = None,
    ) -> ChatResult:
        raise AIUnavailableError(self.reason or "the AI analyst is not configured")


def is_local_url(url: str) -> bool:
    """True when the URL's host is loopback or on a private network (resolved, every address must qualify)."""
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    if host in LOCAL_HOSTNAMES:
        return True
    try:
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            addresses = {ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, None)}
        except OSError:
            return False
    return bool(addresses) and all(a.is_loopback or a.is_private for a in addresses)


class OpenAICompatibleProvider:
    name = "openai_compatible"
    enabled = True
    reason: str | None = None

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 120.0,
        reasoning_effort: str = "",
        supports_tools: bool = True,
        local: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.supports_tools = supports_tools
        self.local = local
        self._reasoning_effort = reasoning_effort
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(
            base_url=self.base_url, headers=headers, timeout=timeout, transport=transport
        )
        self._timeout = timeout

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
        max_tokens: int | None = None,
    ) -> ChatResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "stream": False,
        }
        if tools and self.supports_tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if self._reasoning_effort:
            payload["reasoning_effort"] = self._reasoning_effort
        if max_tokens:
            payload["max_tokens"] = max_tokens
        started = time.perf_counter()
        body = self._post(payload)
        latency = round((time.perf_counter() - started) * 1000, 1)
        try:
            message = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProviderError("the language model returned an unexpected response") from exc
        content = _THINK.sub("", message.get("content") or "").strip()
        calls = [
            ToolCall(
                id=str(call.get("id") or f"call_{i}"),
                name=str(call.get("function", {}).get("name", "")),
                arguments=_arguments(call.get("function", {}).get("arguments")),
            )
            for i, call in enumerate(message.get("tool_calls") or [])
        ]
        usage = {k: int(v) for k, v in (body.get("usage") or {}).items() if isinstance(v, int)}
        log.info(
            "llm call",
            extra={"model": self.model, "ms": latency, "tool_calls": len(calls), **usage},
        )
        return ChatResult(content=content, tool_calls=calls, usage=usage, latency_ms=latency)

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        for attempt in (1, 2):
            try:
                response = self._client.post("/chat/completions", json=payload)
            except httpx.TimeoutException as exc:
                raise AIProviderError(
                    f"the language model did not answer within {self._timeout:.0f} s"
                ) from exc
            except httpx.HTTPError as exc:
                if attempt == 1:
                    time.sleep(1)
                    continue
                raise AIProviderError("the language model could not be reached") from exc
            if response.status_code in (429, 500, 502, 503, 504) and attempt == 1:
                time.sleep(1)
                continue
            if response.status_code in (401, 403):
                raise AIProviderError("the language-model provider rejected the API key")
            if response.status_code == 404:
                raise AIProviderError(f"the provider does not know the model {self.model!r}")
            if response.status_code >= 400:
                raise AIProviderError(f"the language-model provider returned HTTP {response.status_code}")
            try:
                data = response.json()
            except ValueError as exc:
                raise AIProviderError("the language model returned something that is not JSON") from exc
            if not isinstance(data, dict):
                raise AIProviderError("the language model returned an unexpected response")
            return data
        raise AIProviderError(
            "the language model could not be reached"
        )  # pragma: no cover - loop always returns


def _arguments(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    return json.dumps(raw if raw is not None else {})


def build_provider(settings: Settings, *, transport: httpx.BaseTransport | None = None) -> LLMProvider:
    if settings.llm_provider == "disabled":
        return DisabledProvider("The AI analyst is turned off (LLM_PROVIDER=disabled).")
    if not settings.llm_base_url or not settings.llm_model:
        return DisabledProvider("Set LLM_BASE_URL and LLM_MODEL to use the AI analyst.")
    local = is_local_url(settings.llm_base_url)
    if settings.llm_local_only and not local:
        host = urlparse(settings.llm_base_url).hostname
        return DisabledProvider(
            f"LLM_LOCAL_ONLY is on and {host} is not on a local network; no data is sent to it."
        )
    return OpenAICompatibleProvider(
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        api_key=settings.llm_api_key.get_secret_value(),
        timeout=settings.llm_timeout_seconds,
        reasoning_effort=settings.llm_reasoning_effort,
        supports_tools=settings.llm_supports_tools,
        local=local,
        transport=transport,
    )
