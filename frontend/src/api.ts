export type Layer = { layer: string; output: Record<string, unknown> };
export type Run = { id: string; final_database_state: Record<string, string>; expected_database_state: Record<string, string>; turns: { layers: Layer[] }[] };
export type Diagnosis = { intervention: string; effect_size: number; fisher_exact_p_value: number; verdict: string; control: { pass_count: number; trials: number }; intervention_arm: { pass_count: number; trials: number } };

const baseUrl = import.meta.env.VITE_ARTEMIS_API_URL ?? "http://localhost:8000";
const demoScenario = { id: "dashboard-stt-demo", caller_name: "Sam Rivera", confirmation_code: "B8Q2", expected_database_state: { customer_name: "Sam Rivera", confirmation_code: "B8Q2" }, seed: 12, forced_stt_confusion: "P8Q2" };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, { headers: { "Content-Type": "application/json" }, ...init });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail ?? `Request failed (${response.status})`);
  return response.json() as Promise<T>;
}

export async function runDemo(): Promise<{ run: Run; diagnosis: Diagnosis }> {
  const run = await request<Run>("/runs", { method: "POST", body: JSON.stringify(demoScenario) });
  const diagnosis = await request<Diagnosis>(`/runs/${run.id}/diagnoses`, { method: "POST", body: JSON.stringify({ turn_index: 0, intervention: "stt_oracle" }) });
  return { run, diagnosis };
}
