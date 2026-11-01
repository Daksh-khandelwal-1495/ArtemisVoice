"""Application settings (pydantic-settings, reads backend/.env)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "postgresql+asyncpg://voiceflow:voiceflow@localhost:5433/voiceflow"
    openai_api_key: str = ""
    deepgram_api_key: str = ""
    soniox_api_key: str = ""
    cors_origins: str = "http://localhost:3000"  # comma-separated or JSON list
    api_call_mock: bool = True
    auto_seed: bool = True
    sql_echo: bool = False
    db_nullpool: bool = False  # tests: avoid pooling connections across event loops

    @property
    def cors_origin_list(self) -> list[str]:
        v = self.cors_origins.strip()
        if v.startswith("["):
            import json

            return [str(o) for o in json.loads(v)]
        return [o.strip() for o in v.split(",") if o.strip()]

    def env_key_present(self, env_key: str) -> bool:
        """True if the given env var name (e.g. OPENAI_API_KEY) has a non-empty value."""
        import os

        val = getattr(self, env_key.lower(), None)
        if val is None:
            val = os.environ.get(env_key, "")
        return bool(str(val).strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
