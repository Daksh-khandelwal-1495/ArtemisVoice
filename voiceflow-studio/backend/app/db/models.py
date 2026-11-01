"""SQLAlchemy 2 ORM models (see CONTRACT.md > Database).

JSON columns use JSONB on Postgres and fall back to generic JSON elsewhere (sqlite in tests).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


def _ts(**kw):
    return mapped_column(DateTime(timezone=True), server_default=func.now(), default=utcnow, **kw)


class Bot(Base):
    __tablename__ = "bots"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    industry: Mapped[str] = mapped_column(String(100), default="")
    avatar_color: Mapped[str] = mapped_column(String(32), default="#6366f1")
    language: Mapped[str] = mapped_column(String(32), default="en-IN")
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft|published|archived
    config: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    published_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("flow_versions.id", ondelete="SET NULL", use_alter=True, name="fk_bots_published_version"),
        nullable=True,
    )
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts(onupdate=utcnow)


class FlowVersion(Base):
    __tablename__ = "flow_versions"
    __table_args__ = (UniqueConstraint("bot_id", "version", name="uq_flow_versions_bot_version"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    bot_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("bots.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    graph: Mapped[dict[str, Any]] = mapped_column(JSONType)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _ts()


class CallSession(Base):
    __tablename__ = "call_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    bot_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("bots.id", ondelete="CASCADE"), index=True)
    flow_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("flow_versions.id", ondelete="SET NULL"), nullable=True
    )
    mode: Mapped[str] = mapped_column(String(16), default="draft")  # draft|published
    # active|completed|transferred|abandoned|error
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    end_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    current_node: Mapped[str | None] = mapped_column(String(80), nullable=True)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    variables: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    started_at: Mapped[datetime] = _ts(index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_secs: Mapped[float | None] = mapped_column(Float, nullable=True)


class SessionEvent(Base):
    __tablename__ = "session_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("call_sessions.id", ondelete="CASCADE"), index=True
    )
    ts: Mapped[datetime] = _ts()
    # user_transcript|bot_transcript|node_enter|function_call|validation_error|field_captured|
    # lookup|api_call|error|idle|end
    type: Mapped[str] = mapped_column(String(32))
    node_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class CapturedField(Base):
    __tablename__ = "captured_fields"
    __table_args__ = (UniqueConstraint("session_id", "field_name", name="uq_captured_fields_session_field"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("call_sessions.id", ondelete="CASCADE"), index=True
    )
    node_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    field_name: Mapped[str] = mapped_column(String(80))
    value: Mapped[Any] = mapped_column(JSONType, nullable=True)
    valid: Mapped[bool] = mapped_column(Boolean, default=True)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    captured_at: Mapped[datetime] = _ts()


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    columns: Mapped[list[str]] = mapped_column(JSONType, default=list)


class DatasetRow(Base):
    __tablename__ = "dataset_rows"
    __table_args__ = (
        Index("ix_dataset_rows_data_gin", "data", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("datasets.id", ondelete="CASCADE"), index=True
    )
    data: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
