// TypeScript mirror of backend/app/schemas/flow.py + providers.py and the REST contract (CONTRACT.md).
// Keep these in lockstep with the Pydantic models.

/* ------------------------------------------------------------------ flow.py */

export type NodeType =
  | "start"
  | "collect"
  | "conversation"
  | "decision"
  | "lookup"
  | "api_call"
  | "transfer"
  | "end";

export type FieldType =
  | "string"
  | "number"
  | "integer"
  | "boolean"
  | "enum"
  | "date"
  | "phone"
  | "email"
  | "pincode"
  | "pan"
  | "aadhaar_last4"
  | "amount"
  | "vehicle_reg";

export const FIELD_TYPES: FieldType[] = [
  "string",
  "number",
  "integer",
  "boolean",
  "enum",
  "date",
  "phone",
  "email",
  "pincode",
  "pan",
  "aadhaar_last4",
  "amount",
  "vehicle_reg",
];

export type TransitionKind = "on_complete" | "intent" | "expression" | "fallback";
export const TRANSITION_KINDS: TransitionKind[] = ["on_complete", "intent", "expression", "fallback"];

export type ContextStrategyName = "append" | "reset" | "reset_with_summary";
export type FailureAction = "goto" | "transfer" | "end" | "skip";

/** Same pattern as FlowNode.id / CaptureField.name */
export const SLUG_RE = /^[a-z][a-z0-9_]{0,62}$/;

export interface Position {
  x: number;
  y: number;
}

export interface FieldValidation {
  regex?: string | null;
  min?: number | null;
  max?: number | null;
  options?: string[] | null;
  future_only?: boolean;
  past_only?: boolean;
}

export interface CaptureField {
  name: string;
  label: string;
  type: FieldType;
  required: boolean;
  description: string;
  validation: FieldValidation;
  confirm: boolean;
  sensitive: boolean;
  max_retries: number;
  example?: string | null;
}

export interface Transition {
  id: string;
  target: string;
  kind: TransitionKind;
  condition: string;
  label: string;
  priority: number;
}

export interface NodeSettings {
  respond_immediately: boolean;
  context_strategy: ContextStrategyName;
  allow_interruptions: boolean;
  silence_timeout_secs?: number | null;
  max_attempts: number;
  on_failure: FailureAction;
  on_failure_target?: string | null;
}

export interface LookupConfig {
  dataset: string;
  match: Record<string, string>;
  match_mode: "exact" | "icontains";
  save_as: string;
  single: boolean;
  limit: number;
}

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH";

export interface ApiCallConfig {
  method: HttpMethod;
  url: string;
  headers: Record<string, string>;
  body_template?: Record<string, unknown> | null;
  timeout_secs: number;
  save_as: string;
  mock_response?: Record<string, unknown> | null;
}

export interface FlowNode {
  id: string;
  type: NodeType;
  label: string;
  position: Position;
  task_prompt: string;
  say_on_enter?: string | null;
  fields: CaptureField[];
  transitions: Transition[];
  settings: NodeSettings;
  lookup?: LookupConfig | null;
  api_call?: ApiCallConfig | null;
  transfer_number?: string | null;
  end_reason?: string | null;
}

export interface FlowGraph {
  initial_node: string;
  global_prompt: string;
  variables: Record<string, unknown>;
  nodes: FlowNode[];
}

export interface FlowIssue {
  severity: "error" | "warning";
  code: string;
  message: string;
  node_id?: string | null;
  field?: string | null;
  transition_id?: string | null;
}

export interface FlowValidationResult {
  valid: boolean;
  issues: FlowIssue[];
}

/* ------------------------------------------------------------- providers.py */

export type STTProvider = "deepgram" | "soniox" | "openai";
export type LLMProvider = "openai";
export type TTSProvider = "soniox" | "deepgram" | "openai";

export interface STTConfig {
  provider: STTProvider;
  model: string;
  language: string;
  keywords: string[];
  options: Record<string, unknown>;
}

