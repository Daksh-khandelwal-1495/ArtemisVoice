# ArtemisVoice

Causal debugging and regression validation for cascaded voice agents.

ArtemisVoice does not stop at a pass rate. It records a replayable call trace,
tests controlled substitutions of the voice pipeline, and produces evidence for
the layer most likely responsible for a failure. A proposed patch must then pass
the original failure, held-out variants, and the regression suite.

The first implementation slice is intentionally provider-free: it uses a
deterministic confirmation-code agent to prove the trace and replay contract
before Pipecat or external speech/model vendors are introduced.

## Status

The repository now has the Phase 0 foundation:

- versioned, replay-oriented Pydantic trace contracts;
- a deterministic confirmation-code domain with healthy and forced-STT-error
  scenarios;
- a SQLite trace store that restores a saved turn and its database snapshot;
- a control-vs-oracle-STT counterfactual replay with repeated-trial statistics;
- candidate-turn ranking from transcript and final-state evidence;
- a review-required STT patch and a failing/held-out/regression validation gate;
- split-specific seeded-bug reports for attribution, false-blame, and inconclusive rates.
- tests covering valid traces, the seeded failure, and a missing-artifact guard.

See [the implementation plan](docs/IMPLEMENTATION_PLAN.md) for ordered milestones,
acceptance gates, risks, and the intended architecture.

## Run the deterministic foundation

Create an environment with Python 3.12 or newer, then install the development
dependencies:

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m artemisVoice.Evals.cli --scenario scenarios/healthy_confirmation.json
python -m artemisVoice.Evals.cli --seeded-suite seeded_bugs/initial_suite.json --split blind
uvicorn artemisVoice.Evals.api.app:app --reload
```

The CLI writes a replayable run to SQLite and prints the structured result. The
seeded-suite command prints a split-specific confusion matrix and measured
self-validation metrics. The next milestone generalizes the first
counterfactual arm to other pipeline layers.

The API exposes the same workflow through `/runs`,
`/runs/{run_id}/diagnoses/stt`, and approval-gated `/patches` endpoints. It is
the integration surface for the voice agent under `backend/artemisVoice`.
See [the agent integration contract](docs/VOICE_AGENT_INTEGRATION.md) before
wiring a Pipecat or other live agent into the evaluator.
