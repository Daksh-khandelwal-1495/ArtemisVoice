// Shared helpers for test oracles, fault profiles and replay outcomes (INTEGRATION_CONTRACT §2–§3).
import type { FaultProfile, Intervention, ReplayOutcome, TestOracle } from "./types";

export const INTERVENTIONS: { value: Intervention; label: string; hint: string }[] = [
  { value: "control", label: "Control", hint: "Replay exactly as recorded (faults included)" },
  { value: "stt_oracle", label: "STT oracle", hint: "Replace the STT result with the oracle truth" },
  { value: "endpointing_oracle", label: "Endpointing oracle", hint: "Feed the complete utterance to STT" },
  { value: "llm_oracle", label: "LLM oracle", hint: "Correct tool-call arguments to oracle values" },
  { value: "tools_oracle", label: "Tools oracle", hint: "Lookups / API calls return the expected result" },
  { value: "tts_oracle", label: "TTS oracle", hint: "Speak the LLM response text exactly" },
];

export const SESSION_STATUSES = ["completed", "transferred", "abandoned", "error"] as const;

/** The wrong dealer row used by the Tata tools-fault demo (a Mumbai dealer for a Pune caller). */
export const WRONG_DEALER_ROW = {
  dealer_id: "TD001",
  name: "Sahyadri Motors (Demo) - Andheri West",
  city: "Mumbai",
  pincode: "400053",
  address: "Plot 12, Link Road, Andheri West, Mumbai - 400053",
  phone: "022-5550-0001",
  ev_ready: "yes",
  open_hours: "10:00-19:00",
};

export interface FaultPreset {
  id: string;
  layer: "stt" | "endpointing" | "llm" | "tools" | "tts";
  label: string;
  profile: FaultProfile;
}

/** The five Tata demo faults (roadmap Phase 5), with sub-variants. */
export const FAULT_PRESETS: FaultPreset[] = [
  { id: "stt-pincode", layer: "stt", label: "STT 411001 → 41100I", profile: { stt: { replacements: [{ from: "411001", to: "41100I" }], keyword_protected: true } } },
  // "Nexon" is already a seeded Tata STT keyword, so this variant ships unprotected (otherwise it would be a no-op).
  { id: "stt-nexon", layer: "stt", label: "STT Nexon → Nixon", profile: { stt: { replacements: [{ from: "Nexon", to: "Nixon" }], keyword_protected: false } } },
  { id: "endpointing", layer: "endpointing", label: "Endpointing: pincode cut to 4", profile: { endpointing: { truncate_field: "pincode", keep_chars: 4 } } },
  { id: "llm-swap", layer: "llm", label: "LLM pincode ← phone", profile: { llm: { swap_field: { field: "pincode", with_value_of: "phone" } } } },
  { id: "tools-dealer", layer: "tools", label: "Tools: wrong dealer", profile: { tools: { lookup_override: { dataset: "tata_dealers", rows: [WRONG_DEALER_ROW] }, api_error: false } } },
  { id: "tools-error", layer: "tools", label: "Tools: API error", profile: { tools: { lookup_override: null, api_error: true } } },
  { id: "tts-nexon", layer: "tts", label: "TTS Nexon → Nixon", profile: { tts: { replacements: [{ from: "Nexon", to: "Nixon" }] } } },
];

/** Drop empty sections so `{}` means "no faults". */
export function cleanFaultProfile(p: FaultProfile | null | undefined): FaultProfile | null {
  if (!p) return null;
  const out: FaultProfile = {};
  if (p.stt?.replacements?.some((r) => r.from)) out.stt = { ...p.stt, replacements: p.stt.replacements.filter((r) => r.from) };
  if (p.endpointing?.truncate_field) out.endpointing = p.endpointing;
  if (p.llm?.swap_field?.field && p.llm.swap_field.with_value_of) out.llm = p.llm;
  if (p.tools && (p.tools.api_error || p.tools.lookup_override)) out.tools = p.tools;
  if (p.tts?.replacements?.some((r) => r.from)) out.tts = { replacements: p.tts.replacements.filter((r) => r.from) };
  return Object.keys(out).length ? out : null;
}

export const faultLayers = (p: FaultProfile | null | undefined) => Object.keys(cleanFaultProfile(p) ?? {});

const norm = (v: unknown) =>
  String(v ?? "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, " ");

export interface OutcomeCheck {
  key: string;
  expected: string;
  actual: string;
  ok: boolean;
}

/** Captured vs expected, mirroring the backend pass rule (fields ⊆ captured, end node, status, spoken contains). */
export function compareOutcome(oracle: TestOracle | null | undefined, outcome: ReplayOutcome | null | undefined): OutcomeCheck[] {
  if (!oracle || !outcome) return [];
  const rows: OutcomeCheck[] = [];
  const cap = outcome.captured ?? {};
  for (const [k, v] of Object.entries(oracle.expected_fields ?? {})) {
    rows.push({ key: k, expected: String(v), actual: cap[k] == null ? "—" : String(cap[k]), ok: norm(cap[k]) === norm(v) });
  }
  if (oracle.expected_end_node) rows.push({ key: "end node", expected: oracle.expected_end_node, actual: outcome.end_node ?? "—", ok: outcome.end_node === oracle.expected_end_node });
  if (oracle.expected_status) rows.push({ key: "status", expected: oracle.expected_status, actual: String(outcome.status ?? "—"), ok: outcome.status === oracle.expected_status });
  const spoken = (outcome.spoken_texts ?? []).join(" \n ");
  const last = (outcome.spoken_texts ?? []).slice(-3).join(" ");
  for (const c of oracle.expected_bot_response?.contains ?? []) {
    rows.push({ key: `spoken ∋ “${c}”`, expected: c, actual: norm(spoken).includes(norm(c)) ? c : last ? `…${last.slice(-60)}` : "—", ok: norm(spoken).includes(norm(c)) });
  }
  return rows;
}
