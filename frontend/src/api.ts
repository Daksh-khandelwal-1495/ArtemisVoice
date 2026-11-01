// ArtemisVoice API client. Every shape below is intentionally tolerant: the VoiceFlow
// endpoints (contract §4) evolve independently, so optional fields are never assumed.

type Json = Record<string, unknown>;

export type Layer = { layer: string; input?: Json; output: Json; settings?: Json; started_at?: string; finished_at?: string };
export type Artifact = { uri: string; sha256?: string; media_type?: string; bytes?: number };
export type Turn = {
  index?: number;
  node_id?: string;
  layers: Layer[] | Record<string, Omit<Layer, "layer">>;
  artifacts?: Artifact[];
  database_state_before?: Record<string, string>;
  database_state_after?: Record<string, string>;
  state_before?: Json;
  state_after?: Json;
};
export type Run = {
  id: string;
  scenario_id: string;
  seed: number;
  created_at?: string;
  agent_config_hash?: string;
  valid_caller_run?: boolean;
  source?: string;
  studio_session_id?: string | null;
  oracle_id?: string | null;
  bot_name?: string | null;
  bot_id?: string | null;
  expected_spoken_text?: string;
  expected_spoken_contains?: string[];
  final_database_state: Record<string, string>;
  expected_database_state: Record<string, string>;
  turns: Turn[];
};

export type Intervention = "control" | "stt_oracle" | "endpointing_oracle" | "llm_oracle" | "tools_oracle" | "tts_oracle";
export type Verdict = "proven" | "likely" | "inconclusive" | "not_the_agent" | string;
export type Arm = { intervention?: string; pass_count: number; trials: number; replay_run_ids?: string[] };
export type Diagnosis = {
  source_run_id?: string;
  turn_index?: number;
  intervention: string;
  effect_size: number;
  fisher_exact_p_value: number;
  verdict: Verdict;
  control: Arm;
  intervention_arm: Arm;
  summary?: string | null;
  created_at?: string;
};
/** CausalReport from POST/GET /runs/{id}/diagnoses/all (control + every oracle arm). */
export type DiagnosisSet = {
  id?: string;
  source_run_id?: string;
  diagnoses: Diagnosis[];
  control?: Arm;
  verdict?: string | null;
  summary?: string | null;
  proven_layer?: string | null;
  proven_layers?: string[];
  trials?: number;
  turn_index?: number;
  created_at?: string;
};

export type RunSummary = {
  id: string;
  end_node?: string | null;
  trace_source?: string | null;
  turn_count?: number | null;
  studio_session_id?: string | null;
  bot?: string | null;
  scenario?: string | null;
  oracle?: string | null;
  status?: string | null;
  passed?: boolean | null;
  replay_ready?: boolean | null;
  missing: string[];
  last_verdict?: string | null;
  last_intervention?: string | null;
  created_at?: string | null;
};

export type PatchProposal = {
  id: string;
  status?: string;
  patch_type?: string;
  type?: string;
  params?: Json;
  patch?: { type?: string; params?: Json } | null;
  diagnosis_source_run_id?: string;
  source_run_id?: string;
  layer?: string;
  candidate_version_id?: string | null;
  bot_id?: string | null;
  stt_keyword_boosts?: string[];
  graph_diff?: unknown;
  studio_graph_diff?: unknown;
  studio_candidate_id?: string | null;
  studio_bot_id?: string | null;
  studio_session_id?: string | null;
  rationale?: string;
  alternatives?: { type?: string; params?: Json }[];
  promotion?: Json | null;
  source?: string;
  approved_at?: string | null;
  approved_by?: string | null;
  created_at?: string;
};
export type GateSet = { name: string; before_pass_rate?: number; after_pass_rate?: number; before?: number; after?: number; total?: number; cases?: unknown[] };
export type PatchGateResult = { patch_id?: string; sets: GateSet[]; passed: boolean; failure_reason?: string | null; warnings?: string[] };
export type PatchWithGate = { patch: PatchProposal; gate: PatchGateResult | null };

