# ArtemisVoice × VoiceFlow Studio — Integration Contract (binding for all agents)

ArtemisVoice (repo root `backend/`, `frontend/`) is the **voice eval / causal debugger**.
VoiceFlow Studio (`voiceflow-studio/`) is the **agent under test** (Pipecat bots, flows, live calls).
Goal (see the 7-phase plan in `docs/VOICEFLOW_ROADMAP.md`): a Studio session becomes a replay-grade
trace; Artemis replays it with one layer swapped for an oracle, proves which layer caused a failure,
proposes a bounded patch, validates it against failing / held-out / regression cases, and only a
human-approved patch reaches a published flow.

## Ports / env

| Service | Host port | Notes |
|---|---|---|
| Studio API `voiceflow-studio/backend` | 8000 | env `ARTEMIS_API_URL=http://localhost:8001`, `ARTEMIS_DASHBOARD_URL=http://localhost:5173`, `ARTIFACTS_DIR=./artifacts`, `API_CALL_MOCK=true`, `REPLAY_LLM_SEED=7` |
| Studio UI `voiceflow-studio/frontend` | 3000 | `NEXT_PUBLIC_ARTEMIS_DASHBOARD_URL` |
| Artemis API root `backend/` | **8001** | env `STUDIO_API_URL=http://localhost:8000`, `STUDIO_UI_URL=http://localhost:3000`, `ARTEMIS_CORS_ORIGINS=http://localhost:5173,http://localhost:3000` |
| Artemis dashboard root `frontend/` | 5173 | Vite proxy `/api` → `http://localhost:8001` |
| Postgres | 5433 | Studio only |

Never commit `.env` files or keys. Do not copy `.env` files between folders.

## 1. Replay-grade trace (Studio → Artemis) — `GET /api/sessions/{id}/trace`

Studio module `app/tracing/` (schema in `app/tracing/schema.py`, owned by the **Studio tracing agent**).
Each caller turn records all six layers. Live calls capture real artifacts; simulated sessions are
labelled `source: "simulation"` and use text artifacts (`media_type: text/plain`, sha256 of the text)
— never fake audio.

```jsonc
{
  "schema_version": "voiceflow-trace/v1",
  "source": "live|simulation",
  "session": { "id", "bot_id", "bot_slug", "bot_name", "flow_version_id", "flow_version", "mode",
               "status", "end_reason", "started_at", "ended_at", "config_snapshot", "config_hash" },
  "seed": 7,
  "fault_profile": null | FaultProfile,        // §3, simulations / demo variants
  "oracle_id": "uuid|null",
  "replay_ready": true, "missing": ["turn 2: caller audio artifact", "..."],   // honest readiness
  "dataset_snapshot_id": "uuid",               // §2
  "initial_state": { /* session vars before turn 0 (static flow vars + builtins) */ },
  "turns": [ {
    "index": 0, "node_id": "collect_location",
    "state_before": { "car_model": "Nexon" }, "state_after": { "car_model": "Nexon", "city": "Pune", "pincode": "411001" },
    "layers": {
      "caller":        { "started_at", "finished_at", "input": {},
                         "output": { "intended_text": "Pune, 411001" | null, "audio": Artifact|null }, "settings": {} },
      "endpointing":   { "started_at", "finished_at", "input": {},
                         "output": { "speech_start_ms": 0, "speech_end_ms": 1830, "interrupted_bot": false,
                                     "segment_text": "Pune, 4110" /* what reached STT, simulation */ },
                         "settings": { "vad": {...} } },
      "speech_to_text":{ "started_at", "finished_at", "input": { "audio": Artifact|null },
                         "output": { "interim_hypotheses": ["Pune", "Pune 41"], "final_hypothesis": "Pune, 411001", "confidence": 0.93 },
                         "settings": { "provider", "model", "language", "keywords" } },
      "llm":           { "started_at", "finished_at",
                         "input": { "context_hash": "sha256", "message_count": 12, "node_id": "collect_location" },
                         "output": { "response_text": "Thanks! Let me find dealers.",
                                     "tool_calls": [ { "name": "submit_collect_location", "arguments": {...}, "result": {...} } ] },
                         "settings": { "provider", "model", "temperature", "max_tokens", "seed" } },
      "tools":         { "started_at", "finished_at",
                         "input": { "calls": [ { "kind": "lookup|api_call", "dataset|url": "...", "query": {...} } ] },
                         "output": { "results": [ { "kind", "ok": true, "rows|response": ... } ],
                                     "state_before": {...}, "state_after": {...} }, "settings": { "api_call_mock": true } },
      "text_to_speech":{ "started_at", "finished_at", "input": { "text": "Thanks! Let me find dealers." },
                         "output": { "spoken_text": "Thanks! Let me find dealers.", "audio": Artifact|null },
                         "settings": { "provider", "model", "voice", "speed", "sample_rate" } }
    } } ],
  "final_state": { ... }, "end_node": "end_booked", "events": [ ... full ordered event log ... ]
}
Artifact = { "uri": "artifact://<session_id>/<file>", "sha256": "64 hex", "media_type": "audio/wav|text/plain", "bytes": 1234 }
```
`GET /api/artifacts/{session_id}/{file}` serves artifacts. Live calls: caller audio = the user-turn
audio segment (VAD start→stop) as 16 kHz mono WAV; TTS audio = the bot audio per turn as WAV.
`replay_ready=false` whenever any layer is missing — never filled with inferred data.

