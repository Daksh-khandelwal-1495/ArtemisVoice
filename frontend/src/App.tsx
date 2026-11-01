import { useState } from "react";
import { Diagnosis, Layer, Run, runDemo } from "./api";

function layer(layers: Layer[], name: string) { return layers.find((item) => item.layer === name)?.output ?? {}; }
function rate(pass: number, trials: number) { return `${pass}/${trials} (${Math.round((pass / trials) * 100)}%)`; }

export function App() {
  const [run, setRun] = useState<Run>();
  const [diagnosis, setDiagnosis] = useState<Diagnosis>();
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(false);
  const start = async () => { setLoading(true); setError(undefined); try { const result = await runDemo(); setRun(result.run); setDiagnosis(result.diagnosis); } catch (cause) { setError(cause instanceof Error ? cause.message : "Unknown error"); } finally { setLoading(false); } };
  const layers = run?.turns[0].layers ?? [];
  const caller = layer(layers, "caller"); const stt = layer(layers, "speech_to_text");
  return <main>
    <header><div><p className="eyebrow">ARTEMISVOICE / CAUSAL DEBUGGER</p><h1>Prove what broke.</h1><p className="subtle">Run a failed call through a control and a single-layer intervention.</p></div><button onClick={start} disabled={loading}>{loading ? "Investigating…" : "Run demo investigation"}</button></header>
    {!run && !error && <section className="empty"><strong>Ready for an investigation.</strong><span>Start the seeded confirmation-code failure to see the complete causal loop.</span></section>}
    {error && <section className="error"><strong>Cannot reach the evaluator API.</strong><span>{error}</span><code>uvicorn artemisVoice.Evals.api.app:app --reload</code></section>}
    {run && diagnosis && <><section className="verdict"><div><p className="eyebrow">DIAGNOSIS</p><h2>{diagnosis.verdict}</h2><p>Cause hypothesis: <strong>{diagnosis.intervention.replace("_oracle", "")}</strong></p></div><div className="metrics"><div><span>Effect size</span><strong>+{diagnosis.effect_size.toFixed(2)}</strong></div><div><span>p-value</span><strong>{diagnosis.fisher_exact_p_value.toFixed(4)}</strong></div></div></section>
      <section className="grid"><article><p className="eyebrow">TRACE EVIDENCE</p><h3>Entity mismatch</h3><dl><dt>Caller intended</dt><dd>{String(caller.intended_text)}</dd><dt>STT heard</dt><dd className="bad">{String(stt.final_hypothesis)}</dd><dt>Expected code</dt><dd>{run.expected_database_state.confirmation_code}</dd><dt>Stored code</dt><dd className="bad">{run.final_database_state.confirmation_code}</dd></dl></article>
      <article><p className="eyebrow">CONTROLLED REPLAY</p><h3>Only one layer changed</h3><div className="arms"><div><span>Control</span><strong>{rate(diagnosis.control.pass_count, diagnosis.control.trials)}</strong></div><div className="highlight"><span>STT oracle</span><strong>{rate(diagnosis.intervention_arm.pass_count, diagnosis.intervention_arm.trials)}</strong></div></div></article></section>
      <section className="next"><p className="eyebrow">NEXT SAFE ACTION</p><h3>Review a bounded STT patch</h3><p>Approve a keyword-boost or confirmation-readback patch only after it clears failing, held-out, and regression validation.</p></section></>}
  </main>;
}
