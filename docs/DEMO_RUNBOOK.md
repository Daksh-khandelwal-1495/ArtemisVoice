# Demo Runbook: VoiceFlow Studio × ArtemisVoice

This runbook covers the end-to-end demo from [the roadmap](VOICEFLOW_ROADMAP.md): Phase 1
(a real live call) and Phase 7 (call, then evidence, then causal diagnosis, then a reviewed
patch). It also covers the five deliberate failure demos. Endpoints, ports and env vars follow
[the integration contract](INTEGRATION_CONTRACT.md).

| What | URL |
|---|---|
| VoiceFlow Studio UI | http://localhost:3000 |
| VoiceFlow Studio API | http://localhost:8000/api/health |
| Artemis dashboard | http://localhost:5173 |
| Artemis API | http://localhost:8001/health |

---

## 0. Before the demo (10 minutes, once)

1. **Install.** Run `make setup` from the repo root. It initialises the Pipecat submodule, runs
   `uv sync` for Studio, creates the Artemis `.venv` and installs npm packages for both UIs.
   If anything later fails with *"the Pipecat submodule is not initialised"*, run
   `git submodule update --init voiceflow-studio/vendor/pipecat`.
2. **Keys.** Run `cp voiceflow-studio/backend/.env.example voiceflow-studio/backend/.env` and
   fill in the keys for the stack you will use (see step 2 below). Never commit this file or
   copy it anywhere else.
