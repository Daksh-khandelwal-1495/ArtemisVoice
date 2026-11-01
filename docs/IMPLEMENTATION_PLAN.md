# ArtemisVoice implementation plan

## Product outcome

ArtemisVoice is an open-source debugger for a cascaded voice agent. Given a failed
call, it must identify the responsible pipeline layer using controlled replay,
propose a bounded fix, and demonstrate that the fix improves the failure without
regressing held-out or existing scenarios.

The hackathon MVP is deliberately narrow: one Pipecat cascaded agent, one
deterministic domain, 5-10 base scenarios, five intervention types, and a seeded
bug suite. A claim is only shown when the trace, control arm, and repeated trials
support it.

## Baseline and operating decisions

The repository currently contains only the PRD and a short README. The first
implementation must establish the project skeleton, reproducible local setup, and
a deterministic mock domain before adding product features.

Use the following implementation choices unless a validated Pipecat or EVA
constraint requires an alternative:

- Python 3.12, asyncio, FastAPI, Pydantic, SQLAlchemy, SQLite, and pytest for the
  backend and CLI.
- A Pipecat cascaded pipeline behind an adapter interface; a deterministic local
  fake adapter is required for tests and demos without external provider keys.
- A React + TypeScript + Vite interface, added only after a persisted diagnosis is
  available through the API.
- Local audio files and SQLite for the MVP. Every run is keyed by scenario,
  configuration hash, seed, and trace version.
- Provider, model, and credentials configuration comes from environment variables
  and is never committed.

## Architecture

```text
scenario + seed + agent config
             |
             v
simulator --> cascaded agent adapter --> trace store (SQLite + audio files)
             |                                  |
             v                                  v
caller validity check                     candidate-turn locator
                                                |
                                                v
                                   counterfactual experiment runner
                                   control + one substituted layer
                                                |
                                                v
                                    judges + statistics + diagnosis
                                                |
                                                v
                           bounded patch --> validation gate --> regression test
                                                |
                                                v
                                      FastAPI / CLI / React results screen
```

## Repository structure to create

```text
backend/
  artemisVoice/          # teammate's voice-agent code lives here
    Evals/               # this independent evaluation/debugger package
      api/               # FastAPI routes and response schemas
      domain/            # scenarios, deterministic mock tools, state model
      agents/            # Pipecat and deterministic test adapters
      tracing/           # trace contracts, serializers, replay restoration
      evaluation/        # caller validity and LLM judges
      causal/            # locator, interventions, trials, statistics
      patches/           # patch proposals and validation gate
      storage/           # SQLite models and repositories
      cli.py
  tests/
frontend/                # React application, introduced in phase 5
scenarios/               # versioned base scenarios and held-out variants
seeded_bugs/             # reproducible bug configurations and expected causes
docs/                    # architecture, trace contract, demo runbook
```

## Milestones and gates

### Phase 0: foundation and reproducibility (first 2-4 hours)

1. Add Python project metadata, lint/test tooling, `.env.example`, and a concise
   local setup guide.
2. Define Pydantic contracts for scenarios, runs, turns, layer traces,
   experiments, diagnoses, patches, and gate results.
3. Implement a deterministic booking or confirmation-code domain with a mock tool
   database, expected final state, and scenario validation.
4. Add one healthy scenario and one forced speech-to-text failure as fixtures.

Exit gate: `pytest` can execute both fixtures offline and produce a structured run
record. No provider key is needed.

### Phase 1: replay contract (first critical gate; complete before UI)

1. Implement the trace store and immutable per-turn database snapshots.
2. Capture required fields for caller, endpointing, STT, LLM, tools, and TTS,
   including audio references, settings, seeds, timestamps, partials, and hashes.
3. Implement trace validation: a run is unreplayable when a required field or
   referenced artifact is missing.
4. Implement `restore_turn(run_id, turn_index)` and a control replay that uses the
   same seed and unchanged pipeline configuration.
5. Persist a configuration hash and trace schema version with every run.

Exit gate: a recorded failure can be restored at every turn; control replays
reproduce the same final result within a documented tolerance. This is the
highest-risk requirement, so it should be tested before hour 10.

### Phase 2: evaluation and causal diagnosis

1. Implement caller-script validity, field-level final-state diff, tool validity,
   and timing rules. Invalid caller runs must never be attributed to the agent.
2. Implement candidate-turn ranking using entity mismatch, first invalid tool call,
   state change, timing violations, and judge citations; return at most three.
3. Add layer interventions:
   - STT: pass caller-intended text to the LLM.
   - Endpointing: merge a fragmented utterance.
   - LLM/prompt: use a stronger model or scoped corrected prompt.
   - Tools: supply a correct result or corrected arguments.
   - TTS: deliver intended agent text to the caller and confirm the mismatch.
