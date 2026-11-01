"""FlowGraph -> pipecat.flows NodeConfig compiler + runtime state machine.

A :class:`FlowRuntime` owns everything about one call's flow state (captured vars, per-field
attempts, pending confirmations, end status). Nodes are compiled lazily on entry so prompts
see the latest variables. All side effects (DB, RTVI, HTTP, ending the pipeline) go through a
:class:`RuntimeHooks` object, which makes this module testable without audio or a database:
handlers are plain ``async (args, flow_manager)`` callables and the tests drive them with a
fake flow manager.

Node semantics (see CONTRACT.md):

* ``start`` / ``conversation``: LLM talks; one ``goto_<target>`` tool per intent /
  on_complete / fallback transition. Fields on these nodes get a submit tool too.
* ``collect``: one ``submit_<node_id>`` tool; server-side validation, partial submissions,
  per-field retries, confirm read-back; then expression (by priority) -> on_complete -> fallback.
* ``decision`` / ``lookup`` / ``api_call``: no user turn; resolved immediately on entry and the
  runtime keeps walking until it reaches a node that needs the user (or a terminal node).
* ``transfer`` / ``end``: speak, record status, end the pipeline.
* Global tools on every node: ``request_human_agent`` and ``end_call``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable

from loguru import logger

from app.schemas.flow import CaptureField, FlowGraph, FlowNode, Transition
from app.schemas.providers import BotConfig
from app.voice.expr import evaluate
from app.voice.validators import is_empty, mask_value, validate_field

try:
    from zoneinfo import ZoneInfo

    _IST = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover
    _IST = None

MAX_AUTO_HOPS = 25
BUILTIN_VARS = {"bot_name", "session_id", "today", "now", "current_date", "current_time", "caller_number"}
MAX_TOOL_NAME = 64

VOICE_RULES = (
    "You are speaking with a caller on a live voice call. Your replies are converted to speech, so: "
    "keep each reply short (one or two sentences), warm and natural; ask one question at a time; "
    "never use markdown, bullet points, emojis or special characters; say numbers, dates and "
    "amounts the way a person would speak them. Never invent facts, prices, availability or "
    "policies that are not given to you. Use the provided functions to record information and "
    "move the conversation forward, and never mention functions, tools or system messages to the "
    "caller. If the caller asks for a human, call request_human_agent. If the caller clearly wants "
    "to stop or hang up, call end_call."
)

DEFAULT_GOODBYE = "Thank you for your time. Goodbye!"
DEFAULT_TRANSFER = "Please hold while I connect you to one of our team members."
FAILURE_END_MESSAGE = "I'm sorry, I wasn't able to complete this. Someone from our team will get in touch with you. Goodbye!"
NO_AGENT_MESSAGE = "I'm sorry, no one is available to take your call right now. We will call you back soon. Goodbye!"
END_CALL_MESSAGE = "No problem. Thank you for your time, goodbye!"

# --------------------------------------------------------------------------- templating

_PLACEHOLDER = re.compile(r"(\\?)\{\{\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)\s*\}\}")


def _lookup_path(vars: dict[str, Any], path: str) -> tuple[bool, Any]:
    cur: Any = vars
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return False, None
    return True, cur


def _to_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=str)
    return str(v)


def render(text: str | None, vars: dict[str, Any]) -> str:
    """Replace ``{{var}}`` / ``{{obj.key}}`` placeholders; missing values render as ''.

    The output never contains ``{{ident}}`` placeholders (pipecat.flows would try to render
    them again from its own state and fail on missing keys), so braces coming from values
    are neutralised.
    """
    if not text:
        return ""

    def sub(m: re.Match) -> str:
        if m.group(1):  # escaped -> literal, but neutralised for pipecat's renderer
            return "{ {" + m.group(0)[3:]
        found, v = _lookup_path(vars, m.group(2))
        return _to_text(v).replace("{{", "{ {") if found else ""

    return _PLACEHOLDER.sub(sub, text)


def render_obj(obj: Any, vars: dict[str, Any]) -> Any:
    """Recursively render a JSON-ish template. A string that is exactly one placeholder keeps
    the variable's native type (numbers stay numbers in API bodies)."""
    if isinstance(obj, str):
        m = _PLACEHOLDER.fullmatch(obj.strip())
        if m and not m.group(1):
            found, v = _lookup_path(vars, m.group(2))
            return v if found else None
        return render(obj, vars)
    if isinstance(obj, dict):
        return {k: render_obj(v, vars) for k, v in obj.items()}
    if isinstance(obj, list):
        return [render_obj(v, vars) for v in obj]
    return obj


# --------------------------------------------------------------------------- pure helpers


def tool_name(prefix: str, ident: str, taken: set[str] | None = None) -> str:
    base = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{prefix}{ident}")[:MAX_TOOL_NAME]
    name = base
    i = 2
    while taken is not None and name in taken:
        suffix = f"_{i}"
        name = base[: MAX_TOOL_NAME - len(suffix)] + suffix
        i += 1
    if taken is not None:
        taken.add(name)
    return name