export type ReadinessCheck = { label: string; ok: boolean | null; value: string; detail?: string };
export type Readiness = {
  raw: Json;
  providers: ReadinessCheck;
  database: ReadinessCheck;
  studio: ReadinessCheck;
  replayReady: ReadinessCheck;
  missing: ReadinessCheck;
  missingByRun: { run: string; runId: string | null; sessionId: string | null; missing: string[] }[];
  ok: boolean | null;
  recentDiagnoses: Diagnosis[];
};

export class ApiError extends Error {
  status: number;
  body: unknown;
  missing: string[];
  constructor(status: number, message: string, body: unknown, missing: string[]) {
    super(message);
    this.status = status;
    this.body = body;
    this.missing = missing;
  }
}

export const baseUrl: string = import.meta.env.VITE_ARTEMIS_API_URL ?? "/api";
export const studioUiUrl: string = (import.meta.env.VITE_STUDIO_UI_URL ?? "http://localhost:3000").replace(/\/$/, "");
export const studioApiUrl: string = (import.meta.env.VITE_STUDIO_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export const studioSessionUrl = (sessionId: string) => `${studioUiUrl}/sessions/${encodeURIComponent(sessionId)}`;

/** artifact://<session_id>/<file> → Studio artifact URL; other schemes are not fetchable. */
export function artifactUrl(uri: string | undefined): string | null {
  if (!uri) return null;
  const match = /^artifact:\/\/([^/]+)\/(.+)$/.exec(uri);
  if (match) return `${studioApiUrl}/api/artifacts/${encodeURIComponent(match[1])}/${match[2].split("/").map(encodeURIComponent).join("/")}`;
  if (/^https?:\/\//.test(uri)) return uri;
  return null;
}

const obj = (value: unknown): Json => (isObj(value) ? value : {});
const isObj = (value: unknown): value is Json => typeof value === "object" && value !== null && !Array.isArray(value);
const str = (value: unknown): string | null => (typeof value === "string" && value ? value : typeof value === "number" ? String(value) : null);
const strList = (value: unknown): string[] => (Array.isArray(value) ? value.map((item) => (typeof item === "string" ? item : JSON.stringify(item))) : []);

function detailMessage(body: unknown, status: number): { message: string; missing: string[] } {
  const detail = isObj(body) ? body.detail : undefined;
  const missing = strList(isObj(detail) ? detail.missing : isObj(body) ? body.missing : undefined);
  if (typeof detail === "string") return { message: detail, missing };
  if (isObj(detail)) return { message: str(detail.message) ?? str(detail.detail) ?? str(detail.error) ?? `Request failed (${status})`, missing };
  if (Array.isArray(detail)) {
    const first = detail[0];
    return { message: isObj(first) ? `${strList(first.loc).join(".")}: ${str(first.msg) ?? "invalid"}` : `Request failed (${status})`, missing };
  }
  return { message: `Request failed (${status})`, missing };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, { ...init, headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) } });
  } catch {
    throw new ApiError(0, "Artemis API is unreachable", null, []);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const { message, missing } = detailMessage(body, response.status);
    throw new ApiError(response.status, message, body, missing);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

const post = <T>(path: string, body: unknown = {}, headers?: Record<string, string>) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body), headers });

// ---------- Demo (deterministic MVP) ----------

const demoScenario = { id: "dashboard-stt-demo", caller_name: "Sam Rivera", confirmation_code: "B8Q2", expected_database_state: { customer_name: "Sam Rivera", confirmation_code: "B8Q2" }, seed: 12, forced_stt_confusion: "P8Q2" };

export async function runDemo(): Promise<{ run: Run; diagnosis: Diagnosis }> {
  const run = await post<Run>("/runs", demoScenario);
  const diagnosis = await post<Diagnosis>(`/runs/${run.id}/diagnoses`, { turn_index: 0, intervention: "stt_oracle" });
  return { run, diagnosis };
}

// ---------- Health / readiness ----------

export const health = () => request<{ status: string }>("/health");

