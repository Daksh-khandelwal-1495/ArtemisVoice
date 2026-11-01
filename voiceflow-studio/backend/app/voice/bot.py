"""Per-call pipeline: SmallWebRTC -> STT -> user agg -> LLM -> TTS -> output -> assistant agg.

``build_call`` constructs everything (services, VAD, aggregators, worker, FlowManager,
runtime, recorder) without connecting anything, so it can be smoke-tested; ``run_call``
wires the transport events and runs the worker until the call ends.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from loguru import logger

from app.schemas.flow import FlowGraph, FlowNode
from app.schemas.providers import BotConfig
from app.voice.compiler import FlowRuntime, RuntimeHooks, Terminal, filter_rows
from app.voice.providers import ProviderKeys, build_services
from app.voice.recorder import SessionRecorder

IDLE_NUDGE_PROMPT = (
    "The caller has been silent for a while. Briefly and politely check whether they are still "
    "there, then gently repeat your last question."
)
IDLE_END_MESSAGE = "It seems we got disconnected. Feel free to call back anytime. Goodbye!"
MAX_DURATION_MESSAGE = "We've reached the time limit for this call. Our team will follow up with you. Thank you, goodbye!"
CLIENT_READY_GRACE_SECS = 2.5


@dataclass
class CallContext:
    bot_id: uuid.UUID | str
    bot_name: str
    mode: str
    flow_version_id: uuid.UUID | str | None
    flow_version: int | None
    graph: FlowGraph
    config: BotConfig
    session_id: uuid.UUID = field(default_factory=uuid.uuid4)


# --------------------------------------------------------------------------- mute strategy


def _make_interruption_strategy(runtime: FlowRuntime):
    """User mute strategy honouring behaviour + per-node ``allow_interruptions``."""
    from pipecat.frames.frames import BotStartedSpeakingFrame, BotStoppedSpeakingFrame
    from pipecat.turns.user_mute.base_user_mute_strategy import BaseUserMuteStrategy

    class NodeInterruptionMuteStrategy(BaseUserMuteStrategy):
        def __init__(self):
            super().__init__()
            self._bot_speaking = False

        async def process_frame(self, frame) -> bool:
            await super().process_frame(frame)
            if isinstance(frame, BotStartedSpeakingFrame):
                self._bot_speaking = True
            elif isinstance(frame, BotStoppedSpeakingFrame):
                self._bot_speaking = False
            return self._bot_speaking and not runtime.allows_interruptions()

    return NodeInterruptionMuteStrategy()


# --------------------------------------------------------------------------- live hooks


class LiveHooks(RuntimeHooks):
    """Connects the runtime to RTVI, the recorder, the DB (lookups) and HTTP (api_call)."""

    def __init__(self, call: "Call"):
        self.call = call
        self._ending = False

    @property
    def rec(self) -> SessionRecorder:
        return self.call.recorder

    async def node_entered(self, node: FlowNode) -> None:
        self.rec.event("node_enter", node.id, {"label": node.label, "node_type": node.type})
        self.rec.update(current_node=node.id)
        await self.call.send({"type": "node", "node_id": node.id, "label": node.label, "node_type": node.type})
        await self.call.update_idle_timeout()

    async def event(self, type: str, node_id: str | None, payload: dict[str, Any]) -> None:
        self.rec.event(type, node_id, payload)
        await self.call.send({"type": "event", "event": type, "payload": self.call.mask(payload)})

    async def field_captured(self, node_id: str, name: str, value: Any, valid: bool, attempts: int) -> None:
        self.rec.field(node_id, name, value, valid, attempts)

    async def validation(self, node_id: str, errors: dict[str, str]) -> None:
        await self.call.send({"type": "validation", "node_id": node_id, "errors": errors})

    async def vars_changed(self, masked_vars: dict[str, Any], raw_vars: dict[str, Any]) -> None:
        self.rec.update(variables=raw_vars)
        await self.call.send({"type": "vars", "vars": masked_vars})

    async def transfer(self, node_id: str, transfer_number: str | None) -> None:
        await self.call.send({"type": "event", "event": "transfer",
                              "payload": {"node_id": node_id, "transfer_number": transfer_number}})

    async def lookup_rows(self, dataset: str, filters: dict[str, Any], mode: str, limit: int) -> list[dict[str, Any]]:
        return await lookup_dataset_rows(dataset, filters, mode, limit, session_factory=self.call.session_factory)

    async def http_request(self, method: str, url: str, headers: dict[str, str], body: Any, timeout: float) -> Any:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, url, headers=headers or None,
                                        json=body if method != "GET" else None,
                                        params=body if method == "GET" and isinstance(body, dict) else None)
            resp.raise_for_status()
            try:
                return resp.json()
            except ValueError:
                return {"text": resp.text}

    async def end_call(self, flow_manager: Any, text: str | None) -> None:
        await self.call.speak_and_end(text)

    async def ended(self, status: str, end_reason: str | None) -> None:
        await self.call.send({"type": "ended", "status": status, "end_reason": end_reason})


async def lookup_dataset_rows(dataset: str, filters: dict[str, Any], mode: str, limit: int, *, session_factory=None) -> list[dict[str, Any]]:
    """Query ``dataset_rows`` for rows whose ``data->>col`` matches each filter value.

    Uses SQL JSON operators on Postgres; falls back to in-Python filtering (sqlite / errors).
    """
    from sqlalchemy import func, select

    from app.db.models import Dataset, DatasetRow

    if session_factory is None:
        from app.db.session import SessionLocal as session_factory  # noqa: N813

    wanted = {c: v for c, v in filters.items() if v is not None and str(v).strip() != ""}
    if not wanted:
        return []
    async with session_factory() as db:
        ds = (await db.execute(select(Dataset).where(Dataset.name == dataset))).scalar_one_or_none()
        if ds is None:
            raise LookupError(f"dataset '{dataset}' not found")
        ds_id = ds.id
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            try:
                q = select(DatasetRow.data).where(DatasetRow.dataset_id == ds_id)
                for col, val in wanted.items():
                    cell = func.lower(func.trim(DatasetRow.data[col].as_string()))
                    sval = str(val).strip().lower()
                    if mode == "icontains":
                        q = q.where(cell.contains(sval, autoescape=True))
                    else:
                        q = q.where(cell == sval)
                rows = (await db.execute(q.limit(limit))).scalars().all()
                if rows or mode != "icontains":
                    return [dict(r) for r in rows]
            except Exception as e:  # noqa: BLE001
                logger.debug(f"SQL lookup failed, falling back to python filtering: {e}")
                await db.rollback()
        all_rows = (await db.execute(select(DatasetRow.data).where(DatasetRow.dataset_id == ds_id))).scalars().all()
        return filter_rows([dict(r) for r in all_rows], wanted, mode, limit)


# --------------------------------------------------------------------------- call


class Call:
    """One live call: services, pipeline, worker, flow manager, runtime and recorder."""

    def __init__(self, ctx: CallContext, transport: Any, *, keys: ProviderKeys | None = None,
                 session_factory: Any = None, api_call_mock: bool | None = None):
        from pipecat.audio.vad.silero import SileroVADAnalyzer
        from pipecat.audio.vad.vad_analyzer import VADParams
        from pipecat.flows import FlowManager
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.worker import PipelineParams, PipelineWorker
        from pipecat.processors.aggregators.llm_context import LLMContext
        from pipecat.processors.aggregators.llm_response_universal import (
            LLMContextAggregatorPair,
            LLMUserAggregatorParams,
        )

        self.ctx = ctx
        self.config = ctx.config
        self.transport = transport
        self.session_factory = session_factory
        self._client_ready = False
        self._started = False
        self._outbox: list[dict[str, Any]] = []
        self._ending = False
        self._finalized = False
        self._idle_nudges = 0
        self._provider_errors: dict[str, int] = {}
        self._timers: list[asyncio.Task] = []

        if api_call_mock is None:
            try:
                from app.core.config import settings

                api_call_mock = bool(getattr(settings, "api_call_mock", True))
            except Exception:  # noqa: BLE001
                api_call_mock = True

        self.hooks = LiveHooks(self)
        self.runtime = FlowRuntime(graph=ctx.graph, config=ctx.config, hooks=self.hooks, api_call_mock=api_call_mock,
                                   require_grounding=True,
                                   bot_name=ctx.bot_name, session_id=str(ctx.session_id))
        b = ctx.config.behavior
        self.recorder = SessionRecorder(
            session_id=ctx.session_id, bot_id=ctx.bot_id, flow_version_id=ctx.flow_version_id, mode=ctx.mode,
            config_snapshot=ctx.config.model_dump(mode="json"), sensitive_fields=self.runtime.sensitive,
            mask_sensitive=b.mask_sensitive_in_logs, record_transcript=b.record_transcript,
            session_factory=session_factory,
        )

        services = build_services(ctx.config, keys)
        self.stt, self.llm, self.tts = services.stt, services.llm, services.tts

        v = ctx.config.vad
        self.vad = SileroVADAnalyzer(params=VADParams(confidence=v.confidence, start_secs=v.start_secs, stop_secs=v.stop_secs))
        self.context = LLMContext()
        self.aggregators = LLMContextAggregatorPair(
            self.context,
            user_params=LLMUserAggregatorParams(
                vad_analyzer=self.vad,
                user_idle_timeout=float(b.idle_timeout_secs),
                user_mute_strategies=[_make_interruption_strategy(self.runtime)],
            ),
        )
        self.user_agg = self.aggregators.user()
        self.assistant_agg = self.aggregators.assistant()

        self.pipeline = Pipeline([
            transport.input(),
            self.stt,
            self.user_agg,
            self.llm,
            self.tts,
            transport.output(),
            self.assistant_agg,
        ])
        out_sr = ctx.config.tts.sample_rate or 24000
        if ctx.config.tts.provider == "openai":
            out_sr = 24000
        self.worker = PipelineWorker(
            self.pipeline,
            params=PipelineParams(
                audio_in_sample_rate=16000,
                audio_out_sample_rate=out_sr,
                enable_metrics=True,
                enable_usage_metrics=True,
            ),
            idle_timeout_secs=None,  # call lifetime is governed by our own idle / max-duration logic
            conversation_id=str(ctx.session_id),
        )
        self.flow_manager = FlowManager(
            worker=self.worker,
            llm=self.llm,
            context_aggregator=self.aggregators,
            transport=transport,
            global_functions=self.runtime.global_functions(),
        )
        self._register_handlers()

    # ------------------------------------------------------------------ helpers

    def mask(self, payload: dict[str, Any]) -> dict[str, Any]:
        from app.voice.recorder import mask_payload

        return mask_payload(payload, self.runtime.sensitive)

    async def send(self, data: dict[str, Any]) -> None:
        """Send an RTVI server message (buffered until the client is ready)."""
        if not self._client_ready:
            self._outbox.append(data)
            return
        try:
            from pipecat.processors.frameworks.rtvi import RTVIServerMessageFrame

            await self.worker.queue_frame(RTVIServerMessageFrame(data=data))
        except Exception as e:  # noqa: BLE001
            logger.debug(f"RTVI send failed: {e}")

    async def _flush_outbox(self) -> None:
        self._client_ready = True
        pending, self._outbox = self._outbox, []
        for m in pending:
            await self.send(m)

    async def update_idle_timeout(self) -> None:
        try:
            from pipecat.frames.frames import UserIdleTimeoutUpdateFrame

            await self.worker.queue_frame(UserIdleTimeoutUpdateFrame(timeout=self.runtime.silence_timeout()))
        except Exception as e:  # noqa: BLE001
            logger.debug(f"idle timeout update failed: {e}")

    async def speak_and_end(self, text: str | None) -> None:
        if self._ending:
            return
        self._ending = True
        from pipecat.frames.frames import EndFrame, TTSSpeakFrame

        frames: list[Any] = []
        if text:
            speak = TTSSpeakFrame(text=text, append_to_context=True)
            speak.interruptible = False
            frames.append(speak)
        frames.append(EndFrame(reason=self.runtime.end_reason))
        await self.worker.queue_frames(frames)

    async def end_with(self, status: str, reason: str, text: str | None) -> None:
        if self.runtime.end_status is not None and self._ending:
            return
        await self.runtime.finish(status, reason, text)
        await self.speak_and_end(text)

    # ------------------------------------------------------------------ lifecycle

    async def start_flow(self) -> None:
        if self._started:
            return
        self._started = True
        await self._flush_outbox()
        g = self.ctx
        await self.send({"type": "session", "session_id": str(g.session_id), "bot_id": str(g.bot_id),
                         "mode": g.mode, "flow_version": g.flow_version})
        self._timers.append(asyncio.create_task(self._max_duration_timer()))
        try:
            first = await self.runtime.initial()
            if isinstance(first, Terminal):
                await self.flow_manager.initialize()
                await self.speak_and_end(first.text)
            else:
                await self.flow_manager.initialize(first)
        except Exception as e:  # noqa: BLE001
            logger.exception("flow start failed")
            self.recorder.event("error", self.runtime.current_node_id, {"message": f"flow start failed: {e}"})
            await self.end_with("error", "flow_start_failed", self.config.behavior.fallback_message)

    async def _max_duration_timer(self) -> None:
        try:
            await asyncio.sleep(self.config.behavior.max_call_duration_secs)
        except asyncio.CancelledError:
            return
        if self.runtime.end_status is None:
            self.recorder.event("end", self.runtime.current_node_id, {"reason": "max_duration"})
            await self.end_with("abandoned", "max_duration", MAX_DURATION_MESSAGE)

    async def _client_ready_grace(self) -> None:
        try:
            await asyncio.sleep(CLIENT_READY_GRACE_SECS)
        except asyncio.CancelledError:
            return
        if not self._started:
            logger.info("client-ready not received; starting flow anyway")
            await self.start_flow()

    async def on_idle(self) -> None:
        if self.runtime.end_status is not None or self._ending:
            return
        self._idle_nudges += 1
        max_nudges = self.config.behavior.idle_max_nudges
        node = self.runtime.current_node_id
        if self._idle_nudges <= max_nudges:
            self.recorder.event("idle", node, {"nudge": self._idle_nudges})
            await self.send({"type": "event", "event": "idle", "payload": {"nudge": self._idle_nudges}})
            from pipecat.frames.frames import LLMMessagesAppendFrame

            await self.worker.queue_frame(
                LLMMessagesAppendFrame(messages=[{"role": "system", "content": IDLE_NUDGE_PROMPT}], run_llm=True)
            )
        else:
            self.recorder.event("idle", node, {"nudge": self._idle_nudges, "ended": True})
            await self.end_with("abandoned", "idle_timeout", IDLE_END_MESSAGE)

    async def on_error(self, frame: Any) -> None:
        proc = getattr(frame, "processor", None)
        role = None
        for name, svc in (("stt", self.stt), ("llm", self.llm), ("tts", self.tts)):
            if proc is svc:
                role = name
        msg = str(getattr(frame, "error", frame))
        self.recorder.event("error", self.runtime.current_node_id, {"provider": role, "message": msg[:500]})
        await self.send({"type": "event", "event": "error", "payload": {"provider": role, "message": msg[:300]}})
        if role is None or self._ending:
            return
        self._provider_errors[role] = self._provider_errors.get(role, 0) + 1
        unusable = not getattr(proc, "is_usable", True) or getattr(frame, "fatal", False)
        if not unusable and self._provider_errors[role] < 3:
            return
        await self.runtime.finish("error", f"{role}_error", None)
        if role == "tts":
            # TTS is down: cannot speak the fallback message
            self._ending = True
            from pipecat.frames.frames import EndFrame

            await self.worker.queue_frame(EndFrame(reason=f"{role}_error"))
        else:
            await self.speak_and_end(self.config.behavior.fallback_message)

    async def finalize(self) -> None:
        if self._finalized:
            return
        self._finalized = True
        for t in self._timers:
            t.cancel()
        status = self.runtime.end_status or "abandoned"
        reason = self.runtime.end_reason or "client_disconnected"
        if self.runtime.end_status is None:
            self.runtime.end_status, self.runtime.end_reason = status, reason
            self.recorder.event("end", self.runtime.current_node_id, {"status": status, "end_reason": reason})
        await self.recorder.finish(status, reason, self.runtime.vars)
        logger.info(f"call {self.ctx.session_id} finished: {status} ({reason})")

    def _register_handlers(self) -> None:
        transport, worker = self.transport, self.worker

        @transport.event_handler("on_client_connected")
        async def _on_connected(_transport, _client):
            self._timers.append(asyncio.create_task(self._client_ready_grace()))

        @transport.event_handler("on_client_disconnected")
        async def _on_disconnected(_transport, _client):
            if self.runtime.end_status is None:
                self.runtime.end_status, self.runtime.end_reason = "abandoned", "client_disconnected"
            await worker.cancel(reason="client disconnected")

        rtvi = getattr(worker, "_rtvi", None)
        if rtvi is not None:
            @rtvi.event_handler("on_client_ready")
            async def _on_ready(_rtvi):
                await self.start_flow()

        @self.user_agg.event_handler("on_user_turn_idle")
        async def _on_idle(_agg):
            await self.on_idle()

        @self.user_agg.event_handler("on_user_turn_started")
        async def _on_user_started(_agg, _strategy):
            self._idle_nudges = 0

        @self.user_agg.event_handler("on_user_turn_stopped")
        async def _on_user_stopped(_agg, _strategy, message):
            text = getattr(message, "content", None)
            if text:
                self.runtime.note_user_turn(text)
                self.recorder.event("user_transcript", self.runtime.current_node_id, {"text": text})

        @self.assistant_agg.event_handler("on_assistant_turn_stopped")
        async def _on_bot_stopped(_agg, message):
            text = getattr(message, "content", None)
            if text:
                self.recorder.event("bot_transcript", self.runtime.current_node_id,
                                    {"text": text, "interrupted": bool(getattr(message, "interrupted", False))})

        @worker.event_handler("on_pipeline_error")
        async def _on_error(_worker, frame):
            await self.on_error(frame)

        @worker.event_handler("on_pipeline_finished")
        async def _on_finished(_worker, _frame):
            await self.finalize()

    async def run(self) -> None:
        from pipecat.workers.runner import WorkerRunner

        await self.recorder.start()
        runner = WorkerRunner(handle_sigint=False)
        try:
            await runner.add_workers(self.worker)
            await runner.run()
        except Exception as e:  # noqa: BLE001
            logger.exception("call pipeline crashed")
            if self.runtime.end_status is None:
                self.runtime.end_status, self.runtime.end_reason = "error", f"pipeline_crash: {e}"[:100]
        finally:
            await self.finalize()


def build_transport(webrtc_connection: Any):
    from pipecat.transports.base_transport import TransportParams
    from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

    return SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(audio_in_enabled=True, audio_out_enabled=True),
    )


def build_call(ctx: CallContext, transport: Any, **kwargs) -> Call:
    """Construct a :class:`Call` without running it (raises MissingProviderKey early)."""
    return Call(ctx, transport, **kwargs)


async def run_call(ctx: CallContext, webrtc_connection: Any, **kwargs) -> None:
    call = build_call(ctx, build_transport(webrtc_connection), **kwargs)
    await call.run()
