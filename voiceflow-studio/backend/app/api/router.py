"""Aggregate API router mounted at /api."""

from fastapi import APIRouter

from app.api import artifacts, bots, datasets, health, oracles, patches, replay, sessions

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(bots.router)
api_router.include_router(datasets.router)
api_router.include_router(sessions.router)
api_router.include_router(artifacts.router)
api_router.include_router(replay.router)
api_router.include_router(oracles.router)
api_router.include_router(patches.router)
