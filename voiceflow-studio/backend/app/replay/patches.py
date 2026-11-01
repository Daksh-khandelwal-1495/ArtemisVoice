"""Bounded PatchSpec application (INTEGRATION_CONTRACT §3, Phase 6).

``PatchSpec = {type, params}``:

* ``stt_keyterm_boost``  ``{keywords: [str]}`` (or ``keyword``) -> ``config.stt.keywords`` += keywords
* ``vad_stop_secs``      ``{stop_secs: float}`` or ``{delta: float}`` (default +0.4) -> ``config.vad.stop_secs``
* ``confirmation_readback`` ``{fields: [str]}`` -> those fields get ``confirm: true``
* ``stricter_validation``  ``{field, regex?, max_retries?}`` -> field validation tightened
  (default regex per type, e.g. pincode ``[1-9][0-9]{5}``)
* ``fallback_route``     ``{node_id?, target?}`` -> lookup/api_call nodes (or ``node_id``) get a
  fallback transition to ``target`` (default: first transfer node) and ``on_failure: goto``

Pure: returns patched copies + a reviewable diff; nothing is persisted or published here.
"""

from __future__ import annotations

import copy
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.flow import FlowGraph
from app.schemas.providers import BotConfig

PatchType = Literal["stt_keyterm_boost", "confirmation_readback", "stricter_validation", "fallback_route", "vad_stop_secs"]

DEFAULT_REGEX = {
    "pincode": r"[1-9][0-9]{5}",
    "phone": r"[6-9][0-9]{9}",
    "pan": r"[A-Z]{5}[0-9]{4}[A-Z]",
    "aadhaar_last4": r"[0-9]{4}",
    "vehicle_reg": r"[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{4}",
}


class PatchSpec(BaseModel):
    type: PatchType
    params: dict[str, Any] = Field(default_factory=dict)


class PatchError(ValueError):
    pass


def _diff(path: str, before: Any, after: Any, out: list[dict[str, Any]]) -> None:
    if before != after:
        out.append({"path": path, "before": before, "after": after})


def apply_patch(graph: dict[str, Any], config: dict[str, Any], patch: PatchSpec | dict[str, Any]
                ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Return (patched_graph, patched_config, diff)."""
    spec = patch if isinstance(patch, PatchSpec) else PatchSpec.model_validate(patch)
    g = FlowGraph.model_validate(copy.deepcopy(graph)).model_dump(mode="json")
    c = BotConfig.model_validate(copy.deepcopy(config or {})).model_dump(mode="json")
    p = spec.params or {}
    diff: list[dict[str, Any]] = []
    nodes = {n["id"]: n for n in g["nodes"]}

    def find_field(name: str) -> tuple[dict[str, Any], dict[str, Any]]:
        for n in g["nodes"]:
            for f in n.get("fields") or []:
                if f["name"] == name:
                    return n, f
        raise PatchError(f"field '{name}' not found in the flow")

    if spec.type == "stt_keyterm_boost":
        words = list(p.get("keywords") or ([p["keyword"]] if p.get("keyword") else []))
        if not words:
            raise PatchError("stt_keyterm_boost needs params.keywords")
        before = list(c["stt"]["keywords"])
        after = before + [str(w) for w in words if str(w).lower() not in {k.lower() for k in before}]
        c["stt"]["keywords"] = after
        _diff("config.stt.keywords", before, after, diff)
    elif spec.type == "vad_stop_secs":
        before = float(c["vad"]["stop_secs"])
        after = float(p["stop_secs"]) if p.get("stop_secs") is not None else round(before + float(p.get("delta", 0.4)), 3)
        after = max(0.1, min(3.0, after))
        c["vad"]["stop_secs"] = after
        _diff("config.vad.stop_secs", before, after, diff)
    elif spec.type == "confirmation_readback":
        names = list(p.get("fields") or ([p["field"]] if p.get("field") else []))
        if not names:
            raise PatchError("confirmation_readback needs params.fields")
        for name in names:
            n, f = find_field(name)
            _diff(f"nodes.{n['id']}.fields.{name}.confirm", f.get("confirm", False), True, diff)
            f["confirm"] = True
    elif spec.type == "stricter_validation":
        name = p.get("field")
        if not name:
            raise PatchError("stricter_validation needs params.field")
        n, f = find_field(name)
        v = f.setdefault("validation", {})
        regex = p.get("regex") or DEFAULT_REGEX.get(f.get("type", "string"))
        if regex:
            _diff(f"nodes.{n['id']}.fields.{name}.validation.regex", v.get("regex"), regex, diff)
            v["regex"] = regex
        if p.get("max_retries") is not None:
            _diff(f"nodes.{n['id']}.fields.{name}.max_retries", f.get("max_retries"), int(p["max_retries"]), diff)
            f["max_retries"] = int(p["max_retries"])
        if not diff:
            raise PatchError(f"stricter_validation has nothing to change for field '{name}'")
    elif spec.type == "fallback_route":
        target = p.get("target") or next((n["id"] for n in g["nodes"] if n["type"] == "transfer"), None)
        if not target or target not in nodes:
            raise PatchError("fallback_route needs a valid params.target (no transfer node found)")
        ids = [p["node_id"]] if p.get("node_id") else [n["id"] for n in g["nodes"] if n["type"] in ("lookup", "api_call")]
        for nid in ids:
            n = nodes.get(nid)
            if n is None:
                raise PatchError(f"node '{nid}' not found")
            fb = next((t for t in n.get("transitions") or [] if t.get("kind") == "fallback"), None)
            if fb is None:
                fb = {"id": f"t_{nid}_patch_fallback", "target": target, "kind": "fallback", "condition": "",
                      "label": "Patch fallback", "priority": 0}
                n.setdefault("transitions", []).append(fb)
                _diff(f"nodes.{nid}.transitions.fallback", None, target, diff)
            elif fb["target"] != target and p.get("target"):
                _diff(f"nodes.{nid}.transitions.{fb['id']}.target", fb["target"], target, diff)
                fb["target"] = target
            st = n.setdefault("settings", {})
            if st.get("on_failure") != "goto" or st.get("on_failure_target") != target:
                _diff(f"nodes.{nid}.settings.on_failure", [st.get("on_failure"), st.get("on_failure_target")],
                      ["goto", target], diff)
                st["on_failure"], st["on_failure_target"] = "goto", target
    g = FlowGraph.model_validate(g).model_dump(mode="json")
    c = BotConfig.model_validate(c).model_dump(mode="json")
    return g, c, diff
