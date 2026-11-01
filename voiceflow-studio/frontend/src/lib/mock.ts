// In-memory mock backend, enabled with NEXT_PUBLIC_MOCK=1. Mirrors the REST contract closely enough
// to develop the UI without FastAPI running. State resets on page reload.
import { ApiError } from "./api";
import { lintFlow } from "./lint";
import { mergeConfig, normalizeGraph } from "./defaults";
import { applyIntervention, buildMockRun } from "./mock-trace";
import { FAULT_PRESETS } from "./oracle";
import type {
  BotDetail,
  FaultProfile,
  Intervention,
  SessionTrace,
  TestOracle,
  Dataset,
  DatasetRow,
  FlowGraph,
  ProviderCatalog,
  SessionDetail,
  SessionListItem,
  VersionInfo,
} from "./types";
import tataSeed from "./mock-data/tata-motors.json";
import dealerSeed from "./mock-data/tata_dealers.json";

interface MockVersion extends VersionInfo {
  graph: FlowGraph;
}

const now = () => new Date().toISOString();
const id = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;

const providers: ProviderCatalog = {
  stt: [
    { id: "deepgram", name: "Deepgram", models: ["nova-3", "nova-3-general", "nova-2", "nova-2-phonecall"], languages: ["en-IN", "en-US", "en-GB", "hi", "multi"], configured: true },
    { id: "soniox", name: "Soniox", models: ["stt-rt-v5", "stt-rt-v4"], languages: ["en", "hi", "multi"], configured: true },
    { id: "openai", name: "OpenAI", models: ["gpt-4o-transcribe", "gpt-4o-mini-transcribe"], languages: ["en", "hi"], configured: false },
  ],
  llm: [{ id: "openai", name: "OpenAI", models: ["gpt-6-luna", "gpt-6-sol", "gpt-6-astra", "gpt-5.5", "gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini", "gpt-4o", "gpt-5-mini", "gpt-5"], configured: false }],
  tts: [
    { id: "soniox", name: "Soniox", models: ["tts-rt-v2", "tts-rt-v1"], voices: ["Bryce", "Adrian", "Maya", "Nina", "Kenji", "Sofia"], languages: ["en", "hi"], configured: true },
    { id: "deepgram", name: "Deepgram Aura", models: ["aura-2"], voices: ["aura-2-helena-en", "aura-2-thalia-en", "aura-2-andromeda-en", "aura-2-apollo-en", "aura-2-arcas-en"], languages: ["en"], configured: true },
    { id: "openai", name: "OpenAI", models: ["gpt-4o-mini-tts", "tts-1"], voices: ["alloy", "ash", "coral", "echo", "nova", "sage", "shimmer"], languages: ["en", "hi"], configured: false },
  ],
};

interface MockBot {
  bot: Omit<BotDetail, "graph" | "draft_version_id" | "draft_version" | "published_version" | "node_count">;
  versions: MockVersion[];
  published_version_id: string | null;
}

function makeBot(seed: { slug: string; name: string; description?: string; industry?: string; avatar_color?: string; language?: string; config?: unknown; graph: unknown }, status: BotDetail["status"] = "draft"): MockBot {
  const graph = normalizeGraph(seed.graph as FlowGraph);
  const v: MockVersion = { id: id(), version: 1, note: "Initial version", created_at: now(), is_published: false, node_count: graph.nodes.length, graph };
  const bot: MockBot = {
    bot: {
      id: id(),
      slug: seed.slug,
      name: seed.name,
      description: seed.description ?? "",
      industry: seed.industry ?? "General",
      avatar_color: seed.avatar_color ?? "#6366f1",
      language: seed.language ?? "en-IN",
      status,
      config: mergeConfig(seed.config as never),
      session_count: 0,
      last_session_at: null,
      updated_at: now(),
    },
    versions: [v],
    published_version_id: null,
  };
  if (status === "published") {
    bot.published_version_id = v.id;
    v.is_published = true;
  }
  return bot;
}

