# VoiceFlow Studio

Multi-bot voice agent platform built on [Pipecat](https://github.com/pipecat-ai/pipecat) (cloned in `vendor/pipecat`, flows via `pipecat.flows`).
Each bot has a configurable STT / LLM / TTS stack and a visual, versioned conversation flow stored in Postgres.

- **STT**: Deepgram (default), Soniox, OpenAI
- **LLM**: OpenAI (default `gpt-6-luna`; any model, plus OpenAI-compatible `base_url`)
- **TTS**: Soniox (default), Deepgram Aura, OpenAI
- **Seed bots**: Tata Motors (test drive), Kia (service booking), DHI (clinic consultation), DCB Bank (loan pre-qualification)

## Run

Studio is one half of the ArtemisVoice platform. From the **repository root**:

```bash
make setup     # git submodule update --init voiceflow-studio/vendor/pipecat, uv sync, npm install, Artemis venv
cp voiceflow-studio/backend/.env.example voiceflow-studio/backend/.env   # OPENAI / DEEPGRAM / SONIOX keys; never commit
make dev       # Postgres :5433 (docker) + Studio API :8000 + Studio UI :3000 + Artemis :8001/:5173
make up        # or: the whole platform in Docker (root compose.yaml)
```

See the root README (architecture and ports), [`docs/DEMO_RUNBOOK.md`](../docs/DEMO_RUNBOOK.md)
and [`docs/INTEGRATION_CONTRACT.md`](../docs/INTEGRATION_CONTRACT.md).

### Studio only (manual)

```bash
cd voiceflow-studio
git submodule update --init vendor/pipecat           # Pipecat source (pinned); required
docker compose up -d postgres                        # Postgres on :5433
cp backend/.env.example backend/.env                 # add keys (never commit .env)
cd backend && uv sync && uv run uvicorn app.main:app --port 8000   # creates tables + auto-seeds
cd ../frontend && npm install && npm run dev         # http://localhost:3000 (second terminal)
```

`voiceflow-studio/docker-compose.yml` can also run the API in Docker (`docker compose up --build -d`).
It overrides `DATABASE_URL` to `postgres:5432`, and `backend/.env` is optional. Do not run it
alongside the root stack: both publish 5433 and 8000. **Live microphone calls need the API on the
host** on Docker Desktop (macOS/Windows), because WebRTC media cannot reach the container. See
[`docs/DOCKER.md`](../docs/DOCKER.md#live-microphone-calls-webrtc).

Check the database and configured provider keys without exposing their values:

```bash
curl http://localhost:8000/api/health
```

Allow microphone access for `http://localhost:3000` when starting a Test Call, and use headphones.

Tests: `cd backend && uv run pytest -q` (sqlite fallback when Postgres :5433 is down) · Reseed: `uv run python -m app.seed.loader [--reset]` (or `make demo-seed` from the root) · UI without backend: `NEXT_PUBLIC_MOCK=1 npm run dev`

## Architecture

```
Browser (Next.js + React Flow + @pipecat-ai/client-js)
   │  REST /api/*            │  WebRTC  POST/PATCH /api/bots/{id}/offer?mode=draft|published
   ▼                         ▼
FastAPI ─ app/api (bots, flows, versions, datasets, sessions)
        ─ app/voice: providers factory → compiler (FlowGraph → pipecat.flows NodeConfig)
                     → pipeline (SmallWebRTC → STT → LLM → TTS) + recorder
        ─ app/services: flow_validator, safe expression evaluator
   ▼
Postgres: bots, flow_versions, call_sessions, session_events, captured_fields, datasets, dataset_rows
```

Node types: `start`, `collect` (typed fields, required/optional, validation, confirm, sensitive, retries),
`conversation` (intent routing / FAQ), `decision` (expression branches), `lookup` (query a Postgres dataset),
`api_call` (templated HTTP, mockable), `transfer`, `end`. See `CONTRACT.md` for full semantics and edge-case handling.

`frontend/public/daily/` vendors Daily's call-machine bundles for `@daily-co/daily-js@0.94.0` (used by the
SmallWebRTC client for mic handling) so calls don't depend on Daily's CDN; re-download them if you upgrade daily-js.
