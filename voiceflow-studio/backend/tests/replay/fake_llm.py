"""Deterministic fake LLM that behaves like a competent model on Studio flows.

It reads the same inputs a real model gets (system prompt, OpenAI-format messages, tool specs)
and only ever submits values it literally *heard* in the caller's latest utterance. No network.
"""

from __future__ import annotations

import difflib
import json
import re
from typing import Any

from app.replay.llm import LLMResult, ToolCall

CITIES = ["Mumbai", "Pune", "Delhi", "Bengaluru", "Bangalore", "Chennai", "Hyderabad", "Kolkata", "Ahmedabad",
          "Jaipur", "Lucknow", "Nagpur", "Thane", "Noida", "Gurgaon", "Gurugram", "Kochi"]
AFFIRM = re.compile(r"^\s*(yes|yeah|yep|yup|correct|right|haan|sure|that's right|that is right)\b", re.I)


class FakeLLM:
    def __init__(self):
        self.calls = 0
        self.seen_settings: list[dict[str, Any]] = []

    async def complete(self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                       settings: dict[str, Any]) -> LLMResult:
        self.calls += 1
        self.seen_settings.append(dict(settings))
        fns = {t["function"]["name"]: t["function"] for t in tools}
        if not tools:  # summary request
            return LLMResult(text="The caller is booking a test drive.")
        last_sys = max((i for i, m in enumerate(messages) if m["role"] == "system"), default=-1)
        last_user = max((i for i, m in enumerate(messages) if m["role"] == "user"), default=-1)
        task = messages[last_sys]["content"] if last_sys >= 0 else ""
        user = messages[last_user]["content"] if last_user >= 0 else ""
        fresh = last_user > last_sys
        submit = next((n for n in fns if n.startswith("submit_")), None)
        last = messages[-1] if messages else {}

        if last.get("role") == "tool":
            return LLMResult(text=self._respond_to_result(json.loads(last["content"]), task))

        if submit:
            props = fns[submit]["parameters"]["properties"]
            prev = self._last_submit_result(messages, submit, after=last_sys)
            if fresh and prev and prev.get("status") == "needs_confirmation" and AFFIRM.match(user):
                args = {k: "" for k in props if k != "confirmed"}
                args["confirmed"] = True
                return self._call(submit, args)
            values = {k: v for k, v in ((k, self._extract(k, spec, user, task)) for k, spec in props.items()
                                        if k != "confirmed") if v}
            if values:
                args = {k: values.get(k, "") for k in props if k != "confirmed"}
                if "confirmed" in props:
                    args["confirmed"] = False
                return self._call(submit, args)

        gotos = [n for n in fns if n.startswith("goto_")]
        if fresh and gotos:
            best = self._pick_goto(user, [(n, fns[n].get("description") or "") for n in gotos])
            if best:
                return self._call(best, {})
        return LLMResult(text=self._speak(task, fns.get(submit or "", {})))

    # ------------------------------------------------------------------ helpers

    def _call(self, name: str, args: dict[str, Any]) -> LLMResult:
        return LLMResult(text="", tool_calls=[ToolCall(id=f"fake_{self.calls}", name=name, arguments=args)])

    @staticmethod
    def _last_submit_result(messages, submit: str, after: int) -> dict[str, Any] | None:
        ids = {}
        for i, m in enumerate(messages):
            if i <= after:
                continue
            for tc in m.get("tool_calls") or []:
                if tc["function"]["name"] == submit:
                    ids[tc["id"]] = i
        res = None
        for i, m in enumerate(messages):
            if m["role"] == "tool" and m.get("tool_call_id") in ids:
                res = json.loads(m["content"])
        return res

    @staticmethod
    def _pick_goto(user: str, options: list[tuple[str, str]]) -> str | None:
        u = user.lower()
        if AFFIRM.match(user):
            for name, desc in options:
                if "confirm" in desc.lower():
                    return name
        words = {w for w in re.findall(r"[a-z]{4,}", u)}
        scored = sorted(((len(words & set(re.findall(r"[a-z]{4,}", d.lower()))), n) for n, d in options), reverse=True)
        return scored[0][1] if scored and scored[0][0] >= 2 else None

    def _extract(self, name: str, spec: dict[str, Any], user: str, task: str) -> str:
        desc = (spec.get("description") or "")
        d = desc.lower()
        if spec.get("type") == "boolean":
            return ""
        if spec.get("enum"):
            low = user.lower()
            for opt in sorted(spec["enum"], key=len, reverse=True):
                if re.search(rf"\b{re.escape(opt.lower())}\b", low):
                    return opt
            for tok in re.findall(r"[A-Za-z]{4,}", user):
                if any(difflib.SequenceMatcher(None, tok.lower(), o.lower()).ratio() >= 0.75 for o in spec["enum"]):
                    return tok
            return ""
        if "pin code" in d:
            for tok in re.findall(r"\b[0-9][0-9A-Za-z]{2,7}\b", user):
                return tok
            return ""
        if "mobile number" in d:
            m = re.search(r"(?:\+?91[\s-]?)?(?:\d[\s-]?){9}\d", user)
            return re.sub(r"[\s-]", "", m.group(0)) if m else ""
        if "format: date" in d:
            m = re.search(r"day after tomorrow|tomorrow|today|(?:next )?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|\d{4}-\d{2}-\d{2}", user, re.I)
            return m.group(0) if m else ""
        if "chosen from the list" in d:
            for full in re.findall(r'"name":\s*"([^"]+)"', task):
                short = full.split(" (")[0]
                if short.lower() in user.lower():
                    return full
            return ""
        if "name" in name and "name" in d:
            m = re.search(r"(?:my name is|name is|this is)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)", user)
            return m.group(1) if m else ""
        if "city" in d:
            for c in CITIES:
                if re.search(rf"\b{c}\b", user, re.I):
                    return c
            return ""
        return ""

    @staticmethod
    def _labels(task: str) -> list[str]:
        return re.findall(r"^- (.+?) \(`\w+`, required\)", task, re.M)

    def _speak(self, task: str, submit_fn: dict[str, Any]) -> str:
        m = re.search(r"Summarise the booking in one or two sentences:\s*(.+?)\s*Ask if", task, re.S)
        if m:
            return f"To confirm: {m.group(1).rstrip('.')}. Is everything correct?"
        step = task.split("Information known so far")[0]
        dealers = re.findall(r'"name":\s*"([^"]+)"', step)
        if dealers:
            return f"We have {', '.join(dealers[:3])}. Which one is convenient for you?"
        labels = self._labels(task)
        if labels:
            return f"Could you please tell me your {' and '.join(labels)}?"
        return "How can I help you today?"

    @staticmethod
    def _respond_to_result(result: dict[str, Any], task: str) -> str:
        st = result.get("status")
        if st == "invalid":
            return "Sorry, " + " ".join(result.get("errors", {}).values()) + " Could you please repeat that?"
        if st == "needs_confirmation":
            parts = [f"your {k} is {' '.join(str(v))}" for k, v in (result.get("confirm") or {}).items()]
            return "Just to confirm, " + " and ".join(parts) + ". Is that correct?"
        if st == "incomplete":
            return "Could you please tell me your " + " and ".join(result.get("missing") or []) + "?"
        if st == "waiting_for_caller":
            return "Could you please tell me that?"
        return "Okay."


def fake_factory(_config) -> FakeLLM:
    return FakeLLM()
