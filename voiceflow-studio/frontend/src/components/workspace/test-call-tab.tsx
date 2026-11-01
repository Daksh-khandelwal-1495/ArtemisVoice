"use client";

import { PipecatClient, type TransportState } from "@pipecat-ai/client-js";
import { SmallWebRTCTransport } from "@pipecat-ai/small-webrtc-transport";
import { AlertTriangle, Bot as BotIcon, CheckCircle2, Circle, KeyRound, Lock, Mic, MicOff, Phone, PhoneOff, Radio, Save, User, WifiOff, XCircle } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { ENV_KEYS } from "@/components/app-shell";
import { ReadOnlyFlow } from "@/components/flow/flow-editor";
import { Badge, Button, Card, Segmented, cn, statusTone } from "@/components/ui";
import { MOCK, api, formatDetail, offerUrl } from "@/lib/api";
import { ensureLocalDailyCallObject } from "@/lib/daily-local";
import { NODE_META } from "@/lib/flow";
import type { BotDetail, FlowGraph, ProviderCatalog, ServerMessage, VersionDetail } from "@/lib/types";

type Status = "idle" | "preflight" | "connecting" | "connected" | "ended" | "error";
type Msg = { id: number; role: "user" | "bot" | "system"; text: string; final: boolean; ts: number; closed?: boolean; segment?: number; interrupted?: boolean };
type LogItem = { id: number; ts: number; type: string; text: string; tone?: "red" | "amber" | "green" | "blue" };
type CallError = { kind: "mic" | "missing_keys" | "not_published" | "network" | "other"; message: string; missing?: string[] };

let seq = 0;
const nextId = () => ++seq;

const fmtTime = (s: number) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

