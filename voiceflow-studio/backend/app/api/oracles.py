"""Test oracle CRUD (INTEGRATION_CONTRACT §2-§3; regression scenarios use ``tags: ["regression"]``)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import get_bot_or_404, parse_uuid, slugify
from app.db.models import TestOracle, utcnow
from app.db.session import get_db
from app.replay.faults import FaultProfile
from app.replay.oracle import oracle_dict

router = APIRouter(tags=["oracles"])


class ExpectedLookup(BaseModel):
    dataset: str
    match: dict[str, Any] = Field(default_factory=dict)


class ExpectedBotResponse(BaseModel):
    contains: list[str] = Field(default_factory=list)


class OracleBase(BaseModel):
    name: str | None = None
    description: str | None = None
    caller_turns: list[str] | None = None
    expected_fields: dict[str, Any] | None = None
    expected_lookup: ExpectedLookup | None = None
    expected_end_node: str | None = None
    expected_status: str | None = None
    expected_bot_response: ExpectedBotResponse | None = None
    fault_profile: dict[str, Any] | None = None
    tags: list[str] | None = None

    @field_validator("fault_profile")
    @classmethod
    def _fp(cls, v):
        if v is not None:
            FaultProfile.model_validate(v)
        return v


class OracleCreate(OracleBase):
    slug: str | None = Field(default=None, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    caller_turns: list[str] = Field(default_factory=list)


class OraclePatch(OracleBase):
    slug: str | None = Field(default=None, max_length=80)


def _apply(o: TestOracle, data: dict[str, Any]) -> None:
    for k, v in data.items():
        if k in ("expected_fields",):
            v = {str(a): b for a, b in (v or {}).items()}
        if k == "tags":
            v = sorted({str(t) for t in (v or [])})
        setattr(o, k, v)


async def _get(db: AsyncSession, oracle_id: str) -> TestOracle:
    oid = parse_uuid(oracle_id)
    o = await db.get(TestOracle, oid) if oid else None
    if o is None:
        raise HTTPException(status_code=404, detail="Oracle not found")
    return o


@router.get("/bots/{bot_id}/oracles")
async def list_oracles(bot_id: str, tag: str | None = None, db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    bot = await get_bot_or_404(db, bot_id)
    rows = (await db.execute(select(TestOracle).where(TestOracle.bot_id == bot.id)
                             .order_by(TestOracle.created_at, TestOracle.slug))).scalars().all()
    out = [oracle_dict(o) for o in rows]
    if tag:
        wanted = {t.strip() for t in tag.split(",") if t.strip()}
        out = [o for o in out if wanted & set(o["tags"])]
    return out


@router.post("/bots/{bot_id}/oracles", status_code=201)
async def create_oracle(bot_id: str, body: OracleCreate, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    bot = await get_bot_or_404(db, bot_id)
    slug = slugify(body.slug or body.name)
    base, i = slug, 2
    explicit = bool(body.slug)
    while (await db.execute(select(TestOracle.id).where(TestOracle.bot_id == bot.id, TestOracle.slug == slug))).first():
        if explicit:
            raise HTTPException(status_code=409, detail=f"Oracle '{slug}' already exists for this bot")
        slug, i = f"{base[:75]}-{i}", i + 1
    o = TestOracle(bot_id=bot.id, slug=slug, name=body.name, caller_turns=[], expected_fields={}, tags=[])
    _apply(o, body.model_dump(mode="json", exclude={"slug"}, exclude_none=True))
    db.add(o)
    await db.commit()
    return oracle_dict(o)


@router.get("/oracles/{oracle_id}")
async def get_oracle(oracle_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return oracle_dict(await _get(db, oracle_id))


@router.patch("/oracles/{oracle_id}")
async def patch_oracle(oracle_id: str, body: OraclePatch, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    o = await _get(db, oracle_id)
    data = body.model_dump(mode="json", exclude_unset=True)
    if "slug" in data:
        slug = slugify(data.pop("slug") or o.slug)
        clash = (await db.execute(select(TestOracle.id).where(
            TestOracle.bot_id == o.bot_id, TestOracle.slug == slug, TestOracle.id != o.id))).first()
        if clash:
            raise HTTPException(status_code=409, detail=f"Oracle '{slug}' already exists for this bot")
        o.slug = slug
    _apply(o, data)
    o.updated_at = utcnow()
    await db.commit()
    return oracle_dict(o)


@router.delete("/oracles/{oracle_id}", status_code=204)
async def delete_oracle(oracle_id: str, db: AsyncSession = Depends(get_db)) -> Response:
    o = await _get(db, oracle_id)
    await db.delete(o)
    await db.commit()
    return Response(status_code=204)
