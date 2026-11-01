"""Aggregate API router mounted at /api."""

from fastapi import APIRouter

from app.api import bots, datasets, health, sessions

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(bots.router)
api_router.include_router(datasets.router)
api_router.include_router(sessions.router)
