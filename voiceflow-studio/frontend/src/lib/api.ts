import type {
  BotDetail,
  BotStats,
  BotSummary,
  CreateBotBody,
  Dataset,
  DatasetRow,
  DatasetRowsResponse,
  FlowGraph,
  FlowResponse,
  FlowValidationResult,
  Health,
  Intervention,
  OracleBody,
  PatchBotBody,
  PatchCandidate,
  PatchSpec,
  ReplayResponse,
  SessionTrace,
  SimulateBody,
  SimulateResponse,
  TestOracle,
  ProviderCatalog,
  SaveFlowResponse,
  SessionDetail,
  SessionList,
  VersionDetail,
  VersionInfo,
} from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");
export const MOCK = process.env.NEXT_PUBLIC_MOCK === "1";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown, message?: string) {
    super(message ?? (typeof detail === "string" ? detail : `Request failed (${status})`));
    this.status = status;
    this.detail = detail;
  }
}

type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export async function request<T>(method: Method, path: string, body?: unknown): Promise<T> {
  if (MOCK) {
    const { mockRequest } = await import("./mock");
    return mockRequest(method, path, body) as Promise<T>;
  }
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api${path}`, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "Network error: backend unreachable", `Cannot reach backend at ${API_URL}`);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let data: unknown = undefined;
  try {
    data = text ? JSON.parse(text) : undefined;
  } catch {
    data = text;
  }
  if (!res.ok) {
    const detail = (data as { detail?: unknown })?.detail ?? data;
    throw new ApiError(res.status, detail, formatDetail(detail) || `${res.status} ${res.statusText}`);
  }
  return data as T;
}

/** Turn FastAPI `detail` (string | pydantic error list | object) into a readable string. */
export function formatDetail(detail: unknown): string {
  if (detail == null) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        const e = d as { loc?: (string | number)[]; msg?: string };
        return e.loc ? `${e.loc.filter((x) => x !== "body").join(".")}: ${e.msg}` : (e.msg ?? JSON.stringify(d));
      })
      .join("; ");
  }
  if (typeof detail === "object") {
    const o = detail as { message?: string; msg?: string };
    return o.message ?? o.msg ?? JSON.stringify(detail);
  }
  return String(detail);
}

/** Pydantic 422 errors keyed by dotted location (without the leading "body"/"config"). */
export function fieldErrors(err: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  if (!(err instanceof ApiError) || !Array.isArray(err.detail)) return out;
  for (const d of err.detail as { loc?: (string | number)[]; msg?: string }[]) {
    if (!d.loc) continue;
    const loc = d.loc.filter((x) => x !== "body" && x !== "config").join(".");
    out[loc] = d.msg ?? "invalid";
  }
  return out;
}

export const fetcher = <T>(path: string) => request<T>("GET", path);

const enc = encodeURIComponent;

export const api = {
  health: () => request<Health>("GET", "/health"),
  providers: () => request<ProviderCatalog>("GET", "/providers"),

  listBots: () => request<BotSummary[]>("GET", "/bots?include_archived=true"),
  getBot: (id: string) => request<BotDetail>("GET", `/bots/${enc(id)}`),
  createBot: (body: CreateBotBody) => request<BotDetail>("POST", "/bots", body),
  patchBot: (id: string, body: PatchBotBody) => request<BotDetail>("PATCH", `/bots/${enc(id)}`, body),
  deleteBot: (id: string, hard = false) => request<void>("DELETE", `/bots/${enc(id)}${hard ? "?hard=true" : ""}`),
  duplicateBot: (id: string) => request<BotDetail>("POST", `/bots/${enc(id)}/duplicate`),
  stats: (id: string) => request<BotStats>("GET", `/bots/${enc(id)}/stats`),

  getFlow: (id: string) => request<FlowResponse>("GET", `/bots/${enc(id)}/flow`),
  saveFlow: (id: string, graph: FlowGraph, note?: string) =>
    request<SaveFlowResponse>("PUT", `/bots/${enc(id)}/flow`, { graph, note }),
  validateFlow: (id: string, graph: FlowGraph) =>
    request<FlowValidationResult>("POST", `/bots/${enc(id)}/flow/validate`, { graph }),
  publish: (id: string, version_id?: string) =>
    request<BotDetail>("POST", `/bots/${enc(id)}/publish`, version_id ? { version_id } : {}),
  versions: (id: string) => request<VersionInfo[]>("GET", `/bots/${enc(id)}/versions`),
  version: (id: string, versionId: string) =>
    request<VersionDetail>("GET", `/bots/${enc(id)}/versions/${enc(versionId)}`),
  restoreVersion: (id: string, versionId: string) =>
    request<SaveFlowResponse>("POST", `/bots/${enc(id)}/versions/${enc(versionId)}/restore`),

  datasets: () => request<Dataset[]>("GET", "/datasets"),
  datasetRows: async (name: string, q = "", limit = 50, offset = 0): Promise<DatasetRowsResponse> => {
    const r = await request<DatasetRow[] | DatasetRowsResponse>(
      "GET",
      `/datasets/${enc(name)}/rows?q=${enc(q)}&limit=${limit}&offset=${offset}`,
    );
    return Array.isArray(r) ? { items: r, total: r.length } : r;
  },
  addRow: (name: string, data: Record<string, unknown>) =>
    request<DatasetRow>("POST", `/datasets/${enc(name)}/rows`, { data }),
  deleteRow: (name: string, rowId: string) => request<void>("DELETE", `/datasets/${enc(name)}/rows/${enc(rowId)}`),

  sessions: (params: { bot_id?: string; status?: string; mode?: string; include_replay?: boolean; limit?: number; offset?: number }) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => v !== undefined && v !== "" && qs.set(k, String(v)));
    return request<SessionList>("GET", `/sessions?${qs}`);
  },
  session: (id: string, reveal = false) => request<SessionDetail>("GET", `/sessions/${enc(id)}${reveal ? "?reveal=true" : ""}`),

  // Replay-grade trace + evidence (INTEGRATION_CONTRACT §1)
  trace: (id: string) => request<SessionTrace>("GET", `/sessions/${enc(id)}/trace`),
  artemisEvidence: (id: string) => request<Record<string, unknown>>("GET", `/sessions/${enc(id)}/artemis-evidence`),

  // Test oracles (§2)
  oracles: (botId: string) => request<TestOracle[]>("GET", `/bots/${enc(botId)}/oracles`),
  oracle: (id: string) => request<TestOracle>("GET", `/oracles/${enc(id)}`),
  createOracle: (botId: string, body: OracleBody) => request<TestOracle>("POST", `/bots/${enc(botId)}/oracles`, body),
  patchOracle: (id: string, body: OracleBody) => request<TestOracle>("PATCH", `/oracles/${enc(id)}`, body),
  deleteOracle: (id: string) => request<void>("DELETE", `/oracles/${enc(id)}`),
  bindOracle: (sessionId: string, oracle_id: string, turn_truths?: Record<number, string>) =>
    request<unknown>("POST", `/sessions/${enc(sessionId)}/oracle`, turn_truths ? { oracle_id, turn_truths } : { oracle_id }),

  // Replay / simulate / patch candidates (§3)
  replay: (sessionId: string, body: { intervention: Intervention; trials: number; seed?: number; patch?: PatchSpec }) =>
    request<ReplayResponse>("POST", `/sessions/${enc(sessionId)}/replay`, body),
  simulate: (botId: string, body: SimulateBody) => request<SimulateResponse>("POST", `/bots/${enc(botId)}/simulate`, body),
  createPatchCandidate: (botId: string, patch: PatchSpec, base_version_id?: string) =>
    request<PatchCandidate>("POST", `/bots/${enc(botId)}/patch-candidates`, base_version_id ? { patch, base_version_id } : { patch }),
};

/** Resolve `artifact://<session_id>/<file>` to the Studio artifact endpoint. */
export function artifactUrl(uri: string): string {
  const m = /^artifact:\/\/([^/]+)\/(.+)$/.exec(uri);
  if (!m) return uri;
  return `${API_URL}/api/artifacts/${enc(m[1])}/${m[2].split("/").map(enc).join("/")}`;
}

/** Artemis causal-debugger dashboard (root `frontend/`, Vite :5173, hash routes). */
export const ARTEMIS_DASHBOARD_URL = (process.env.NEXT_PUBLIC_ARTEMIS_DASHBOARD_URL ?? "http://localhost:5173").replace(/\/$/, "");
/** Deep link that asks the Artemis dashboard to import a Studio session. Change the route here only. */
export const artemisImportUrl = (sessionId: string) => `${ARTEMIS_DASHBOARD_URL}/#/runs?import=${enc(sessionId)}`;

/** Trigger a browser download of a JSON value. */
export function downloadJson(filename: string, value: unknown) {
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const offerUrl = (botId: string, mode: "draft" | "published") =>
  `${API_URL}/api/bots/${enc(botId)}/offer?mode=${mode}`;