function check(label: string, ok: boolean | null, value: string, detail?: string): ReadinessCheck {
  return { label, ok, value, detail };
}

function boolish(value: unknown): boolean | null {
  if (typeof value === "boolean") return value;
  if (isObj(value)) {
    for (const key of ["ok", "healthy", "reachable", "configured", "valid", "status"]) {
      const inner = value[key];
      if (typeof inner === "boolean") return inner;
      if (typeof inner === "string") return ["ok", "healthy", "up", "reachable", "configured"].includes(inner.toLowerCase());
    }
  }
  if (typeof value === "string") return ["ok", "healthy", "up", "reachable", "configured", "true"].includes(value.toLowerCase());
  return null;
}

export function normalizeReadiness(raw: Json): Readiness {
  // Providers: bool | {configured, providers:{name:bool}} | {name: bool|{configured}} | list
  const providersRaw = raw.providers ?? raw.providers_configured;
  let providers: ReadinessCheck;
  if (isObj(providersRaw) && ("artemis" in providersRaw || "studio" in providersRaw)) {
    // Real shape: { artemis: {valid, error, mode, stt, tts, judge, ...}, studio: {...} | null }
    const artemis = obj(providersRaw.artemis);
    const studioProviders = providersRaw.studio;
    const valid = typeof artemis.valid === "boolean" ? artemis.valid : null;
    const parts = [str(artemis.mode) && `mode ${str(artemis.mode)}`, str(artemis.stt) && `STT ${str(artemis.stt)}`, str(artemis.tts) && `TTS ${str(artemis.tts)}`, str(artemis.judge) && `judge ${str(artemis.judge)}`].filter(Boolean);
    const studioPart = isObj(studioProviders) ? `Studio: ${Object.entries(studioProviders).map(([key, value]) => `${key} ${boolish(value) === false ? "✕" : typeof value === "string" ? value : "✓"}`).join(", ")}` : studioProviders === null ? "Studio: not reported" : "";
    providers = check("Providers configured", valid, valid === null ? "Unknown" : valid ? "Valid" : "Invalid", str(artemis.error) ?? ([parts.join(" · "), studioPart].filter(Boolean).join(" | ") || undefined));
  } else if (Array.isArray(providersRaw)) {
    const items = providersRaw.filter(isObj);
    const ok = items.filter((item) => boolish(item.configured ?? item) === true).length;
    providers = check("Providers configured", items.length ? ok === items.length : null, `${ok}/${items.length}`, items.map((item) => `${str(item.name) ?? str(item.provider) ?? "?"}: ${boolish(item.configured ?? item) ? "yes" : "no"}`).join(" · "));
  } else if (isObj(providersRaw)) {
    const nested = isObj(providersRaw.providers) ? providersRaw.providers : isObj(providersRaw.details) ? providersRaw.details : providersRaw;
    const entries = Object.entries(nested).filter(([key]) => !["configured", "ok", "status", "providers", "details"].includes(key));
    const ok = entries.filter(([, value]) => boolish(value) === true).length;
    const top = boolish(providersRaw.configured ?? providersRaw.ok);
    providers = check("Providers configured", top ?? (entries.length ? ok === entries.length : null), entries.length ? `${ok}/${entries.length}` : top ? "Yes" : "No", entries.map(([key, value]) => `${key}: ${boolish(value) ? "yes" : "no"}`).join(" · "));
  } else {
    const ok = boolish(providersRaw);
    providers = check("Providers configured", ok, ok === null ? "Unknown" : ok ? "Yes" : "No");
  }

  const dbRaw = raw.database ?? raw.db ?? raw.database_healthy;
  const dbOk = boolish(dbRaw);
  const database = check("Database healthy", dbOk, dbOk === null ? "Unknown" : dbOk ? "Healthy" : "Unhealthy", isObj(dbRaw) ? str(dbRaw.path) ?? str(dbRaw.error) ?? str(dbRaw.detail) ?? undefined : undefined);

  const studioRaw = raw.studio ?? raw.studio_reachable;
  const studioOk = isObj(studioRaw) && typeof studioRaw.reachable === "boolean" ? studioRaw.reachable : boolish(studioRaw);
  const studio = check("Studio reachable", studioOk, studioOk === null ? "Unknown" : studioOk ? "Reachable" : "Unreachable", isObj(studioRaw) ? [str(studioRaw.url), str(studioRaw.error)].filter(Boolean).join(" · ") || undefined : undefined);

  const replay = isObj(raw.replay) ? raw.replay : isObj(raw.replay_readiness) ? raw.replay_readiness : raw;
  const readyCount = Number(replay.replay_ready_runs ?? replay.ready ?? replay.replay_ready ?? replay.ready_runs ?? NaN);
  const totalCount = Number(replay.total_runs ?? replay.total ?? replay.imported_runs ?? raw.imported_runs ?? NaN);
  const refused = Number(replay.refused_imports ?? NaN);
  const replayReady = check("Replay-ready runs", Number.isFinite(readyCount) ? readyCount > 0 : null, Number.isFinite(readyCount) ? (Number.isFinite(totalCount) ? `${readyCount}/${totalCount}` : String(readyCount)) : "—", [Number.isFinite(totalCount) ? `${totalCount} imported` : "", Number.isFinite(refused) && refused > 0 ? `${refused} refused` : ""].filter(Boolean).join(" · ") || undefined);

  // missing artifacts: list[{run_id, missing}] | {run_id: [..]} | runs:[{id, missing}]
  const missingByRun: Readiness["missingByRun"] = [];
  const missingRaw = raw.missing_artifacts ?? raw.missing ?? replay.missing_artifacts ?? raw.runs;
  if (Array.isArray(missingRaw)) {
    for (const item of missingRaw) if (isObj(item)) {
      const list = strList(item.missing ?? item.missing_artifacts);
      const runId = str(item.run_id) ?? str(item.id);
      const sessionId = str(item.session_id) ?? str(item.studio_session_id);
      if (list.length) missingByRun.push({ run: runId ?? sessionId ?? "run", runId, sessionId, missing: list });
    }
  } else if (isObj(missingRaw)) {
    for (const [run, value] of Object.entries(missingRaw)) { const list = strList(value); if (list.length) missingByRun.push({ run, runId: run, sessionId: null, missing: list }); }
  }
  const missingCount = typeof raw.missing_artifacts_count === "number" ? raw.missing_artifacts_count : missingByRun.reduce((sum, item) => sum + item.missing.length, 0);
  const missing = check("Missing trace artifacts", missingCount === 0, String(missingCount), missingByRun.length ? `${missingByRun.length} run(s) affected` : "No gaps reported");

  const recentRaw = raw.recent_diagnoses ?? raw.diagnoses;
  const recentDiagnoses = Array.isArray(recentRaw) ? recentRaw.filter(isDiagnosis) : [];
  return { raw, ok: typeof raw.ok === "boolean" ? raw.ok : null, providers, database, studio, replayReady, missing, missingByRun, recentDiagnoses };
}