const blankGraph = (): FlowGraph =>
  normalizeGraph({
    initial_node: "start",
    global_prompt: "You are a helpful voice assistant.",
    variables: {},
    nodes: [
      { id: "start", type: "start", label: "Start", position: { x: 0, y: 0 }, task_prompt: "Greet the caller.", transitions: [{ id: "t1", target: "end", kind: "on_complete", condition: "", label: "", priority: 0 }] },
      { id: "end", type: "end", label: "End", position: { x: 0, y: 200 }, say_on_enter: "Goodbye!", end_reason: "completed" },
    ] as never,
  });

const state = (() => {
  const tata = makeBot(tataSeed as never, "published");
  tata.bot.session_count = 2;
  tata.bot.last_session_at = now();
  const blank = makeBot({ slug: "demo-blank", name: "Demo Support Bot", description: "Blank starter flow", industry: "Retail", avatar_color: "#0ea5e9", graph: blankGraph() });
  const bots: MockBot[] = [tata, blank];
  const datasets: (Dataset & { rows: DatasetRow[] })[] = [
    {
      id: id(),
      name: dealerSeed.name,
      description: dealerSeed.description,
      columns: dealerSeed.columns,
      rows: (dealerSeed.rows as Record<string, unknown>[]).map((data) => ({ id: id(), data })),
      row_count: 0,
    },
  ];
  const s1: SessionDetail = {
    id: id(),
    bot_id: tata.bot.id,
    bot_name: tata.bot.name,
    bot_slug: tata.bot.slug,
    flow_version_id: tata.versions[0].id,
    flow_version: 1,
    mode: "draft",
    status: "completed",
    end_reason: "booked",
    current_node: "end_booked",
    config_snapshot: tata.bot.config,
    variables: { car_model: "Nexon EV", city: "Mumbai", pincode: "400053", phone: "98******10" },
    started_at: new Date(Date.now() - 3600_000).toISOString(),
    ended_at: new Date(Date.now() - 3600_000 + 184_000).toISOString(),
    duration_secs: 184,
    masked: true,
    events: [
      { id: id(), ts: new Date(Date.now() - 3600_000).toISOString(), type: "node_enter", node_id: "start", payload: { label: "Start" } },
      { id: id(), ts: new Date(Date.now() - 3599_000).toISOString(), type: "bot_transcript", node_id: "start", payload: { text: "Namaste! Thank you for calling Tata Motors." } },
      { id: id(), ts: new Date(Date.now() - 3594_000).toISOString(), type: "user_transcript", node_id: "start", payload: { text: "I want to book a test drive for the Nexon EV." } },
      { id: id(), ts: new Date(Date.now() - 3593_000).toISOString(), type: "node_enter", node_id: "collect_model", payload: {} },
      { id: id(), ts: new Date(Date.now() - 3590_000).toISOString(), type: "field_captured", node_id: "collect_model", payload: { field: "car_model", value: "Nexon EV" } },
      { id: id(), ts: new Date(Date.now() - 3570_000).toISOString(), type: "validation_error", node_id: "collect_location", payload: { errors: { pincode: "Pincode must be 6 digits" } } },
      { id: id(), ts: new Date(Date.now() - 3500_000).toISOString(), type: "end", node_id: "end_booked", payload: { status: "completed", end_reason: "booked" } },
    ],
    captured: [
      { id: id(), node_id: "collect_model", field_name: "car_model", value: "Nexon EV", valid: true, attempts: 1, captured_at: now() },
      { id: id(), node_id: "collect_location", field_name: "city", value: "Mumbai", valid: true, attempts: 1, captured_at: now() },
      { id: id(), node_id: "collect_location", field_name: "pincode", value: "400053", valid: true, attempts: 2, captured_at: now() },
      { id: id(), node_id: "collect_contact", field_name: "phone", value: "98******10", valid: true, attempts: 1, captured_at: now(), sensitive: true },
    ],
  };
  const s2: SessionDetail = { ...s1, id: id(), status: "abandoned", end_reason: "user_hangup", duration_secs: 41, events: s1.events.slice(0, 3), captured: s1.captured.slice(0, 1) };
  const oracles = seedOracles(tata.bot.id);
  return { bots, datasets, sessions: [s1, s2], oracles, traces: new Map<string, SessionTrace>() };
})();

