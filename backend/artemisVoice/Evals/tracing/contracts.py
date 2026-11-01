from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from artemisVoice.Evals import TRACE_SCHEMA_VERSION


class Layer(StrEnum):
    CALLER = "caller"
    ENDPOINTING = "endpointing"
    STT = "speech_to_text"
    LLM = "llm"
    TOOLS = "tools"
    TTS = "text_to_speech"


class TraceContractError(ValueError):
    """Raised when a trace cannot safely be used for replay."""


class ArtifactRef(BaseModel):
    """An immutable artifact reference; path can point to local MVP audio."""

    model_config = ConfigDict(frozen=True)

    uri: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    media_type: str = Field(min_length=1)


class LayerTrace(BaseModel):
    """Normalized evidence emitted by one layer during a turn."""

    model_config = ConfigDict(frozen=True)

    layer: Layer
    input: dict[str, Any]
    output: dict[str, Any]
    settings: dict[str, Any]
    seed: int | None = Field(default=None, ge=0)
    started_at: datetime
    finished_at: datetime

    @model_validator(mode="after")
    def timestamps_are_ordered(self) -> "LayerTrace":
        if self.finished_at < self.started_at:
            raise ValueError("layer trace finishes before it starts")
        return self


class TurnTrace(BaseModel):
    """Everything needed to restore one pipeline turn exactly."""

    model_config = ConfigDict(frozen=True)

    index: int = Field(ge=0)
    database_state_before: dict[str, str]
    database_state_after: dict[str, str]
    database_hash_before: str = Field(pattern=r"^[a-f0-9]{64}$")
    database_hash_after: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifacts: list[ArtifactRef]
    layers: list[LayerTrace]

    @model_validator(mode="after")
    def is_complete(self) -> "TurnTrace":
        captured = {entry.layer for entry in self.layers}
        missing = set(Layer) - captured
        if missing:
            raise ValueError(f"turn is missing layer traces: {sorted(str(item) for item in missing)}")
        if not self.artifacts:
            raise ValueError("turn is missing the caller audio artifact")
        if self.database_hash_before != state_hash(self.database_state_before):
            raise ValueError("database_hash_before does not match its snapshot")
        if self.database_hash_after != state_hash(self.database_state_after):
            raise ValueError("database_hash_after does not match its snapshot")
        return self


class RunTrace(BaseModel):
    """Persisted replay contract for an entire scenario run."""

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    schema_version: str = TRACE_SCHEMA_VERSION
    scenario_id: str
    agent_config_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    seed: int = Field(ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    valid_caller_run: bool
    final_database_state: dict[str, str]
    turns: list[TurnTrace] = Field(min_length=1)

    @model_validator(mode="after")
    def ordered_turns_and_final_state(self) -> "RunTrace":
        if [turn.index for turn in self.turns] != list(range(len(self.turns))):
            raise ValueError("turn indexes must begin at zero and be contiguous")
        if self.final_database_state != self.turns[-1].database_state_after:
            raise ValueError("final database state must match the last turn snapshot")
        return self

    def restore_turn(self, index: int) -> TurnTrace:
        try:
            return self.turns[index]
        except IndexError as error:
            raise TraceContractError(f"turn {index} does not exist in run {self.id}") from error


def state_hash(state: dict[str, str]) -> str:
    """Stable state hash so a replay can prove the database snapshot it restored."""

    canonical = "\n".join(f"{key}={state[key]}" for key in sorted(state))
    return sha256(canonical.encode("utf-8")).hexdigest()


def text_artifact_ref(text: str, *, label: str) -> ArtifactRef:
    """Temporary deterministic artifact used until live TTS writes audio files."""

    digest = sha256(text.encode("utf-8")).hexdigest()
    return ArtifactRef(uri=f"memory://audio/{label}/{digest}.wav", sha256=digest, media_type="audio/wav")