export const readiness = async () => normalizeReadiness((await request<Json>("/readiness")) ?? {});

// ---------- VoiceFlow runs ----------

function isDiagnosis(value: unknown): value is Diagnosis {
  return isObj(value) && typeof value.intervention === "string" && isObj(value.control) && isObj(value.intervention_arm);
}

export function normalizeRunSummary(item: Json): RunSummary {
  const run = isObj(item.run) ? item.run : item;
  const last = isObj(item.last_diagnosis) ? item.last_diagnosis : isObj(item.diagnosis) ? item.diagnosis : null;
  const session = isObj(item.session) ? item.session : null;
  const passed = typeof item.passed === "boolean" ? item.passed : null;
  return {
    id: str(item.run_id) ?? str(run.id) ?? "",
    studio_session_id: str(item.session_id) ?? str(item.studio_session_id) ?? str(run.studio_session_id) ?? str(session?.id),
    end_node: str(item.end_node),
    trace_source: str(item.trace_source) ?? str(item.mode),
    turn_count: typeof item.turn_count === "number" ? item.turn_count : null,
    bot: str(item.bot_name) ?? str(item.bot) ?? str(item.bot_slug) ?? str(session?.bot_name) ?? str(run.bot_name),
    scenario: str(item.scenario_id) ?? str(run.scenario_id),
    oracle: str(item.oracle_name) ?? str(item.oracle_slug) ?? str(item.oracle_id) ?? str(run.oracle_id),
    status: str(item.status) ?? str(session?.status),
    passed,
    replay_ready: typeof item.replay_ready === "boolean" ? item.replay_ready : typeof run.replay_ready === "boolean" ? (run.replay_ready as boolean) : null,
    missing: strList(item.missing ?? run.missing),
    last_verdict: str(item.last_verdict) ?? str(last?.verdict),
    last_intervention: str(item.last_intervention) ?? str(last?.intervention),
    created_at: str(item.created_at) ?? str(item.imported_at) ?? str(run.created_at),
  };
}

