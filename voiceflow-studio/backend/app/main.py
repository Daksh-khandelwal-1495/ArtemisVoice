"""FastAPI entrypoint: `uv run uvicorn app.main:app --port 8000`."""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.api.router import api_router
from app.core.config import settings
from app.db.session import init_db


# Pipecat logs every frame link / ICE candidate / prompt at DEBUG; keep the console readable.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
logger.remove()
logger.add(sys.stderr, level=os.getenv("LOG_LEVEL", "INFO").upper())


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        await init_db()
        if settings.auto_seed:
            from app.seed.loader import auto_seed_if_empty

            await auto_seed_if_empty()
    except Exception as e:  # noqa: BLE001 - keep the API up so /api/health can report it
        logger.error(f"startup: database init/seed failed: {e}")
    yield
    try:
        from app.voice.router import shutdown_voice

        await shutdown_voice()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"shutdown: voice cleanup skipped: {e}")


app = FastAPI(title="VoiceFlow Studio API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

try:
    from app.voice.router import router as voice_router

    # Voice router may declare full paths (/api/bots/{id}/offer) or relative ones (/bots/{id}/offer).
    _paths = [getattr(r, "path", "") for r in voice_router.routes]
    _has_api = voice_router.prefix.startswith("/api") or any(p.startswith("/api") for p in _paths)
    app.include_router(voice_router, prefix="" if _has_api else "/api")
except Exception as e:  # noqa: BLE001 - voice runtime is optional until implemented
    logger.warning(f"voice router not available ({type(e).__name__}: {e}); /api/bots/{{id}}/offer disabled")
