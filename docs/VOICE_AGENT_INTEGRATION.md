# Voice Agent Integration

The evaluator is deliberately separate from the agent under test. Put the agent
implementation anywhere under `backend/artemisVoice`; connect it to the
evaluator by implementing `VoiceAgentAdapter` from
`artemisVoice.Evals.agents.protocol`.

```python
from artemisVoice.Evals.agents.protocol import VoiceAgentAdapter
from artemisVoice.Evals.api.app import create_app

voice_agent: VoiceAgentAdapter = MyPipecatAgentAdapter(...)
app = create_app("artemis_voice.db", agent=voice_agent)
```

`run(scenario)` must return a valid `RunTrace`. It must include, for every turn:

- caller intended text, audio artifact, voice, mutation, and seed;
- endpointing events and timestamps;
- STT partial/final hypotheses, confidence, model, and settings;
- full LLM request/response, model settings, seed, and token timing;
- tool calls, arguments, results, and database hashes before and after; and
- TTS input, normalized text, audio artifact, and word timestamps.

The evaluator verifies that the trace has all six pipeline layers, an audio
artifact, contiguous turns, and matching database hashes. If those invariants
are not met, the adapter must fail the run rather than return a partial trace.

The API can immediately create and persist runs from any compliant adapter.
Counterfactual substitutions and automatic patch application remain
adapter-specific: implement them only after the agent exposes deterministic
turn restoration. Until then, use the supplied deterministic adapter to test
the evaluator itself and do not claim live-agent causal attribution.
