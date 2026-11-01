# ArtemisVoice

Causal debugging and regression validation for cascaded voice agents.

ArtemisVoice does not stop at a pass rate. It records a replayable call trace,
tests controlled substitutions of the voice pipeline, and produces evidence for
the layer most likely responsible for a failure. A proposed patch must then pass
the original failure, held-out variants, and the regression suite.

The first implementation slice is intentionally provider-free: it uses a
deterministic confirmation-code agent to prove the trace and replay contract
before Pipecat or external speech/model vendors are introduced.

## Architecture

The repository holds two applications that run side by side:

| App | Path | What it is |
|---|---|---|
| **VoiceFlow Studio** | `voiceflow-studio/` | The *agent under test*: a multi-bot Pipecat voice platform (FastAPI + Postgres API, Next.js flow editor and browser test calls). Every session is recorded as a replay-grade, six-layer trace. |
| **ArtemisVoice** | `backend/`, `frontend/` | The *causal debugger*: imports a Studio trace, replays it with one layer swapped for an oracle (STT, endpointing, LLM, tools, TTS), proves which layer caused the failure, proposes a bounded patch and validates it before a human approves it. |

```
 Browser ── Studio UI :3000 ──REST/WebRTC──▶ Studio API :8000 ──▶ Postgres :5433
    │                                          ▲   (trace, simulate, replay, patch candidates)
    │                                          │ STUDIO_API_URL
    └──── Artemis dashboard :5173 ──/api──▶ Artemis API :8001 ──▶ SQLite (artemis_voice.db)
```

- Studio exposes `GET /api/sessions/{id}/trace`, `/artemis-evidence`, `/simulate`, `/replay`
  and patch-candidate endpoints. Artemis calls them through `STUDIO_API_URL`; Studio never
  depends on Artemis to run calls.
- Both UIs deep-link to each other (`STUDIO_UI_URL/sessions/{id}`, "Open in Artemis").
- A patch only reaches a published Studio flow after a human approves it in Artemis.

The binding contract between the apps (trace schema, endpoints, ports, env vars) is
[docs/INTEGRATION_CONTRACT.md](docs/INTEGRATION_CONTRACT.md); the plan is
[docs/VOICEFLOW_ROADMAP.md](docs/VOICEFLOW_ROADMAP.md).

### Ports

| Service | Host port | Dev command | Docker service |
|---|---|---|---|
| Studio API | 8000 | `uv run uvicorn app.main:app --port 8000` (in `voiceflow-studio/backend`) | `studio-api` |
| Studio UI | 3000 | `npm run dev` (in `voiceflow-studio/frontend`) | `studio-ui` |
| Artemis API | 8001 | `.venv/bin/uvicorn artemisVoice.Evals.api.app:app --port 8001` | `artemis-api` (8001→8000) |
| Artemis dashboard | 5173 | `npm run dev` (in `frontend`, proxies `/api` → :8001) | `artemis-dashboard` (nginx) |
| Postgres (Studio only) | 5433 | `docker compose up -d postgres` | `postgres` |

### Quickstart

Requires Python 3.12, [uv](https://docs.astral.sh/uv/), Node 22 and Docker.

```bash
make setup        # pipecat submodule, Studio uv sync, Artemis .venv, npm installs
cp voiceflow-studio/backend/.env.example voiceflow-studio/backend/.env   # add provider keys; never commit
make dev          # Postgres in Docker + all four dev servers, prints URLs; Ctrl-C stops them
make test         # Artemis ruff + pytest, Studio pytest
```

Full Docker stack instead: `make up` / `make down` (see [docs/DOCKER.md](docs/DOCKER.md)).
For live microphone calls on macOS, run the Studio API on the host
(`make dev`, or `make up-hybrid` + `make studio-api`).
The end-to-end demo, including the five failure demos, is in
[docs/DEMO_RUNBOOK.md](docs/DEMO_RUNBOOK.md).

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
uvicorn artemisVoice.Evals.api.app:app --reload --port 8001   # 8000 is the Studio API
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
For the reasoning behind the workflow and an end-to-end example, read
[ArtemisVoice from first principles](docs/FIRST_PRINCIPLES.md).

## VoiceFlow Studio integration (local dev)

Run the Artemis API on **port 8001** next to VoiceFlow Studio (port 8000); the
container image still listens on 8000 internally.

```bash
STUDIO_API_URL=http://localhost:8000 STUDIO_UI_URL=http://localhost:3000 \
ARTEMIS_CORS_ORIGINS=http://localhost:5173,http://localhost:3000 \
uvicorn artemisVoice.Evals.api.app:app --reload --port 8001
```

- `POST /voiceflow/import {session_id}` imports a replay-grade Studio trace as a
  `RunTrace(source="voiceflow")` (422 with `missing` when not replay-ready); `GET /voiceflow/runs`.
- `POST /runs/{id}/diagnoses/all {trials}` runs control + five oracle arms through Studio's
  sandboxed replay and returns a Fisher-exact verdict (`proven|compound|likely|inconclusive|not_the_agent`).
- `POST /voiceflow/patches {source_run_id, layer}` → bounded Studio patch candidate;
  `/patches/{id}/validate` (failing / held-out / regression oracles) must pass before
  `/patches/{id}/approve` (named human, forwarded as `X-Approved-By`) promotes it;
  `/patches/{id}/remember` saves a Studio regression oracle.
- `GET /readiness` reports providers, database, Studio reachability and replay readiness.

See [the integration contract](docs/INTEGRATION_CONTRACT.md) §3-§4.

The evaluator also includes four caller-valid red-team mutation operators:
hard entities, self-correction, policy boundaries, and intent switches.
