import type { Artifact, Layer, Run, Turn } from "./api";

type Json = Record<string, unknown>;
const isObj = (value: unknown): value is Json => typeof value === "object" && value !== null && !Array.isArray(value);

/** Accept both Artemis (list of LayerTrace) and Studio (dict keyed by layer) shapes. */
export function layersOf(turn: Turn | undefined): Record<string, Layer> {
  const result: Record<string, Layer> = {};
  if (!turn) return result;
  if (Array.isArray(turn.layers)) {
    for (const entry of turn.layers) if (entry && typeof entry.layer === "string") result[entry.layer] = { ...entry, output: entry.output ?? {} };
  } else if (isObj(turn.layers)) {
    for (const [name, entry] of Object.entries(turn.layers)) if (isObj(entry)) result[name] = { layer: name, ...(entry as Omit<Layer, "layer">), output: (entry as Json).output as Json ?? {} };
  }
  return result;
}

export const obj = (value: unknown): Json => (isObj(value) ? value : {});
export const text = (value: unknown): string | null => (typeof value === "string" ? value : typeof value === "number" ? String(value) : null);
export const num = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) ? value : null);

export function asArtifact(value: unknown): Artifact | null {
  return isObj(value) && typeof value.uri === "string" ? (value as Artifact) : null;
}

export function stateBefore(turn: Turn, layers: Record<string, Layer>): Json {
  return obj(turn.state_before ?? turn.database_state_before ?? layers.tools?.output?.state_before);
}
export function stateAfter(turn: Turn, layers: Record<string, Layer>): Json {
  return obj(turn.state_after ?? turn.database_state_after ?? layers.tools?.output?.state_after ?? layers.tools?.output?.database_state);
}

export type StateRow = { key: string; expected?: string; actual?: string; match: boolean | null };

export function stateDiff(expected: Json, actual: Json): StateRow[] {
  const keys = Array.from(new Set([...Object.keys(expected), ...Object.keys(actual)]));
  const norm = (value: unknown) => (value === undefined || value === null ? undefined : String(value));
  return keys
    .map((key) => {
      const e = norm(expected[key]);
      const a = norm(actual[key]);
      return { key, expected: e, actual: a, match: e === undefined ? null : e.trim().toLowerCase() === (a ?? "").trim().toLowerCase() };
    })
    .sort((x, y) => Number(x.match !== false) - Number(y.match !== false) || Number(x.key.startsWith("__")) - Number(y.key.startsWith("__")) || x.key.localeCompare(y.key));
}

export function changedKeys(before: Json, after: Json): string[] {
  return Array.from(new Set([...Object.keys(before), ...Object.keys(after)])).filter((key) => JSON.stringify(before[key]) !== JSON.stringify(after[key]));
}

export function lastSpokenText(run: Run): string | null {
  for (let index = run.turns.length - 1; index >= 0; index--) {
    const spoken = text(layersOf(run.turns[index]).text_to_speech?.output?.spoken_text);
    if (spoken) return spoken;
  }
  return null;
}

/** Contract §4: expected ⊆ final ∧ spoken contains all expected strings (exact match for deterministic runs). */
export function runOutcome(run: Run): { passed: boolean; spokenChecks: { needle: string; ok: boolean }[] } {
  const diff = stateDiff(obj(run.expected_database_state), obj(run.final_database_state));
  const spoken = (lastSpokenText(run) ?? "").toLowerCase();
  const needles = run.expected_spoken_contains?.length ? run.expected_spoken_contains : run.source !== "voiceflow" && run.expected_spoken_text ? [run.expected_spoken_text] : [];
  const spokenChecks = needles.map((needle) => ({ needle, ok: spoken.includes(needle.toLowerCase()) }));
  return { passed: diff.every((row) => row.match !== false) && spokenChecks.every((item) => item.ok), spokenChecks };
}

/** Word-level highlight: which tokens of `actual` are not present in `reference`. */
export function tokenDiff(reference: string | null, actual: string | null): { token: string; bad: boolean }[] {
  if (!actual) return [];
  const ref = new Set((reference ?? "").toLowerCase().split(/[\s,.!?]+/).filter(Boolean));
  return actual.split(/(\s+)/).map((token) => {
    const clean = token.toLowerCase().replace(/[,.!?]/g, "");
    return { token, bad: reference !== null && clean.trim() !== "" && !ref.has(clean) };
  });
}
