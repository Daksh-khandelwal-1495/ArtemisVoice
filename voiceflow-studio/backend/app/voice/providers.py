"""BotConfig -> pipecat STT / LLM / TTS service instances.

Every provider is constructed with the current (settings-based) pipecat API:
``Service(api_key=..., settings=Service.Settings(...))``. Missing API keys raise
:class:`MissingProviderKey`, which the router maps to HTTP 503.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from app.schemas.providers import BotConfig, LLMConfig, STTConfig, TTSConfig

ENV_KEYS = {
    "deepgram": "DEEPGRAM_API_KEY",
    "soniox": "SONIOX_API_KEY",
    "openai": "OPENAI_API_KEY",
}


class MissingProviderKey(RuntimeError):
    """Raised when a configured provider has no API key in the environment.

    Attributes:
        missing: list of ``{"role": "stt"|"llm"|"tts", "provider": str, "env_key": str}``.
    """

    def __init__(self, missing: list[dict[str, str]]):
        self.missing = missing
        keys = sorted({m["env_key"] for m in missing})
        parts = ", ".join(f"{m['role'].upper()} provider '{m['provider']}'" for m in missing)
        super().__init__(f"Missing API key(s) {', '.join(keys)} required for {parts}. Set them in backend/.env.")

    @property
    def env_keys(self) -> list[str]:
        return sorted({m["env_key"] for m in self.missing})


@dataclass
class ProviderKeys:
    deepgram: str = ""
    soniox: str = ""
    openai: str = ""

    def get(self, provider: str) -> str:
        return (getattr(self, provider, "") or "").strip()

    @classmethod
    def from_settings(cls) -> "ProviderKeys":
        from app.core.config import settings

        return cls(
            deepgram=getattr(settings, "deepgram_api_key", "") or "",
            soniox=getattr(settings, "soniox_api_key", "") or "",
            openai=getattr(settings, "openai_api_key", "") or "",
        )


def check_keys(config: BotConfig, keys: ProviderKeys | None = None) -> None:
    """Raise MissingProviderKey listing every role whose provider key is absent."""
    keys = keys or ProviderKeys.from_settings()
    missing = []
    for role, provider in (("stt", config.stt.provider), ("llm", config.llm.provider), ("tts", config.tts.provider)):
        if role == "llm" and config.llm.base_url and config.llm.options.get("api_key"):
            continue
        if not keys.get(provider):
            missing.append({"role": role, "provider": provider, "env_key": ENV_KEYS[provider]})
    if missing:
        raise MissingProviderKey(missing)


def _language(code: str | None):
    """Map a BCP-47-ish code to pipecat ``Language`` (falls back to the raw string)."""
    from pipecat.transcriptions.language import Language

    if not code:
        return None
    for cand in (code, code.lower(), code.split("-")[0].lower()):
        try:
            return Language(cand)
        except ValueError:
            continue
    return code


def _is_lang(v) -> bool:
    from pipecat.transcriptions.language import Language

    return isinstance(v, Language)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# --------------------------------------------------------------------------- STT


def build_stt(cfg: STTConfig, keys: ProviderKeys):
    opts = dict(cfg.options or {})
    if cfg.provider == "deepgram":
        from pipecat.services.deepgram.stt import DeepgramSTTService

        s: dict[str, Any] = {
            "model": cfg.model or "nova-3",
            "language": cfg.language or "en-IN",
            "smart_format": True,
            "punctuate": True,
            "interim_results": True,
        }
        if cfg.keywords:
            # nova-3 uses keyterm prompting; older models use keyword boosting
            if (cfg.model or "").startswith("nova-3"):
                s["keyterm"] = list(cfg.keywords)
            else:
                s["keywords"] = [f"{k}:2" if ":" not in k else k for k in cfg.keywords]
        for k in ("numerals", "endpointing", "utterance_end_ms", "smart_format", "profanity_filter", "keyterm"):
            if k in opts:
                s[k] = opts.pop(k)
        settings = DeepgramSTTService.Settings(**s)
        return DeepgramSTTService(api_key=keys.get("deepgram"), settings=settings, **_init_kwargs(opts, {"base_url", "sample_rate"}))

    if cfg.provider == "soniox":
        from pipecat.services.soniox.stt import SonioxContextObject, SonioxSTTService

        s = {"model": cfg.model or "stt-rt-v5"}
        lang = cfg.language or "en"
        if lang and lang != "multi":
            hints = []
            for code in [lang] + list(opts.pop("extra_languages", []) or []):
                lv = _language(code.split("-")[0])
                if _is_lang(lv):
                    hints.append(lv)
            if hints:
                s["language_hints"] = hints
        if cfg.keywords:
            s["context"] = SonioxContextObject(terms=list(cfg.keywords))
        for k in ("language_hints_strict", "max_endpoint_delay_ms", "endpoint_sensitivity"):
            if k in opts:
                s[k] = opts.pop(k)
        settings = SonioxSTTService.Settings(**s)
        return SonioxSTTService(api_key=keys.get("soniox"), settings=settings, **_init_kwargs(opts, {"url", "sample_rate", "vad_force_turn_endpoint"}))

    if cfg.provider == "openai":
        from pipecat.services.openai.stt import OpenAISTTService

        model = cfg.model or "gpt-4o-transcribe"
        s = {"model": model}
        lang = _language((cfg.language or "en").split("-")[0])
        if _is_lang(lang):
            s["language"] = lang
        if cfg.keywords:
            if model.startswith("gpt-transcribe"):
                s["keywords"] = list(cfg.keywords)
            else:
                s["prompt"] = "Vocabulary: " + ", ".join(cfg.keywords)
        settings = OpenAISTTService.Settings(**s)
        return OpenAISTTService(api_key=keys.get("openai"), settings=settings, **_init_kwargs(opts, {"base_url"}))

    raise ValueError(f"Unknown STT provider: {cfg.provider}")


# --------------------------------------------------------------------------- LLM


def _is_reasoning_model(model: str) -> bool:
    m = model.lower()
    return m.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4"))


def _tools_need_no_reasoning(model: str) -> bool:
    # gpt-6* rejects function tools on /v1/chat/completions unless reasoning_effort="none"
    return model.lower().startswith("gpt-6")


def build_llm(cfg: LLMConfig, keys: ProviderKeys):
    from pipecat.services.openai.llm import OpenAILLMService

    if cfg.provider != "openai":
        raise ValueError(f"Unknown LLM provider: {cfg.provider}")
    opts = dict(cfg.options or {})
    model = cfg.model or "gpt-4.1-mini"
    s: dict[str, Any] = {"model": model}
    if _tools_need_no_reasoning(model):
        effort = opts.pop("reasoning_effort", "none")
        s["extra"] = {"reasoning_effort": effort}
        if effort == "none":
            s["temperature"] = cfg.temperature
        if cfg.max_tokens:
            s["max_completion_tokens"] = cfg.max_tokens if effort == "none" else max(cfg.max_tokens, 1024)
    elif _is_reasoning_model(model):
        if cfg.max_tokens:
            s["max_completion_tokens"] = max(cfg.max_tokens, 1024)
    else:
        s["temperature"] = cfg.temperature
        if cfg.max_tokens:
            s["max_tokens"] = cfg.max_tokens
    for k in ("top_p", "seed", "frequency_penalty", "presence_penalty"):
        if k in opts:
            s[k] = opts.pop(k)
    api_key = opts.pop("api_key", None) or keys.get("openai")
    kwargs: dict[str, Any] = {"api_key": api_key, "settings": OpenAILLMService.Settings(**s)}
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url
    kwargs.update(_init_kwargs(opts, {"organization", "project", "service_tier", "retry_timeout_secs", "retry_on_timeout"}))
    return OpenAILLMService(**kwargs)


# --------------------------------------------------------------------------- TTS


def build_tts(cfg: TTSConfig, keys: ProviderKeys):
    opts = dict(cfg.options or {})
    sr = cfg.sample_rate or None
    if cfg.provider == "soniox":
        from pipecat.services.soniox.tts import VALID_SAMPLE_RATES, SonioxTTSService

        if sr and sr not in VALID_SAMPLE_RATES:
            logger.warning(f"Soniox TTS does not support sample rate {sr}; using 24000")
            sr = 24000
        s: dict[str, Any] = {"model": cfg.model or "tts-rt-v2", "voice": cfg.voice or "Bryce"}
        lang = _language((cfg.language or "en").split("-")[0])
        if lang is not None:
            s["language"] = lang
        if cfg.speed and cfg.speed != 1.0:
            s["speed"] = _clamp(cfg.speed, 0.7, 1.3)
        if "reduce_silence" in opts:
            s["reduce_silence"] = opts.pop("reduce_silence")
        return SonioxTTSService(api_key=keys.get("soniox"), sample_rate=sr, settings=SonioxTTSService.Settings(**s), **_init_kwargs(opts, {"url"}))

    if cfg.provider == "deepgram":
        from pipecat.services.deepgram.tts import DeepgramTTSService

        voice = cfg.voice or "aura-2-helena-en"
        if not voice.startswith("aura"):
            voice = "aura-2-helena-en"
        s = {"voice": voice}
        if cfg.speed and cfg.speed != 1.0:
            s["speed"] = _clamp(cfg.speed, 0.7, 1.5)
        return DeepgramTTSService(api_key=keys.get("deepgram"), sample_rate=sr, settings=DeepgramTTSService.Settings(**s), **_init_kwargs(opts, {"base_url"}))

    if cfg.provider == "openai":
        from pipecat.services.openai.tts import OpenAITTSService

        s = {"model": cfg.model or "gpt-4o-mini-tts", "voice": cfg.voice or "alloy"}
        if cfg.speed and cfg.speed != 1.0:
            s["speed"] = _clamp(cfg.speed, 0.25, 4.0)
        if "instructions" in opts:
            s["instructions"] = opts.pop("instructions")
        # OpenAI TTS is fixed at 24 kHz
        return OpenAITTSService(api_key=keys.get("openai"), sample_rate=24000, settings=OpenAITTSService.Settings(**s), **_init_kwargs(opts, {"base_url"}))

    raise ValueError(f"Unknown TTS provider: {cfg.provider}")


def _init_kwargs(opts: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    """Pass through only known init-level options; ignore the rest (logged)."""
    out = {k: v for k, v in opts.items() if k in allowed}
    ignored = set(opts) - allowed
    if ignored:
        logger.debug(f"Ignoring unsupported provider options: {sorted(ignored)}")
    return out


@dataclass
class Services:
    stt: Any
    llm: Any
    tts: Any
    extras: dict[str, Any] = field(default_factory=dict)


def build_services(config: BotConfig, keys: ProviderKeys | None = None) -> Services:
    """Validate keys and construct all three services."""
    keys = keys or ProviderKeys.from_settings()
    check_keys(config, keys)
    return Services(stt=build_stt(config.stt, keys), llm=build_llm(config.llm, keys), tts=build_tts(config.tts, keys))
