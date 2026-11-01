"""LLM client used by the text driver (OpenAI chat completions with tools).

The client is injectable: :func:`get_llm_factory` returns the factory used by the API routers
(``factory(config: BotConfig) -> LLMClient``); tests override it (``set_llm_factory`` or a
FastAPI dependency override) with a deterministic fake, so no API key is needed.

Settings mirror :func:`app.voice.providers.build_llm` (gpt-6* needs ``reasoning_effort="none"``
+ ``max_completion_tokens`` for function tools; other reasoning models use
``max_completion_tokens``), but the replay always runs at temperature 0 with a fixed ``seed``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from app.schemas.providers import BotConfig, LLMConfig
from app.voice.providers import _is_reasoning_model, _tools_need_no_reasoning


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResult:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None


class LLMClient(Protocol):
    async def complete(self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                       settings: dict[str, Any]) -> LLMResult: ...


def request_params(cfg: LLMConfig, seed: int | None) -> dict[str, Any]:
    """OpenAI chat.completions kwargs (model + sampling) for a deterministic replay."""
    opts = dict(cfg.options or {})
    model = cfg.model or "gpt-4.1-mini"
    p: dict[str, Any] = {"model": model}
    if _tools_need_no_reasoning(model):
        effort = opts.get("reasoning_effort", "none")
        p["reasoning_effort"] = effort
        if effort == "none":
            p["temperature"] = 0
        if cfg.max_tokens:
            p["max_completion_tokens"] = cfg.max_tokens if effort == "none" else max(cfg.max_tokens, 1024)
    elif _is_reasoning_model(model):
        if cfg.max_tokens:
            p["max_completion_tokens"] = max(cfg.max_tokens, 1024)
    else:
        p["temperature"] = 0
        if cfg.max_tokens:
            p["max_tokens"] = cfg.max_tokens
    if seed is not None:
        p["seed"] = int(seed)
    return p


def trace_settings(cfg: LLMConfig, seed: int | None) -> dict[str, Any]:
    p = request_params(cfg, seed)
    return {
        "provider": cfg.provider,
        "model": p["model"],
        "temperature": p.get("temperature", 0),
        "max_tokens": p.get("max_tokens") or p.get("max_completion_tokens"),
        "seed": seed,
        **({"reasoning_effort": p["reasoning_effort"]} if "reasoning_effort" in p else {}),
    }


class OpenAIChatClient:
    """Real OpenAI client (``openai`` SDK). Requires OPENAI_API_KEY (or llm.options.api_key)."""

    def __init__(self, config: BotConfig, api_key: str | None = None):
        from openai import AsyncOpenAI

        cfg = config.llm
        key = api_key or (cfg.options or {}).get("api_key")
        if not key:
            from app.voice.providers import ProviderKeys

            key = ProviderKeys.from_settings().get("openai")
        if not key:
            from app.voice.providers import MissingProviderKey

            raise MissingProviderKey([{"role": "llm", "provider": "openai", "env_key": "OPENAI_API_KEY"}])
        kwargs: dict[str, Any] = {"api_key": key}
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        self._client = AsyncOpenAI(**kwargs)

    async def complete(self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                       settings: dict[str, Any]) -> LLMResult:
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        params = dict(settings)
        if tools:
            params["tools"] = tools
        resp = await self._client.chat.completions.create(messages=msgs, **params)
        msg = resp.choices[0].message
        calls = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args if isinstance(args, dict) else {}))
        return LLMResult(text=msg.content or "", tool_calls=calls, raw=None)


LLMFactory = Callable[[BotConfig], LLMClient]


def default_llm_factory(config: BotConfig) -> LLMClient:
    return OpenAIChatClient(config)


_factory: LLMFactory = default_llm_factory


def set_llm_factory(factory: LLMFactory | None) -> None:
    global _factory
    _factory = factory or default_llm_factory


def get_llm_factory() -> LLMFactory:
    """FastAPI dependency (overridable in tests)."""
    return _factory