def _sorted_expr(node: FlowNode) -> list[Transition]:
    exprs = [t for t in node.transitions if t.kind == "expression"]
    return sorted(exprs, key=lambda t: t.priority)  # stable: ties keep authoring order


def _first(node: FlowNode, kind: str) -> Transition | None:
    return next((t for t in node.transitions if t.kind == kind), None)


def select_transition(node: FlowNode, vars: dict[str, Any], *, prefer_fallback: bool = False) -> Transition | None:
    """First matching expression (by priority), else on_complete, else fallback.

    ``prefer_fallback`` (lookup not found / api error) picks fallback before on_complete.
    """
    for t in _sorted_expr(node):
        if t.condition and evaluate(t.condition, vars):
            return t
    if prefer_fallback and (fb := _first(node, "fallback")):
        return fb
    return _first(node, "on_complete") or _first(node, "fallback")


def filter_rows(rows: list[dict[str, Any]], filters: dict[str, Any], mode: str = "exact", limit: int = 5) -> list[dict[str, Any]]:
    """Pure dataset matching used by lookup nodes (case-insensitive)."""
    def norm(v: Any) -> str:
        return re.sub(r"\s+", " ", _to_text(v).strip().lower())

    wanted = {col: norm(v) for col, v in filters.items() if not is_empty(v)}
    if not wanted:
        return []
    out = []
    for row in rows:
        ok = True
        for col, val in wanted.items():
            cell = norm(row.get(col))
            if mode == "icontains":
                if val not in cell and cell not in val or not cell:
                    ok = False
                    break
            elif cell != val:
                ok = False
                break
        if ok:
            out.append(row)
            if len(out) >= limit:
                break
    return out


_JSON_TYPES = {"boolean": "boolean", "integer": "integer", "number": "number"}

_TYPE_HINTS = {
    "phone": "10-digit Indian mobile number",
    "email": "email address",
    "pincode": "6-digit Indian PIN code",
    "pan": "PAN card number, 10 characters like ABCDE1234F",
    "aadhaar_last4": "last 4 digits of Aadhaar",
    "amount": "amount in rupees; pass what the caller said, e.g. '5 lakh' or 500000",
    "vehicle_reg": "vehicle registration number like MH12AB1234",
    "date": "date; pass what the caller said, e.g. 'tomorrow', '15 August' or 2025-08-15",
    "boolean": "true for yes, false for no",
}


def field_schema(f: CaptureField) -> dict[str, Any]:
    desc = [f.label]
    if f.description:
        desc.append(f.description)
    if hint := _TYPE_HINTS.get(f.type):
        desc.append(f"Format: {hint}.")
    v = f.validation
    if f.type in ("number", "integer", "amount"):
        if v.min is not None:
            desc.append(f"Minimum {v.min:g}.")
        if v.max is not None:
            desc.append(f"Maximum {v.max:g}.")
    if f.type == "date" and v.future_only:
        desc.append("Must be today or later.")
    if f.type == "date" and v.past_only:
        desc.append("Must be in the past.")
    if f.example:
        desc.append(f"Example: {f.example}.")
    if not f.required:
        desc.append("Optional.")
    schema: dict[str, Any] = {"type": _JSON_TYPES.get(f.type, "string"), "description": " ".join(desc)}
    if f.type == "enum" and v.options:
        schema["enum"] = list(v.options)
    return schema


# --------------------------------------------------------------------------- hooks


class RuntimeHooks:
    """Side-effect sink. Default implementation does nothing (used by tests / preview)."""

    async def node_entered(self, node: FlowNode) -> None: ...

    async def event(self, type: str, node_id: str | None, payload: dict[str, Any]) -> None: ...

    async def field_captured(self, node_id: str, name: str, value: Any, valid: bool, attempts: int) -> None: ...

    async def validation(self, node_id: str, errors: dict[str, str]) -> None: ...

    async def vars_changed(self, masked_vars: dict[str, Any], raw_vars: dict[str, Any]) -> None: ...

    async def lookup_rows(self, dataset: str, filters: dict[str, Any], mode: str, limit: int) -> list[dict[str, Any]]:
        return []

    async def http_request(self, method: str, url: str, headers: dict[str, str], body: Any, timeout: float) -> Any:
        raise RuntimeError("HTTP not available")

    async def transfer(self, node_id: str, transfer_number: str | None) -> None: ...

    async def end_call(self, flow_manager: Any, text: str | None) -> None:
        """Speak ``text`` (if any) then end the pipeline."""

    async def ended(self, status: str, end_reason: str | None) -> None: ...


@dataclass
class Terminal:
    """Returned by :meth:`FlowRuntime.enter` for transfer/end nodes with a fixed message."""

    status: str
    end_reason: str | None
    text: str | None
    node_id: str | None = None


WAIT_FOR_CALLER = (
    "The caller has not said this. Never guess or assume values. "
    "Ask your question (if you have not already) and wait for the caller's answer."
)


