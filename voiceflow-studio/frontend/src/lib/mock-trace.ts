// Mock-mode (NEXT_PUBLIC_MOCK=1) trace / simulate / replay engine. Deterministic text driver that
// mimics the Studio replay sandbox closely enough to exercise the Trace and Oracles & Demos UIs.
import { compareOutcome } from "./oracle";
import type {
  FaultProfile,
  FlowGraph,
  Intervention,
  ReplayOutcome,
  SessionDetail,
  SessionEvent,
  SessionTrace,
  TestOracle,
  TraceArtifact,
  TraceTurn,
} from "./types";

const uid = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;

/** Deterministic pseudo-sha256 (mock only — 64 hex chars derived from the content). */
function fakeSha(s: string): string {
  let out = "";
  let h = 2166136261;
  for (let r = 0; r < 8; r++) {
    for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619 + r);
    out += (h >>> 0).toString(16).padStart(8, "0");
  }
  return out;
}

const textArtifact = (sid: string, file: string, text: string): TraceArtifact => ({
  uri: `artifact://${sid}/${file}`,
  sha256: fakeSha(text),
  media_type: "text/plain",
  bytes: new TextEncoder().encode(text).length,
});

const replaceAll = (text: string, reps: { from: string; to: string }[] | undefined, skip?: (from: string) => boolean) =>
  (reps ?? []).reduce((t, r) => (r.from && !skip?.(r.from) ? t.split(r.from).join(r.to) : t), text);

export interface MockBuildInput {
  sessionId: string;
  botId: string;
  botName: string;
  botSlug: string;
  graph: FlowGraph;
  keywords: string[];
  oracle: TestOracle;
  fault: FaultProfile | null;
  source: "live" | "simulation" | "replay";
  mode: SessionDetail["mode"];
  seed: number;
  dealers: Record<string, unknown>[];
}

export interface MockBuildResult {
  trace: SessionTrace;
  session: SessionDetail;
  outcome: ReplayOutcome;
  passed: boolean;
}

/** Remove the fault the given oracle arm replaces (everything else stays — faults are part of the SUT). */
export function applyIntervention(fault: FaultProfile | null, iv: Intervention): FaultProfile | null {
  if (!fault || iv === "control") return fault;
  const f = structuredClone(fault);
  if (iv === "stt_oracle") delete f.stt;
  if (iv === "endpointing_oracle") delete f.endpointing;
  if (iv === "llm_oracle") delete f.llm;
  if (iv === "tools_oracle") delete f.tools;
  if (iv === "tts_oracle") delete f.tts;
  return f;
}