export async function listVoiceflowRuns(includeRefused = false): Promise<RunSummary[]> {
  const body = await request<unknown>(`/voiceflow/runs${includeRefused ? "?include_refused=true" : ""}`);
  const list = Array.isArray(body) ? body : isObj(body) && Array.isArray(body.runs) ? body.runs : isObj(body) && Array.isArray(body.items) ? body.items : [];
  return list.filter(isObj).map(normalizeRunSummary).filter((item) => item.id || item.studio_session_id);
}

/** POST /voiceflow/import → run summary ({run_id, session_id, ..., created}); 422 detail.missing when not replay-ready. */
export async function importVoiceflowSession(sessionId: string): Promise<RunSummary & { created?: boolean }> {
  const body = await post<unknown>("/voiceflow/import", { session_id: sessionId });
  const summary = normalizeRunSummary(obj(body));
  return { ...summary, created: isObj(body) && typeof body.created === "boolean" ? body.created : undefined };
}

export const getRun = (id: string) => request<Run>(`/runs/${encodeURIComponent(id)}`);

export type RubricStatus = "pass" | "fail" | "warn" | "pending" | "not_applicable";
export type Rubric = { number: number; id: string; name: string; stage: string; question: string; status: RubricStatus; summary: string; details: string[] };
export type RubricReport = { run_id: string; counts: Record<RubricStatus, number>; rubrics: Rubric[] };
export const getRubrics = (id: string) => request<RubricReport>(`/runs/${encodeURIComponent(id)}/rubrics`);

// ---------- Diagnoses ----------

export function normalizeDiagnosisSet(body: unknown): DiagnosisSet {
  if (Array.isArray(body)) return { diagnoses: body.filter(isDiagnosis) };
  if (isObj(body)) {
    const list = body.arms ?? body.diagnoses ?? body.results ?? body.items;
    const provenLayers = strList(body.proven_layers);
    return {
      id: str(body.id) ?? undefined,
      source_run_id: str(body.source_run_id) ?? undefined,
      diagnoses: Array.isArray(list) ? list.filter(isDiagnosis) : isDiagnosis(body) ? [body] : [],
      control: isObj(body.control) ? (body.control as unknown as Arm) : undefined,
      verdict: str(body.verdict),
      summary: str(body.summary) ?? str(body.verdict_sentence),
      proven_layer: provenLayers[0] ?? str(body.proven_layer) ?? str(body.proven_intervention),
      proven_layers: provenLayers,
      trials: typeof body.trials === "number" ? body.trials : undefined,
      turn_index: typeof body.turn_index === "number" ? body.turn_index : undefined,
      created_at: str(body.created_at) ?? undefined,
    };
  }
  return { diagnoses: [] };
}

export async function diagnoseAll(runId: string, trials: number, turnIndex?: number): Promise<DiagnosisSet> {
  return normalizeDiagnosisSet(await post<unknown>(`/runs/${encodeURIComponent(runId)}/diagnoses/all`, turnIndex === undefined ? { trials } : { trials, turn_index: turnIndex }));
}