export interface LLMConfig {
  provider: LLMProvider;
  model: string;
  temperature: number;
  max_tokens?: number | null;
  base_url?: string | null;
  options: Record<string, unknown>;
}

export interface TTSConfig {
  provider: TTSProvider;
  model: string;
  voice: string;
  language: string;
  speed: number;
  sample_rate?: number | null;
  options: Record<string, unknown>;
}

export interface VADConfig {
  stop_secs: number;
  start_secs: number;
  confidence: number;
}

export interface BehaviorConfig {
  allow_interruptions: boolean;
  idle_timeout_secs: number;
  idle_max_nudges: number;
  max_call_duration_secs: number;
  greeting?: string | null;
  fallback_message: string;
  record_transcript: boolean;
  mask_sensitive_in_logs: boolean;
}

export interface BotConfig {
  stt: STTConfig;
  llm: LLMConfig;
  tts: TTSConfig;
  vad: VADConfig;
  behavior: BehaviorConfig;
}

export interface ProviderEntry {
  id: string;
  name: string;
  models: string[];
  languages?: string[];
  voices?: string[];
  configured: boolean;
}

export interface ProviderCatalog {
  stt: ProviderEntry[];
  llm: ProviderEntry[];
  tts: ProviderEntry[];
}

/* -------------------------------------------------------------- REST models */

export type BotStatus = "draft" | "published" | "archived";

export interface Health {
  ok: boolean;
  db: boolean;
  providers: Record<string, boolean>;
}

export interface BotSummary {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  industry: string | null;
  avatar_color: string | null;
  language: string | null;
  status: BotStatus;
  config: BotConfig;
  draft_version: number | null;
  published_version: number | null;
  node_count: number;
  session_count: number;
  last_session_at: string | null;
  updated_at: string;
  created_at?: string;
  published_version_id?: string | null;
  /** Not in the contract's BotSummary; read from /stats when present. */
  completion_rate?: number | null;
}

export interface BotDetail extends BotSummary {
  graph: FlowGraph;
  draft_version_id: string | null;
}

export interface CreateBotBody {
  name: string;
  slug?: string;
  description?: string;
  industry?: string;
  avatar_color?: string;
  template?: string; // 'blank' | <existing bot slug>
}

export interface PatchBotBody {
  name?: string;
  description?: string;
  industry?: string;
  avatar_color?: string;
  language?: string;
  status?: BotStatus;
  config?: BotConfig;
}

export interface FlowResponse {
  version: number;
  version_id: string;
  graph: FlowGraph;
}

export interface SaveFlowResponse {
  version: number;
  version_id: string;
  validation: FlowValidationResult;
}

export interface VersionInfo {
  id: string;
  version: number;
  note: string | null;
  created_at: string;
  is_published: boolean;
  node_count: number;
}

export interface VersionDetail extends VersionInfo {
  graph: FlowGraph;
}

/** Template vars the runtime injects without capture. */
export const BUILTIN_VARS = ["bot_name", "session_id", "today", "now", "current_date", "current_time", "caller_number"];

export interface Dataset {
  id: string;
  name: string;
  description: string | null;
  columns: string[];
  row_count: number;
}

export interface DatasetRow {
  id: string;
  data: Record<string, unknown>;
}

/** GET /datasets/{name}/rows — contract doesn't pin the envelope; we accept array or {items,total}. */
export interface DatasetRowsResponse {
  items: DatasetRow[];
  total: number;
  columns?: string[];
}

export type SessionStatus = "active" | "completed" | "transferred" | "abandoned" | "error";

export type SessionEventType =
  | "user_transcript"
  | "bot_transcript"
  | "node_enter"
  | "function_call"
  | "validation_error"
  | "field_captured"
  | "lookup"
  | "api_call"
  | "error"
  | "idle"
  | "end";

/** `replay` sessions are produced by the Studio replay sandbox (hidden from lists by default). */
export type SessionMode = "draft" | "published" | "simulation" | "replay";

