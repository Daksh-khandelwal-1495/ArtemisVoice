"""Text driver: runs the REAL compiled flow (``FlowRuntime`` + ``compile_node`` configs) turn by
turn with text input, mirroring pipecat.flows semantics without audio:

* ``role_message`` -> system instruction; ``task_messages`` appended / reset per
  ``context_strategy`` (``reset_with_summary`` asks the LLM for a summary, falls back to append);
* ``pre_actions`` ``tts_say`` are spoken (and kept in context), ``post_actions``
  ``end_conversation`` ends the call; ``respond_immediately`` runs the LLM on node entry;
* function handlers return ``(result, next)``: ``None`` -> LLM runs again with the result,
  a NodeConfig -> transition, ``NO_RESPONSE`` -> silence (terminal nodes speak via the hooks);
* node functions + the global functions (``request_human_agent`` / ``end_call``) are offered.

Grounding is on (``require_grounding=True``) and ``note_user_turn`` receives exactly what the bot
"heard" (the STT final hypothesis). All side effects go through :class:`SandboxTools`.

Every caller turn produces a contract §1 trace turn (six layers, text/plain artifacts with the
sha256 of the text) recorded through :class:`app.tracing.collector.TraceCollector` when given.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from loguru import logger

from app.replay.faults import FaultProfile, llm_swap_args, tts_speak
from app.replay.llm import LLMClient, ToolCall, request_params, trace_settings
from app.replay.oracle import database_state, graph_fields
from app.replay.sandbox import SandboxTools
from app.schemas.flow import FlowGraph, FlowNode
from app.schemas.providers import BotConfig
from app.voice.compiler import FlowRuntime, RuntimeHooks, Terminal, tool_name

INTERVENTIONS = ("control", "stt_oracle", "endpointing_oracle", "llm_oracle", "tools_oracle", "tts_oracle")
MAX_LLM_CALLS_PER_TURN = 16


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _compact(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def grounded_in(value: Any, texts: list[str]) -> bool:
    """True if ``value`` was literally said (alphanumerics-compact match) in ``texts``."""
    v = _compact(value)
    if not v:
        return False
    return v in _compact(" ".join(texts))


@dataclass
class TurnInput:
    index: int
    truth: str | None  # what the caller actually said (oracle truth / intended text)
    segment: str  # what endpointing delivered to STT
    heard: str  # STT final hypothesis = what the bot hears
    truncated: bool = False
    stt_applied: list[dict[str, str]] = field(default_factory=list)
    derived_from: str = "simulation"  # simulation | recorded | oracle


class _DriverHooks(RuntimeHooks):
    def __init__(self, driver: "TextDriver"):
        self.d = driver

    def _rec(self, *a, **kw) -> None:
        if self.d.recorder is not None:
            try:
                self.d.recorder.event(*a, **kw)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"driver recorder event failed: {e}")

    async def node_entered(self, node: FlowNode) -> None:
        self._rec("node_enter", node.id, {"label": node.label, "node_type": node.type})
        if self.d.recorder is not None:
            self.d.recorder.update(current_node=node.id)

    async def event(self, type: str, node_id: str | None, payload: dict[str, Any]) -> None:
        self._rec(type, node_id, payload)

    async def field_captured(self, node_id: str, name: str, value: Any, valid: bool, attempts: int) -> None:
        if self.d.recorder is not None:
            self.d.recorder.field(node_id, name, value, valid, attempts)

    async def vars_changed(self, masked_vars: dict[str, Any], raw_vars: dict[str, Any]) -> None:
        if self.d.recorder is not None:
            self.d.recorder.update(variables=raw_vars)

    async def lookup_rows(self, dataset: str, filters: dict[str, Any], mode: str, limit: int) -> list[dict[str, Any]]:
        return await self.d.tools.lookup(dataset, filters, mode, limit)

    async def mock_api_call(self, node_id: str, method: str, url: str, body: Any, mock_result: Any) -> Any:
        return await self.d.tools.mock_api_call(node_id, method, url, body, mock_result)

    async def http_request(self, method: str, url: str, headers: dict[str, str], body: Any, timeout: float) -> Any:
        return await self.d.tools.http_request(method, url, headers, body, timeout)

    async def end_call(self, flow_manager: Any, text: str | None) -> None:
        await self.d._terminal(text)


class TextDriver:
    def __init__(
        self,
        *,
        graph: FlowGraph,
        config: BotConfig,
        llm: LLMClient,
        tools: SandboxTools,
        seed: int,
        session_id: str,
        bot_name: str = "",
        recorder: Any = None,
        collector: Any = None,
        profile: FaultProfile | None = None,
        intervention: str = "control",
        oracle: dict[str, Any] | None = None,
        initial_state: dict[str, Any] | None = None,
        today: date | None = None,
        synthesizer: Any = None,
    ):
        if intervention not in INTERVENTIONS:
            raise ValueError(f"unknown intervention {intervention!r}")
        self.graph, self.config, self.llm, self.tools = graph, config, llm, tools
        self.seed, self.session_id = seed, str(session_id)
        self.recorder, self.collector = recorder, collector
        self.synthesizer = synthesizer  # app.replay.audio.Synthesizer: optional WAV rendering of each turn
        self.profile, self.intervention, self.oracle = profile, intervention, oracle or {}
        self.known_values = dict(self.oracle.get("expected_fields") or {})
        if today is None and initial_state and initial_state.get("today"):
            try:
                today = date.fromisoformat(str(initial_state["today"]))
            except ValueError:
                today = None
        self.today = today
        self.runtime = FlowRuntime(
            graph=graph, config=config, hooks=_DriverHooks(self), api_call_mock=True,
            today_fn=(lambda: today) if today else None, bot_name=bot_name, session_id=self.session_id,
            require_grounding=True,
        )
        if initial_state:
            self.runtime.vars = copy.deepcopy(initial_state)
            self.runtime._refresh_builtins()
        self.initial_state = copy.deepcopy(self.runtime.vars)
        self.llm_params = request_params(config.llm, seed)
        self.llm_trace_settings = trace_settings(config.llm, seed)
        self.globals = self.runtime.global_functions()
        self.system = ""
        self.messages: list[dict[str, Any]] = []
        self.functions: list[Any] = []
        self.handlers: dict[str, Any] = {}
        self.ended = False
        self.spoken_log: list[dict[str, Any]] = []  # every utterance: {turn, node_id, text, spoken}
        self.greeting_spoken: list[str] = []
        self.turn_records: list[dict[str, Any]] = []
        self._call_seq = 0
        self._turn: dict[str, Any] | None = None

    # ------------------------------------------------------------------ public API

    @property
    def status(self) -> str:
        return self.runtime.end_status or "active"

    def db_state(self) -> dict[str, str]:
        return database_state(self.graph, self.runtime.vars, end_node=self.runtime.current_node_id,
                              status=self.status, captured=self.runtime.captured)

    async def start(self) -> None:
        first = await self.runtime.initial()
        await self._set_node(first)

    async def run(self, turns: list[TurnInput]) -> None:
        await self.start()
        for i, t in enumerate(turns):
            if self.ended:
                break
            await self.run_turn(t, last=i == len(turns) - 1)

    async def run_turn(self, ti: TurnInput, *, last: bool = False) -> dict[str, Any]:
        rt = self.runtime
        node_before = rt.current_node_id
        state_before = copy.deepcopy(rt.vars)
        db_before = self.db_state()
        self._turn = {"index": ti.index, "llm_calls": 0, "context_hash": None, "message_count": None,
                      "texts": [], "tool_calls": [], "tts_inputs": [], "spoken": [], "submits": set()}
        if self.collector is not None:
            self.collector.begin_turn(ti.index, node_before, state_before)
        t0 = _now()
        heard = (ti.heard or "").strip()
        t_llm0 = _now()
        if heard and not self.ended:
            rt.note_user_turn(heard)
            self.messages.append({"role": "user", "content": heard})
            if self.recorder is not None:
                self.recorder.event("user_transcript", rt.current_node_id, {"text": heard})
            await self._run_llm()
            if self.intervention == "llm_oracle" and not self.ended:
                await self._oracle_missing_submit(heard)
        if self._turn["context_hash"] is None:
            self._turn["context_hash"], self._turn["message_count"] = self._context_hash()
        if last and not self.ended and rt.end_status is None:
            await rt.finish("abandoned", "caller_script_exhausted", None)
        t_end = _now()
        calls, results = self.tools.drain()
        state_after = copy.deepcopy(rt.vars)
        db_after = self.db_state()
        record = await self._build_turn(ti, node_before, state_before, state_after, db_before, db_after,
                                        calls, results, t0, t_llm0, t_end)
        self.turn_records.append(record)
        if self.collector is not None:
            for name, lay in record["layers"].items():
                self.collector.record_layer(name, **lay)
            self.collector.end_turn(state_after)
        self._turn = None
        return record

    def outcome(self) -> dict[str, Any]:
        from app.replay.oracle import captured_fields, lookup_results

        final_spoken = self.turn_records[-1]["layers"]["text_to_speech"]["output"]["spoken_text"] if self.turn_records else (
            " ".join(self.greeting_spoken))
        return {
            "captured": captured_fields(self.graph, self.runtime.vars, self.runtime.captured),
            "end_node": self.runtime.current_node_id,
            "status": self.status,
            "end_reason": self.runtime.end_reason,
            "spoken_texts": [s["spoken"] for s in self.spoken_log],
            "final_spoken_text": final_spoken,
            "lookups": lookup_results(self.graph, self.runtime.vars),
            "turns": len(self.turn_records),
        }

    # ------------------------------------------------------------------ flows semantics

    def _tool_specs(self) -> list[dict[str, Any]]:
        return [
            {"type": "function", "function": {
                "name": f.name, "description": f.description,
                "parameters": {"type": "object", "properties": dict(f.properties or {}), "required": list(f.required or [])},
            }}
            for f in self.functions
        ]

    def _context_hash(self) -> tuple[str, int]:
        payload = {"system": self.system, "messages": self.messages,
                   "tools": self._tool_specs(), "llm": self.llm_params}
        return sha256_text(_canonical(payload)), len(self.messages)

    async def _set_node(self, cfg: Any) -> None:
        if cfg is None or self.ended:
            return
        if isinstance(cfg, Terminal):
            await self._terminal(cfg.text)
            return
        for action in cfg.get("pre_actions") or []:
            if action.get("type") == "tts_say" and action.get("text"):
                self._speak(action["text"], from_llm=False)
            elif action.get("type") == "end_conversation":
                await self._terminal(None)
                return
        if cfg.get("role_message"):
            self.system = cfg["role_message"]
        task = copy.deepcopy(cfg.get("task_messages") or [])
        strategy = getattr(getattr(cfg.get("context_strategy"), "strategy", None), "value",
                           getattr(cfg.get("context_strategy"), "strategy", None))
        strategy = str(strategy or "append").lower()
        if strategy == "reset":
            self.messages = task
        elif strategy == "reset_with_summary":
            summary = await self._summary(cfg.get("context_strategy"))
            if summary:
                self.messages = [{"role": "system", "content": f"Conversation so far: {summary}"}] + task
            else:
                self.messages.extend(task)
        else:
            self.messages.extend(task)
        self.functions = list(cfg.get("functions") or []) + list(self.globals)
        self.handlers = {f.name: f.handler for f in self.functions}
        if cfg.get("respond_immediately", True):
            await self._run_llm()
        for action in cfg.get("post_actions") or []:
            if action.get("type") == "end_conversation":
                await self._terminal(None)

    async def _summary(self, strategy_cfg: Any) -> str | None:
        prompt = getattr(strategy_cfg, "summary_prompt", None) or "Summarize the conversation."
        try:
            res = await self.llm.complete(system=prompt, messages=copy.deepcopy(self.messages), tools=[],
                                          settings=self.llm_params)
            return (res.text or "").strip() or None
        except Exception as e:  # noqa: BLE001 - mirror pipecat: fall back to APPEND
            logger.warning(f"summary failed, appending instead: {e}")
            return None

    async def _run_llm(self) -> None:
        while not self.ended:
            if self._turn is not None:
                if self._turn["llm_calls"] >= MAX_LLM_CALLS_PER_TURN:
                    logger.warning("driver: LLM call budget for this turn exhausted")
                    return
            again = await self._llm_round()
            if not again:
                return

    async def _llm_round(self) -> bool:
        ctx_hash, count = self._context_hash()
        if self._turn is not None:
            self._turn["llm_calls"] += 1
            if self._turn["context_hash"] is None:
                self._turn["context_hash"], self._turn["message_count"] = ctx_hash, count
        res = await self.llm.complete(system=self.system, messages=copy.deepcopy(self.messages),
                                      tools=self._tool_specs(), settings=self.llm_params)
        text = (res.text or "").strip()
        calls = [ToolCall(id=self._next_call_id(), name=c.name, arguments=dict(c.arguments or {}))
                 for c in res.tool_calls]
        raw_args = [dict(c.arguments) for c in calls]
        for c in calls:
            c.arguments, applied = self._filter_args(c.name, c.arguments)
            c.applied = applied  # type: ignore[attr-defined]
        msg: dict[str, Any] = {"role": "assistant", "content": text or None}
        if calls:
            msg["tool_calls"] = [{"id": c.id, "type": "function",
                                  "function": {"name": c.name, "arguments": _canonical(c.arguments)}} for c in calls]
        if text or calls:
            self.messages.append(msg)
        if text:
            if self._turn is not None:
                self._turn["texts"].append(text)
            self._speak(text, from_llm=True)
        if not calls:
            return False
        again = await self._process_calls(calls, raw_args)
        # The bot already asked the caller something this round: wait for the answer rather than
        # generating another reply (which repeats the question).
        if again and text.rstrip().endswith(("?", "؟")):
            return False
        return again

    def _next_call_id(self) -> str:
        self._call_seq += 1
        return f"call_{self._call_seq:04d}"

    async def _process_calls(self, calls: list[ToolCall], raw_args: list[dict[str, Any]] | None = None,
                             *, synthesized: bool = False) -> bool:
        from pipecat.flows import NO_RESPONSE

        run_again = False
        pending = None
        for i, c in enumerate(calls):
            handler = self.handlers.get(c.name)
            nxt = None
            if handler is None:
                result: Any = {"status": "error", "error": f"unknown function {c.name}"}
                run_again = True
            else:
                try:
                    out = await handler(dict(c.arguments), self)
                except Exception as e:  # noqa: BLE001 - pipecat returns errors to the LLM
                    logger.warning(f"driver: handler {c.name} failed: {e}")
                    out = ({"status": "error", "error": str(e)}, None)
                result, nxt = out if isinstance(out, tuple) else (out, None)
                if result is None:
                    result = {"status": "acknowledged"}
            self.messages.append({"role": "tool", "tool_call_id": c.id, "content": _canonical(result)})
            if self._turn is not None:
                entry = {"name": c.name, "arguments": c.arguments, "result": result}
                if raw_args is not None and raw_args[i] != c.arguments:
                    entry["llm_arguments"] = raw_args[i]
                if getattr(c, "applied", None):
                    entry["intervention"] = c.applied
                if synthesized:
                    entry["synthesized_by"] = "llm_oracle"
                self._turn["tool_calls"].append(entry)
                if c.name.startswith("submit_"):
                    self._turn["submits"].add(c.name)
            if nxt is NO_RESPONSE:
                continue
            if nxt:
                pending = nxt
            else:
                run_again = True
        if self.ended:
            return False
        if pending is not None:
            await self._set_node(pending)
            return False
        return run_again

    # ------------------------------------------------------------------ layer behaviour

    def _filter_args(self, name: str, args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """LLM fault (system under test) or the LLM oracle's correction of tool arguments."""
        if self.intervention != "llm_oracle":
            return llm_swap_args(args, self.profile, vars=self.runtime.vars, known_values=self.known_values)
        node = self.runtime.current_node
        if node is None or not node.fields or name != tool_name("submit_", node.id):
            return args, None
        heard = list(self.runtime.user_utterances)
        changed = {}
        out = dict(args)
        for f in node.fields:
            exp = self.known_values.get(f.name)
            if exp is None or f.name in self.runtime.captured:
                continue
            if grounded_in(exp, heard) and str(out.get(f.name, "")) != str(exp):
                changed[f.name] = {"from": out.get(f.name), "to": exp}
                out[f.name] = exp
        return out, ({"llm_oracle_corrections": changed} if changed else None)

    async def _oracle_missing_submit(self, heard: str) -> None:
        node = self.runtime.current_node
        if node is None or not node.fields:
            return
        submit = tool_name("submit_", node.id)
        if submit in (self._turn or {}).get("submits", set()) or submit not in self.handlers:
            return
        args: dict[str, Any] = {}
        for f in node.fields:
            exp = self.known_values.get(f.name)
            if exp is not None and f.name not in self.runtime.captured and grounded_in(exp, [heard]):
                args[f.name] = exp
        if not args:
            return
        for f in node.fields:
            args.setdefault(f.name, "")
        call = ToolCall(id=self._next_call_id(), name=submit, arguments=args)
        self.messages.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": call.id, "type": "function", "function": {"name": submit, "arguments": _canonical(args)}}]})
        if await self._process_calls([call], synthesized=True):
            await self._run_llm()

    def _speak(self, text: str, *, from_llm: bool) -> None:
        spoken = text if self.intervention == "tts_oracle" else tts_speak(text, self.profile)[0]
        if not from_llm:
            self.messages.append({"role": "assistant", "content": text})
        entry = {"turn": self._turn["index"] if self._turn else None, "node_id": self.runtime.current_node_id,
                 "text": text, "spoken": spoken}
        self.spoken_log.append(entry)
        if self._turn is not None:
            self._turn["tts_inputs"].append(text)
            self._turn["spoken"].append(spoken)
        else:
            self.greeting_spoken.append(spoken)
        if self.recorder is not None:
            self.recorder.event("bot_transcript", self.runtime.current_node_id, {"text": spoken, "interrupted": False})

    async def _terminal(self, text: str | None) -> None:
        if self.ended:
            return
        self.ended = True
        if text:
            self._speak(text, from_llm=False)

    # ------------------------------------------------------------------ trace

    async def _artifact(self, name: str, text: str) -> dict[str, Any]:
        data = (text or "").encode("utf-8")
        if self.collector is not None:
            try:
                return dict(await self.collector.save_artifact(name, data, "text/plain"))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"driver: artifact save failed: {e}")
        return {"uri": f"artifact://{self.session_id}/{name}", "sha256": hashlib.sha256(data).hexdigest(),
                "media_type": "text/plain", "bytes": len(data)}

    async def _audio_artifact(self, name: str, text: str, role: str) -> dict[str, Any] | None:
        """Synthesized WAV for ``text`` (simulations only); None when disabled or synthesis fails."""
        if self.synthesizer is None or self.collector is None or not (text or "").strip():
            return None
        data = await self.synthesizer(text, role)
        if not data:
            return None
        try:
            return dict(await self.collector.save_artifact(name, data, "audio/wav"))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"driver: audio artifact save failed: {e}")
            return None

    async def _build_turn(self, ti: TurnInput, node_before, state_before, state_after, db_before, db_after,
                          calls, results, t0, t_llm0, t_end) -> dict[str, Any]:
        i = ti.index
        cfg = self.config
        t = self._turn or {}
        caller_text = ti.truth if ti.truth is not None else ti.heard
        caller_art = await self._artifact(f"turn-{i:03d}-caller.txt", caller_text or "")
        seg_art = await self._artifact(f"turn-{i:03d}-segment.txt", ti.segment or "")
        tts_text = " ".join(t.get("tts_inputs") or [])
        spoken = " ".join(t.get("spoken") or [])
        tts_art = await self._artifact(f"turn-{i:03d}-tts.txt", spoken)
        # Optional synthesized speech: audio becomes the primary artifact, text stays as the transcript.
        caller_wav = await self._audio_artifact(f"turn-{i:03d}-caller.wav", caller_text or "", "caller")
        seg_wav = caller_wav if (ti.segment or "") == (caller_text or "") else \
            await self._audio_artifact(f"turn-{i:03d}-segment.wav", ti.segment or "", "caller")
        tts_wav = await self._audio_artifact(f"turn-{i:03d}-tts.wav", spoken, "bot")
        synthesized = {"audio_synthesized": True} if caller_wav or tts_wav else {}
        if caller_wav:
            caller_art, caller_txt = caller_wav, caller_art
        else:
            caller_txt = None
        if seg_wav:
            seg_art = seg_wav
        if tts_wav:
            tts_art, tts_txt = tts_wav, tts_art
        else:
            tts_txt = None
        words = (ti.segment or "").split()
        interim = [" ".join(words[:k]) for k in range(1, len(words))]
        tool_calls = t.get("tool_calls") or []
        primary = next((c for c in tool_calls if c["name"].startswith("submit_")), tool_calls[0] if tool_calls else None)
        ts0, ts1 = t0.isoformat(), t_end.isoformat()
        speech_end = 250 + 55 * len(ti.segment or "")
        layers = {
            "caller": {
                "started_at": ts0, "finished_at": ts0, "input": {"turn_index": i},
                "output": {"intended_text": ti.truth, "audio": caller_art, **synthesized,
                           **({"transcript_artifact": caller_txt} if caller_txt else {})},
                "settings": {"simulated": True, "derived_from": ti.derived_from},
            },
            "endpointing": {
                "started_at": ts0, "finished_at": ts0, "input": {"audio": caller_art},
                "output": {"speech_start_ms": 0, "speech_end_ms": speech_end, "interrupted_bot": False,
                           "segment_text": ti.segment, "truncated": ti.truncated},
                "settings": {"vad": cfg.vad.model_dump(mode="json"), "simulated": True},
            },
            "speech_to_text": {
                "started_at": ts0, "finished_at": t_llm0.isoformat(), "input": {"audio": seg_art},
                "output": {"interim_hypotheses": interim, "final_hypothesis": ti.heard,
                           "confidence": 0.8 if ti.stt_applied else 1.0, "applied_faults": ti.stt_applied},
                "settings": {"provider": cfg.stt.provider, "model": cfg.stt.model, "language": cfg.stt.language,
                             "keywords": list(cfg.stt.keywords), "simulated": True},
            },
            "llm": {
                "started_at": t_llm0.isoformat(), "finished_at": ts1,
                "input": {"context_hash": t.get("context_hash"), "message_count": t.get("message_count"),
                          "node_id": node_before},
                "output": {"response_text": " ".join(t.get("texts") or []), "tool_calls": tool_calls,
                           "tool_name": primary["name"] if primary else None,
                           "arguments": primary["arguments"] if primary else {}},
                "settings": dict(self.llm_trace_settings),
            },
            "tools": {
                "started_at": t_llm0.isoformat(), "finished_at": ts1, "input": {"calls": calls},
                "output": {"results": results, "state_before": state_before, "state_after": state_after,
                           "database_state_before": db_before, "database_state": db_after},
                "settings": {"api_call_mock": True, "sandbox": True,
                             "tools_oracle": self.intervention == "tools_oracle"},
            },
            "text_to_speech": {
                "started_at": ts1, "finished_at": ts1, "input": {"text": tts_text},
                "output": {"spoken_text": spoken, "audio": tts_art, **synthesized,
                           **({"transcript_artifact": tts_txt} if tts_txt else {})},
                "settings": {"provider": cfg.tts.provider, "model": cfg.tts.model, "voice": cfg.tts.voice,
                             "speed": cfg.tts.speed, "sample_rate": cfg.tts.sample_rate, "simulated": True},
            },
        }
        return {"index": i, "node_id": node_before, "state_before": state_before, "state_after": state_after,
                "layers": layers, "end_node": self.runtime.current_node_id}


def graph_field_types(graph: FlowGraph) -> dict[str, str]:
    return {k: f.type for k, f in graph_fields(graph).items()}