4. Run triage first, then continue surviving arms plus control to call completion.
   Keep non-intervened layers live in each continuation.
5. Implement repeated-trial comparisons, sequential extension from 5 to 10+ trials,
   Fisher exact tests, effect sizes, and verdicts: proven, likely, compound,
   inconclusive, and not-the-agent.

Exit gate: seeded one-layer failures recover only under their expected intervention;
the control arm does not create a false diagnosis.

### Phase 3: fixes and proof

1. Define bounded patch types per proven layer: keyword/confirmation rules,
   endpoint settings, scoped prompt changes, tool schemas, and pronunciation rules.
2. Generate a reviewable unified diff or structured configuration patch. A patch
   always begins `pending` and must be approved explicitly before application.
3. Build the validation gate: failing scenario, held-out variants, and regression
   suite. Persist before/after rates and statistical results for every set.
4. Promote a confirmed failure to a versioned regression scenario automatically.

Exit gate: the forced STT failure has an approved patch that improves the failing
and held-out scenarios without a statistically meaningful regression elsewhere.

### Phase 4: self-validation and scenario search

1. Add 5-10 base scenarios and the seeded suite (target: 24 cases) across STT,
   endpointing, LLM/prompt, tools, TTS, compound, caller error, and healthy runs.
2. Split the seeded suite into a tuning set and a blind reporting set. Freeze
   thresholds before measuring the blind set.
3. Report attribution accuracy, false-blame rate, inconclusive rate, fix success,
   time/cost per diagnosis, and a confusion matrix.
4. Implement four P0 red-team mutations: hard entities, self-correction, policy
   boundary, and intent switch. Validate every mutated caller run before scoring.
5. Add barge-in and Thompson-sampling allocation only after the core loop is
   stable; they are P1.

Exit gate: the CLI can generate the self-validation report from a clean database
and clearly separates healthy/caller-error calls from agent failures.

### Phase 5: product surfaces and demo

1. Expose runs, traces, experiments, diagnoses, patches, and validation results
   through FastAPI and a JSON-producing CLI.
2. Build one results screen that shows failing audio/transcript, candidate turn,
   intervention arms, evidence, patch diff, and before/after results. Evidence
   clicks must seek to the correct audio location.
3. Add human-review queue behaviour for safety flags, low confidence, and judge
   disagreement. Do not auto-apply patches.
4. Precompute a canonical demo, record a backup, and keep one small replay live.

Exit gate: a newcomer can state the cause from the results screen in under 30
seconds, and a CI invocation can fail on a regression.

## P0 delivery order

1. Project skeleton and deterministic mock domain.
2. Trace schema, persistence, snapshots, and replay validation.
3. Healthy + seeded STT failure with deterministic judge and state diff.
4. Control replay + STT intervention + repeated-trial verdict.
5. Candidate locator and remaining interventions.
6. Bounded patch plus the three-set validation gate.
7. Seeded-suite report and minimal API/CLI.
8. Minimal result screen.

Red-team allocation, word-timed barge-ins, live-audio confirmation, pairwise
interventions, shareable reports, and the polished UI are deferred until the
preceding item is demonstrably working.

## Test strategy

- Unit tests: trace serialization and validation, state snapshot restoration,
  candidate scoring, each intervention, statistical verdict boundaries, and patch
  approval rules.
- Contract tests: every agent adapter must emit a complete trace and support
  restore/replay for each turn.
- Integration tests: healthy, caller-invalid, single-layer, and compound seeded
  scenarios against the deterministic adapter.
- End-to-end test: failed run -> diagnosis -> approval -> patch -> validation ->
  regression scenario, with all records persisted.
- Manual demo checks: audio seek accuracy, evidence quotes present in trace,
  human-review queue visibility, and no secret values in API or UI output.

## Risks and required decisions

1. Verify EVA's licence and extension points in the first hour. If it cannot
   supply reliable turn hooks, retain the adapter boundary and proceed with the
   local Pipecat harness.
2. Choose the initial domain after a short failure-rate trial; confirmation-code
   booking is the recommended default because it makes STT errors easy to expose.
3. Select STT, TTS, and LLM providers and confirm their support for offline or
   faster-than-realtime operation. The deterministic adapter remains the fallback.
4. Assign ownership for 30-50 human labels before claiming judge agreement.
5. Treat PRD success figures as targets, not achieved measurements; competitor and
   research claims require fresh verification before public use.

## Immediate next implementation slice

Create the backend scaffold and deterministic confirmation-code domain, then
implement the versioned trace contract with a healthy fixture and an STT-confusion
fixture. The next commit should prove the Phase 1 replay gate before introducing
external voice providers or the frontend.