export interface SessionListItem {
  id: string;
  bot_id: string;
  bot_name: string;
  status: SessionStatus;
  end_reason: string | null;
  mode: SessionMode;
  started_at: string;
  ended_at: string | null;
  duration_secs: number | null;
  captured_count: number;
  required_missing: string[] | null;
}

export interface SessionList {
  items: SessionListItem[];
  total: number;
}

export interface SessionEvent {
  id: string;
  ts: string;
  type: SessionEventType;
  node_id: string | null;
  payload: Record<string, unknown>;
}

export interface CapturedField {
  id: string;
  node_id: string | null;
  field_name: string;
  value: unknown;
  valid: boolean;
  attempts: number;
  captured_at: string;
  sensitive?: boolean;
}

export interface SessionDetail {
  id: string;
  bot_id: string;
  bot_name?: string;
  bot_slug?: string;
  flow_version?: number | null;
  masked?: boolean;
  flow_version_id: string | null;
  mode: SessionMode;
  status: SessionStatus;
  end_reason: string | null;
  current_node: string | null;
  config_snapshot: Partial<BotConfig> | null;
  variables: Record<string, unknown> | null;
  started_at: string;
  ended_at: string | null;
  duration_secs: number | null;
  events: SessionEvent[];
  captured: CapturedField[];
}

export interface BotStats {
  total_sessions: number;
  completed: number;
  transferred: number;
  abandoned: number;
  error?: number;
  active?: number;
  avg_duration_secs: number | null;
  completion_rate: number | null;
  field_capture_rates: Record<string, number>;
}

/* ------------------------------------------- RTVI server messages (voice) */

export type ServerMessage =
  | { type: "session"; session_id: string; bot_id: string; mode: string; flow_version: number }
  | { type: "node"; node_id: string; label: string; node_type: NodeType }
  | { type: "vars"; vars: Record<string, unknown> }
  | { type: "validation"; node_id: string; errors: Record<string, string> }
  | { type: "event"; event: SessionEventType | "transfer" | string; payload: Record<string, unknown> }
  | { type: "ended"; status: SessionStatus; end_reason: string | null };

/* ------------------------------------- Replay-grade trace (INTEGRATION_CONTRACT §1) */

export interface TraceArtifact {
  uri: string; // artifact://<session_id>/<file>
  sha256: string;
  media_type: string; // audio/wav | text/plain | application/json
  bytes: number;
}

export type TraceLayerName = "caller" | "endpointing" | "speech_to_text" | "llm" | "tools" | "text_to_speech";

export interface TraceLayer<I = Record<string, unknown>, O = Record<string, unknown>> {
  started_at?: string | null;
  finished_at?: string | null;
  input?: I;
  output?: O;
  settings?: Record<string, unknown>;
}

export interface TraceToolCall {
  name: string;
  arguments?: Record<string, unknown>;
  result?: unknown;
}

export interface TraceTurnLayers {
  caller?: TraceLayer<Record<string, unknown>, { intended_text?: string | null; audio?: TraceArtifact | null }>;
  endpointing?: TraceLayer<
    Record<string, unknown>,
    { speech_start_ms?: number | null; speech_end_ms?: number | null; interrupted_bot?: boolean; segment_text?: string | null }
  >;
  speech_to_text?: TraceLayer<
    { audio?: TraceArtifact | null },
    { interim_hypotheses?: string[]; final_hypothesis?: string | null; confidence?: number | null }
  >;
  llm?: TraceLayer<
    { context_hash?: string; message_count?: number; node_id?: string | null },
    { response_text?: string | null; tool_calls?: TraceToolCall[] }
  >;
  tools?: TraceLayer<
    { calls?: Record<string, unknown>[] },
    { results?: Record<string, unknown>[]; state_before?: Record<string, unknown>; state_after?: Record<string, unknown> }
  >;
  text_to_speech?: TraceLayer<{ text?: string | null }, { spoken_text?: string | null; audio?: TraceArtifact | null }>;
}

