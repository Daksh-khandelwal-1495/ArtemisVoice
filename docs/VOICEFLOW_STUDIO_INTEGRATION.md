# VoiceFlow Studio integration

VoiceFlow Studio (`voiceflow-studio/`) is the **agent under test**. It runs Pipecat bots with
versioned flows and live browser calls. ArtemisVoice (`backend/`, `frontend/`) is the **causal
debugger**. This page explains how the two connect. The binding details (trace schema,
endpoints, ports, env vars and which agent owns what) are in
**[INTEGRATION_CONTRACT.md](INTEGRATION_CONTRACT.md)**. The phased plan is in
[VOICEFLOW_ROADMAP.md](VOICEFLOW_ROADMAP.md), and the step-by-step demo is in
[DEMO_RUNBOOK.md](DEMO_RUNBOOK.md).

## Topology

```
Studio UI :3000 ── REST + WebRTC ──▶ Studio API :8000 ──▶ Postgres :5433
                                        ▲   /api/sessions/{id}/trace · /artemis-evidence
                                        │   /api/bots/{id}/simulate · /api/sessions/{id}/replay
                                        │   /api/bots/{id}/patch-candidates[/{cid}/promote]
                                        │   /api/bots/{id}/oracles · /api/artifacts/{sid}/{file}
                                        │
Artemis dashboard :5173 ── /api ──▶ Artemis API :8001 ──▶ SQLite
                                     VoiceFlowAdapter (STUDIO_API_URL)
```

| Direction | Env var | Dev value | Docker value |
|---|---|---|---|
| Artemis API calls Studio API | `STUDIO_API_URL` | `http://localhost:8000` | `http://studio-api:8000` |
| Artemis links to the Studio UI | `STUDIO_UI_URL`, `VITE_STUDIO_UI_URL` | `http://localhost:3000` | same (browser URL) |
| Dashboard reads Studio directly | `VITE_STUDIO_API_URL` | `http://localhost:8000` | same (browser URL) |
| Studio links to Artemis | `ARTEMIS_DASHBOARD_URL`, `NEXT_PUBLIC_ARTEMIS_DASHBOARD_URL` | `http://localhost:5173` | same |
| Studio calls Artemis (optional) | `ARTEMIS_API_URL` | `http://localhost:8001` | `http://artemis-api:8000` |
| Studio CORS | `CORS_ORIGINS` | `http://localhost:3000,http://localhost:5173` | same |
| Artemis CORS | `ARTEMIS_CORS_ORIGINS` | `http://localhost:5173,http://localhost:3000` | same |

Run everything with `make dev` (host processes plus Postgres in Docker) or `make up` (all in
Docker; see [DOCKER.md](DOCKER.md) for the WebRTC caveat on macOS).

## Data flow

1. **Session to trace.** Every live call or simulation is recorded by `app/tracing/` as a
   `voiceflow-trace/v1` document. Each caller turn has six layers: caller, endpointing, STT, LLM,
   tools and TTS. Each layer has inputs, outputs, settings and SHA-256-addressed artifacts. Live
   calls store real audio. Simulations are labelled `source: "simulation"` and use text artifacts.
   `replay_ready` is `false` (with a `missing` list) whenever any layer is absent. Missing data is
   never inferred.
2. **Evidence export.** `GET /api/sessions/{id}/artemis-evidence` (Studio **Export evidence**)
   is the human-readable bundle. It honours sensitive-field masking (`?reveal=true` uses Studio's
   local debugging policy).
3. **Import.** `POST /voiceflow/import {session_id}` on Artemis fetches the trace. It refuses with
   422 and the `missing` list when the trace is not replay-ready. Otherwise it stores a `RunTrace`
   with `source=voiceflow`.
4. **Causal replay.** `POST /runs/{id}/diagnoses/all` runs a control arm and the five oracle arms
   through Studio's `POST /api/sessions/{id}/replay`. Replay is a text driver over the real
   compiled flow. It restores the session's initial state and dataset snapshot, forces
   `API_CALL_MOCK=true`, and never writes datasets or performs side effects. Fisher exact tests
   decide `proven` / `likely` / `inconclusive`.
5. **Patch.** A proven layer maps to a bounded `PatchSpec` (`stt_keyterm_boost`,
   `vad_stop_secs`, `stricter_validation`, `confirmation_readback`, `fallback_route`). Studio
   creates a **draft** candidate. Artemis validates it on the failing, held-out and regression
   oracles. Only a human approval (`X-Approved-By`) promotes it to a published flow. Accepted
   failures become regression oracles.

## Test oracle and demo data

The seeded Tata Motors bot ships oracle `tata-happy` (Nexon / Pune / 411001 / Asha Sharma /
9876543210) and five fault variants (`tata-stt`, `tata-endpointing`, `tata-llm`, `tata-tools`,
`tata-tts`). The oracle comes from the test case, never from an LLM. `make demo-seed` (re)loads
them. Use **draft** mode and `API_CALL_MOCK=true` for all demos.

## Provider roles

Studio defaults to Deepgram STT, OpenAI LLM (`gpt-6-luna`) and Soniox TTS. The bot Settings tab
can also switch STT and TTS to OpenAI, in which case only `OPENAI_API_KEY` is needed. Provider
credits pay for the live call and the replay LLM. They do not define the expected result; the
oracle and the sandbox snapshot do that.
