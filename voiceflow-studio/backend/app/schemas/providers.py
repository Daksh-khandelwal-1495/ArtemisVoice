"""Per-bot provider + behaviour configuration (stored as bots.config JSONB)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

STTProvider = Literal["deepgram", "soniox", "openai"]
LLMProvider = Literal["openai"]
TTSProvider = Literal["soniox", "deepgram", "openai"]


class STTConfig(BaseModel):
    provider: STTProvider = "deepgram"
    model: str = "nova-3"
    language: str = "en-IN"
    keywords: list[str] = Field(default_factory=list)  # boost brand words: "Nexon", "Seltos"
    options: dict[str, Any] = Field(default_factory=dict)


class LLMConfig(BaseModel):
    provider: LLMProvider = "openai"
    model: str = "gpt-6-luna"
    temperature: float = Field(default=0.4, ge=0, le=2)
    max_tokens: int | None = Field(default=300, ge=16, le=4096)
    base_url: str | None = None        # OpenAI-compatible endpoints
    options: dict[str, Any] = Field(default_factory=dict)


class TTSConfig(BaseModel):
    provider: TTSProvider = "soniox"
    model: str = "tts-rt-v2"
    voice: str = "Bryce"
    language: str = "en"
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    sample_rate: int | None = 24000
    options: dict[str, Any] = Field(default_factory=dict)


class VADConfig(BaseModel):
    stop_secs: float = Field(default=0.6, ge=0.1, le=3)
    start_secs: float = Field(default=0.2, ge=0.05, le=2)
    confidence: float = Field(default=0.7, ge=0, le=1)


class BehaviorConfig(BaseModel):
    allow_interruptions: bool = True
    idle_timeout_secs: int = Field(default=20, ge=5, le=300)       # user silence before nudge
    idle_max_nudges: int = Field(default=2, ge=0, le=10)           # then end call
    max_call_duration_secs: int = Field(default=900, ge=30, le=7200)
    greeting: str | None = None
    fallback_message: str = "Sorry, I'm having trouble right now. Let me connect you to a human agent."
    record_transcript: bool = True
    mask_sensitive_in_logs: bool = True


class BotConfig(BaseModel):
    stt: STTConfig = Field(default_factory=STTConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    vad: VADConfig = Field(default_factory=VADConfig)
    behavior: BehaviorConfig = Field(default_factory=BehaviorConfig)


# Catalog served at GET /api/providers. `env_key` is checked to report `configured`.
PROVIDER_CATALOG: dict[str, list[dict[str, Any]]] = {
    "stt": [
        {"id": "deepgram", "name": "Deepgram", "env_key": "DEEPGRAM_API_KEY",
         "models": ["nova-3", "nova-3-general", "nova-2", "nova-2-phonecall"],
         "languages": ["en-IN", "en-US", "en-GB", "hi", "multi"]},
        {"id": "soniox", "name": "Soniox", "env_key": "SONIOX_API_KEY",
         "models": ["stt-rt-v5", "stt-rt-v4"], "languages": ["en", "hi", "multi"]},
        {"id": "openai", "name": "OpenAI", "env_key": "OPENAI_API_KEY",
         "models": ["gpt-4o-transcribe", "gpt-4o-mini-transcribe"], "languages": ["en", "hi"]},
    ],
    "llm": [
        {"id": "openai", "name": "OpenAI", "env_key": "OPENAI_API_KEY",
         "models": ["gpt-6-luna", "gpt-6-sol", "gpt-6-astra", "gpt-5.5", "gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini", "gpt-4o", "gpt-5-mini", "gpt-5"]},
    ],
    "tts": [
        {"id": "soniox", "name": "Soniox", "env_key": "SONIOX_API_KEY",
         "models": ["tts-rt-v2", "tts-rt-v1"],
         "voices": ["Bryce", "Adrian", "Maya", "Nina", "Kenji", "Sofia"], "languages": ["en", "hi"]},
        {"id": "deepgram", "name": "Deepgram Aura", "env_key": "DEEPGRAM_API_KEY",
         "models": ["aura-2"],
         "voices": ["aura-2-helena-en", "aura-2-thalia-en", "aura-2-andromeda-en", "aura-2-apollo-en", "aura-2-arcas-en"],
         "languages": ["en"]},
        {"id": "openai", "name": "OpenAI", "env_key": "OPENAI_API_KEY",
         "models": ["gpt-4o-mini-tts", "tts-1"],
         "voices": ["alloy", "ash", "coral", "echo", "nova", "sage", "shimmer"], "languages": ["en", "hi"]},
    ],
}