@dataclass
class FlowRuntime:
    graph: FlowGraph
    config: BotConfig = field(default_factory=BotConfig)
    hooks: RuntimeHooks = field(default_factory=RuntimeHooks)
    api_call_mock: bool = True
    today_fn: Callable[[], date] | None = None
    bot_name: str = ""
    session_id: str = ""
    caller_number: str | None = None

    vars: dict[str, Any] = field(default_factory=dict)
    captured: set[str] = field(default_factory=set)
    skipped: set[str] = field(default_factory=set)
    field_failures: dict[str, int] = field(default_factory=dict)
    node_failures: dict[str, int] = field(default_factory=dict)
    pending_confirm: dict[str, Any] = field(default_factory=dict)
    current_node_id: str | None = None
    end_status: str | None = None
    end_reason: str | None = None
    history: list[str] = field(default_factory=list)
    # Live calls set this: tool calls must be grounded in what the caller actually said.
    require_grounding: bool = False
    user_utterances: list[str] = field(default_factory=list)
    _turns_at_entry: int = 0

    def __post_init__(self) -> None:
        self.nodes: dict[str, FlowNode] = {n.id: n for n in self.graph.nodes}
        self.vars = {**dict(self.graph.variables), **self.vars}
        self.sensitive: set[str] = {f.name for n in self.graph.nodes for f in n.fields if f.sensitive}
        self._refresh_builtins()

    def _refresh_builtins(self) -> None:
        """Built-in template vars (the flow validator treats these as always defined)."""
        now = datetime.now(_IST) if _IST else datetime.now()
        today = self.today()
        self.vars.update(
            bot_name=self.bot_name,
            session_id=self.session_id,
            caller_number=self.caller_number,
            today=today.isoformat(),
            current_date=today.strftime("%d %B %Y"),
            now=now.strftime("%Y-%m-%d %H:%M"),
            current_time=now.strftime("%I:%M %p"),
        )

    # ------------------------------------------------------------------ grounding

    def note_user_turn(self, text: str) -> None:
        if text and text.strip():
            self.user_utterances.append(text.strip())

    @property
    def caller_has_spoken(self) -> bool:
        return bool(self.user_utterances)

    def _is_grounded(self, value: Any) -> bool:
        """True if ``value`` plausibly comes from something the caller said."""
        if isinstance(value, bool):
            return True
        heard = " ".join(self.user_utterances).lower()
        heard_compact = re.sub(r"[^a-z0-9]", "", heard)
        heard_digits = re.sub(r"\D", "", heard)
        v = str(value).lower().strip()
        compact = re.sub(r"[^a-z0-9]", "", v)
        if not compact:
            return True
        if compact in heard_compact:
            return True
        digits = re.sub(r"\D", "", v)
        if digits and len(digits) >= 3 and digits in heard_digits:
            return True
        words = [w for w in re.findall(r"[a-z]{3,}", v)]
        return bool(words) and any(w in heard for w in words)

    # ------------------------------------------------------------------ utilities

    def today(self) -> date:
        if self.today_fn:
            return self.today_fn()
        return datetime.now(_IST).date() if _IST else date.today()

    @property
    def current_node(self) -> FlowNode | None:
        return self.nodes.get(self.current_node_id or "")

    def allows_interruptions(self) -> bool:
        node = self.current_node
        node_ok = node.settings.allow_interruptions if node else True
        return bool(self.config.behavior.allow_interruptions and node_ok)

    def silence_timeout(self) -> float:
        node = self.current_node
        if node and node.settings.silence_timeout_secs:
            return float(node.settings.silence_timeout_secs)
        return float(self.config.behavior.idle_timeout_secs)

    def masked_vars(self, *, force: bool = True) -> dict[str, Any]:
        """Vars with sensitive fields masked (always for the UI; logs honour the setting)."""
        if not force and not self.config.behavior.mask_sensitive_in_logs:
            return dict(self.vars)
        return {k: (mask_value(v) if k in self.sensitive else v) for k, v in self.vars.items()}

    def _mask_args(self, args: dict[str, Any]) -> dict[str, Any]:
        if not self.config.behavior.mask_sensitive_in_logs:
            return dict(args)
        return {k: (mask_value(v) if k in self.sensitive else v) for k, v in args.items()}

    def first_node_of_type(self, type_: str) -> FlowNode | None:
        return next((n for n in self.graph.nodes if n.type == type_), None)

    async def _safe(self, coro) -> Any:
        try:
            return await coro
        except Exception as e:  # noqa: BLE001 - hooks must never break the call
            logger.warning(f"runtime hook failed: {e}")
            return None

    async def _event(self, type_: str, node_id: str | None, payload: dict[str, Any]) -> None:
        await self._safe(self.hooks.event(type_, node_id, payload))

    async def _vars_changed(self) -> None:
        await self._safe(self.hooks.vars_changed(self.masked_vars(), dict(self.vars)))

    # ------------------------------------------------------------------ prompts

    def role_message(self) -> str:
        parts = []
        if self.graph.global_prompt:
            parts.append(render(self.graph.global_prompt, self.vars))
        parts.append(VOICE_RULES)
        parts.append(f"Today's date is {self.today().strftime('%A, %d %B %Y')}.")
        return "\n\n".join(parts)

    def _known_info(self, exclude: set[str] | None = None) -> str:
        exclude = exclude or set()
        items = []
        for k, v in self.vars.items():
            if k.startswith("_") or k in exclude or k in BUILTIN_VARS or v is None:
                continue
            txt = _to_text(v)
            if len(txt) > 600:
                txt = txt[:600] + "..."
            items.append(f"- {k}: {txt}")
        return "\n".join(items)

    def _fields_block(self, node: FlowNode) -> str:
        lines = []
        for f in node.fields:
            if f.name in self.captured or f.name in self.skipped:
                continue
            bits = [f"- {f.label} (`{f.name}`, {'required' if f.required else 'optional'})"]
            if f.description:
                bits.append(f": {f.description}")
            if f.type == "enum" and f.validation.options:
                bits.append(f" Options: {', '.join(f.validation.options)}.")
            if f.example:
                bits.append(f" Example: {f.example}.")
            lines.append("".join(bits))
        return "\n".join(lines)

    def task_content(self, node: FlowNode, *, say_text: str | None = None) -> str:
        parts = [f"CURRENT STEP: {node.label} ({node.type})."]
        if node.task_prompt:
            parts.append(render(node.task_prompt, self.vars))
        if say_text:
            parts.append(f'The caller has just heard you say: "{say_text}". Do not repeat it; continue from there.')
        submit = self._submit_name(node)
        if node.fields:
            remaining = self._fields_block(node)
            if remaining:
                parts.append(
                    "Collect the following from the caller, asking for one or two items at a time:\n" + remaining
                )
                parts.append(
                    f"Call `{submit}` as soon as the caller gives any of these values; you may submit only some of "
                    "them (pass an empty string for values not given yet). Pass values as the caller said them; the "
                    "system normalises formats. If the result has status 'invalid', briefly tell the caller what was "
                    "wrong and ask again only for those fields. If it lists missing fields, ask for those next. "
                    "Only submit values the caller actually said; never guess, assume or use the examples. "
                    "Ask, then wait for the caller's answer before calling the function."
                )
            else:
                parts.append(f"All information for this step is collected. Call `{submit}` to continue.")
            confirm = [f for f in node.fields if f.confirm and f.name not in self.captured]
            if confirm:
                labels = ", ".join(f.label for f in confirm)
                parts.append(
                    f"Before submitting {labels}, read the value back to the caller (spell letters and digits one by "
                    "one) and ask them to confirm it is correct. Set `confirmed` to true only after they clearly say "
                    "yes; if they say it is wrong, ask for it again."
                )
        if node.type in ("start", "conversation"):
            routes = [t for t in node.transitions if t.kind in ("intent", "on_complete", "fallback")]
            if routes:
                parts.append(
                    "When the caller's need matches one of the available goto functions, call it to move on."
                )
        info = self._known_info()
        if info:
            parts.append("Information known so far (use it, do not ask again):\n" + info)
        return "\n\n".join(parts)

    # ------------------------------------------------------------------ compile

    def _submit_name(self, node: FlowNode) -> str:
        return tool_name("submit_", node.id)

    def _context_strategy(self, node: FlowNode):
        from pipecat.flows import ContextStrategy, ContextStrategyConfig

        s = node.settings.context_strategy
        if s == "reset":
            return ContextStrategyConfig(strategy=ContextStrategy.RESET)
        if s == "reset_with_summary":
            return ContextStrategyConfig(
                strategy=ContextStrategy.RESET_WITH_SUMMARY,
                summary_prompt=(
                    "Summarize the conversation so far in a few sentences for the agent taking over, including "
                    "every detail the caller provided and any open questions."
                ),
            )
        return ContextStrategyConfig(strategy=ContextStrategy.APPEND)

    def compile_node(self, node: FlowNode) -> dict[str, Any]:
        """Build a pipecat.flows NodeConfig (a dict) for a conversational node."""
        from pipecat.flows import FlowsFunctionSchema

        say = self._say_text(node)
        functions: list[Any] = []
        taken: set[str] = {"request_human_agent", "end_call"}

        if node.fields:
            name = self._submit_name(node)
            taken.add(name)
            props = {f.name: field_schema(f) for f in node.fields}
            required = [f.name for f in node.fields if f.required and f.name not in self.captured and f.name not in self.skipped]
            if any(f.confirm for f in node.fields):
                props["confirmed"] = {
                    "type": "boolean",
                    "description": "True only after the caller explicitly confirmed the values you read back.",
                }
                required.append("confirmed")
            labels = ", ".join(f.label for f in node.fields)
            functions.append(
                FlowsFunctionSchema(
                    name=name,
                    description=f"Submit the caller's answers for: {labels}. Partial submissions are allowed.",
                    properties=props,
                    required=required,
                    handler=self._make_submit_handler(node),
                    timeout_secs=30,
                )
            )

        intent_kinds = ("intent",) if node.type == "collect" else ("intent", "on_complete", "fallback")
        for t in node.transitions:
            if t.kind not in intent_kinds or t.target not in self.nodes:
                continue
            target = self.nodes[t.target]
            if t.kind == "intent":
                desc = t.condition or t.label or f"Move to: {target.label}"
            elif t.kind == "on_complete":
                desc = t.condition or t.label or f"Call when this step is done, to move on to: {target.label}."
            else:
                desc = t.condition or t.label or f"Call when the caller's request does not fit the other options ({target.label})."
            functions.append(
                FlowsFunctionSchema(
                    name=tool_name("goto_", t.target, taken),
                    description=desc,
                    properties={},
                    required=[],
                    handler=self._make_goto_handler(node, t),
                    timeout_secs=30,
                )
            )

        cfg: dict[str, Any] = {
            "name": node.id,
            "role_message": self.role_message(),
            "task_messages": [{"role": "system", "content": self.task_content(node, say_text=say)}],
            "functions": functions,
            "respond_immediately": bool(node.settings.respond_immediately),
            "context_strategy": self._context_strategy(node),
        }
        if say:
            cfg["pre_actions"] = [{"type": "tts_say", "text": say}]
            # The line already spoken ends with a question: wait for the caller instead of
            # letting the LLM produce another turn (and guess an answer) on its own.
            cfg["respond_immediately"] = False
        return cfg

    def _say_text(self, node: FlowNode) -> str | None:
        text = node.say_on_enter
        if not text and node.type == "start" and node.id == self.graph.initial_node:
            text = self.config.behavior.greeting
        return render(text, self.vars) if text else None

    def compile_router_node(self, node: FlowNode) -> dict[str, Any]:
        """Decision node with intent transitions: the LLM silently picks a route."""
        from pipecat.flows import FlowsFunctionSchema

        taken: set[str] = {"request_human_agent", "end_call"}
        functions = []
        for t in node.transitions:
            if t.kind in ("intent", "fallback") and t.target in self.nodes:
                desc = t.condition or t.label or f"Route to {self.nodes[t.target].label}"
                functions.append(
                    FlowsFunctionSchema(
                        name=tool_name("goto_", t.target, taken), description=desc, properties={}, required=[],
                        handler=self._make_goto_handler(node, t), timeout_secs=30,
                    )
                )
        content = (
            f"CURRENT STEP: {node.label} (decision). {render(node.task_prompt, self.vars)}\n\n"
            "Decide which route applies based on the conversation so far and call exactly one function. "
            "Do not say anything to the caller in this step."
        )
        info = self._known_info()
        if info:
            content += "\n\nInformation known so far:\n" + info
        return {
            "name": node.id,
            "role_message": self.role_message(),
            "task_messages": [{"role": "system", "content": content}],
            "functions": functions,
            "respond_immediately": True,
            "context_strategy": self._context_strategy(node),
        }

    def global_functions(self) -> list[Any]:
        from pipecat.flows import FlowsFunctionSchema

        reason = {"reason": {"type": "string", "description": "Short reason, in the caller's words."}}
        return [
            FlowsFunctionSchema(
                name="request_human_agent",
                description="The caller explicitly asks to speak with a human agent / real person.",
                properties=reason, required=[], handler=self._handle_human, timeout_secs=30,
            ),
            FlowsFunctionSchema(
                name="end_call",
                description="The caller clearly wants to stop, is not interested, or says goodbye before the flow is done.",
                properties=reason, required=[], handler=self._handle_end_call, timeout_secs=30,
            ),
        ]

    # ------------------------------------------------------------------ entering nodes

    async def initial(self) -> Any:
        return await self.enter(self.graph.initial_node)

    async def enter(self, node_id: str, *, _hops: int = 0) -> Any:
        """Enter ``node_id``; auto-advance through decision/lookup/api_call nodes.

        Returns a NodeConfig dict (conversational nodes / LLM goodbye) or :class:`Terminal`.
        """
        if _hops > MAX_AUTO_HOPS:
            await self._event("error", node_id, {"message": "too many automatic transitions (loop?)"})
            return await self.finish("error", "flow_loop", FAILURE_END_MESSAGE)
        node = self.nodes.get(node_id)
        if node is None:
            await self._event("error", node_id, {"message": f"unknown node '{node_id}'"})
            return await self.finish("error", "missing_node", FAILURE_END_MESSAGE)
        self._turns_at_entry = len(self.user_utterances)

        revisit = node.id in self.history
        self.current_node_id = node.id
        self.history.append(node.id)
        if revisit and node.fields:
            # Coming back to a collect step (e.g. "try a lower amount"): ask for its fields again.
            for f in node.fields:
                self.captured.discard(f.name)
                self.skipped.discard(f.name)
                self.pending_confirm.pop(f.name, None)
                self.field_failures.pop(f.name, None)
                if f.name in self.vars:
                    self.vars[f"previous_{f.name}"] = self.vars.pop(f.name)
            self.node_failures.pop(node.id, None)
        self._refresh_builtins()
        await self._safe(self.hooks.node_entered(node))

        if node.type == "decision":
            t = next((t for t in _sorted_expr(node) if t.condition and evaluate(t.condition, self.vars)), None)
            if t is None and any(x.kind == "intent" for x in node.transitions):
                return self.compile_router_node(node)
            t = t or _first(node, "fallback") or _first(node, "on_complete")
            return await self._follow(node, t, _hops)

        if node.type == "lookup":
            found = await self._run_lookup(node)
            return await self._follow(node, select_transition(node, self.vars, prefer_fallback=not found), _hops)

        if node.type == "api_call":
            ok = await self._run_api_call(node)
            return await self._follow(node, select_transition(node, self.vars, prefer_fallback=not ok), _hops)

        if node.type == "transfer":
            text = render(node.say_on_enter, self.vars) or DEFAULT_TRANSFER
            payload = {"transfer_number": node.transfer_number, "node_id": node.id}
            await self._event("end", node.id, {"status": "transferred", **payload})
            await self._safe(self.hooks.transfer(node.id, node.transfer_number))
            return await self.finish("transferred", node.end_reason or "transferred", text, node_id=node.id)

        if node.type == "end":
            text = render(node.say_on_enter, self.vars)
            reason = node.end_reason or "completed"
            if not text and node.task_prompt:
                # Let the LLM produce a contextual goodbye, then end.
                await self._mark_end("completed", reason)
                return {
                    "name": node.id,
                    "role_message": self.role_message(),
                    "task_messages": [{"role": "system", "content": render(node.task_prompt, self.vars) + " Then say goodbye."}],
                    "functions": [],
                    "respond_immediately": True,
                    "post_actions": [{"type": "end_conversation"}],
                }
            return await self.finish("completed", reason, text or DEFAULT_GOODBYE, node_id=node.id)

        # start / collect / conversation
        if node.fields and node.type == "collect" and self._collect_done(node):
            # everything already captured upstream -> move straight on
            return await self._follow(node, select_transition(node, self.vars), _hops)
        return self.compile_node(node)

    async def _follow(self, node: FlowNode, t: Transition | None, hops: int) -> Any:
        if t is None or t.target not in self.nodes:
            await self._event("error", node.id, {"message": "no matching transition"})
            return await self._transfer_or_end("no_transition", hops)
        return await self.enter(t.target, _hops=hops + 1)

    async def _mark_end(self, status: str, reason: str | None) -> None:
        if self.end_status is None:
            self.end_status, self.end_reason = status, reason
            await self._safe(self.hooks.ended(status, reason))

    async def finish(self, status: str, reason: str | None, text: str | None, node_id: str | None = None) -> Terminal:
        await self._mark_end(status, reason)
        if status != "transferred":
            await self._event("end", node_id or self.current_node_id, {"status": status, "end_reason": reason})
        return Terminal(status=status, end_reason=reason, text=text, node_id=node_id)

    async def _transfer_or_end(self, reason: str, hops: int = 0, *, message: str | None = None) -> Any:
        transfer = self.first_node_of_type("transfer")
        if transfer is not None and hops <= MAX_AUTO_HOPS:
            return await self.enter(transfer.id, _hops=hops + 1)
        return await self.finish("abandoned", reason, message or FAILURE_END_MESSAGE)

    # ------------------------------------------------------------------ lookup / api

    async def _run_lookup(self, node: FlowNode) -> bool:
        cfg = node.lookup
        if cfg is None:
            await self._event("error", node.id, {"message": "lookup node has no configuration"})
            return False
        filters = {col: self.vars.get(var) for col, var in cfg.match.items()}
        rows: list[dict[str, Any]] = []
        error = None
        try:
            rows = await self.hooks.lookup_rows(cfg.dataset, filters, cfg.match_mode, cfg.limit) or []
        except Exception as e:  # noqa: BLE001
            error = str(e)
            logger.warning(f"lookup failed: {e}")
        found = bool(rows)
        self.vars[cfg.save_as] = (rows[0] if rows else None) if cfg.single else rows
        self.vars[f"{cfg.save_as}_found"] = found
        payload = {"dataset": cfg.dataset, "filters": self._mask_args(filters), "found": found, "count": len(rows)}
        if error:
            payload["error"] = error
        await self._event("lookup", node.id, payload)
        await self._vars_changed()
        return found

    async def _run_api_call(self, node: FlowNode) -> bool:
        cfg = node.api_call
        if cfg is None:
            await self._event("error", node.id, {"message": "api_call node has no configuration"})
            self.vars["api_result_error"] = "not configured"
            return False
        url = render(cfg.url, self.vars).strip()
        headers = {k: render(v, self.vars) for k, v in cfg.headers.items()}
        body = render_obj(cfg.body_template, self.vars) if cfg.body_template is not None else None
        mocked = self.api_call_mock or not url
        error: str | None = None
        result: Any = None
        if mocked:
            result = render_obj(cfg.mock_response, self.vars) if cfg.mock_response is not None else {"ok": True}
        else:
            try:
                result = await self.hooks.http_request(cfg.method, url, headers, body, cfg.timeout_secs)
            except Exception as e:  # noqa: BLE001
                error = str(e) or type(e).__name__
        self.vars[cfg.save_as] = result
        self.vars[f"{cfg.save_as}_error"] = error
        await self._event(
            "api_call", node.id,
            {"method": cfg.method, "url": url, "mocked": mocked, "ok": error is None, "error": error},
        )
        await self._vars_changed()
        return error is None

    # ------------------------------------------------------------------ collect

    def _collect_done(self, node: FlowNode) -> bool:
        return all(
            f.name in self.captured or f.name in self.skipped for f in node.fields if f.required
        ) and not any(f.confirm and f.name in self.pending_confirm for f in node.fields)

    def _make_submit_handler(self, node: FlowNode):
        async def handler(args: dict[str, Any], flow_manager: Any = None):
            return await self.wrap(flow_manager, await self.submit(node, args))

        handler.__name__ = f"submit_{node.id}"
        return handler

    async def submit(self, node: FlowNode, args: dict[str, Any]) -> tuple[Any, Any]:
        """Core collect logic. Returns (result_for_llm, next) where next is None, a NodeConfig
        or a Terminal."""
        await self._event("function_call", node.id, {"function": self._submit_name(node), "args": self._mask_args(args)})
        if self.require_grounding:
            if not self.caller_has_spoken:
                await self._event("validation_error", node.id, {"rejected": "no_caller_input"})
                return {"status": "waiting_for_caller", "message": WAIT_FOR_CALLER}, None
            if len(self.user_utterances) == self._turns_at_entry:
                # Nothing said since entering this node: only keep values the caller said earlier.
                ungrounded = [k for k, v in args.items() if k != "confirmed" and not is_empty(v) and not self._is_grounded(v)]
                for k in ungrounded:
                    args = {**args, k: ""}
                if ungrounded:
                    await self._event("validation_error", node.id, {"rejected_ungrounded": ungrounded})
                if all(is_empty(v) for k, v in args.items() if k != "confirmed"):
                    return {"status": "waiting_for_caller", "message": WAIT_FOR_CALLER}, None
        today = self.today()
        confirmed_arg = args.get("confirmed")
        confirmed = confirmed_arg is True or (isinstance(confirmed_arg, str) and confirmed_arg.lower() in ("true", "yes"))
        errors: dict[str, str] = {}
        captured_now: list[str] = []
        needs_confirm: list[CaptureField] = []
        exhausted: list[CaptureField] = []

        for f in node.fields:
            raw = args.get(f.name)
            if is_empty(raw):
                # value given earlier, now being confirmed
                if f.confirm and f.name in self.pending_confirm:
                    if confirmed:
                        await self._store(node, f, self.pending_confirm.pop(f.name))
                        captured_now.append(f.name)
                    else:
                        needs_confirm.append(f)
                continue
            res = validate_field(f, raw, today=today)
            if res.ok:
                if f.confirm and not confirmed:
                    # valid but not yet confirmed by the caller: hold it until read back + "yes"
                    self.pending_confirm[f.name] = res.value
                    needs_confirm.append(f)
                    continue
                self.pending_confirm.pop(f.name, None)
                await self._store(node, f, res.value)
                captured_now.append(f.name)
            else:
                n = self.field_failures.get(f.name, 0) + 1
                self.field_failures[f.name] = n
                errors[f.name] = res.error or "invalid value"
                await self._safe(self.hooks.field_captured(node.id, f.name, raw if not f.sensitive else mask_value(raw), False, n))
                if n > f.max_retries:
                    if f.required:
                        exhausted.append(f)
                    else:
                        self.skipped.add(f.name)
                        errors.pop(f.name, None)
                        await self._event("validation_error", node.id, {"field": f.name, "skipped": True, "attempts": n})

        if errors:
            await self._safe(self.hooks.validation(node.id, errors))
            await self._event("validation_error", node.id, {"errors": errors})
        if captured_now:
            await self._vars_changed()

        if exhausted:
            names = [f.name for f in exhausted]
            await self._event("validation_error", node.id, {"max_retries_exceeded": names})
            return await self.on_failure(node, f"max_retries:{','.join(names)}")

        missing = [
            f.name for f in node.fields
            if f.required and f.name not in self.captured and f.name not in self.skipped
            and f.name not in self.pending_confirm and f.name not in errors
        ]

        if errors or missing or needs_confirm:
            progress = bool(captured_now or errors or needs_confirm)
            if not progress:
                n = self.node_failures.get(node.id, 0) + 1
                self.node_failures[node.id] = n
                if n >= node.settings.max_attempts:
                    return await self.on_failure(node, "max_attempts")
            result: dict[str, Any] = {
                "status": "invalid" if errors else ("needs_confirmation" if needs_confirm else "incomplete"),
                "errors": errors,
                "missing": missing,
                "captured": captured_now,
            }
            if needs_confirm:
                result["confirm"] = {f.name: self.pending_confirm.get(f.name) for f in needs_confirm}
                result["instructions"] = (
                    "Read these values back to the caller and ask them to confirm. If they confirm, call the "
                    "function again with confirmed=true; if they correct it, submit the corrected value."
                )
            elif errors:
                result["instructions"] = "Tell the caller briefly what was wrong and ask again only for these fields."
            else:
                result["instructions"] = "Ask the caller for the missing fields."
            return result, None

        # complete
        t = select_transition(node, self.vars)
        if t is None or t.target not in self.nodes:
            return {"status": "complete"}, None
        return {"status": "complete"}, await self.enter(t.target)

    async def _store(self, node: FlowNode, f: CaptureField, value: Any) -> None:
        self.vars[f.name] = value
        self.captured.add(f.name)
        attempts = self.field_failures.get(f.name, 0) + 1
        await self._safe(self.hooks.field_captured(node.id, f.name, value, True, attempts))
        shown = mask_value(value) if (f.sensitive and self.config.behavior.mask_sensitive_in_logs) else value
        await self._event("field_captured", node.id, {"field": f.name, "value": shown, "attempts": attempts})

    async def on_failure(self, node: FlowNode, reason: str) -> tuple[Any, Any]:
        action = node.settings.on_failure
        result = {"status": "failed", "reason": reason}
        await self._event("error", node.id, {"on_failure": action, "reason": reason})
        if action == "goto" and node.settings.on_failure_target in self.nodes:
            return result, await self.enter(node.settings.on_failure_target)  # type: ignore[arg-type]
        if action == "skip":
            for f in node.fields:
                if f.required and f.name not in self.captured:
                    self.skipped.add(f.name)
                    self.pending_confirm.pop(f.name, None)
            t = select_transition(node, self.vars)
            if t is not None and t.target in self.nodes:
                return result, await self.enter(t.target)
            return result, await self._transfer_or_end(reason)
        if action == "end":
            return result, await self.finish("abandoned", reason, FAILURE_END_MESSAGE)
        # "transfer" (default) and invalid goto
        return result, await self._transfer_or_end(reason)

    # ------------------------------------------------------------------ goto / globals

    def _make_goto_handler(self, node: FlowNode, t: Transition):
        async def handler(args: dict[str, Any], flow_manager: Any = None):
            await self._event("function_call", node.id, {"function": f"goto_{t.target}", "transition_id": t.id})
            if self.require_grounding and not self.caller_has_spoken:
                return await self.wrap(flow_manager, ({"status": "waiting_for_caller", "message": WAIT_FOR_CALLER}, None))
            return await self.wrap(flow_manager, (None, await self.enter(t.target)))

        handler.__name__ = f"goto_{t.target}"
        return handler

    async def _handle_human(self, args: dict[str, Any], flow_manager: Any = None):
        await self._event("function_call", self.current_node_id, {"function": "request_human_agent", "args": args})
        transfer = self.first_node_of_type("transfer")
        if transfer is not None:
            nxt = await self.enter(transfer.id)
        else:
            end = self.first_node_of_type("end")
            nxt = await self.finish("abandoned", "human_requested_unavailable", NO_AGENT_MESSAGE, node_id=end.id if end else None)
        return await self.wrap(flow_manager, ({"status": "transferring"}, nxt))

    async def _handle_end_call(self, args: dict[str, Any], flow_manager: Any = None):
        await self._event("function_call", self.current_node_id, {"function": "end_call", "args": args})
        nxt = await self.finish("abandoned", "user_ended", END_CALL_MESSAGE)
        return await self.wrap(flow_manager, ({"status": "ending"}, nxt))

    # ------------------------------------------------------------------ flows glue

    async def wrap(self, flow_manager: Any, outcome: tuple[Any, Any]) -> tuple[Any, Any]:
        """Convert a runtime outcome to what pipecat.flows expects from a handler."""
        result, nxt = outcome
        if isinstance(nxt, Terminal):
            from pipecat.flows import NO_RESPONSE

            await self._safe(self.hooks.end_call(flow_manager, nxt.text))
            return (result if result is not None else {"status": "ended"}), NO_RESPONSE
        return result, nxt

    def preview(self, node_id: str) -> dict[str, Any]:
        """Debug view of the compiled prompts/tools for a node (no side effects)."""
        node = self.nodes[node_id]
        info: dict[str, Any] = {"node_id": node.id, "node_type": node.type, "label": node.label}
        if node.type in ("start", "collect", "conversation") or (
            node.type == "decision" and any(t.kind == "intent" for t in node.transitions)
        ):
            cfg = self.compile_router_node(node) if node.type == "decision" else self.compile_node(node)
            tools = list(self.global_functions()) + list(cfg.get("functions", []))
            info.update(
                role_message=cfg["role_message"],
                task_messages=cfg["task_messages"],
                pre_actions=[{k: v for k, v in a.items() if k != "handler"} for a in cfg.get("pre_actions", [])],
                respond_immediately=cfg["respond_immediately"],
                context_strategy=node.settings.context_strategy,
                tools=[
                    {"name": f.name, "description": f.description, "parameters": {
                        "type": "object", "properties": f.properties, "required": f.required}}
                    for f in tools
                ],
            )
        else:
            info.update(
                role_message=self.role_message(),
                task_messages=[],
                tools=[],
                auto_advance=node.type in ("decision", "lookup", "api_call"),
                transitions=[t.model_dump() for t in node.transitions],
                say_on_enter=render(node.say_on_enter, self.vars) if node.say_on_enter else None,
            )
        return info
