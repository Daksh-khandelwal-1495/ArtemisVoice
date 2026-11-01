from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum


class ProviderName(StrEnum):
    MOCK = "mock"
    LOCAL = "local"
    OPENAI = "openai"
    CEREBRAS = "cerebras"
    EXA = "exa"


class ProviderConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ProviderSettings:
    """Configuration names providers without placing secrets in traces or logs."""

    mode: ProviderName
    stt: ProviderName
    tts: ProviderName
    judge: ProviderName
    research: ProviderName
    openai_configured: bool
    cerebras_configured: bool
    exa_configured: bool

    @classmethod
    def from_environment(cls) -> ProviderSettings:
        def provider(name: str, default: ProviderName) -> ProviderName:
            try:
                return ProviderName(os.getenv(name, default).lower())
            except ValueError as error:
                raise ProviderConfigurationError(f"{name} is not a supported provider") from error

        return cls(
            mode=provider("ARTEMIS_PROVIDER_MODE", ProviderName.MOCK),
            stt=provider("ARTEMIS_STT_PROVIDER", ProviderName.OPENAI),
            tts=provider("ARTEMIS_TTS_PROVIDER", ProviderName.LOCAL),
            judge=provider("ARTEMIS_JUDGE_PROVIDER", ProviderName.CEREBRAS),
            research=provider("ARTEMIS_RESEARCH_PROVIDER", ProviderName.EXA),
            openai_configured=bool(os.getenv("OPENAI_API_KEY")),
            cerebras_configured=bool(os.getenv("CEREBRAS_API_KEY")),
            exa_configured=bool(os.getenv("EXA_API_KEY")),
        )

    def validate(self) -> None:
        if self.mode is ProviderName.MOCK:
            return
        required = {
            ProviderName.OPENAI: self.openai_configured,
            ProviderName.CEREBRAS: self.cerebras_configured,
            ProviderName.EXA: self.exa_configured,
        }
        missing = [provider for provider in (self.stt, self.tts, self.judge, self.research) if not required.get(provider, True)]
        if missing:
            raise ProviderConfigurationError(
                f"missing credentials for: {', '.join(sorted(set(missing)))}"
            )