/** GET /runs/{id}/diagnoses/all → latest CausalReport, or null when none exists (404). */
export async function latestReport(runId: string): Promise<DiagnosisSet | null> {
  try {
    return normalizeDiagnosisSet(await request<unknown>(`/runs/${encodeURIComponent(runId)}/diagnoses/all`));
  } catch (error) {
    if (error instanceof ApiError && [404, 405].includes(error.status)) return null;
    throw error;
  }
}

/** GET /diagnoses → recent CausalReports across runs. */
export async function recentReports(limit = 20): Promise<DiagnosisSet[] | null> {
  try {
    const body = await request<unknown>(`/diagnoses?limit=${limit}`);
    return Array.isArray(body) ? body.map(normalizeDiagnosisSet) : [];
  } catch (error) {
    if (error instanceof ApiError && [404, 405].includes(error.status)) return null;
    throw error;
  }
}

// ---------- Patches ----------

export const patchType = (patch: PatchProposal) => patch.patch?.type ?? patch.type ?? patch.patch_type ?? "unknown";
export function patchParams(patch: PatchProposal): Json {
  if (patch.patch?.params) return patch.patch.params;
  if (patch.params) return patch.params;
  if (patch.stt_keyword_boosts?.length) return { keywords: patch.stt_keyword_boosts };
  return {};
}

const unwrapPatch = (body: unknown): PatchProposal => (isObj(body) && isObj(body.proposal) ? (body.proposal as unknown as PatchProposal) : isObj(body) && isObj(body.patch) && typeof body.patch.id === "string" ? (body.patch as unknown as PatchProposal) : (body as PatchProposal));

export async function proposeVoiceflowPatch(sourceRunId: string, layer: string): Promise<PatchProposal> {
  return unwrapPatch(await post<unknown>("/voiceflow/patches", { source_run_id: sourceRunId, layer }));
}

const withGate = (body: unknown): PatchWithGate => ({ patch: unwrapPatch(body), gate: isObj(body) && isObj(body.gate) ? normalizeGate(body.gate) : null });

/** GET /patches → [{patch, gate}] (all sources). null when the listing is not available. */
export async function listPatches(): Promise<PatchWithGate[] | null> {
  try {
    const body = await request<unknown>("/patches");
    const list = Array.isArray(body) ? body : isObj(body) && Array.isArray(body.patches) ? body.patches : [];
    return list.filter(isObj).map(withGate).filter((item) => typeof item.patch?.id === "string");
  } catch (error) {
    if (error instanceof ApiError && [404, 405].includes(error.status)) return null;
    throw error;
  }
}

