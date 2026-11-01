from __future__ import annotations

from typing import Protocol, runtime_checkable

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
