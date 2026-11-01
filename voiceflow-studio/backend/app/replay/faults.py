"""FaultProfile (INTEGRATION_CONTRACT §3) + deterministic per-layer fault functions.

Faults are part of the *system under test*: they apply in control replays and in every oracle
arm except the arm that replaces the faulty layer. All functions are pure and deterministic.

```jsonc
{ "stt": { "replacements": [ {"from": "411001", "to": "41100I"} ], "keyword_protected": true },
  "endpointing": { "truncate_field": "pincode", "keep_chars": 4 },
  "llm": { "swap_field": {"field": "pincode", "with_value_of": "phone"} },
  "tools": { "lookup_override": {"dataset": "tata_dealers", "rows": [ {...} ]}, "api_error": false },
  "tts": { "replacements": [ {"from": "Nexon", "to": "Nixon"} ] } }
```

Additive (optional) knobs, documented here so patches can genuinely fix a fault:

* ``stt.keyword_protected`` (default true): a replacement is skipped when its ``from`` word is one
  of ``bot.config.stt.keywords`` (case-insensitive) - so the ``stt_keyterm_boost`` patch fixes it.
* ``endpointing.cutoff_below_stop_secs`` (default 1.0): the cut-off only happens while
  ``bot.config.vad.stop_secs`` is below this value - so the ``vad_stop_secs`` patch fixes it.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.providers import BotConfig
from app.voice.validators import is_empty


class Replacement(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from", min_length=1)
    to: str = ""
    keyword_protected: bool | None = None  # per-replacement override of stt.keyword_protected


class STTFault(BaseModel):
    replacements: list[Replacement] = Field(default_factory=list)
    keyword_protected: bool = True


class EndpointingFault(BaseModel):
    truncate_field: str | None = None
    keep_chars: int = Field(default=4, ge=0)
    cutoff_below_stop_secs: float = 1.0


class SwapField(BaseModel):
    field: str
    with_value_of: str


class LLMFault(BaseModel):
    swap_field: SwapField | None = None


class LookupOverride(BaseModel):
    dataset: str | None = None  # None -> every lookup
    rows: list[dict[str, Any]] = Field(default_factory=list)


class ToolsFault(BaseModel):
    lookup_override: LookupOverride | None = None
    api_error: bool = False


class TTSFault(BaseModel):
    replacements: list[Replacement] = Field(default_factory=list)


class FaultProfile(BaseModel):
    stt: STTFault | None = None
    endpointing: EndpointingFault | None = None
    llm: LLMFault | None = None
    tools: ToolsFault | None = None
    tts: TTSFault | None = None

    def dump(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)

    @classmethod
    def parse(cls, data: Any) -> "FaultProfile":
        if isinstance(data, FaultProfile):
            return data
        return cls.model_validate(data or {})


# --------------------------------------------------------------------------- text helpers


def _word_pattern(word: str) -> re.Pattern:
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(word)}(?![A-Za-z0-9])", re.IGNORECASE)


def replace_words(text: str, replacements: list[Replacement], protected: set[str] | None = None) -> tuple[str, list[dict[str, str]]]:
    """Whole-word, case-insensitive replacements; returns (text, applied replacements)."""
    applied: list[dict[str, str]] = []
    prot = {p.lower() for p in (protected or set())}
    for r in replacements:
        if r.from_.lower() in prot and r.keyword_protected is not False:
            continue
        new, n = _word_pattern(r.from_).subn(r.to, text)
        if n:
            applied.append({"from": r.from_, "to": r.to})
            text = new
    return text, applied


# --------------------------------------------------------------------------- layers


def stt_transcribe(segment: str, profile: FaultProfile | None, config: BotConfig) -> tuple[str, list[dict[str, str]]]:
    """Simulated STT: the segment text with the profile's STT confusions applied."""
    if not profile or not profile.stt or not segment:
        return segment, []
    protected = set(config.stt.keywords)
    reps = [r if r.keyword_protected is not None else r.model_copy(update={"keyword_protected": profile.stt.keyword_protected})
            for r in profile.stt.replacements]
    return replace_words(segment, reps, protected)


_TYPE_PATTERNS = {
    "pincode": r"(?<!\d)[1-9]\d{5}(?!\d)",
    "phone": r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)",
    "aadhaar_last4": r"(?<!\d)\d{4}(?!\d)",
}


def endpoint_segment(
    utterance: str,
    profile: FaultProfile | None,
    config: BotConfig,
    *,
    value_hint: str | None = None,
    field_type: str | None = None,
) -> tuple[str, bool]:
    """Simulated endpointing: the part of ``utterance`` that reached STT.

    With ``endpointing.truncate_field`` the turn is cut ``keep_chars`` characters into that
    field's value (located via ``value_hint`` - the oracle value - or the field type pattern).
    Returns (segment, truncated).
    """
    ep = profile.endpointing if profile else None
    if not utterance or ep is None or not ep.truncate_field:
        return utterance, False
    if float(config.vad.stop_secs) >= float(ep.cutoff_below_stop_secs):
        return utterance, False
    idx = -1
    if value_hint:
        idx = utterance.lower().find(str(value_hint).lower())
    if idx < 0 and field_type in _TYPE_PATTERNS:
        m = re.search(_TYPE_PATTERNS[field_type], utterance)
        idx = m.start() if m else -1
    if idx < 0:
        return utterance, False
    return utterance[: idx + ep.keep_chars], True


def llm_swap_args(
    args: dict[str, Any],
    profile: FaultProfile | None,
    *,
    vars: dict[str, Any],
    known_values: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """LLM tool-argument fault: ``field`` receives the value of ``with_value_of``.

    The swapped-in value is resolved from (1) the same call's arguments, (2) captured vars,
    (3) ``known_values`` (the scenario's caller facts). Returns (args, applied|None).
    """
    sw = profile.llm.swap_field if profile and profile.llm else None
    if sw is None or sw.field not in args or is_empty(args.get(sw.field)) or args.get(sw.field) == "":
        return args, None
    value = None
    for source in (args, vars, known_values or {}):
        v = source.get(sw.with_value_of)
        if not is_empty(v) and v != "":
            value = v
            break
    if value is None or str(value) == str(args[sw.field]):
        return args, None
    out = {**args, sw.field: str(value)}
    return out, {"field": sw.field, "from": args[sw.field], "to": str(value), "with_value_of": sw.with_value_of}


def tts_speak(text: str, profile: FaultProfile | None) -> tuple[str, list[dict[str, str]]]:
    """Simulated TTS: the text actually spoken (TTS mispronunciations / substitutions)."""
    if not profile or not profile.tts or not text:
        return text, []
    return replace_words(text, profile.tts.replacements)