3. **Start everything.** Run `make dev`. This starts Postgres in Docker plus the four dev servers
   and prints the URLs. The Studio API runs on the host, so live WebRTC microphone calls work.
   - `make up` (everything in Docker) is fine for simulations, replays and diagnosis. For the
     **live call** on macOS, use `make dev`, or run `make up-hybrid` and then `make studio-api`
     (see [DOCKER.md](DOCKER.md#live-microphone-calls-webrtc)).
4. **Seed.** Run `make demo-seed`. This upserts the seeded bots and datasets, the Tata Motors
   oracle `tata-happy` and the five fault variants. Run `make demo-reset` instead to wipe Studio
   sessions as well.
5. **Health check.**
   - `curl localhost:8000/api/health` should show `"db": true` and `true` for each provider you
     configured.
   - The **Overview** page of the Artemis dashboard shows the readiness cards: providers
     configured, database healthy, Studio reachable and replay readiness.
6. **Audio.** Use **headphones**. Open http://localhost:3000 in Chrome and allow the microphone
   for `localhost:3000` when asked.

## 1. Configure providers (Studio, then Tata Motors bot, then **Settings**)

Open the **Tata Motors** bot and stay in **draft** mode. On the **Settings** tab, choose one of:

| Option | STT | LLM | TTS | Keys in `backend/.env` |
|---|---|---|---|---|
| Default demo | Deepgram | OpenAI `gpt-6-luna` | Soniox | `DEEPGRAM_API_KEY`, `OPENAI_API_KEY`, `SONIOX_API_KEY` |
| All-OpenAI (simplest) | OpenAI | OpenAI `gpt-6-luna` | OpenAI | `OPENAI_API_KEY` |

On the same tab, under Speech-to-text **Keywords**, make sure `Nexon` is listed. **Save**
the bot. Providers whose key is missing appear as not configured in `/api/providers` and
the call will fail fast.

## 2. Phase 1: a real microphone call

1. Tata Motors bot, **Test call** tab, mode **draft**, then **Start**. Wait for the greeting.
2. Speak this script, one answer per bot question, pausing after each:

   | Bot asks for | You say |
   |---|---|
   | Car model | "I'd like to test drive the **Nexon**." |
   | City | "**Pune**." |
   | Pincode | "**four one one zero zero one**" (411001) |
   | Name | "**Asha Sharma**." |
   | Phone | "**nine eight seven six five four three two one zero**" (9876543210) |

3. Let the bot confirm the details and the dealer, then hang up. If it reads back a wrong value,
   that is fine; the failure becomes the thing you diagnose.
4. **Sessions** tab: open the new session. It shows the transcript, flow version and config
   snapshot, function calls, lookup and API evidence (`API_CALL_MOCK=true`, so nothing real is
   booked) and the captured fields.
5. **Trace** tab: check the six layers per turn (caller audio, endpointing, STT, LLM, tools,
   TTS audio), the audio players and the **replay readiness** badge. If anything is missing, the
   tab lists it. Studio never fills a missing layer with inferred data.
6. Verify from a terminal:

   ```bash
   cd voiceflow-studio/backend
   uv run python scripts/verify_session.py <session_id>
   ```

   It prints per-turn layer coverage, artifact checksums and `replay_ready`.
7. Click **Export evidence**. This downloads `artemis-evidence-<id>.json` (also available at
   `GET /api/sessions/<id>/artemis-evidence`).

**Phase 1 done:** a real call has a visible Studio session and an Artemis-readable evidence bundle.

## 3. Phase 7: from the call to a reviewed patch

1. **Import.** Either click **Open in Artemis** on the Studio session, or go to the Artemis
   dashboard, open **VoiceFlow runs** and import by session id (`POST /voiceflow/import`). If the
   trace is not replay-ready, Artemis refuses with a 422 and lists the missing artifacts.
   It does not import partial evidence.
2. **Run detail.** You see the six-layer turn timeline (playable audio), and the oracle values
   next to the actual ones (expected `Nexon / Pune / 411001 / Asha Sharma / 9876543210`).
3. **Diagnose.** Click **Run causal diagnosis**. Artemis replays the session through Studio as a
   control arm plus five oracle arms (STT, endpointing, LLM, tools, TTS). Each arm has N trials
   at `REPLAY_LLM_SEED=7`. The arms table shows pass counts, Fisher-exact p-values and a verdict
   sentence, for example *"Replacing only the STT result changed the outcome from failure to
   success in 5/5 replays."* A successful happy-path call shows the control arm passing and no
   layer blamed. That is also a valid result.
4. **Patch.** On a proven layer, open **Patches** and review the bounded proposal (for example an
   `stt_keyterm_boost` for `411001`). Studio creates it as a **draft** candidate version; nothing
   is published.
5. **Validate.** Click **Validate**. The candidate is replayed against the failing case, the
   held-out oracles and the saved regression oracles. Every gate must pass.
6. **Approve.** Click **Approve** (human only). Artemis calls Studio's promote endpoint with
   `X-Approved-By`, which publishes the version. Click **Remember** to save the failure as a
   regression oracle in Studio and a RegressionCase in Artemis.
7. Back in Studio, the bot's **Versions** tab shows the newly published version.

## 4. The five failure demos (deterministic, no microphone needed)

Each variant is a scripted simulation of the Tata journey with one fault injected (contract §3).
They are reproducible and need only `OPENAI_API_KEY`: the real flow LLM runs at temperature 0
with a fixed seed, and the caller is scripted from the oracle.

For each of `tata-stt`, `tata-endpointing`, `tata-llm`, `tata-tools`, `tata-tts`:

1. Studio, Tata Motors bot, **Oracles & Demos** tab, pick the fault variant, then **Simulate**
   (draft). The result shows `passed: false` and a link to the new session (`source: simulation`,
   text artifacts, no fake audio).
2. Click **Open in Artemis**, which imports the session.
3. Click **Run causal diagnosis** and check the expected verdict:

| Variant | Injected fault | Expected proven layer | Expected patch |
|---|---|---|---|
| `tata-stt` | STT mishears `411001` as `41100I` (or `Nexon` as `Nixon`) | **STT**: only `stt_oracle` flips to pass | `stt_keyterm_boost` (keyword-protected, so the fix is real) |
| `tata-endpointing` | Pincode cut off after 4 chars (`4110`) | **Endpointing** | `vad_stop_secs` +0.4 / `confirmation_readback` |
| `tata-llm` | Correct transcript, but the LLM writes the phone number into `pincode` | **LLM** | `stricter_validation` + `confirmation_readback` |
| `tata-tools` | Dealer lookup returns the wrong dealer (or API error) | **Tools** | `fallback_route` |
| `tata-tts` | Response text correct, spoken confirmation says `Nixon` | **TTS** | `confirmation_readback` |

4. Optionally continue with **Patches**, then **Validate**, **Approve** and **Remember**, as in
   section 3.

**Phase 5 done** when all five variants attribute to the right layer and the control arm
fails in each.

## 5. Troubleshooting

| Symptom | Fix |
|---|---|
| `make setup` / Docker build fails: Pipecat submodule not initialised | `git submodule update --init voiceflow-studio/vendor/pipecat` |
| Call connects but bot errors immediately, or provider shows not configured | The key is missing in `voiceflow-studio/backend/.env`; restart the Studio API after editing. `curl localhost:8000/api/health` shows which providers are set (never the values). |
| Bot keeps interrupting itself / transcribes its own voice | Use **headphones**. Speaker output leaking into the mic triggers VAD and barge-in. |
| "Microphone access was blocked" | Allow the mic for `http://localhost:3000` in the browser site settings. Use `localhost`, not a LAN IP (getUserMedia needs a secure context). |
| Call hangs on "Connecting" when the Studio API runs in Docker | WebRTC media cannot reach the container on Docker Desktop. Run the API on the host: `make dev`, or `make up-hybrid` + `make studio-api`. See [DOCKER.md](DOCKER.md#live-microphone-calls-webrtc). |
| Mic works but no audio processing / Daily bundle errors in console | The Daily call-machine bundles are served locally from `voiceflow-studio/frontend/public/daily/` (pinned to `@daily-co/daily-js@0.94.0`). Check that `http://localhost:3000/daily/audio-processor-bundle.js` returns JS. After upgrading daily-js, re-download the bundles. |
| `port 8000 is already in use` (or 3000/8001/5173/5433) | A previous `make up` or a stray server is running. `make down`, or `lsof -nP -iTCP:<port> -sTCP:LISTEN` and stop it. Do not run `voiceflow-studio/docker-compose.yml` and the root `compose.yaml` together. |
| Artemis says Studio unreachable | Dev: Studio API must be on :8000. Docker: `STUDIO_API_URL=http://studio-api:8000` (hybrid: `http://host.docker.internal:8000`). |
| Import refused with 422 + `missing` | The trace is not replay-ready (for example a turn without caller audio). Run `verify_session.py`, re-run the call, or use a simulation. |
| Studio API cannot reach Postgres | Host runs use `localhost:5433` (`docker compose up -d postgres`). In Docker, compose overrides `DATABASE_URL` to `postgres:5432`. |
| Too much / too little log output | `LOG_LEVEL=DEBUG make dev` (or `make up`) shows every Pipecat frame and prompt; `INFO` is readable. |
