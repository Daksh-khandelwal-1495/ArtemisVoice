# VoiceFlow Studio — Shared Contract

Multi-bot voice agent platform: Pipecat (vendored at `vendor/pipecat`, flows = `pipecat.flows`) +
FastAPI + Postgres backend, Next.js flow-builder frontend.

Default stack: STT **Deepgram**, LLM **OpenAI**, TTS **Soniox**. All three can be switched per bot from the UI.

## Layout & ownership

| Path | Owner | Notes |
|---|---|---|
| `backend/app/schemas/flow.py`, `providers.py` | **contract (do not change shape)** | Pydantic source of truth. Additive optional fields OK. |
| `backend/app/main.py`, `app/core/config.py`, `app/db/*`, `app/api/*`, `app/services/flow_validator.py`, `app/services/expressions.py`, `app/seed/loader.py` | Backend API agent | |
| `backend/app/voice/*` | Voice runtime agent | Exposes `router` in `app/voice/router.py`; main.py includes it. |
| `backend/app/seed/bots/*.json`, `backend/app/seed/datasets/*.json` | Seed content agent | |
| `frontend/*` | Frontend agent | Next.js App Router, TS, Tailwind, `@xyflow/react`. |

Ports: Postgres `localhost:5433` (docker-compose), backend `:8000`, frontend `:3000`.
Python: `cd backend && uv run ...` (venv already synced; add deps with `uv add`).

## Database (Postgres, SQLAlchemy 2 async, `app/db/models.py`)

All ids UUID, timestamps `timestamptz` default now().

- **bots**: `id, slug (unique), name, description, industry, avatar_color, language, status ('draft'|'published'|'archived'), config JSONB (BotConfig), published_version_id FK flow_versions NULL, created_at, updated_at`
- **flow_versions**: `id, bot_id FK cascade, version int, graph JSONB (FlowGraph), note, created_at`; unique(bot_id, version). Every PUT of the flow creates a new version (draft = highest version). Publish points `bots.published_version_id` at a version.
- **call_sessions**: `id, bot_id FK, flow_version_id FK, mode ('draft'|'published'), status ('active'|'completed'|'transferred'|'abandoned'|'error'), end_reason, current_node, config_snapshot JSONB, variables JSONB (all captured vars), started_at, ended_at, duration_secs`
- **session_events**: `id, session_id FK cascade, ts, type ('user_transcript'|'bot_transcript'|'node_enter'|'function_call'|'validation_error'|'field_captured'|'lookup'|'api_call'|'error'|'idle'|'end'), node_id, payload JSONB`
- **captured_fields**: `id, session_id FK cascade, node_id, field_name, value JSONB, valid bool, attempts int, captured_at`; unique(session_id, field_name) (upsert)
- **datasets**: `id, name unique, description, columns JSONB (list[str])`
- **dataset_rows**: `id, dataset_id FK cascade, data JSONB` (GIN index on data)

`app/db/session.py` exports `engine`, `SessionLocal` (async_sessionmaker), `get_db` (FastAPI dep), `init_db()` (create_all).

## REST API (prefix `/api`, JSON)

