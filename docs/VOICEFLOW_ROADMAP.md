# VoiceFlow × Artemis roadmap

## Phase 1 — Presentable live-call demo
Use one seeded bot (Tata Motors) in draft mode.
1. Providers: default demo Deepgram STT + OpenAI LLM + Soniox TTS; simpler alternative OpenAI for STT, LLM and TTS.
2. Run a real microphone call.
3. Scripted journey: model Nexon · city Pune · pincode 411001 · name Asha Sharma · phone 9876543210.
4. Studio stores: session, transcripts, flow version/config snapshot, function calls, lookup/API evidence, captured fields.
5. Export `/api/sessions/{id}/artemis-evidence`.
Done when: a real call has a visible Studio session plus an Artemis-readable evidence bundle.
Commit: `feat: add live VoiceFlow demo workflow`

## Phase 2 — Make the trace replay-eligible
First-class Pipecat instrumentation for every caller turn:
| Layer | Capture required |
|---|---|
| Caller | recorded input audio artifact + SHA-256 |
| Endpointing | VAD start/end, interruptions, timing |
| STT | interim/final hypotheses, confidence, provider settings |
| LLM | input context hash, response, tool-call request/result, model settings |
| Tools | sandbox input/output and before/after state snapshot |
| TTS | source text, generated audio artifact + SHA-256, voice settings |
Done when: one completed Studio session can be converted honestly into RunTrace without mocked or inferred layer data.
Commit: `feat: capture replay-grade Pipecat traces`

## Phase 3 — Safe replay sandbox
1. Keep all demo API calls in `API_CALL_MOCK=true`.
2. Snapshot the seeded datasets and session variables before each test.
3. Test oracle: expected captured fields, expected lookup/API result, expected terminal node/status, expected bot response.
4. Restore this state before every control or intervention replay.
5. Block live external calls, bookings, callbacks and database mutations during replay.
Done when: a replay reproduces the same baseline result with the same seed/configuration.
Commit: `feat: add VoiceFlow replay sandbox and test oracle`

## Phase 4 — Connect Studio traces to Artemis causal tests
1. VoiceFlow adapter implementing Artemis's `VoiceAgentAdapter`.
2. Import a replay-grade Studio session as an Artemis run.
3. Run control, STT oracle, endpointing oracle, LLM oracle, tools oracle and TTS oracle replays.
4. Display the result in the causal dashboard.
Done when: Artemis can say e.g. "Replacing only the STT result changed the outcome from failure to success in 5/5 replays."
Commit: `feat: diagnose VoiceFlow sessions with causal replay`

## Phase 5 — Deliberate failure demos (Tata flow variants)
1. STT confusion: 411001 → 41100I or Nexon → Nixon.
2. Endpointing cut-off: omit the last part of a pincode.
3. LLM tool-argument error: captures wrong field despite correct transcript.
4. Lookup/tool failure: wrong dealer result or simulated API error.
5. TTS error: response text correct internally but spoken confirmation wrong.
Each has a known expected outcome and a corresponding oracle intervention.
Done when: the demo proves attribution across all five layers.
Commit: one commit per failure layer.

## Phase 6 — Patch and regression workflow
1. Bounded repair proposal from a proven diagnosis: STT keyword/keyterm boost, confirmation readback, stricter field validation, retry or fallback route.
2. Validate on the failing case, held-out cases and saved regression cases.
3. Require approval before changing a published VoiceFlow flow.
4. Save accepted failures as regression scenarios.
Done when: the system progresses from diagnosis to safe, reviewable improvement.
Commit: `feat: validate VoiceFlow patches against regressions`

## Phase 7 — Demo polish and handoff
1. A single "Demo Runbook" document.
2. One-click predictable demo bot and dataset.
3. Dashboard links from Artemis to Studio session evidence.
4. Health/readiness cards: providers configured, database healthy, replay readiness, missing trace artifacts.
5. Demo sequence: make call → view Studio session → export evidence → run causal diagnosis → review candidate patch.