export interface TraceTurn {
  index: number;
  node_id: string | null;
  state_before?: Record<string, unknown>;
  state_after?: Record<string, unknown>;
  layers: TraceTurnLayers;
  missing?: string[];
}

export interface SessionTrace {
  schema_version: string;
  source: "live" | "simulation" | "replay";
  session: {
    id: string;
    bot_id: string;
    bot_slug?: string;
    bot_name?: string;
    flow_version_id?: string | null;
    flow_version?: number | null;
    mode: SessionMode;
    status: SessionStatus;
    end_reason?: string | null;
    started_at?: string;
    ended_at?: string | null;
    duration_secs?: number | null;
    config_snapshot?: Partial<BotConfig> | null;
    config_hash?: string | null;
  };
  seed?: number | null;
  fault_profile?: FaultProfile | null;
  oracle_id?: string | null;
  replay_ready: boolean;
  missing: string[];
  masked?: boolean;
  captured?: { node_id: string | null; field_name: string; value: unknown; valid: boolean; attempts: number; captured_at: string | null }[];
  dataset_snapshot_id?: string | null;
  initial_state?: Record<string, unknown>;
  turns: TraceTurn[];
  final_state?: Record<string, unknown>;
  end_node?: string | null;
  events?: unknown[];
}

/* ----------------------------------------------- Test oracles (§2) */

export interface ExpectedLookup {
  dataset: string;
  match: Record<string, string>;
}

export interface TestOracle {
  id: string;
  bot_id: string;
  slug: string;
  name: string;
  caller_turns: string[];
  expected_fields: Record<string, string>;
  expected_lookup: ExpectedLookup | null;
  expected_end_node: string | null;
  expected_status: SessionStatus | null;
  expected_bot_response: { contains: string[] } | null;
  tags: string[];
  /** Optional demo-variant fault profile (Tata demo variants carry one). */
  fault_profile?: FaultProfile | null;
  description?: string | null;
  created_at?: string;
  updated_at?: string;
}

export type OracleBody = Partial<Omit<TestOracle, "id" | "bot_id" | "created_at">>;

/* ------------------------------------- Fault profiles, replay, simulate (§3) */

export interface TextReplacement {
  from: string;
  to: string;
}

export interface FaultProfile {
  stt?: { replacements?: TextReplacement[]; keyword_protected?: boolean } | null;
  endpointing?: { truncate_field: string; keep_chars: number; cutoff_below_stop_secs?: number } | null;
  llm?: { swap_field?: { field: string; with_value_of: string } | null } | null;
  tools?: { lookup_override?: { dataset: string | null; rows: Record<string, unknown>[] } | null; api_error?: boolean } | null;
  tts?: { replacements?: TextReplacement[] } | null;
}

export type Intervention = "control" | "stt_oracle" | "endpointing_oracle" | "llm_oracle" | "tools_oracle" | "tts_oracle";

export interface ReplayOutcome {
  captured: Record<string, unknown>;
  end_node: string | null;
  status: SessionStatus | string | null;
  spoken_texts?: string[];
}

export interface ReplayTrial {
  trial: number;
  passed: boolean;
  outcome: ReplayOutcome;
  run_trace?: unknown;
  replay_session_id?: string | null;
}

export interface ReplayResponse {
  session_id: string;
  intervention: Intervention;
  seed: number | null;
  trials: ReplayTrial[];
}

export interface SimulateBody {
  oracle_id: string;
  fault_profile?: FaultProfile | null;
  mode: "draft" | "published";
  seed?: number;
}

export interface SimulateResponse {
  session_id: string;
  passed: boolean;
  outcome: ReplayOutcome;
  trace_url?: string;
}

export type PatchType = "stt_keyterm_boost" | "confirmation_readback" | "stricter_validation" | "fallback_route" | "vad_stop_secs";

export interface PatchSpec {
  type: PatchType;
  params: Record<string, unknown>;
}

export interface PatchCandidate {
  candidate_version_id: string;
  config?: Partial<BotConfig>;
  graph_diff?: unknown;
}