- `GET /health` → `{ok, db: bool, providers: {deepgram: bool, openai: bool, soniox: bool}}`
- `GET /providers` → `{stt:[...], llm:[...], tts:[...]}` from `PROVIDER_CATALOG` + `configured: bool` per entry (env key present), env_key omitted.
- `GET /bots` → `BotSummary[]` = `{id, slug, name, description, industry, avatar_color, language, status, config, draft_version, published_version, node_count, session_count, last_session_at, updated_at}`
- `POST /bots` `{name, slug?, description?, industry?, avatar_color?, template?: 'blank'|<existing bot slug>}` → BotDetail (creates v1 with a minimal start→end flow, or copies template). 409 on duplicate slug.
- `GET /bots/{id_or_slug}` → BotDetail = BotSummary + `{graph (latest draft FlowGraph), draft_version_id}`
- `PATCH /bots/{id}` `{name?, description?, industry?, avatar_color?, language?, status?, config?: BotConfig}` (config validated with BotConfig; 422 with pydantic errors)
- `DELETE /bots/{id}` → 204 (soft: status=archived unless `?hard=true`)
- `POST /bots/{id}/duplicate` → BotDetail
- `GET /bots/{id}/flow` → `{version, version_id, graph}`
- `PUT /bots/{id}/flow` `{graph: FlowGraph, note?}` → `{version, version_id, validation: FlowValidationResult}`. Saves even with errors (draft), but 422 if graph fails Pydantic parsing.
- `POST /bots/{id}/flow/validate` `{graph}` → FlowValidationResult
- `POST /bots/{id}/publish` `{version_id?}` → 400 with validation if the version has errors; else bot with published_version.
- `GET /bots/{id}/versions` → `[{id, version, note, created_at, is_published, node_count}]`
- `POST /bots/{id}/versions/{version_id}/restore` → copies it as new latest draft version
- `GET /datasets` → `[{id,name,description,columns,row_count}]`; `GET /datasets/{name}/rows?q=&limit=&offset=`; `POST /datasets/{name}/rows` `{data}`; `DELETE /datasets/{name}/rows/{row_id}`
- `GET /sessions?bot_id=&status=&limit=&offset=` → `{items:[{id,bot_id,bot_name,status,end_reason,mode,started_at,ended_at,duration_secs,captured_count,required_missing}], total}`
- `GET /sessions/{id}` → session + `events[]` + `captured[]`
- `GET /sessions/{id}/artemis-evidence` → a per-turn evidence bundle for ArtemisVoice. This is intentionally marked `replay_eligible: false` until raw audio, provider-native layer artifacts, an expected-outcome oracle, and sandbox replay state are supplied.
- `GET /bots/{id}/stats` → `{total_sessions, completed, transferred, abandoned, avg_duration_secs, completion_rate, field_capture_rates: {field: rate}}`

Errors: `{detail: string | object}` with proper status codes.

### Flow validation rules (`app/services/flow_validator.py`, returns FlowIssue list)
Errors: missing transition target; unreachable node from initial (warning); no `end`/`transfer` node reachable (error); `collect` node with zero fields; enum field without options; invalid regex; duplicate field name across nodes (warning — later overwrites); `expression` transition whose expression fails to parse or references unknown vars (warning for unknown); `lookup` without dataset / unknown dataset (error if datasets list provided); `api_call` without url and without mock_response; `end` node with outgoing transitions (warning); non-end node with no transitions (error); `on_failure == goto` without valid target; node type `start` must be initial and at most one; `decision` nodes need ≥2 transitions and only `expression`/`intent`/`fallback` kinds; `{{var}}` placeholders in prompts referencing vars never captured upstream (warning).

### Expressions (`app/services/expressions.py`)
`evaluate(expr: str, vars: dict) -> bool` and `parse_names(expr) -> set[str]`. Safe AST evaluator (no eval): comparisons, and/or/not, in, literals, names, arithmetic + - * /, `len()`, `lower()`. Unknown var → None (comparisons with None are False). Used by both validator and voice runtime.

## Node semantics (runtime compiles FlowGraph → pipecat.flows NodeConfig)