function seedOracles(botId: string): TestOracle[] {
  const base: Omit<TestOracle, "id" | "slug" | "name" | "tags" | "fault_profile"> = {
    bot_id: botId,
    caller_turns: [
      "Hi, I'd like to book a test drive for the Nexon",
      "Pune, 411001",
      "The first one is fine",
      "Tomorrow morning",
      "Asha Sharma, 9876543210",
      "Yes, please confirm",
    ],
    expected_fields: { car_model: "Nexon", city: "Pune", pincode: "411001", customer_name: "Asha Sharma", phone: "9876543210" },
    expected_lookup: { dataset: "tata_dealers", match: { city: "Pune" } },
    expected_end_node: "end_booked",
    expected_status: "completed",
    expected_bot_response: { contains: ["Nexon", "411001"] },
    created_at: now(),
  };
  const preset = (pid: string): FaultProfile | null => FAULT_PRESETS.find((p) => p.id === pid)?.profile ?? null;
  const mk = (slug: string, name: string, tags: string[], fault: FaultProfile | null): TestOracle => ({ ...structuredClone(base), id: id(), slug, name, tags, fault_profile: fault });
  return [
    mk("tata-happy", "Tata happy path", ["regression"], null),
    mk("tata-stt", "Tata · STT pincode confusion", ["demo"], preset("stt-pincode")),
    mk("tata-endpointing", "Tata · endpointing cut-off", ["demo"], preset("endpointing")),
    mk("tata-llm", "Tata · LLM tool-argument error", ["demo"], preset("llm-swap")),
    mk("tata-tools", "Tata · wrong dealer lookup", ["demo"], preset("tools-dealer")),
    mk("tata-tts", "Tata · TTS misreads model", ["demo", "held-out"], preset("tts-nexon")),
  ];
}

function runMock(bot: MockBot, oracle: TestOracle, fault: FaultProfile | null, opts: { source: "live" | "simulation" | "replay"; mode: SessionDetail["mode"]; seed: number; sid?: string }) {
  const r = buildMockRun({
    sessionId: opts.sid ?? id(),
    botId: bot.bot.id,
    botName: bot.bot.name,
    botSlug: bot.bot.slug,
    graph: latest(bot).graph,
    keywords: bot.bot.config?.stt?.keywords ?? [],
    oracle,
    fault,
    source: opts.source,
    mode: opts.mode,
    seed: opts.seed,
    dealers: state.datasets.find((d) => d.name === "tata_dealers")?.rows.map((r) => r.data) ?? [],
  });
  r.session.config_snapshot = bot.bot.config;
  r.session.flow_version_id = latest(bot).id;
  r.trace.session.config_snapshot = bot.bot.config;
  state.sessions.unshift(r.session);
  state.traces.set(r.session.id, r.trace);
  bot.bot.session_count += 1;
  bot.bot.last_session_at = now();
  return r;
}

let seeded = false;
/** Seed one live (not replay-ready) and one simulated (replay-ready, failing) Tata session. */
function ensureSeedRuns() {
  if (seeded) return;
  seeded = true;
  const tata = state.bots[0];
  const happy = state.oracles.find((o) => o.slug === "tata-happy")!;
  const stt = state.oracles.find((o) => o.slug === "tata-stt")!;
  // Stable ids so mock deep links (/sessions/mock-live?tab=trace) survive page reloads.
  const live = runMock(tata, happy, null, { source: "live", mode: "draft", seed: 7, sid: "mock-live" });
  live.trace.oracle_id = null;
  live.trace.missing.push("no test oracle bound");
  runMock(tata, stt, stt.fault_profile ?? null, { source: "simulation", mode: "simulation", seed: 7, sid: "mock-sim-stt" });
}

