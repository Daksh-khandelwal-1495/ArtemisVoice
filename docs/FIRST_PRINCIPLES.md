# ArtemisVoice From First Principles

## What problem are we solving

A voice agent is a chain of systems, not one model.

```text
caller audio -> endpointing -> speech to text -> LLM -> tools -> text to speech -> caller
```

When a call fails, a conventional evaluation usually gives a score or a failed
scenario. That is useful for measuring quality, but it does not establish what
an engineer should change. The LLM may look wrong only because speech-to-text
misheard a code. A correct LLM may call a tool that maps an argument incorrectly.
A successful database update may still be spoken incorrectly by text-to-speech.

ArtemisVoice is designed to answer a narrower and more useful question:

> Which pipeline layer caused this call to fail, what is the smallest safe
> change, and did that change improve more than the original example?

## The first-principles idea: change one variable

To establish causation, do not merely inspect a correlation. Re-run the same
failure while changing one thing.

For each failed call, ArtemisVoice retains a replayable trace. At a chosen turn,
it runs two arms:

1. **Control arm**: replay from the same turn with nothing changed.
2. **Intervention arm**: replay from the same turn with exactly one layer given
   the correct information or behavior.

If the control continues to fail while only the speech-to-text intervention
recovers, speech-to-text has causal evidence behind it. If neither arm recovers,
the correct answer is inconclusive, not a confident guess.

The system repeats both arms because one stochastic run is weak evidence. It
compares their pass rates and records an effect size plus a Fisher exact-test
p-value. The current verdict vocabulary is:

- **Proven**: a substantial recovery with statistical evidence.
- **Likely**: a meaningful recovery that did not reach significance within the
  available trial budget.
- **Compound**: no individual intervention works, but a combination does.
- **Inconclusive**: no intervention has enough evidence.
- **Not the agent**: the control passes consistently or the simulated caller was
  invalid.

## What ArtemisVoice records

A valid trace is the contract that makes replay possible. Each turn contains:

- caller intended text, audio artifact, voice, seed, and applied mutation;
- endpointing boundaries, interruptions, and timestamps;
- STT partial/final hypotheses, confidence, model, and settings;
- LLM request, response, model settings, seed, and token timing;
- tool calls, arguments, results, plus database snapshots and hashes; and
- TTS input, spoken output, audio artifact, and word timing.

The database snapshot before and after every turn matters. A replay must restart
from the same world state as the failed call; otherwise an apparent fix may just
be caused by different data.

## System architecture

```text
Scenario + seed
       |
       v
Voice-agent adapter ------------------------+
       |                                    |
       v                                    v
RunTrace -> SQLite trace store -> candidate-turn locator
                                      |
                                      v
                              control + intervention arms
                                      |
                                      v
                           judges, statistics, diagnosis record
                                      |
                                      v
                reviewable patch -> validation gate -> regression case
                                      |
                                      v
                           API, CLI, future results interface
```

The agent adapter is deliberately separate from the evaluator. A teammate can
build a Pipecat agent under `backend/artemisVoice` while ArtemisVoice receives a
complete `RunTrace` through the documented `VoiceAgentAdapter` contract.

## The five interventions

The evaluator has a separate intervention for each cascaded layer.

| Suspected layer | Controlled substitution | Recovery evidence |
|---|---|---|
| Endpointing | Merge the intended utterance before STT | Pass rate improves |
| STT | Feed caller-intended text to the LLM | Pass rate improves |
| LLM/prompt | Supply the correct decision at the LLM boundary | Pass rate improves |
| Tools | Supply correct tool arguments/results | Pass rate improves |
| TTS | Deliver intended spoken output to the caller | Call outcome or spoken-output check recovers |

The deterministic mock adapter currently implements all five fault classes. It
is used for fast, free CI and for proving evaluator behavior. A live adapter
must preserve the same trace and replay rules before ArtemisVoice makes causal
claims about a real voice agent.

## User journey example

### 1. A failure is found

Priya owns a booking agent. The caller says, “My confirmation code is B8Q2.”
The agent stores `P8Q2` instead.

The trace shows:

```text
Caller intended: B8Q2
STT final hypothesis: P8Q2
LLM tool argument: P8Q2
Tool database state: P8Q2
Expected database state: B8Q2
```

The candidate-turn locator ranks this turn because the caller text and STT
output disagree and the database state diverges from the expected state.

### 2. Artemis runs the experiment

```text
Control:          replay unchanged                 -> 0/5 pass
STT oracle:       replace P8Q2 with B8Q2 at STT    -> 5/5 pass
LLM oracle:       correct only the LLM boundary    -> 0/5 pass
Tools oracle:     correct only tool output         -> 0/5 pass
TTS oracle:       correct only spoken output       -> 0/5 pass
```

The diagnosis is `speech_to_text`, with a +1.00 pass-rate effect and a recorded
comparison against control. ArtemisVoice did not infer blame from the transcript;
it tested the hypothesis.

### 3. A human reviews a bounded patch

The system proposes a limited STT keyword boost for the relevant code pattern,
or a confirmation/read-back rule. The proposal begins in `pending` state. It
cannot be applied until a person approves it.

### 4. The patch must prove itself

The validation gate runs three groups:

| Group | Required result |
|---|---|
| Original failing scenario | Improvement |
| Held-out variants, such as `B8Q3` | Improvement without memorizing `B8Q2` |
| Healthy regression suite | No regression |

Only a patch that clears all gates can promote the failure to a permanent
regression case. Future runs include it automatically.

### 5. Ambiguity is visible, not hidden

If an LLM judge has low confidence, abstains, raises a safety flag, or cites text
that is absent from the trace, ArtemisVoice sends the result to the human-review
queue. A weak or unsupported judge output never silently becomes a diagnosis.

## Provider strategy

The system keeps providers behind a configuration boundary:

- **Mock**: default for deterministic tests and CI; no credentials or network.
- **OpenAI**: planned STT/TTS for high-fidelity audio evaluation.
- **Local**: pre-recorded audio and local models for inexpensive replay.
- **Cerebras**: text-only qualitative judges and patch proposals.
- **Exa**: offline research for scenario seeds, policies, and competitive checks.

Exa is intentionally excluded from the latency-sensitive call loop. Provider
secrets are environment variables and are never stored in traces.

## Current state and next boundary

ArtemisVoice currently has a tested deterministic implementation of the full
causal loop: traces, all five mock interventions, control comparisons, patches,
validation gates, regressions, judges, a review queue, red-team mutations, CLI,
API, and CI.

The next boundary is not another scorecard. It is the real Pipecat adapter:
capture its complete per-turn trace, restore a failed turn deterministically,
and demonstrate that the same control-versus-intervention experiment works on a
live cascaded voice agent.
