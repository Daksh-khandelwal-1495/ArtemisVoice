from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

from artemisVoice.Evals.domain.models import Scenario
from artemisVoice.Evals.tracing.contracts import (
    Layer,
    LayerTrace,
    RunTrace,
    TurnTrace,
    state_hash,
    text_artifact_ref,
)


class DeterministicConfirmationAgent:
    """MVP adapter with a controlled STT bug for causal-replay development."""

    def __init__(self, *, stt_keyword_boosts: frozenset[str] = frozenset()) -> None:
        self._stt_keyword_boosts = stt_keyword_boosts

    @property
    def config(self) -> dict[str, str | int | list[str]]:
        return {
            "adapter": "deterministic-confirmation",
            "stt_keyword_boosts": sorted(self._stt_keyword_boosts),
            "version": 1,
        }

    @property
    def config_hash(self) -> str:
        encoded = "\n".join(f"{key}={value}" for key, value in sorted(self.config.items()))
        return sha256(encoded.encode("utf-8")).hexdigest()

    def run(self, scenario: Scenario) -> RunTrace:
        started = datetime.now(UTC)
        before = {"customer_name": scenario.caller_name, "confirmation_code": ""}
        caller_text = scenario.caller_text()
        endpoint_code = scenario.forced_endpointing_code or scenario.confirmation_code
        transcript_code = endpoint_code
        boosted = any(
            scenario.confirmation_code.startswith(keyword) for keyword in self._stt_keyword_boosts
        )
        if scenario.forced_stt_confusion and not boosted:
            transcript_code = scenario.forced_stt_confusion
        transcript = caller_text.replace(scenario.confirmation_code, transcript_code)
        llm_code = scenario.forced_llm_code or transcript_code
        tool_code = scenario.forced_tool_code or llm_code
        spoken_code = scenario.forced_tts_code or tool_code
        after = {"customer_name": scenario.caller_name, "confirmation_code": tool_code}
        finished = datetime.now(UTC)

        layers = [
            LayerTrace(
                layer=Layer.CALLER,
                input={"scenario_id": scenario.id},
                output={"intended_text": caller_text},
                settings={"voice": "deterministic"},
                seed=scenario.seed,
                started_at=started,
                finished_at=finished,
            ),
            LayerTrace(
                layer=Layer.ENDPOINTING,
                input={"audio_artifact": "caller"},
                output={"heard_code": endpoint_code, "speech_start_ms": 0, "speech_end_ms": 1000, "interruptions": []},
                settings={"silence_threshold_ms": 500},
                seed=scenario.seed,
                started_at=started,
                finished_at=finished,
            ),
            LayerTrace(
                layer=Layer.STT,
                input={"audio_artifact": "caller"},
                output={"final_hypothesis": transcript, "confidence": 1.0},
                settings={"keyword_boosts": sorted(self._stt_keyword_boosts), "model": "deterministic"},
                seed=scenario.seed,
                started_at=started,
                finished_at=finished,
            ),
            LayerTrace(
                layer=Layer.LLM,
                input={"transcript": transcript},
                output={"tool_name": "store_confirmation", "arguments": {"code": llm_code}},
                settings={"model": "deterministic"},
                seed=scenario.seed,
                started_at=started,
                finished_at=finished,
            ),
            LayerTrace(
                layer=Layer.TOOLS,
                input={"name": "store_confirmation", "arguments": {"code": llm_code}},
                output={"database_state": after},
                settings={"tool": "deterministic-memory-db"},
                started_at=started,
                finished_at=finished,
            ),
            LayerTrace(
                layer=Layer.TTS,
                input={"text": f"I saved confirmation code {spoken_code}."},
                output={"spoken_text": f"I saved confirmation code {spoken_code}."},
                settings={"voice": "deterministic"},
                seed=scenario.seed,
                started_at=started,
                finished_at=finished,
            ),
        ]
        turn = TurnTrace(
            index=0,
            database_state_before=before,
            database_state_after=after,
            database_hash_before=state_hash(before),
            database_hash_after=state_hash(after),
            artifacts=[text_artifact_ref(caller_text, label=scenario.id)],
            layers=layers,
        )
        return RunTrace(
            scenario_id=scenario.id,
            agent_config_hash=self.config_hash,
            seed=scenario.seed,
            valid_caller_run=scenario.caller_valid,
            expected_database_state=scenario.expected_database_state,
            expected_spoken_text=f"I saved confirmation code {scenario.confirmation_code}.",
            final_database_state=after,
            turns=[turn],
        )
