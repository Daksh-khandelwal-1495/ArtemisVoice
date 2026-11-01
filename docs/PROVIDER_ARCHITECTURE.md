# Provider Architecture

Use `mock` mode in CI and deterministic tests. It never reads credentials or
makes a network request.

For live evaluation, keep the pipeline cascaded:

- OpenAI: STT (`gpt-4o-mini-transcribe` or `gpt-transcribe`) and selectively TTS.
- Local: cached/pre-recorded WAV fixtures and optional local TTS for cheap replay.
- Cerebras: text-only qualitative judges and bounded patch proposals. Do not use
  it as the audio provider.
- Exa: offline research for scenario seeds, competitor checks, and policy facts;
  never call it inside a live conversation or trial arm.

Set provider names and credentials in `.env`, copied from `.env.example`. Do not
store raw provider secrets, request headers, or full credentials in a trace.
