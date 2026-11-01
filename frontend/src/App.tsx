import { useState } from "react";
import { Diagnosis, Layer, Run, runDemo } from "./api";

const layerLabels: Record<string, { short: string; label: string }> = {
  caller: { short: "01", label: "Caller" },
  endpointing: { short: "02", label: "Endpointing" },
  speech_to_text: { short: "03", label: "Speech to text" },
  llm: { short: "04", label: "Reasoning" },
  tools: { short: "05", label: "Tools" },
  text_to_speech: { short: "06", label: "Voice response" },
};

function getLayer(layers: Layer[], name: string) { return layers.find((item) => item.layer === name)?.output ?? {}; }
function rate(pass: number, trials: number) { return `${pass}/${trials} · ${Math.round((pass / trials) * 100)}%`; }
function display(value: unknown) { return typeof value === "string" || typeof value === "number" ? String(value) : JSON.stringify(value); }

function DetailRows({ output }: { output: Record<string, unknown> }) {
  return <dl className="detail-rows">{Object.entries(output).map(([key, value]) => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{display(value)}</dd></div>)}</dl>;
}

export function App() {
  const [run, setRun] = useState<Run>();
  const [diagnosis, setDiagnosis] = useState<Diagnosis>();
  const [selectedLayer, setSelectedLayer] = useState("speech_to_text");
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(false);

  const start = async () => {
    setLoading(true); setError(undefined);
    try {
      const result = await runDemo();
      setRun(result.run); setDiagnosis(result.diagnosis); setSelectedLayer("speech_to_text");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unknown error");
    } finally { setLoading(false); }
  };

  const layers = run?.turns[0].layers ?? [];
  const caller = getLayer(layers, "caller"); const stt = getLayer(layers, "speech_to_text");
  const selected = getLayer(layers, selectedLayer); const selection = layerLabels[selectedLayer];
  const observedCode = run?.final_database_state.confirmation_code; const expectedCode = run?.expected_database_state.confirmation_code;

  return <main>
    <header className="topbar"><a className="brand" href="#top" aria-label="ArtemisVoice home"><span className="brand-mark">A</span> ARTEMIS<span>VOICE</span></a><div className="environment"><i /> Local evaluator <span>Mock agent</span></div></header>
    <section className="hero" id="top"><div><p className="eyebrow">CAUSAL VOICE-AGENT DEBUGGER</p><h1>Find the layer<br /><em>that changed the outcome.</em></h1><p className="lede">Compare a failed call with an otherwise identical replay where one pipeline layer is repaired.</p></div><aside className="hero-action"><div className="action-icon">↯</div><div><strong>STT confusion demo</strong><span>Confirmation code is transcribed incorrectly</span></div><button onClick={start} disabled={loading}>{loading ? "Investigating…" : run ? "Run again" : "Run investigation"}<b>→</b></button></aside></section>
    {!run && !error && <section className="empty"><span className="empty-orb">◌</span><strong>No trace selected</strong><p>Start the seeded demo to inspect its complete evidence trail and causal replay.</p></section>}
    {error && <section className="error"><strong>Evaluator API unavailable</strong><span>{error}</span><code>python -m uvicorn artemisVoice.Evals.api.app:app --reload</code></section>}
    {run && diagnosis && <><section className="case-bar"><div><span className="case-label">INVESTIGATION</span><strong>{run.scenario_id}</strong></div><div><span className="case-label">TURN</span><strong>01 / {String(run.turns.length).padStart(2, "0")}</strong></div><div><span className="case-label">SEED</span><strong>{run.seed}</strong></div><div className="case-status"><i /> Caller input valid</div></section>
      <section className="verdict"><div className="verdict-copy"><p className="eyebrow">CAUSAL VERDICT</p><div className="verdict-title"><span className="verdict-check">✓</span><h2>{diagnosis.verdict}</h2></div><p>Repairing <strong>{diagnosis.intervention.replace("_oracle", "").replaceAll("_", " ")}</strong> changes this call from failure to success.</p></div><div className="confidence"><span>CAUSAL EFFECT</span><strong>+{diagnosis.effect_size.toFixed(2)}</strong><small>p = {diagnosis.fisher_exact_p_value.toFixed(4)}</small></div></section>
      <section className="section-heading"><div><p className="eyebrow">01 / TRACE</p><h3>Follow the observed call</h3></div><span>Choose a layer to inspect its recorded output.</span></section>
      <section className="trace-panel"><nav className="pipeline" aria-label="Recorded voice pipeline">{layers.map((entry, index) => { const meta = layerLabels[entry.layer] ?? { short: String(index + 1), label: entry.layer }; const problematic = entry.layer === "speech_to_text"; return <button className={`layer ${selectedLayer === entry.layer ? "selected" : ""} ${problematic ? "problematic" : ""}`} key={entry.layer} onClick={() => setSelectedLayer(entry.layer)}><span className="layer-number">{meta.short}</span><strong>{meta.label}</strong>{problematic && <i title="Observed mismatch" />}</button>; })}</nav><article className="layer-detail"><div className="detail-head"><div><span className="detail-kicker">RECORDED OUTPUT</span><h4>{selection?.label ?? selectedLayer}</h4></div><span className="captured">Captured</span></div><DetailRows output={selected} /></article></section>
      <section className="evidence-grid"><article className="evidence-card mismatch"><p className="eyebrow">OBSERVED MISMATCH</p><h3>The identifier drifted</h3><div className="comparison"><div><span>Caller intended</span><code>{display(caller.intended_text)}</code></div><div><span>Transcript stored</span><code className="bad">{display(stt.final_hypothesis)}</code></div></div></article><article className="evidence-card state"><p className="eyebrow">STATE CHECK</p><h3>Wrong value committed</h3><div className="state-values"><div><span>Expected</span><strong>{expectedCode}</strong></div><span className="state-arrow">→</span><div><span>Observed</span><strong className="bad">{observedCode}</strong></div></div></article></section>
      <section className="section-heading replay-heading"><div><p className="eyebrow">02 / REPLAY</p><h3>Test one change at a time</h3></div><span>Same trace, seed, configuration, and database snapshot.</span></section>
      <section className="replay-grid"><article className="arm control"><div><span className="arm-label">CONTROL</span><span className="arm-copy">Original trace</span></div><strong>{rate(diagnosis.control.pass_count, diagnosis.control.trials)}</strong><p>No layers replaced</p></article><div className="replay-arrow">→</div><article className="arm intervention"><div><span className="arm-label">INTERVENTION</span><span className="arm-copy">STT output repaired</span></div><strong>{rate(diagnosis.intervention_arm.pass_count, diagnosis.intervention_arm.trials)}</strong><p>Only the suspected layer changes</p></article></section>
      <section className="next"><div><p className="eyebrow">NEXT SAFE ACTION</p><h3>Review a bounded STT patch</h3><p>Propose keyword boost or confirmation readback, then validate against failing, held-out, and regression cases.</p></div><button className="secondary" disabled>Patch workflow next <b>→</b></button></section></>}
  </main>;
}
