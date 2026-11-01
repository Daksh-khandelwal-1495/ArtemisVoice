"""Asynchronous, failure-tolerant persistence of a call session.

All writes go through a single background queue so the voice pipeline never waits on the
database, and every DB error is logged and swallowed (a broken DB must never break a call).

Writes: ``call_sessions`` (create / current_node / variables / final status + duration),
``session_events`` (append), ``captured_fields`` (upsert on (session_id, field_name)).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from app.voice.validators import mask_value

Op = Callable[[Any], Awaitable[None]]

_MASK_KEYS = {"value", "raw"}


def json_safe(obj: Any) -> Any:
    try:
        return json.loads(json.dumps(obj, default=str))
    except Exception:  # noqa: BLE001
        return str(obj)


def mask_payload(payload: Any, sensitive: set[str]) -> Any:
    """Mask values of sensitive fields wherever they appear in an event payload."""
    if not sensitive:
        return payload
    if isinstance(payload, dict):
        field_name = payload.get("field")
        out = {}
        for k, v in payload.items():
            if k in sensitive and not isinstance(v, (dict, list)):
                out[k] = mask_value(v)
            elif k in _MASK_KEYS and field_name in sensitive and not isinstance(v, (dict, list)):
                out[k] = mask_value(v)
            else:
                out[k] = mask_payload(v, sensitive)
        return out
    if isinstance(payload, list):
        return [mask_payload(v, sensitive) for v in payload]
    return payload


class SessionRecorder:
    def __init__(
        self,
        *,
        bot_id: uuid.UUID | str,
        flow_version_id: uuid.UUID | str | None,
        mode: str,
        config_snapshot: dict[str, Any] | None = None,
        session_id: uuid.UUID | None = None,
        sensitive_fields: set[str] | None = None,
        mask_sensitive: bool = True,
        record_transcript: bool = True,
        session_factory: Any = None,
    ):
        self.session_id = session_id or uuid.uuid4()
        self.bot_id = _uuid(bot_id)
        self.flow_version_id = _uuid(flow_version_id) if flow_version_id else None
        self.mode = mode
        self.config_snapshot = json_safe(config_snapshot or {})
        self.sensitive = set(sensitive_fields or set()) if mask_sensitive else set()
        self.record_transcript = record_transcript
        self._factory = session_factory
        self._queue: asyncio.Queue[Op | None] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._started_mono = time.monotonic()
        self._finished = False
        self._created = False
        self.errors = 0

    # ------------------------------------------------------------------ plumbing

    def _session_factory(self):
        if self._factory is None:
            from app.db.session import SessionLocal

            self._factory = SessionLocal
        return self._factory

    async def start(self) -> None:
        self._started_mono = time.monotonic()
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name=f"recorder-{self.session_id}")
        self._enqueue(self._op_create)

    def _enqueue(self, op: Op) -> None:
        if self._finished:
            return
        self._queue.put_nowait(op)

    async def _run(self) -> None:
        while True:
            op = await self._queue.get()
            if op is None:
                self._queue.task_done()
                break
            try:
                await self._exec(op)
            finally:
                self._queue.task_done()

    async def _exec(self, op: Op) -> None:
        for attempt in (1, 2):
            try:
                factory = self._session_factory()
                async with factory() as db:
                    await op(db)
                    await db.commit()
                return
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    self.errors += 1
                    logger.warning(f"[recorder {self.session_id}] DB write failed: {e}")
                else:
                    await asyncio.sleep(0.2)

    async def flush(self, timeout: float = 5.0) -> None:
        try:
            await asyncio.wait_for(self._queue.join(), timeout)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ operations

    async def _op_create(self, db) -> None:
        from app.db.models import CallSession

        if self._created:
            return
        existing = await db.get(CallSession, self.session_id)
        if existing is None:
            db.add(
                CallSession(
                    id=self.session_id,
                    bot_id=self.bot_id,
                    flow_version_id=self.flow_version_id,
                    mode=self.mode,
                    status="active",
                    config_snapshot=self.config_snapshot,
                    variables={},
                )
            )
        self._created = True

    def event(self, type: str, node_id: str | None = None, payload: dict[str, Any] | None = None,
              *, mask: bool = True) -> None:
        """Append a session event. ``mask=False`` stores the payload verbatim (trace events
        need real state for replay; they are masked when served)."""
        if type in ("user_transcript", "bot_transcript") and not self.record_transcript:
            return
        data = json_safe(mask_payload(payload or {}, self.sensitive) if mask else (payload or {}))
        ts = datetime.now(timezone.utc)

        async def op(db) -> None:
            from app.db.models import SessionEvent

            db.add(SessionEvent(session_id=self.session_id, ts=ts, type=type, node_id=node_id, payload=data))

        self._enqueue(op)

    def field(self, node_id: str | None, name: str, value: Any, valid: bool, attempts: int) -> None:
        val = json_safe(value)

        async def op(db) -> None:
            from sqlalchemy import select

            from app.db.models import CapturedField

            row = (
                await db.execute(
                    select(CapturedField).where(
                        CapturedField.session_id == self.session_id, CapturedField.field_name == name
                    )
                )
            ).scalar_one_or_none()
            now = datetime.now(timezone.utc)
            if row is None:
                db.add(CapturedField(session_id=self.session_id, node_id=node_id, field_name=name, value=val,
                                     valid=valid, attempts=attempts, captured_at=now))
            else:
                if valid or not row.valid:  # never overwrite a valid capture with a later invalid try
                    row.value = val
                    row.valid = valid
                row.node_id = node_id
                row.attempts = max(row.attempts or 0, attempts)
                row.captured_at = now

        self._enqueue(op)

    def update(self, *, current_node: str | None = None, variables: dict[str, Any] | None = None) -> None:
        vars_ = json_safe(variables) if variables is not None else None

        async def op(db) -> None:
            from app.db.models import CallSession

            row = await db.get(CallSession, self.session_id)
            if row is None:
                return
            if current_node is not None:
                row.current_node = current_node
            if vars_ is not None:
                row.variables = vars_

        self._enqueue(op)

    async def finish(self, status: str, end_reason: str | None, variables: dict[str, Any] | None = None) -> None:
        """Write the final status / duration, drain the queue and stop the writer."""
        if self._finished:
            return
        duration = round(time.monotonic() - self._started_mono, 2)
        vars_ = json_safe(variables) if variables is not None else None
        ended = datetime.now(timezone.utc)

        async def op(db) -> None:
            from app.db.models import CallSession

            row = await db.get(CallSession, self.session_id)
            if row is None:
                return
            row.status = status
            row.end_reason = end_reason
            row.ended_at = ended
            row.duration_secs = duration
            if vars_ is not None:
                row.variables = vars_

        self._enqueue(op)
        self._finished = True
        self._queue.put_nowait(None)
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=10)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[recorder {self.session_id}] did not drain cleanly: {e}")

    @property
    def duration(self) -> float:
        return round(time.monotonic() - self._started_mono, 2)


def _uuid(v: Any) -> uuid.UUID:
    return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))