- `start`: greeting/persona. `say_on_enter` or `behavior.greeting`. Transitions as for conversation.
- `collect`: LLM gets one tool `submit_<node_id>` whose JSON-schema properties = `fields` (required list = required fields). Handler validates every field server-side (type coercion + validation rules + Indian formats: phone 10 digits starting 6-9 optionally +91; pincode 6 digits not starting 0; PAN `[A-Z]{5}[0-9]{4}[A-Z]`; vehicle_reg like `MH12AB1234`; amount accepts "5 lakh"/"2.5 crore"/"50k"). Invalid → result `{status:'invalid', errors:{field: msg}}` so LLM re-asks only those; per-field attempts tracked; exceeding `max_retries` on a required field triggers node `on_failure`; optional field over retries is skipped. Partial submissions allowed: valid ones stored, missing required ones re-asked. `confirm: true` fields → tool gets a `confirmed: boolean` arg and the prompt instructs read-back. Then transition: first matching `expression` (by priority), else `on_complete`, else `fallback`.
- `conversation`: free Q&A (FAQ). One tool per `intent` transition `goto_<target>` with description = condition.
- `decision`: no user turn (`respond_immediately` irrelevant); evaluates expression transitions immediately on entry, else `fallback`.
- `lookup`: queries `dataset_rows` where `data->>col` matches captured vars; stores into vars[`save_as`] and `<save_as>_found` bool; then decision-like transition (expressions may use `<save_as>_found`).
- `api_call`: HTTP with `{{var}}` templating (mock when `API_CALL_MOCK=true` or url empty); stores result; errors → `<save_as>_error` and fallback transition.
- `transfer`: speaks message, emits `transfer` event, ends session status `transferred`.
- `end`: speaks `say_on_enter`, ends with `end_reason`, status `completed`.
Global: every node also gets `request_human_agent` tool (→ first transfer node, or end) and `end_call` tool (user wants to stop → status `abandoned`). Idle: nudge `idle_max_nudges` times then end `abandoned`. Max call duration enforced. Provider errors → speak `fallback_message` if TTS works, log `error` event, status `error`.

## Voice connection (`app/voice/router.py`)

SmallWebRTC (pipecat `SmallWebRTCTransport`), compatible with `@pipecat-ai/client-js` + `@pipecat-ai/small-webrtc-transport`:
- `POST /api/bots/{id}/offer?mode=draft|published` body `{sdp, type, pc_id?, restart_pc?, request_data?: {config_override?: BotConfig partial}}` → `{sdp, type, pc_id}`. Mode default `draft` (test latest flow). 409 if published mode and nothing published. 503 with clear message if a required API key is missing for the chosen providers.
- `PATCH /api/bots/{id}/offer` ICE candidates `{pc_id, candidates:[...]}` (mirror pipecat runner `run.py` ~L1123-1180).
- The bot uses RTVI. Server → client custom messages via `RTVIServerMessageFrame(data=...)`, `data.type` one of:
  - `{"type":"session", "session_id", "bot_id", "mode", "flow_version"}` (first)
  - `{"type":"node", "node_id", "label", "node_type"}` on every node enter
  - `{"type":"vars", "vars": {...masked}}` after captures
  - `{"type":"validation", "node_id", "errors": {field: msg}}`
  - `{"type":"event", "event": <session_events.type>, "payload": {...}}`
  - `{"type":"ended", "status", "end_reason"}`
  Transcripts come through standard RTVI user/bot transcription events.

## Frontend (`frontend/`, Next.js App Router + TS + Tailwind + @xyflow/react)

`NEXT_PUBLIC_API_URL` default `http://localhost:8000`. TS types mirror the Pydantic schemas above.
Pages: `/` bots dashboard; `/bots/[id]` workspace with tabs **Flow** (canvas + node palette + inspector + validation panel), **Settings** (STT/LLM/TTS/VAD/behavior), **Test call** (WebRTC call, live transcript, highlighted current node, captured fields), **Sessions**, **Versions**; `/sessions/[id]`; `/datasets`.

## Seed bots
`tata-motors` (test drive booking: model, city, pincode → dealer lookup, date, name, phone), `kia` (service appointment: vehicle_reg, model, km, issue, slot), `dhi` (DHI hair clinic consultation: concern, duration, age, city → clinic lookup, slot, name, phone), `dcb-bank-loan` (personal/home loan pre-qualification: loan type, amount, employment, monthly income, city, PAN with confirm → eligibility decision → callback or rejection). Datasets: `tata_dealers`, `kia_service_centers`, `kia_models`, `dhi_clinics`, `dcb_existing_customers`, `dcb_loan_products`.