function traceFor(s: SessionDetail): SessionTrace {
  const t = state.traces.get(s.id);
  if (t) return t;
  return {
    schema_version: "voiceflow-trace/v1",
    source: "live",
    session: { id: s.id, bot_id: s.bot_id, bot_name: s.bot_name, bot_slug: s.bot_slug, flow_version: s.flow_version, mode: s.mode, status: s.status, end_reason: s.end_reason, started_at: s.started_at, ended_at: s.ended_at },
    seed: null,
    fault_profile: null,
    oracle_id: null,
    replay_ready: false,
    missing: ["session has no trace_turn events (recorded before tracing was enabled)", "no test oracle bound"],
    turns: [],
    final_state: s.variables ?? {},
    end_node: s.current_node,
  };
}

function findBot(key: string): MockBot {
  const b = state.bots.find((x) => x.bot.id === key || x.bot.slug === key);
  if (!b) throw new ApiError(404, `Bot '${key}' not found`);
  return b;
}

const latest = (b: MockBot) => b.versions[b.versions.length - 1];

function summary(b: MockBot) {
  const l = latest(b);
  const pub = b.versions.find((v) => v.id === b.published_version_id);
  return {
    ...b.bot,
    draft_version: l.version,
    published_version: pub?.version ?? null,
    published_version_id: b.published_version_id,
    node_count: l.graph.nodes.length,
    completion_rate: b.bot.session_count ? 0.5 : null,
  };
}

const detail = (b: MockBot): BotDetail => ({ ...summary(b), graph: structuredClone(latest(b).graph), draft_version_id: latest(b).id });

function validate(graph: FlowGraph) {
  const issues = lintFlow(graph, state.datasets.map((d) => d.name));
  return { valid: !issues.some((i) => i.severity === "error"), issues };
}

