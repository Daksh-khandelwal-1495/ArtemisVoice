from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from artemisVoice.Evals.domain.models import Scenario
from artemisVoice.Evals.tracing.contracts import RunTrace


@runtime_checkable
class VoiceAgentAdapter(Protocol):
    """Minimal contract a teammate's cascaded voice agent must implement.

    `run` must return a complete `RunTrace`: every layer, immutable database
    snapshots, artifact references, configuration hash, and seed are required.
    The evaluator rejects incomplete traces rather than pretending they replay.
    """

    @property
    def config_hash(self) -> str: ...

    def run(self, scenario: Scenario) -> RunTrace: ...


class ReplayTrial(BaseModel):
    """One counterfactual replay executed by the agent under test itself."""

    model_config = ConfigDict(frozen=True)

    trial: int = Field(ge=0)
    passed: bool
    outcome: dict[str, Any] = Field(default_factory=dict)
    run_trace: RunTrace | None = None
    replay_session_id: str | None = None


@runtime_checkable
class ReplayingVoiceAgentAdapter(VoiceAgentAdapter, Protocol):
    """An adapter whose agent restores a recorded run and replays it with one layer swapped.

    The replay engine delegates to `replay` for runs whose behaviour cannot be
    recomputed in-process (e.g. `source=voiceflow`). `intervention` is one of
    control | stt_oracle | endpointing_oracle | llm_oracle | tools_oracle | tts_oracle.
    """

    def replay(
        self,
        source_run: RunTrace,
        turn_index: int,
        intervention: str,
        seed: int,
        *,
        trials: int = 1,
    ) -> list[ReplayTrial]: ...