export function buildMockRun(x: MockBuildInput): MockBuildResult {
  const { oracle, fault, sessionId: sid } = x;
  const fieldNode: Record<string, string> = {};
  for (const n of x.graph.nodes) for (const f of n.fields ?? []) fieldNode[f.name] = n.id;
  const protectedWord = (w: string) => !!fault?.stt?.keyword_protected && x.keywords.some((k) => k.toLowerCase() === w.toLowerCase());
  const t0 = Date.now() - 120_000;
  const iso = (ms: number) => new Date(t0 + ms).toISOString();
  const ev = (ms: number, type: SessionEvent["type"], node_id: string | null, payload: Record<string, unknown>): SessionEvent => ({ id: uid(), ts: iso(ms), type, node_id, payload });

  const state: Record<string, unknown> = {};
  const initial = { ...state };
  const events: SessionEvent[] = [ev(0, "node_enter", "start", { label: "Start" })];
  const spoken: string[] = [];
  const turns: TraceTurn[] = [];
  const missing: string[] = [];
  let lookupOk = true;
  let endNode: string | null = null;
  let status: SessionDetail["status"] = "active";
  let clock = 2000;

  oracle.caller_turns.forEach((truth, i) => {
    const before = { ...state };
    const fieldsHere = Object.entries(oracle.expected_fields).filter(([, v]) => v && truth.toLowerCase().includes(String(v).toLowerCase()));
    const node = fieldsHere.length ? fieldNode[fieldsHere[0][0]] ?? "start" : i === oracle.caller_turns.length - 1 ? "confirm_booking" : "start";

    // endpointing: truncate the protected field value
    let segment = truth;
    const ep = fault?.endpointing;
    if (ep?.truncate_field) {
      const v = oracle.expected_fields[ep.truncate_field];
      if (v && segment.includes(v)) segment = segment.slice(0, segment.indexOf(v) + Math.max(0, ep.keep_chars));
    }
    // STT: text replacements (skipped for protected keywords)
    const final = replaceAll(segment, fault?.stt?.replacements, protectedWord);
    const interim = final.split(" ").map((_, k, a) => a.slice(0, k + 1).join(" ")).slice(0, -1);

    // LLM: extract fields present in what reached the bot
    const args: Record<string, unknown> = {};
    for (const [k, v] of fieldsHere) {
      let val = String(v);
      if (ep?.truncate_field === k) val = val.slice(0, ep.keep_chars);
      val = replaceAll(val, fault?.stt?.replacements, protectedWord);
      if (!final.includes(val)) continue;
      args[k] = val;
    }
    const swap = fault?.llm?.swap_field;
    if (swap && swap.field in args) args[swap.field] = oracle.expected_fields[swap.with_value_of] ?? args[swap.field];
    Object.assign(state, args);

    // Tools: lookup when the city arrives
    const calls: Record<string, unknown>[] = [];
    const results: Record<string, unknown>[] = [];
    if ("city" in args && oracle.expected_lookup) {
      const lk = oracle.expected_lookup;
      calls.push({ kind: "lookup", dataset: lk.dataset, query: { city: args.city } });
      let rows = x.dealers.filter((r) => String(r.city ?? "").toLowerCase() === String(args.city).toLowerCase());
      if (fault?.tools?.lookup_override) rows = fault.tools.lookup_override.rows;
      if (fault?.tools?.api_error) {
        results.push({ kind: "lookup", ok: false, error: "simulated API error (503)" });
        lookupOk = false;
        endNode = "no_dealer";
      } else {
        results.push({ kind: "lookup", ok: true, rows });
        lookupOk = rows.some((r) => Object.entries(lk.match).every(([c, v]) => String(r[c] ?? "").toLowerCase() === String(v).toLowerCase()));
        state.dealers_found = rows.length > 0;
        if (rows[0]) state.dealer_name = rows[0].name;
      }
      events.push(ev(clock + 900, "lookup", "lookup_dealers", { dataset: lk.dataset, found: results[0].ok ? (results[0].rows as unknown[]).length : 0 }));
    }

    const isLast = i === oracle.caller_turns.length - 1;
    const text = isLast
      ? endNode === "no_dealer"
        ? "Sorry, I couldn't find a dealer right now. Someone will call you back."
        : `Your ${state.car_model ?? "Tata"} test drive at ${state.dealer_name ?? "the dealer"} in ${state.city ?? "your city"} is booked, ${state.customer_name ?? "thank you"}. Pincode ${state.pincode ?? "—"}.`
      : Object.keys(args).length
        ? `Got it — ${Object.entries(args)
            .map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`)
            .join(", ")}.`
        : "Sure, could you tell me a bit more?";
    const said = replaceAll(text, fault?.tts?.replacements);
    spoken.push(said);

    const toolCalls = Object.keys(args).length ? [{ name: `submit_${node}`, arguments: args, result: { status: "ok" } }] : [];
    events.push(ev(clock, "user_transcript", node, { text: final }));
    toolCalls.forEach((c) => events.push(ev(clock + 400, "function_call", node, { name: c.name, args: c.arguments })));
    Object.entries(args).forEach(([k, v]) => events.push(ev(clock + 500, "field_captured", node, { field: k, value: v })));
    events.push(ev(clock + 1500, "bot_transcript", node, { text: said }));

    const callerAudio = x.source === "live" ? null : textArtifact(sid, `turn${i}_caller.txt`, truth);
    const ttsAudio = x.source === "live" ? null : textArtifact(sid, `turn${i}_tts.txt`, said);
    if (!callerAudio) missing.push(`turn ${i}: caller audio artifact`);
    if (!ttsAudio) missing.push(`turn ${i}: TTS audio artifact`);
    const durMs = 600 + truth.length * 55;
    turns.push({
      index: i,
      node_id: node,
      state_before: before,
      state_after: { ...state },
      layers: {
        caller: { started_at: iso(clock - durMs), finished_at: iso(clock), input: {}, output: { intended_text: x.source === "live" ? null : truth, audio: callerAudio }, settings: {} },
        endpointing: {
          started_at: iso(clock - durMs),
          finished_at: iso(clock),
          input: {},
          output: { speech_start_ms: 120, speech_end_ms: 120 + Math.round((durMs * segment.length) / truth.length), interrupted_bot: false, segment_text: segment },
          settings: { vad: { stop_secs: 0.8, confidence: 0.7 } },
        },
        speech_to_text: {
          started_at: iso(clock - 300),
          finished_at: iso(clock),
          input: { audio: callerAudio },
          output: { interim_hypotheses: interim, final_hypothesis: final, confidence: final === truth ? 0.96 : 0.81 },
          settings: { provider: "deepgram", model: "nova-3", language: "en-IN", keywords: x.keywords },
        },
        llm: {
          started_at: iso(clock),
          finished_at: iso(clock + 700),
          input: { context_hash: fakeSha(`${sid}:${i}:${final}`), message_count: 3 + i * 2, node_id: node },
          output: { response_text: text, tool_calls: toolCalls },
          settings: { provider: "openai", model: "gpt-4.1-mini", temperature: 0, max_tokens: 300, seed: x.seed },
        },
        tools: { started_at: iso(clock + 800), finished_at: iso(clock + 900), input: { calls }, output: { results, state_before: before, state_after: { ...state } }, settings: { api_call_mock: true } },
        text_to_speech: {
          started_at: iso(clock + 1000),
          finished_at: iso(clock + 1500),
          input: { text },
          output: { spoken_text: said, audio: ttsAudio },
          settings: { provider: "soniox", model: "tts-rt-v2", voice: "Maya", speed: 1, sample_rate: 24000 },
        },
      },
    });
    clock += 7000;
  });

  if (!endNode) endNode = oracle.expected_end_node ?? "end_booked";
  status = endNode === "no_dealer" ? "abandoned" : (oracle.expected_status ?? "completed");
  events.push(ev(clock, "end", endNode, { status, end_reason: endNode === "end_booked" ? "booked" : endNode }));

  const captured = Object.fromEntries(Object.entries(state).filter(([k]) => k in oracle.expected_fields || k === "dealer_name"));
  const outcome: ReplayOutcome = { captured, end_node: endNode, status, spoken_texts: spoken };
  const passed = lookupOk && compareOutcome(oracle, outcome).every((c) => c.ok);

  const session: SessionDetail = {
    id: sid,
    bot_id: x.botId,
    bot_name: x.botName,
    bot_slug: x.botSlug,
    flow_version_id: null,
    flow_version: 1,
    mode: x.mode,
    status,
    end_reason: endNode === "end_booked" ? "booked" : endNode,
    current_node: endNode,
    config_snapshot: null,
    variables: { ...state },
    started_at: iso(0),
    ended_at: iso(clock),
    duration_secs: Math.round(clock / 1000),
    events,
    captured: Object.entries(captured).map(([k, v]) => ({ id: uid(), node_id: fieldNode[k] ?? null, field_name: k, value: v, valid: true, attempts: 1, captured_at: iso(clock) })),
  };

  const trace: SessionTrace = {
    schema_version: "voiceflow-trace/v1",
    source: x.source,
    session: { id: sid, bot_id: x.botId, bot_slug: x.botSlug, bot_name: x.botName, flow_version: 1, mode: x.mode, status, end_reason: session.end_reason, started_at: session.started_at, ended_at: session.ended_at, config_hash: fakeSha(x.botId) },
    seed: x.seed,
    fault_profile: fault,
    oracle_id: oracle.id,
    replay_ready: missing.length === 0,
    missing,
    dataset_snapshot_id: uid(),
    initial_state: initial,
    turns,
    final_state: { ...state },
    end_node: endNode,
    events,
  };
  return { trace, session, outcome, passed };
}