function addVersion(b: MockBot, graph: FlowGraph, note: string | null) {
  const v: MockVersion = { id: id(), version: latest(b).version + 1, note, created_at: now(), is_published: false, node_count: graph.nodes.length, graph: structuredClone(graph) };
  b.versions.push(v);
  b.bot.updated_at = now();
  return v;
}

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function mockRequest(method: string, fullPath: string, body: unknown): Promise<unknown> {
  await delay(120);
  const [path, qs] = fullPath.split("?");
  const q = new URLSearchParams(qs ?? "");
  const seg = path.split("/").filter(Boolean).map(decodeURIComponent);
  const b = (body ?? {}) as Record<string, unknown>;

  if (seg[0] === "health") return { ok: true, db: true, providers: { deepgram: true, openai: false, soniox: true } };
  if (seg[0] === "providers") return providers;

  if (seg[0] === "bots") {
    if (seg.length === 1 && method === "GET") return state.bots.map(summary);
    if (seg.length === 1 && method === "POST") {
      const slug = (b.slug as string) || String(b.name).toLowerCase().replace(/[^a-z0-9]+/g, "-");
      if (state.bots.some((x) => x.bot.slug === slug)) throw new ApiError(409, `Slug '${slug}' already exists`);
      const tpl = b.template && b.template !== "blank" ? findBot(String(b.template)) : null;
      const nb = makeBot({
        slug,
        name: String(b.name),
        description: (b.description as string) ?? "",
        industry: (b.industry as string) ?? tpl?.bot.industry ?? "General",
        avatar_color: (b.avatar_color as string) ?? "#6366f1",
        config: tpl?.bot.config,
        graph: tpl ? latest(tpl).graph : blankGraph(),
      });
      state.bots.push(nb);
      return detail(nb);
    }
    const bot = findBot(seg[1]);
    if (seg.length === 2) {
      if (method === "GET") return detail(bot);
      if (method === "PATCH") {
        const { config, ...rest } = b;
        Object.assign(bot.bot, rest);
        if (config) bot.bot.config = mergeConfig(config as never);
        bot.bot.updated_at = now();
        return detail(bot);
      }
      if (method === "DELETE") {
        if (q.get("hard") === "true") state.bots = state.bots.filter((x) => x !== bot);
        else bot.bot.status = "archived";
        return undefined;
      }
    }
    const sub = seg[2];
    if (sub === "duplicate") {
      const nb = makeBot({ ...bot.bot, slug: `${bot.bot.slug}-copy-${state.bots.length}`, name: `${bot.bot.name} (copy)`, description: bot.bot.description ?? "", industry: bot.bot.industry ?? "", avatar_color: bot.bot.avatar_color ?? "", language: bot.bot.language ?? "", graph: latest(bot).graph });
      state.bots.push(nb);
      return detail(nb);
    }
    if (sub === "stats")
      return { total_sessions: 2, completed: 1, transferred: 0, abandoned: 1, error: 0, active: 0, avg_duration_secs: 112, completion_rate: 0.5, field_capture_rates: { car_model: 1, city: 0.5 } };
    if (sub === "flow" && seg.length === 3) {
      if (method === "GET") return { version: latest(bot).version, version_id: latest(bot).id, graph: latest(bot).graph };
      if (method === "PUT") {
        const v = addVersion(bot, normalizeGraph(b.graph as FlowGraph), (b.note as string) ?? null);
        return { version: v.version, version_id: v.id, validation: validate(v.graph) };
      }
    }
    if (sub === "flow" && seg[3] === "validate") return validate(normalizeGraph(b.graph as FlowGraph));
    if (sub === "publish") {
      const v = bot.versions.find((x) => x.id === b.version_id) ?? latest(bot);
      const res = validate(v.graph);
      if (!res.valid) throw new ApiError(400, { message: "Flow has validation errors", validation: res });
      bot.versions.forEach((x) => (x.is_published = x.id === v.id));
      bot.published_version_id = v.id;
      bot.bot.status = "published";
      return detail(bot);
    }
    if (sub === "versions") {
      if (seg.length === 3) return [...bot.versions].reverse().map(({ graph: _g, ...rest }) => (void _g, rest));
      const v = bot.versions.find((x) => x.id === seg[3]);
      if (!v) throw new ApiError(404, "Version not found");
      if (seg[4] === "restore") {
        const nv = addVersion(bot, v.graph, `Restored from v${v.version}`);
        return { version: nv.version, version_id: nv.id, validation: validate(nv.graph) };
      }
      return v;
    }
    if (sub === "offer") throw new ApiError(503, "Mock mode: voice calls require the real backend.");
    if (sub === "oracles") {
      if (method === "GET") return state.oracles.filter((o) => o.bot_id === bot.bot.id);
      if (method === "POST") {
        const slug = (b.slug as string) || String(b.name ?? "oracle").toLowerCase().replace(/[^a-z0-9]+/g, "-");
        if (state.oracles.some((o) => o.bot_id === bot.bot.id && o.slug === slug)) throw new ApiError(409, `Oracle slug '${slug}' already exists`);
        const o: TestOracle = {
          caller_turns: [],
          expected_fields: {},
          expected_lookup: null,
          expected_end_node: null,
          expected_status: "completed",
          expected_bot_response: null,
          tags: [],
          fault_profile: null,
          ...(b as Partial<TestOracle>),
          id: id(),
          bot_id: bot.bot.id,
          slug,
          name: String(b.name ?? slug),
          created_at: now(),
        };
        state.oracles.push(o);
        return o;
      }
    }
    if (sub === "simulate") {
      const o = state.oracles.find((x) => x.id === b.oracle_id);
      if (!o) throw new ApiError(404, "Oracle not found");
      if (b.mode === "published" && !bot.published_version_id) throw new ApiError(409, "Bot has no published version");
      const r = runMock(bot, o, (b.fault_profile as FaultProfile | null | undefined) ?? null, { source: "simulation", mode: "simulation", seed: Number(b.seed ?? 7) });
      return { session_id: r.session.id, passed: r.passed, outcome: r.outcome, trace_url: `/api/sessions/${r.session.id}/trace` };
    }
    if (sub === "patch-candidates") return { candidate_version_id: id(), config: bot.bot.config, graph_diff: { patch: b.patch } };
  }

  if (seg[0] === "oracles") {
    const o = state.oracles.find((x) => x.id === seg[1]);
    if (!o) throw new ApiError(404, "Oracle not found");
    if (method === "GET") return o;
    if (method === "PATCH") return Object.assign(o, b, { id: o.id, bot_id: o.bot_id });
    if (method === "DELETE") {
      state.oracles = state.oracles.filter((x) => x !== o);
      return undefined;
    }
  }

  if (seg[0] === "datasets") {
    if (seg.length === 1) return state.datasets.map(({ rows, ...d }) => ({ ...d, row_count: rows.length }));
    const ds = state.datasets.find((d) => d.name === seg[1]);
    if (!ds) throw new ApiError(404, `Dataset '${seg[1]}' not found`);
    if (seg[2] === "rows" && seg.length === 3 && method === "GET") {
      const term = (q.get("q") ?? "").toLowerCase();
      const items = ds.rows.filter((r) => !term || JSON.stringify(r.data).toLowerCase().includes(term));
      const off = Number(q.get("offset") ?? 0);
      const lim = Number(q.get("limit") ?? 50);
      return { items: items.slice(off, off + lim), total: items.length, columns: ds.columns };
    }
    if (seg[2] === "rows" && method === "POST") {
      const row = { id: id(), data: b.data as Record<string, unknown> };
      ds.rows.push(row);
      return row;
    }
    if (seg[2] === "rows" && method === "DELETE") {
      ds.rows = ds.rows.filter((r) => r.id !== seg[3]);
      return undefined;
    }
  }

  if (seg[0] === "sessions") {
    ensureSeedRuns();
    if (seg.length === 1) {
      let list = state.sessions;
      if (q.get("include_replay") !== "true" && q.get("mode") !== "replay") list = list.filter((s) => s.mode !== "replay");
      if (q.get("mode")) list = list.filter((s) => s.mode === q.get("mode"));
      if (q.get("bot_id")) list = list.filter((s) => s.bot_id === q.get("bot_id"));
      if (q.get("status")) list = list.filter((s) => s.status === q.get("status"));
      const items: SessionListItem[] = list.map((s) => ({
        id: s.id,
        bot_id: s.bot_id,
        bot_name: s.bot_name ?? "",
        status: s.status,
        end_reason: s.end_reason,
        mode: s.mode,
        started_at: s.started_at,
        ended_at: s.ended_at,
        duration_secs: s.duration_secs,
        captured_count: s.captured.length,
        required_missing: s.status === "completed" ? [] : ["phone", "customer_name"],
      }));
      return { items, total: items.length };
    }
    const s = state.sessions.find((x) => x.id === seg[1]);
    if (!s) throw new ApiError(404, "Session not found");
    if (seg[2] === "trace") return traceFor(s);
    if (seg[2] === "artemis-evidence") {
      const t = traceFor(s);
      return { schema_version: "voiceflow-artemis-evidence/v1", replay_eligible: t.replay_ready, missing: t.missing, session: t.session, turns: t.turns, variables: s.variables };
    }
    if (seg[2] === "oracle" && method === "POST") {
      const o = state.oracles.find((x) => x.id === b.oracle_id);
      if (!o) throw new ApiError(404, "Oracle not found");
      const t = traceFor(s);
      t.oracle_id = o.id;
      t.missing = t.missing.filter((m) => m !== "no test oracle bound");
      t.replay_ready = t.missing.length === 0;
      state.traces.set(s.id, t);
      return { session_id: s.id, oracle_id: o.id };
    }
    if (seg[2] === "replay" && method === "POST") {
      const t = traceFor(s);
      if (!t.replay_ready) throw new ApiError(422, { message: "Session is not replay-ready", missing: t.missing });
      const o = state.oracles.find((x) => x.id === t.oracle_id);
      if (!o) throw new ApiError(422, "Bind a test oracle before replaying");
      const bot = findBot(s.bot_id);
      const iv = (b.intervention as Intervention) ?? "control";
      const seed = Number(b.seed ?? t.seed ?? 7);
      const trials = Array.from({ length: Math.min(10, Math.max(1, Number(b.trials ?? 1))) }, (_, i) => {
        const r = runMock(bot, o, applyIntervention(t.fault_profile ?? null, iv), { source: "replay", mode: "replay", seed });
        return { trial: i, passed: r.passed, outcome: r.outcome, replay_session_id: r.session.id };
      });
      return { session_id: s.id, intervention: iv, seed, trials };
    }
    return s;
  }

  throw new ApiError(404, `Mock: no handler for ${method} ${fullPath}`);
}
