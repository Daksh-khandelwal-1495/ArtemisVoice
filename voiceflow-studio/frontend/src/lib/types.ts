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

export interface SessionListItem {
  id: string;
  bot_id: string;
  bot_name: string;
  status: SessionStatus;
  end_reason: string | null;
  mode: "draft" | "published";
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
  mode: "draft" | "published";
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
