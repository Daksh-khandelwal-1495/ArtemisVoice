import type {
  BotConfig,
  CaptureField,
  FlowGraph,
  FlowNode,
  NodeSettings,
  NodeType,
  Transition,
} from "./types";

export const defaultNodeSettings = (): NodeSettings => ({
  respond_immediately: true,
  context_strategy: "append",
  allow_interruptions: true,
  silence_timeout_secs: null,
  max_attempts: 3,
  on_failure: "transfer",
  on_failure_target: null,
});

export const defaultBotConfig = (): BotConfig => ({
  stt: { provider: "deepgram", model: "nova-3", language: "en-IN", keywords: [], options: {} },
  llm: { provider: "openai", model: "gpt-6-luna", temperature: 0.4, max_tokens: 300, base_url: null, options: {} },
  tts: {
    provider: "soniox",
    model: "tts-rt-v2",
    voice: "Bryce",
    language: "en",
    speed: 1.0,
    sample_rate: 24000,
    options: {},
  },
  vad: { stop_secs: 0.6, start_secs: 0.2, confidence: 0.7 },
  behavior: {
    allow_interruptions: true,
    idle_timeout_secs: 20,
    idle_max_nudges: 2,
    max_call_duration_secs: 900,
    greeting: null,
    fallback_message: "Sorry, I'm having trouble right now. Let me connect you to a human agent.",
    record_transcript: true,
    mask_sensitive_in_logs: true,
  },
});

export function newField(existing: string[] = []): CaptureField {
  let i = existing.length + 1;
  let name = `field_${i}`;
  while (existing.includes(name)) name = `field_${++i}`;
  return {
    name,
    label: `Field ${i}`,
    type: "string",
    required: true,
    description: "",
    validation: { regex: null, min: null, max: null, options: null, future_only: false, past_only: false },
    confirm: false,
    sensitive: false,
    max_retries: 3,
    example: null,
  };
}

export const uid = (prefix = "t") => `${prefix}_${Math.random().toString(36).slice(2, 9)}`;

export function newTransition(target: string, kind: Transition["kind"] = "on_complete"): Transition {
  return { id: uid("t"), target, kind, condition: "", label: "", priority: 0 };
}

const LABELS: Record<NodeType, string> = {
  start: "Start",
  collect: "Collect details",
  conversation: "Conversation",
  decision: "Decision",
  lookup: "Lookup",
  api_call: "API call",
  transfer: "Transfer to agent",
  end: "End call",
};

export function uniqueNodeId(type: NodeType, existing: string[]): string {
  let i = 1;
  let id = `${type}_${i}`;
  while (existing.includes(id)) id = `${type}_${++i}`;
  return id;
}

export function newNode(type: NodeType, existingIds: string[], position = { x: 0, y: 0 }): FlowNode {
  const node: FlowNode = {
    id: uniqueNodeId(type, existingIds),
    type,
    label: LABELS[type],
    position,
    task_prompt: "",
    say_on_enter: null,
    fields: [],
    transitions: [],
    settings: defaultNodeSettings(),
    lookup: null,
    api_call: null,
    transfer_number: null,
    end_reason: null,
  };
  if (type === "collect") node.fields = [newField()];
  if (type === "lookup") node.lookup = { dataset: "", match: {}, match_mode: "exact", save_as: "result", single: true, limit: 5 };
  if (type === "api_call")
    node.api_call = {
      method: "POST",
      url: "",
      headers: {},
      body_template: null,
      timeout_secs: 8,
      save_as: "api_result",
      mock_response: null,
    };
  if (type === "end") {
    node.end_reason = "completed";
    node.say_on_enter = "Thank you for calling. Goodbye!";
  }
  if (type === "transfer") node.say_on_enter = "Let me connect you to one of our agents.";
  if (type === "decision") node.settings.respond_immediately = false;
  return node;
}

/** Fill in defaults so partial server payloads are safe to edit. */
export function normalizeGraph(g: Partial<FlowGraph> | null | undefined): FlowGraph {
  const nodes = (g?.nodes ?? []).map((n) => {
    const settings = { ...defaultNodeSettings(), ...(n.settings ?? {}) };
    return {
      ...n,
      position: n.position ?? { x: 0, y: 0 },
      task_prompt: n.task_prompt ?? "",
      fields: (n.fields ?? []).map((f) => ({
        ...f,
        type: f.type ?? "string",
        required: f.required ?? true,
        description: f.description ?? "",
        validation: { ...(f.validation ?? {}) },
        confirm: f.confirm ?? false,
        sensitive: f.sensitive ?? false,
        max_retries: f.max_retries ?? 3,
      })),
      transitions: (n.transitions ?? []).map((t) => ({
        ...t,
        kind: t.kind ?? "on_complete",
        condition: t.condition ?? "",
        label: t.label ?? "",
        priority: t.priority ?? 0,
      })),
      settings,
    } as FlowNode;
  });
  return {
    initial_node: g?.initial_node ?? nodes[0]?.id ?? "",
    global_prompt: g?.global_prompt ?? "",
    variables: g?.variables ?? {},
    nodes,
  };
}

export function mergeConfig(c: Partial<BotConfig> | null | undefined): BotConfig {
  const d = defaultBotConfig();
  return {
    stt: { ...d.stt, ...(c?.stt ?? {}) },
    llm: { ...d.llm, ...(c?.llm ?? {}) },
    tts: { ...d.tts, ...(c?.tts ?? {}) },
    vad: { ...d.vad, ...(c?.vad ?? {}) },
    behavior: { ...d.behavior, ...(c?.behavior ?? {}) },
  };
}
