# VoiceFlow Studio integration

VoiceFlow Studio is ArtemisVoice's first live agent target. It already uses Pipecat
and stores a stable configuration snapshot, flow version, call events, captured
fields and final variables for every session.

## What works now

After a completed Studio call, request:

```text
GET /api/sessions/{session_id}/artemis-evidence
```

The response normalizes each caller turn into the same investigation layers used by
Artemis: caller, endpointing, STT, LLM, tools and TTS. It retains the actual
provider/model settings selected for the bot. It also includes function calls,
lookups, API calls and validation errors, so the dashboard can explain how the
transcript led to a flow decision.

The endpoint honours Studio's sensitive-field masking by default. `?reveal=true`
uses the same local debugging policy as Studio's existing session-detail endpoint.

## Why this is evidence, not yet a causal replay

The exporter returns `replay_eligible: false`. A transcript alone is useful for
triage, but it is not proof that STT caused an outcome. We must not fill the
missing facts with an LLM or pretend a transcript hash is an audio artifact.

To make a Studio session eligible for Artemis counterfactual replay, capture:

1. The caller audio artifact and SHA-256 for every user turn.
2. STT's final and interim hypotheses, confidence, endpoint timing and exact
   provider settings.
3. The LLM request, response/tool calls and deterministic settings where
   supported.
4. TTS source text plus generated audio artifact and checksum.
5. A test oracle: expected captured variables, expected external-tool effect and
   expected spoken result. This comes from the test case or authoritative system,
   never from an LLM.
6. A sandbox restore/apply handler for lookups and API calls. Replays must not
   create production bookings, callbacks or other side effects.

## Recommended first test

Use the Tata Motors test-drive flow in **draft** mode with sandboxed datasets and
`API_CALL_MOCK=true`. Define a scripted caller journey with expected captured
fields:

```json
{
  "model": "Nexon",
  "city": "Pune",
  "pincode": "411001",
  "name": "Asha Sharma",
  "phone": "9876543210"
}
```

Run it through the Studio Test Call UI, inspect the exported evidence, then add
the above artifacts. Artemis can then compare the baseline against a replay that
only replaces one layer—for example, the STT transcription of `411001`—while
keeping the same flow version, bot configuration and sandbox data snapshot.

## Provider roles

The Studio defaults are Deepgram STT, OpenAI LLM and Soniox TTS, but the flow
editor supports OpenAI for STT and TTS too. OpenAI credits are appropriate for
running the selected OpenAI provider in the live test call. They do not establish
the expected result; the scripted test case and sandbox state do that.
