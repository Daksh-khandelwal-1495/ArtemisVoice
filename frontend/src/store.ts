import type { DiagnosisSet, PatchGateResult, PatchProposal } from "./api";

// Per-browser memory of what this viewer ran. The API is the source of truth; this only
// fills gaps where list endpoints do not exist yet (recent diagnoses, patch list).

const KEY = "artemis.dashboard.v1";

export type DiagnosisRecord = { runId: string; trials: number; at: string; set: DiagnosisSet; scenario?: string | null };
export type PatchRecord = { patch: PatchProposal; runId: string; layer: string; intervention?: string; at: string; gate?: PatchGateResult; remembered?: boolean };
type State = { diagnoses: DiagnosisRecord[]; patches: Record<string, PatchRecord> };

function load(): State {
  try {
    const parsed = JSON.parse(localStorage.getItem(KEY) ?? "null");
    if (parsed && Array.isArray(parsed.diagnoses) && typeof parsed.patches === "object") return parsed as State;
  } catch { /* storage unavailable */ }
  return { diagnoses: [], patches: {} };
}

function save(state: State) {
  try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* storage unavailable */ }
}

export function recordDiagnosis(record: DiagnosisRecord) {
  const state = load();
  state.diagnoses = [record, ...state.diagnoses.filter((item) => !(item.runId === record.runId && item.at === record.at))].slice(0, 25);
  save(state);
}

export const recentDiagnoses = () => load().diagnoses;
export const latestDiagnosisFor = (runId: string) => load().diagnoses.find((item) => item.runId === runId);

export function recordPatch(record: PatchRecord) {
  const state = load();
  state.patches[record.patch.id] = { ...state.patches[record.patch.id], ...record };
  save(state);
}

export function updatePatch(id: string, update: Partial<PatchRecord>) {
  const state = load();
  const existing = state.patches[id];
  if (!existing) return;
  state.patches[id] = { ...existing, ...update };
  save(state);
}

export const getPatchRecord = (id: string): PatchRecord | undefined => load().patches[id];
export const listPatchRecords = () => Object.values(load().patches).sort((a, b) => b.at.localeCompare(a.at));
