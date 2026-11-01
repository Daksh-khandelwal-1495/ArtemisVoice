# VoiceFlow Studio

Multi-bot voice agent platform built on [Pipecat](https://github.com/pipecat-ai/pipecat) (cloned in `vendor/pipecat`, flows via `pipecat.flows`).
Each bot has a configurable STT / LLM / TTS stack and a visual, versioned conversation flow stored in Postgres.

- **STT**: Deepgram (default), Soniox, OpenAI
- **LLM**: OpenAI (default `gpt-6-luna`; any model, plus OpenAI-compatible `base_url`)
- **TTS**: Soniox (default), Deepgram Aura, OpenAI
- **Seed bots**: Tata Motors (test drive), Kia (service booking), DHI (clinic consultation), DCB Bank (loan pre-qualification)

## Run

```bash
git submodule update --init voiceflow-studio/vendor/pipecat   # Pipecat source (pinned)
cd voiceflow-studio
docker compose up -d                       # Postgres on :5433
cp backend/.env.example backend/.env       # add OPENAI_API_KEY, DEEPGRAM_API_KEY, SONIOX_API_KEY (never commit .env)
cd backend && uv sync && uv run uvicorn app.main:app --port 8000   # creates tables + auto-seeds
cd frontend && npm install && npm run dev  # http://localhost:3000
```

Tests: `cd backend && uv run pytest -q` · Reseed: `uv run python -m app.seed.loader [--reset]` · UI without backend: `NEXT_PUBLIC_MOCK=1 npm run dev`

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