function display(v: unknown) {
  if (v == null || v === "") return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

async function readErrorBody(e: unknown): Promise<{ status?: number; detail?: unknown }> {
  const err = e as { status?: number; cause?: unknown };
  const resp = err?.cause instanceof Response ? err.cause : e instanceof Response ? e : null;
  if (resp) {
    try {
      const body = await resp.clone().json();
      return { status: resp.status, detail: body?.detail ?? body };
    } catch {
      return { status: resp.status };
    }
  }
  return { status: err?.status };
}

export function TestCallTab({ bot, draftGraph, dirty, onSave }: { bot: BotDetail; draftGraph: FlowGraph; dirty: boolean; onSave: () => Promise<boolean> }) {
  const [mode, setMode] = useState<"draft" | "published">("draft");
  const [status, setStatus] = useState<Status>("idle");
  const [transport, setTransport] = useState<TransportState | null>(null);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [log, setLog] = useState<LogItem[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [visited, setVisited] = useState<string[]>([]);
  const [vars, setVars] = useState<Record<string, unknown>>({});
  const [invalid, setInvalid] = useState<Record<string, { msg: string; ts: number }>>({});
  const [session, setSession] = useState<{ id: string; flow_version?: number } | null>(null);
  const [ended, setEnded] = useState<{ status: string; end_reason: string | null } | null>(null);
  const [error, setError] = useState<CallError | null>(null);
  const [level, setLevel] = useState(0);
  const [micOn, setMicOn] = useState(true);
  const [botSpeaking, setBotSpeaking] = useState(false);
  const [userSpeaking, setUserSpeaking] = useState(false);
  const botSpeakingRef = useRef(false);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [endedAt, setEndedAt] = useState<number | null>(null);

  const clientRef = useRef<PipecatClient | null>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const userHangup = useRef(false);
  const endedRef = useRef(false);
  const gotBotOutput = useRef(false);
  const chatRef = useRef<HTMLDivElement>(null);

  const { data: catalog } = useSWR<ProviderCatalog>("providers", api.providers);
  const { data: pubVersion } = useSWR<VersionDetail>(
    mode === "published" && bot.published_version_id ? `version:${bot.id}:${bot.published_version_id}` : null,
    () => api.version(bot.id, bot.published_version_id!),
  );
  const graph = mode === "published" && pubVersion?.graph ? pubVersion.graph : draftGraph;

  const addLog = useCallback((type: string, text: string, tone?: LogItem["tone"]) => {
    setLog((l) => [...l.slice(-300), { id: nextId(), ts: Date.now(), type, text, tone }]);
  }, []);

  // timer
  useEffect(() => {
    if (status !== "connected") return;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [status]);

  useEffect(() => {
    chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  // disconnect on unmount
  useEffect(
    () => () => {
      clientRef.current?.disconnect().catch(() => {});
    },
    [],
  );

  const missingKeys = useMemo(() => {
    if (!catalog) return [];
    const c = bot.config;
    const out = new Set<string>();
    const check = (list: ProviderCatalog["stt"], id: string) => {
      const p = list.find((x) => x.id === id);
      if (p && !p.configured) out.add(ENV_KEYS[id] ?? `${id} key`);
    };
    check(catalog.stt, c.stt.provider);
    check(catalog.llm, c.llm.provider);
    check(catalog.tts, c.tts.provider);
    return [...out];
  }, [catalog, bot.config]);

  const pushUser = (text: string, final: boolean) =>
    setMessages((m) => {
      const last = m[m.length - 1];
      const next = last?.role === "user" && !last.final ? [...m.slice(0, -1), { ...last, text, final }] : [...m, { id: nextId(), role: "user" as const, text, final, ts: Date.now() }];
      // a final user turn closes the current bot bubble
      return final ? next.map((x) => (x.role === "bot" ? { ...x, closed: true } : x)) : next;
    });

  // Bot output arrives once per spoken segment (sentence) and may be re-sent as the segment
  // progresses; key it by segment so each sentence shows up exactly once.
  const pushBot = (text: string, segment?: number) =>
    setMessages((m) => {
      const t = text.trim();
      if (!t) return m;
      if (segment != null) {
        const i = m.findIndex((x) => x.role === "bot" && x.segment === segment);
        if (i >= 0) return m.map((x, j) => (j === i ? { ...x, text: t } : x));
      }
      const last = m[m.length - 1];
      if (last?.role === "bot" && !last.closed) {
        if (last.text.endsWith(t)) return m;
        return [...m.slice(0, -1), { ...last, text: `${last.text}${last.text && !last.text.endsWith(" ") ? " " : ""}${t}`, segment: segment ?? last.segment }];
      }
      return [...m, { id: nextId(), role: "bot", text: t, final: true, ts: Date.now(), segment }];
    });

  const onServerMessage = (raw: unknown) => {
    const msg = raw as ServerMessage;
    if (!msg || typeof msg !== "object" || !("type" in msg)) return;
    switch (msg.type) {
      case "session":
        setSession({ id: msg.session_id, flow_version: msg.flow_version });
        addLog("session", `Session ${msg.session_id.slice(0, 8)} · ${msg.mode} · flow v${msg.flow_version}`, "blue");
        break;
      case "node":
        setCurrent(msg.node_id);
        setVisited((v) => (v[v.length - 1] === msg.node_id ? v : [...v, msg.node_id]));
        addLog("node", `→ ${msg.label} (${msg.node_id})`, "blue");
        setMessages((m) => [...m.map((x) => (x.role === "bot" ? { ...x, closed: true } : x)), { id: nextId(), role: "system", text: `${NODE_META[msg.node_type]?.label ?? msg.node_type}: ${msg.label}`, final: true, ts: Date.now() }]);
        break;
      case "vars":
        setVars(msg.vars ?? {});
        setInvalid((inv) => Object.fromEntries(Object.entries(inv).filter(([k]) => msg.vars?.[k] == null)));
        addLog("vars", `${Object.keys(msg.vars ?? {}).length} vars`, "green");
        break;
      case "validation":
        setInvalid((inv) => ({ ...inv, ...Object.fromEntries(Object.entries(msg.errors ?? {}).map(([k, v]) => [k, { msg: v, ts: Date.now() }])) }));
        addLog("validation", Object.entries(msg.errors ?? {}).map(([k, v]) => `${k}: ${v}`).join("; "), "red");
        break;
      case "event": {
        const p = msg.payload ?? {};
        const tone = msg.event === "error" ? "red" : msg.event === "idle" ? "amber" : undefined;
        if (msg.event === "user_transcript" || msg.event === "bot_transcript") break; // already shown via RTVI
        addLog(msg.event, Object.keys(p).length ? JSON.stringify(p).slice(0, 220) : "", tone);
        break;
      }
      case "ended":
        endedRef.current = true;
        setEnded({ status: msg.status, end_reason: msg.end_reason });
        addLog("ended", `${msg.status}${msg.end_reason ? ` · ${msg.end_reason}` : ""}`, "green");
        break;
    }
  };

  const reset = () => {
    setMessages([]);
    setLog([]);
    setCurrent(null);
    setVisited([]);
    setVars({});
    setInvalid({});
    setSession(null);
    setEnded(null);
    setError(null);
    setEndedAt(null);
    setLevel(0);
    endedRef.current = false;
    userHangup.current = false;
    gotBotOutput.current = false;
  };

  const connect = async () => {
    reset();
    if (MOCK) {
      setError({ kind: "other", message: "Mock mode: test calls need the real backend (unset NEXT_PUBLIC_MOCK)." });
      setStatus("error");
      return;
    }
    if (mode === "published" && bot.published_version == null) {
      setError({ kind: "not_published", message: "This bot has no published version yet. Publish it or test in draft mode." });
      setStatus("error");
      return;
    }
    if (missingKeys.length) {
      setError({ kind: "missing_keys", message: "The backend is missing API keys for this bot's providers.", missing: missingKeys });
      setStatus("error");
      return;
    }
    setStatus("preflight");
    // Mic permission check up front so we can show a clear message.
    try {
      const s = await navigator.mediaDevices.getUserMedia({ audio: true });
      s.getTracks().forEach((t) => t.stop());
    } catch (e) {
      const name = (e as DOMException)?.name;
      setError({
        kind: "mic",
        message:
          name === "NotAllowedError" || name === "SecurityError"
            ? "Microphone access was blocked. Allow mic permission for this site in your browser and try again."
            : name === "NotFoundError"
              ? "No microphone found. Connect a microphone and try again."
              : `Could not access the microphone (${name || "unknown error"}).`,
      });
      setStatus("error");
      return;
    }

    setStatus("connecting");
    addLog("transport", `Connecting (${mode})…`);
    ensureLocalDailyCallObject();
    const client = new PipecatClient({
      transport: new SmallWebRTCTransport(),
      enableMic: true,
      enableCam: false,
      callbacks: {
        onTransportStateChanged: (s) => {
          setTransport(s);
          addLog("transport", s);
        },
        onConnected: () => {
          setStatus("connected");
          setStartedAt(Date.now());
          setNow(Date.now());
        },
        onBotReady: () => addLog("bot", "ready", "green"),
        onDisconnected: () => {
          setEndedAt(Date.now());
          setLevel(0);
          if (!endedRef.current && !userHangup.current) {
            setError({ kind: "network", message: "The call dropped unexpectedly (network or server disconnect)." });
            setStatus("error");
          } else setStatus("ended");
          addLog("transport", "disconnected");
        },
        onServerMessage,
        onUserTranscript: (d) => d.text && pushUser(d.text, d.final),
        onBotOutput: (d) => {
          const kind = d.output_type ?? d.aggregated_by ?? d.text_type;
          if (kind === "word" || d.spoken_status === "in-progress") return;
          gotBotOutput.current = true;
          pushBot(d.text, d.segment_id);
        },
        onBotTranscript: (d) => {
          if (!gotBotOutput.current) pushBot(d.text);
        },
        onBotStartedSpeaking: () => {
          botSpeakingRef.current = true;
          setBotSpeaking(true);
        },
        onBotStoppedSpeaking: () => {
          botSpeakingRef.current = false;
          setBotSpeaking(false);
          setMessages((m) => m.map((x) => (x.role === "bot" ? { ...x, closed: true } : x)));
        },
        onUserStartedSpeaking: () => {
          setUserSpeaking(true);
          if (botSpeakingRef.current) {
            // barge-in: the server stops TTS; mark the bot line as cut off
            addLog("interrupt", "Caller interrupted the bot", "amber");
            setMessages((m) => {
              const i = m.map((x) => x.role).lastIndexOf("bot");
              return i < 0 ? m : m.map((x, j) => (j === i ? { ...x, interrupted: true, closed: true } : x));
            });
          }
        },
        onUserStoppedSpeaking: () => setUserSpeaking(false),
        onLocalAudioLevel: (l) => setLevel(l),
        onTrackStarted: (track, participant) => {
          // SmallWebRTC reports the bot's track without a participant; only skip our own mic.
          if (track.kind === "audio" && !participant?.local && audioRef.current) {
            audioRef.current.srcObject = new MediaStream([track]);
            audioRef.current.play().catch(() => {});
          }
        },
        onDeviceError: (e) => {
          setError({ kind: "mic", message: e.type === "permissions" ? "Microphone permission denied." : `Device error: ${e.message}` });
          addLog("device", e.message, "red");
        },
        onError: (m) => addLog("error", formatDetail((m as { data?: unknown }).data ?? m), "red"),
      },
    });
    clientRef.current = client;
    try {
      await client.connect({ webrtcRequestParams: { endpoint: offerUrl(bot.id, mode) } });
    } catch (e) {
      const { status: code, detail } = await readErrorBody(e);
      type Missing = string | { role?: string; provider?: string; env_key?: string };
      const d = detail as { message?: string; missing?: Missing[] } | string | undefined;
      if (code === 503) {
        const missing = typeof d === "object" ? (d?.missing ?? []).map((m) => (typeof m === "string" ? m : `${m.env_key ?? m.provider}${m.role ? ` (${m.role})` : ""}`)) : undefined;
        setError({ kind: "missing_keys", message: typeof d === "string" ? d : (d?.message ?? "Provider API key missing"), missing });
      }
      else if (code === 409) setError({ kind: "not_published", message: formatDetail(d) || "Bot is not published." });
      else if (code) setError({ kind: "other", message: `Offer failed (${code}): ${formatDetail(d)}` });
      else setError({ kind: "network", message: `Could not connect to the voice server: ${(e as Error)?.message ?? String(e)}` });
      setStatus("error");
      clientRef.current = null;
      client.disconnect().catch(() => {});
    }
  };

  const hangup = async () => {
    userHangup.current = true;
    await clientRef.current?.disconnect().catch(() => {});
    clientRef.current = null;
    setStatus("ended");
    setEndedAt((t) => t ?? Date.now());
  };

  const toggleMic = () => {
    const c = clientRef.current;
    if (!c) return;
    c.enableMic(!micOn);
    setMicOn(!micOn);
  };

  const elapsed = startedAt ? ((endedAt ?? now) - startedAt) / 1000 : 0;
  const live = status === "connected" || status === "connecting" || status === "preflight";
  const requiredFields = useMemo(() => graph.nodes.flatMap((n) => n.fields.map((f) => ({ ...f, node: n.id }))), [graph]);

  return (
    <div className="grid min-h-0 flex-1 grid-cols-[minmax(340px,1fr)_minmax(360px,1.1fr)_320px]">
      <audio ref={audioRef} autoPlay />
      {/* left: controls + transcript */}
      <div className="flex min-h-0 flex-col border-r border-border">
        <div className="space-y-3 border-b border-border bg-panel p-3">
          <div className="flex items-center justify-between">
            <Segmented
              value={mode}
              onChange={(m) => !live && setMode(m)}
              items={[
                { value: "draft", label: `Draft v${bot.draft_version}`, disabled: live },
                { value: "published", label: bot.published_version ? `Published v${bot.published_version}` : "Published", disabled: live },
              ]}
            />
            <div className="flex items-center gap-2 font-mono text-[13px] tabular-nums">
              {status === "connected" && <span className="size-2 animate-pulse rounded-full bg-red-500" />}
              {fmtTime(elapsed)}
            </div>
          </div>
          {mode === "draft" && dirty && (
            <div className="flex items-center justify-between gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-[12px] text-amber-700 dark:text-amber-300">
              Unsaved flow edits are not included in the draft call.
              <Button size="xs" variant="outline" onClick={onSave}>
                <Save className="size-3" /> Save now
              </Button>
            </div>
          )}
          <div className="flex items-center gap-2">
            {!live ? (
              <Button variant="primary" onClick={connect} className="flex-1 bg-emerald-600">
                <Phone className="size-4" /> {status === "ended" || status === "error" ? "Call again" : "Start test call"}
              </Button>
            ) : (
              <Button variant="danger" onClick={hangup} className="flex-1" loading={status !== "connected"}>
                <PhoneOff className="size-4" /> {status === "connected" ? "End call" : "Connecting…"}
              </Button>
            )}
            <Button variant="outline" onClick={toggleMic} disabled={status !== "connected"} title={micOn ? "Mute" : "Unmute"}>
              {micOn ? <Mic className="size-4" /> : <MicOff className="size-4 text-red-500" />}
            </Button>
            <div className="flex h-8 w-24 items-end gap-0.5 rounded-md border border-border px-1.5 py-1" title="Mic level">
              {Array.from({ length: 12 }).map((_, i) => (
                <span
                  key={i}
                  className={cn("flex-1 rounded-sm transition-all", level * 12 > i ? (i > 9 ? "bg-red-500" : i > 6 ? "bg-amber-500" : "bg-emerald-500") : "bg-panel-2")}
                  style={{ height: `${25 + (i / 12) * 75}%` }}
                />
              ))}
            </div>
          </div>
          <div className="flex items-center gap-2 text-[11px] text-muted">
            <Radio className="size-3" /> {transport ?? "idle"}
            {session && (
              <>
                · session <span className="font-mono">{session.id.slice(0, 8)}</span>
              </>
            )}
            · {bot.config.stt.provider}/{bot.config.llm.model}/{bot.config.tts.provider}
          </div>
          {status === "connected" && (
            <div className="flex items-center gap-2 text-[11.5px]">
              <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5", botSpeaking ? "bg-brand/15 text-brand" : "bg-panel-2 text-muted")}>
                <BotIcon className="size-3" /> {botSpeaking ? "Bot speaking" : "Bot listening"}
              </span>
              <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5", userSpeaking ? "bg-emerald-500/15 text-emerald-600" : "bg-panel-2 text-muted")}>
                <User className="size-3" /> {userSpeaking ? "You&apos;re speaking" : "You: silent"}
              </span>
              {!micOn && (
                <span className="inline-flex items-center gap-1 rounded-full bg-red-500/15 px-2 py-0.5 text-red-600">
                  <MicOff className="size-3" /> Mic muted — the bot can&apos;t hear you
                </span>
              )}
            </div>
          )}
        </div>

        {error && (
          <div className="m-3 rounded-lg border border-red-500/30 bg-red-500/5 p-3 text-[12.5px]">
            <div className="flex items-center gap-2 font-semibold text-red-600 dark:text-red-400">
              {error.kind === "mic" ? <MicOff className="size-4" /> : error.kind === "missing_keys" ? <KeyRound className="size-4" /> : error.kind === "network" ? <WifiOff className="size-4" /> : <AlertTriangle className="size-4" />}
              {error.kind === "mic" ? "Microphone unavailable" : error.kind === "missing_keys" ? "Provider keys missing" : error.kind === "not_published" ? "Not published" : error.kind === "network" ? "Connection problem" : "Call failed"}
            </div>
            <div className="mt-1 text-muted">{error.message}</div>
            {error.missing && error.missing.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {error.missing.map((m) => (
                  <code key={m} className="rounded bg-red-500/10 px-1.5 py-0.5 font-mono text-[11px] text-red-700 dark:text-red-300">
                    {m}
                  </code>
                ))}
              </div>
            )}
            {error.kind === "missing_keys" && <div className="mt-2 text-[11.5px] text-muted">Add them to <code>backend/.env</code> and restart the backend, or switch providers in Settings.</div>}
            {error.kind === "not_published" && <div className="mt-2 text-[11.5px] text-muted">Switch to Draft mode, or publish the bot from the header.</div>}
          </div>
        )}

        {ended && (
          <Card className="m-3 p-3">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="size-4 text-emerald-500" />
              <span className="text-[13px] font-semibold">Call ended</span>
              <Badge tone={statusTone(ended.status)}>{ended.status}</Badge>
              {ended.end_reason && <span className="font-mono text-[11.5px] text-muted">{ended.end_reason}</span>}
            </div>
            <div className="mt-2 grid grid-cols-3 gap-2 text-[11.5px] text-muted">
              <div>
                Duration <div className="font-mono text-[13px] text-fg">{fmtTime(elapsed)}</div>
              </div>
              <div>
                Nodes visited <div className="text-[13px] text-fg">{visited.length}</div>
              </div>
              <div>
                Captured <div className="text-[13px] text-fg">{requiredFields.filter((f) => vars[f.name] != null && vars[f.name] !== "").length}</div>
              </div>
            </div>
            {session && (
              <a href={`/sessions/${session.id}`} className="mt-2 inline-block text-[12px] text-brand hover:underline">
                Open session details →
              </a>
            )}
          </Card>
        )}

        <div ref={chatRef} className="flex-1 space-y-2 overflow-y-auto p-3 thin-scroll">
          {messages.length === 0 && !error && (
            <div className="flex h-full flex-col items-center justify-center gap-2 text-center text-muted">
              <Phone className="size-8 text-subtle" />
              <div className="text-[13px] font-medium text-fg">Talk to your bot</div>
              <div className="max-w-xs text-[12px]">Start a call to test the {mode} flow over WebRTC. The live transcript, current node and captured fields appear here.</div>
            </div>
          )}
          {messages.map((m) =>
            m.role === "system" ? (
              <div key={m.id} className="flex items-center gap-2 py-0.5 text-[10.5px] uppercase tracking-wider text-subtle">
                <span className="h-px flex-1 bg-border" />
                {m.text}
                <span className="h-px flex-1 bg-border" />
              </div>
            ) : (
              <div key={m.id} className={cn("flex gap-2", m.role === "user" && "flex-row-reverse")}>
                <span className={cn("mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full", m.role === "bot" ? "bg-brand/10 text-brand" : "bg-panel-2 text-muted")}>
                  {m.role === "bot" ? <BotIcon className="size-3.5" /> : <User className="size-3.5" />}
                </span>
                <div
                  className={cn(
                    "max-w-[80%] rounded-2xl px-3 py-1.5 text-[13px] leading-relaxed",
                    m.role === "bot" ? "rounded-tl-sm bg-panel-2" : "rounded-tr-sm bg-brand text-brand-fg",
                    !m.final && "opacity-60",
                  )}
                >
                  {m.text}
                  {m.interrupted && <span className="ml-1.5 text-[10.5px] font-medium text-amber-600">— interrupted</span>}
                </div>
              </div>
            ),
          )}
        </div>
      </div>

      {/* middle: live flow */}
      <div className="relative flex min-h-0 flex-col border-r border-border">
        <div className="absolute left-3 top-3 z-10 flex items-center gap-2 rounded-md border border-border bg-panel px-2 py-1 text-[12px] shadow-sm">
          {current ? (
            <>
              <span className="size-2 rounded-full" style={{ background: NODE_META[graph.nodes.find((n) => n.id === current)?.type ?? "start"].color }} />
              Current: <b>{graph.nodes.find((n) => n.id === current)?.label ?? current}</b>
            </>
          ) : (
            <span className="text-muted">Flow preview ({mode})</span>
          )}
        </div>
        <ReadOnlyFlow graph={graph} current={current} visited={visited} fitKey={mode} />
      </div>

      {/* right: vars + events */}
      <div className="flex min-h-0 flex-col">
        <div className="max-h-[55%] overflow-y-auto border-b border-border p-3 thin-scroll">
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted">Captured fields</h3>
            <span className="text-[11px] text-muted">
              {requiredFields.filter((f) => f.required && vars[f.name] != null && vars[f.name] !== "").length}/{requiredFields.filter((f) => f.required).length} required
            </span>
          </div>
          <div className="space-y-0.5">
            {requiredFields.map((f) => {
              const val = vars[f.name];
              const ok = val != null && val !== "";
              const bad = invalid[f.name];
              return (
                <div key={`${f.node}.${f.name}.${bad?.ts ?? 0}`} className={cn("flex items-start gap-2 rounded px-1.5 py-1", bad && !ok && "animate-flash")}>
                  {ok ? <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-emerald-500" /> : bad ? <XCircle className="mt-0.5 size-3.5 shrink-0 text-red-500" /> : <Circle className="mt-0.5 size-3.5 shrink-0 text-subtle" />}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1 text-[12px]">
                      <span className="font-mono">{f.name}</span>
                      {f.required && <span className="text-red-500">*</span>}
                      {f.sensitive && <Lock className="size-3 text-muted" />}
                    </div>
                    {bad && !ok && <div className="text-[11px] text-red-500">{bad.msg}</div>}
                  </div>
                  <span className={cn("max-w-[45%] truncate text-right text-[12px]", ok ? "text-fg" : "text-subtle")} title={display(val)}>
                    {display(val)}
                  </span>
                </div>
              );
            })}
            {Object.entries(vars)
              .filter(([k]) => !requiredFields.some((f) => f.name === k))
              .map(([k, v]) => (
                <div key={k} className="flex items-start gap-2 rounded px-1.5 py-1">
                  <span className="mt-0.5 size-3.5 shrink-0 text-center text-[10px] text-subtle">≡</span>
                  <span className="flex-1 font-mono text-[12px] text-muted">{k}</span>
                  <span className="max-w-[55%] truncate text-right text-[12px]" title={display(v)}>
                    {display(v)}
                  </span>
                </div>
              ))}
          </div>
        </div>
        <div className="flex min-h-0 flex-1 flex-col">
          <h3 className="px-3 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wider text-muted">Event log</h3>
          <div className="flex-1 overflow-y-auto px-3 pb-3 font-mono text-[11px] thin-scroll">
            {log.length === 0 && <div className="text-subtle">No events yet.</div>}
            {log.map((l) => (
              <div key={l.id} className="flex gap-2 border-b border-border/60 py-0.5">
                <span className="shrink-0 text-subtle">{new Date(l.ts).toLocaleTimeString([], { hour12: false })}</span>
                <span
                  className={cn(
                    "shrink-0 font-semibold",
                    l.tone === "red" && "text-red-500",
                    l.tone === "amber" && "text-amber-500",
                    l.tone === "green" && "text-emerald-500",
                    l.tone === "blue" && "text-blue-500",
                    !l.tone && "text-muted",
                  )}
                >
                  {l.type}
                </span>
                <span className="break-all text-fg/80">{l.text}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
