"""Datasets (lookup tables) and their rows."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import parse_uuid
from app.db.models import Dataset, DatasetRow
from app.db.session import get_db

router = APIRouter(tags=["datasets"])


class RowCreate(BaseModel):
    data: dict[str, Any]


async def _get_dataset(db: AsyncSession, name: str) -> Dataset:
    ds = (await db.execute(select(Dataset).where(Dataset.name == name))).scalar_one_or_none()
    if ds is None:
        raise HTTPException(status_code=404, detail=f"Dataset '{name}' not found")
    return ds


def _row(r: DatasetRow) -> dict[str, Any]:
    return {"id": str(r.id), "data": r.data}


@router.get("/datasets")
async def list_datasets(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    q = (
        select(Dataset, func.count(DatasetRow.id))
        .outerjoin(DatasetRow, DatasetRow.dataset_id == Dataset.id)
        .group_by(Dataset.id)
        .order_by(Dataset.name)
    )
    return [
        {"id": str(d.id), "name": d.name, "description": d.description or "", "columns": d.columns or [],
         "row_count": c}
        for d, c in (await db.execute(q)).all()
    ]


@router.get("/datasets/{name}/rows")
async def list_rows(
    name: str,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ds = await _get_dataset(db, name)
    cond = [DatasetRow.dataset_id == ds.id]
    if q:
        terms = [t for t in q.split() if t]
        for t in terms:  # every term must appear somewhere in the row (case-insensitive)
            cond.append(cast(DatasetRow.data, String).ilike(f"%{t}%"))
    total = (await db.execute(select(func.count(DatasetRow.id)).where(*cond))).scalar() or 0
    rows = (
        await db.execute(select(DatasetRow).where(*cond).order_by(DatasetRow.id).limit(limit).offset(offset))
    ).scalars().all()
    return {"items": [_row(r) for r in rows], "total": total, "columns": ds.columns or []}


@router.post("/datasets/{name}/rows", status_code=201)
async def add_row(name: str, body: RowCreate, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    ds = await _get_dataset(db, name)
    row = DatasetRow(dataset_id=ds.id, data=body.data)
    db.add(row)
    cols = list(ds.columns or [])
    new_cols = [k for k in body.data if k not in cols]
    if new_cols:
        ds.columns = cols + new_cols
    await db.commit()
    return _row(row)


@router.delete("/datasets/{name}/rows/{row_id}", status_code=204)
async def delete_row(name: str, row_id: str, db: AsyncSession = Depends(get_db)) -> Response:
    ds = await _get_dataset(db, name)
    rid = parse_uuid(row_id)
    row = await db.get(DatasetRow, rid) if rid else None
    if row is None or row.dataset_id != ds.id:
        raise HTTPException(status_code=404, detail="Row not found")
    await db.delete(row)
    await db.commit()
    return Response(status_code=204)