/** GET /patches/{id} → {patch, gate}. null on 404. */
export async function getPatch(id: string): Promise<PatchWithGate | null> {
  try {
    return withGate(await request<unknown>(`/patches/${encodeURIComponent(id)}`));
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export function normalizeGate(body: unknown): PatchGateResult {
  const gate = isObj(body) && isObj(body.gate) ? body.gate : isObj(body) ? body : {};
  const setsRaw = gate.sets;
  let sets: GateSet[] = [];
  if (Array.isArray(setsRaw)) sets = setsRaw.filter(isObj).map((item) => ({ ...(item as Json), name: str(item.name) ?? "set" }) as GateSet);
  else if (isObj(setsRaw)) sets = Object.entries(setsRaw).filter(([, value]) => isObj(value)).map(([name, value]) => ({ ...(value as Json), name }) as GateSet);
  return { patch_id: str(gate.patch_id) ?? undefined, sets, passed: gate.passed === true, failure_reason: str(gate.failure_reason), warnings: strList(gate.warnings) };
}

export async function validatePatch(id: string): Promise<PatchGateResult> {
  return normalizeGate(await post<unknown>(`/patches/${encodeURIComponent(id)}/validate`, {}));
}

export async function approvePatch(id: string, approver: string): Promise<PatchProposal> {
  return unwrapPatch(await post<unknown>(`/patches/${encodeURIComponent(id)}/approve`, { approved_by: approver }, { "X-Approved-By": approver }));
}

export async function rememberPatch(id: string, name?: string): Promise<Json> {
  return post<Json>(`/patches/${encodeURIComponent(id)}/remember`, name ? { name } : {});
}

/** GET /regressions → saved RegressionCases (used to show "remembered" state). */
export async function listRegressions(): Promise<Json[]> {
  const body = await request<unknown>("/regressions");
  return Array.isArray(body) ? body.filter(isObj) : [];
}

// ---------- Shared semantics ----------

export const LAYERS = ["caller", "endpointing", "speech_to_text", "llm", "tools", "text_to_speech"] as const;
export type LayerName = (typeof LAYERS)[number];

export const layerMeta: Record<string, { short: string; label: string; oracle?: Intervention }> = {
  caller: { short: "01", label: "Caller" },
  endpointing: { short: "02", label: "Endpointing", oracle: "endpointing_oracle" },
  speech_to_text: { short: "03", label: "Speech to text", oracle: "stt_oracle" },
  llm: { short: "04", label: "Reasoning (LLM)", oracle: "llm_oracle" },
  tools: { short: "05", label: "Tools", oracle: "tools_oracle" },
  text_to_speech: { short: "06", label: "Voice response (TTS)", oracle: "tts_oracle" },
};

export const interventionLayer: Record<string, LayerName> = {
  stt_oracle: "speech_to_text",
  endpointing_oracle: "endpointing",
  llm_oracle: "llm",
  tools_oracle: "tools",
  tts_oracle: "text_to_speech",
};

/** Short layer keyword used by POST /voiceflow/patches {layer}. */
export const interventionPatchLayer: Record<string, string> = {
  stt_oracle: "stt",
  endpointing_oracle: "endpointing",
  llm_oracle: "llm",
  tools_oracle: "tools",
  tts_oracle: "tts",
};

export const interventionLabel = (value: string) =>
  ({ control: "Control", stt_oracle: "STT oracle", endpointing_oracle: "Endpointing oracle", llm_oracle: "LLM oracle", tools_oracle: "Tools oracle", tts_oracle: "TTS oracle" })[value] ?? value.replaceAll("_", " ");

const shortLayerName: Record<string, string> = { stt_oracle: "STT", endpointing_oracle: "endpointing", llm_oracle: "LLM", tools_oracle: "tools", tts_oracle: "TTS" };

export function verdictSentence(set: DiagnosisSet): string {
  if (set.summary) return set.summary;
  const ranked = [...set.diagnoses].sort((a, b) => b.effect_size - a.effect_size);
  const proven = ranked.find((item) => item.verdict === "proven");
  if (proven) {
    const arm = proven.intervention_arm;
    return `Replacing only the ${shortLayerName[proven.intervention] ?? proven.intervention} result changed the outcome from failure to success in ${arm.pass_count}/${arm.trials} replays.`;
  }
  if (ranked.some((item) => item.verdict === "not_the_agent")) return "The control arm already passes; the failure does not reproduce, so no layer can be blamed.";
  const likely = ranked.find((item) => item.verdict === "likely");
  if (likely) return `Repairing the ${shortLayerName[likely.intervention] ?? likely.intervention} layer likely helps (${likely.intervention_arm.pass_count}/${likely.intervention_arm.trials}), but the evidence is not yet significant.`;
  return "No single-layer repair changed the outcome. The diagnosis is inconclusive.";
}

export function provenDiagnosis(set: DiagnosisSet | undefined): Diagnosis | undefined {
  if (!set) return undefined;
  if (set.proven_layer) {
    // proven_layers holds Layer values (speech_to_text, ...); accept interventions / short names too.
    const match = set.diagnoses.find((item) => item.intervention === set.proven_layer || interventionLayer[item.intervention] === set.proven_layer || interventionPatchLayer[item.intervention] === set.proven_layer);
    if (match) return match;
  }
  return [...set.diagnoses].filter((item) => item.verdict === "proven").sort((a, b) => b.effect_size - a.effect_size || a.fisher_exact_p_value - b.fisher_exact_p_value)[0];
}
