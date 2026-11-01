"""Health + provider catalog."""

from __future__ import annotations

import copy
from typing import Any

from fastapi import APIRouter
from loguru import logger
from sqlalchemy import text

from app.core.config import settings
from app.db.session import engine
from app.schemas.providers import PROVIDER_CATALOG

router = APIRouter(tags=["meta"])


@router.get("/health")
async def health() -> dict[str, Any]:
    db_ok = False
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"health: database check failed: {e}")
    return {
        "ok": True,
        "db": db_ok,
        "providers": {
            "deepgram": settings.env_key_present("DEEPGRAM_API_KEY"),
            "openai": settings.env_key_present("OPENAI_API_KEY"),
            "soniox": settings.env_key_present("SONIOX_API_KEY"),
        },
    }


@router.get("/providers")
async def providers() -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for kind, entries in PROVIDER_CATALOG.items():
        out[kind] = []
        for entry in entries:
            e = copy.deepcopy(entry)
            env_key = e.pop("env_key", None)
            e["configured"] = settings.env_key_present(env_key) if env_key else True
            out[kind].append(e)
    return out