## 2. Sandbox + test oracle (Studio, **replay agent**)

- `API_CALL_MOCK=true` forced during replay; replay never writes datasets, never performs api_call
  side effects (bookings/callbacks), and records sessions with `mode="replay"` (excluded from normal
  session lists unless `?include_replay=true`).
- Dataset snapshots: `dataset_snapshots(id, created_at, datasets JSONB {name: rows})`; a snapshot is
  taken at session start (live + simulation) and referenced by the trace. Replays read lookups from
  the snapshot, not live tables.
- `test_oracles(id, bot_id, slug, name, caller_turns JSONB [str] /* true caller utterances in order */,
  expected_fields JSONB {name: str}, expected_lookup JSONB {dataset, match {col: val}} | null,
  expected_end_node, expected_status, expected_bot_response JSONB {contains: [str]} | null, created_at)`.
  `POST /api/sessions/{id}/oracle {oracle_id, turn_truths?: {turn_index: text}}` binds an oracle to a
  session (turn truths default to `caller_turns` aligned by order).
- Outcome check `passed = expected_fields ⊆ final captured (normalised) ∧ end_node/status match ∧
  expected lookup row found ∧ final confirmation spoken_text contains every expected_bot_response.contains`.

## 3. Replay API + fault injection (Studio, **replay agent**)

`POST /api/sessions/{id}/replay` `{intervention, trials: 1-10, seed?: int, patch?: PatchSpec}` →
```jsonc
{ "session_id", "intervention", "seed", "trials": [ { "trial": 0, "passed": false,
    "outcome": { "captured": {...}, "end_node", "status", "spoken_texts": [...] },
    "run_trace": RunTrace /* Artemis schema 1.0, see §4 */, "replay_session_id": "uuid" } ] }
```
Interventions: `control | stt_oracle | endpointing_oracle | llm_oracle | tools_oracle | tts_oracle`.
Replay = text driver over the **real compiled flow** (`app/voice/compiler.py` FlowRuntime + node
configs + bot LLM with temperature 0 and `seed`), restoring `initial_state` + dataset snapshot first.
Per caller turn i:
- control: bot input = recorded `final_hypothesis`, recorded endpointing; tools from snapshot; TTS =
  the session's TTS behaviour (fault profile applies). Faults in the session's `fault_profile` are
  part of the system under test and apply in control.
- `stt_oracle`: input = oracle truth of the *segment that reached STT* (endpointing kept).
- `endpointing_oracle`: input = STT(full truth utterance) — the recorded STT behaviour (fault map /
  keyword rules) applied to the complete utterance, so only the cut-off is fixed.
- `llm_oracle`: tool-call arguments for fields named in `expected_fields` are corrected to oracle
  values (and a missing submit is issued) — everything else from the real LLM.
- `tools_oracle`: lookups/api_calls return the oracle `expected_lookup` row / successful mock.
- `tts_oracle`: spoken text = LLM response text exactly.

`FaultProfile` (stored on simulations / demo variants; live calls may opt in for demos):
```jsonc
{ "stt": { "replacements": [ {"from": "411001", "to": "41100I"}, {"from": "Nexon", "to": "Nixon"} ],
           "keyword_protected": true /* a replacement is skipped when the source word is in bot.config.stt.keywords */ },
  "endpointing": { "truncate_field": "pincode", "keep_chars": 4 },
  "llm": { "swap_field": {"field": "pincode", "with_value_of": "phone"} },
  "tools": { "lookup_override": {"dataset": "tata_dealers", "rows": [ {...wrong dealer...} ]}, "api_error": false },
  "tts": { "replacements": [ {"from": "Nexon", "to": "Nixon"} ] } }
```
`keyword_protected` makes the STT keyterm-boost patch genuinely fix the STT fault.

Simulations: `POST /api/bots/{id}/simulate {oracle_id, fault_profile?, mode: draft|published, seed?}`
→ `{session_id, passed, outcome, trace_url}`; the caller is scripted from `oracle.caller_turns`
(deterministic — no LLM caller), so runs are reproducible. Seeded demo (Tata Motors): oracle
`tata-happy` (Nexon / Pune / 411001 / Asha Sharma / 9876543210) and five fault variants
`tata-stt`, `tata-endpointing`, `tata-llm`, `tata-tools`, `tata-tts` (`app/seed/demos/tata.json`).

Patch application for validation: `PatchSpec = {type: stt_keyterm_boost|confirmation_readback|stricter_validation|fallback_route|vad_stop_secs, params}`.
`POST /api/bots/{id}/patch-candidates {patch, base_version_id?}` → creates a **draft** flow version /
config copy (never publishes) → `{candidate_version_id, config, graph_diff}`.
`POST /api/bots/{id}/patch-candidates/{cid}/promote` requires header `X-Approved-By` and is only called
by Artemis after human approval → publishes. Regression scenarios: `POST /api/bots/{id}/oracles` (CRUD
`GET/POST /api/bots/{id}/oracles`, `GET/PATCH/DELETE /api/oracles/{id}`, `tags: ["regression"]`).

## 4. Artemis side (**Artemis backend agent**)

Generalise the trace contract without breaking the deterministic demo or its 19 tests:
- `RunTrace` gains optional `source` (`deterministic|voiceflow`), `studio_session_id`, `expected_spoken_contains: list[str]`;
  success for voiceflow runs = expected state ⊆ final state ∧ spoken text contains all expected strings.
  `database_state` for voiceflow = captured fields + `__end_node` + `__status` as strings.
- `VoiceFlowAdapter(VoiceAgentAdapter)` in `artemisVoice/Evals/agents/voiceflow.py`: talks to Studio over HTTP
  (`STUDIO_API_URL`), `config_hash` = Studio session config_hash, `run(scenario)` = Studio simulate,
  `replay(source_run, turn_index, intervention, seed)` = Studio replay endpoint. Replay engine delegates
  to the adapter when present (deterministic agent keeps its in-process path).
- `POST /voiceflow/import {session_id}` → fetches `/trace` from Studio, refuses with 422 + `missing` when
  `replay_ready=false`, stores a RunTrace (`source=voiceflow`). `GET /voiceflow/runs`.
- `POST /runs/{id}/diagnoses` accepts every intervention for voiceflow runs; `POST /runs/{id}/diagnoses/all
  {trials}` runs control + 5 oracle arms → list of Diagnosis (Fisher exact, proven ≥0.5 & p<0.05, likely ≥0.3).
  Summary string like "Replacing only the STT result changed the outcome from failure to success in 5/5 replays."
- Patches for voiceflow (Phase 6): `POST /voiceflow/patches {source_run_id, layer}` maps a proven layer
  to a bounded PatchSpec (stt → `stt_keyterm_boost` with the misheard expected value; endpointing →
  `vad_stop_secs` +0.4 / `confirmation_readback`; llm → `stricter_validation` (regex for the field) +
  `confirmation_readback`; tools → `fallback_route`; tts → `confirmation_readback`), creates a Studio
  patch candidate, then `/patches/{id}/validate` replays failing + held-out + regression oracles against
  the candidate via Studio simulate; `/patches/{id}/approve` (human) → Studio promote. Accepted
  failures → `/patches/{id}/remember` saves a Studio regression oracle + Artemis RegressionCase.
- `GET /readiness` → providers configured, DB healthy, Studio reachable, replay readiness counts,
  missing artifacts per imported run.

## 5. UIs
- **Artemis dashboard** (root `frontend/`): nav **Overview** (readiness cards, recent diagnoses),
  **VoiceFlow runs** (import by session id, list), **Run detail** (6-layer turn timeline with artifacts
  playable, oracle vs actual diff, "Run causal diagnosis" → arms table with pass counts, p-values,
  verdict sentence), **Patches** (proposal, gate results per set, Approve, Remember), existing **Demo**.
  Links to Studio session (`STUDIO_UI_URL/sessions/{id}`).
- **Studio UI**: session detail gets **Trace** tab (per-turn layers, audio players, replay readiness,
  missing list), "Export evidence", "Open in Artemis"; bot workspace gets **Oracles & Demos** tab
  (oracle CRUD, fault variant picker, Simulate, link to Artemis import).
