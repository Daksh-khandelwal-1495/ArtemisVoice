"use client";

import { ArrowLeft, CheckCircle2, FlaskConical, History, ListChecks, PhoneCall, Rocket, Save, Settings, ShieldCheck, Workflow } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { FlowEditor, type Selection } from "@/components/flow/flow-editor";
import { Badge, Button, ErrorBox, Spinner, Tabs, statusTone, useToast } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { normalizeGraph } from "@/lib/defaults";
import { lintFlow } from "@/lib/lint";
import type { BotDetail, Dataset, FlowGraph, FlowValidationResult } from "@/lib/types";
import { useGraphHistory } from "@/lib/use-graph-history";
import { OraclesTab } from "./oracles-tab";
import { SessionsTab } from "./sessions-tab";
import { SettingsTab } from "./settings-tab";
import { TestCallTab } from "./test-call-tab";
import { VersionsTab } from "./versions-tab";

type Tab = "flow" | "settings" | "test" | "sessions" | "oracles" | "versions";
const TABS: Tab[] = ["flow", "settings", "test", "sessions", "oracles", "versions"];

function NameEditor({ bot, onSaved }: { bot: BotDetail; onSaved: () => void }) {
  const [name, setName] = useState(bot.name);
  const [last, setLast] = useState(bot.name);
  const toast = useToast();
  if (bot.name !== last) {
    setLast(bot.name);
    setName(bot.name);
  }
  const commit = async () => {
    const v = name.trim();
    if (!v || v === bot.name) return setName(bot.name);
    try {
      await api.patchBot(bot.id, { name: v });
      onSaved();
    } catch (e) {
      toast((e as Error).message, "error");
      setName(bot.name);
    }
  };
  return (
    <input
      value={name}
      onChange={(e) => setName(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "Escape") setName(bot.name);
      }}
      className="min-w-0 rounded-md border border-transparent bg-transparent px-1.5 py-0.5 text-[15px] font-semibold tracking-tight hover:border-border focus:border-brand focus:outline-none"
      style={{ width: `${Math.max(8, name.length + 2)}ch` }}
      aria-label="Bot name"
    />
  );
}

export function Workspace({ botId }: { botId: string }) {
  const router = useRouter();
  const params = useSearchParams();
  const toast = useToast();
  const initialTab = (params.get("tab") as Tab) || "flow";
  const [tab, setTabState] = useState<Tab>(TABS.includes(initialTab) ? initialTab : "flow");
  const setTab = (t: Tab) => {
    setTabState(t);
    const url = new URL(window.location.href);
    url.searchParams.set("tab", t);
    window.history.replaceState(null, "", url);
  };

  const { data: bot, error, mutate } = useSWR<BotDetail>(`bot:${botId}`, () => api.getBot(botId));
  const { data: datasets } = useSWR<Dataset[]>("datasets", api.datasets);

  const history = useGraphHistory();
  const { graph, reset } = history;
  const [savedJson, setSavedJson] = useState<string | null>(null);
  const [loadedVersion, setLoadedVersion] = useState<string | null>(null);
  const [server, setServer] = useState<{ json: string; result: FlowValidationResult } | null>(null);
  const [selection, setSelection] = useState<Selection>(null);
  const [busy, setBusy] = useState<"save" | "validate" | "publish" | null>(null);

  const currentJson = useMemo(() => (graph ? JSON.stringify(graph) : null), [graph]);
  const dirty = !!graph && savedJson !== null && currentJson !== savedJson;

  // Load graph from server on first fetch, or when the draft version changes and there are no local edits.
  // (derived-state update during render; React re-renders immediately without committing)
  if (bot) {
    const vid = bot.draft_version_id ?? `v${bot.draft_version}`;
    if (vid !== loadedVersion && !dirty) {
      const g = normalizeGraph(bot.graph);
      reset(g);
      setSavedJson(JSON.stringify(g));
      setLoadedVersion(vid);
    }
  }

  const datasetNames = useMemo(() => (datasets ?? []).map((d) => d.name), [datasets]);
  const lint = useMemo(() => (graph ? lintFlow(graph, datasetNames) : []), [graph, datasetNames]);
  const serverFresh = server && server.json === currentJson;
  const issues = serverFresh ? server.result.issues : lint;
  const errorCount = issues.filter((i) => i.severity === "error").length;

  const save = useCallback(async (): Promise<boolean> => {
    if (!graph) return false;
    setBusy("save");
    try {
      const json = JSON.stringify(graph);
      const r = await api.saveFlow(botId, graph);
      setSavedJson(json);
      setLoadedVersion(r.version_id);
      setServer({ json, result: r.validation });
      const errs = r.validation.issues.filter((i) => i.severity === "error").length;
      toast(errs ? `Saved v${r.version} with ${errs} validation error${errs > 1 ? "s" : ""}` : `Saved v${r.version}`, errs ? "info" : "success");
      mutate();
      return true;
    } catch (e) {
      toast(e instanceof ApiError && e.status === 422 ? `Flow rejected: ${e.message}` : (e as Error).message, "error");
      return false;
    } finally {
      setBusy(null);
    }
  }, [graph, botId, toast, mutate]);

  const validate = async () => {
    if (!graph) return;
    setBusy("validate");
    try {
      const json = JSON.stringify(graph);
      const r = await api.validateFlow(botId, graph);
      setServer({ json, result: r });
      toast(r.valid ? `Valid · ${r.issues.length} warnings` : `${r.issues.filter((i) => i.severity === "error").length} errors found`, r.valid ? "success" : "error");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(null);
    }
  };

  const publish = async () => {
    if (!graph) return;
    if (lint.some((i) => i.severity === "error")) {
      toast("Fix validation errors before publishing", "error");
      setTab("flow");
      return;
    }
    if (dirty && !(await save())) return;
    setBusy("publish");
    try {
      const b = await api.publish(botId);
      toast(`Published v${b.published_version}`, "success");
      mutate();
    } catch (e) {
      if (e instanceof ApiError && e.status === 400 && typeof e.detail === "object" && e.detail && "validation" in e.detail) {
        const v = (e.detail as { validation: FlowValidationResult }).validation;
        setServer({ json: JSON.stringify(graph), result: v });
        setTab("flow");
      }
      toast((e as Error).message, "error");
    } finally {
      setBusy(null);
    }
  };

  // Cmd+S
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        if (dirty && !busy) save();
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [dirty, busy, save]);

  // Warn on navigate-away with unsaved changes (tab close + in-app link clicks).
  useEffect(() => {
    if (!dirty) return;
    const before = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    const click = (e: MouseEvent) => {
      const a = (e.target as HTMLElement).closest("a[href]") as HTMLAnchorElement | null;
      if (!a || a.target === "_blank" || e.metaKey || e.ctrlKey) return;
      const url = new URL(a.href, window.location.href);
      if (url.origin !== window.location.origin || url.pathname === window.location.pathname) return;
      if (!window.confirm("You have unsaved flow changes. Leave without saving?")) {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", before);
    document.addEventListener("click", click, true);
    return () => {
      window.removeEventListener("beforeunload", before);
      document.removeEventListener("click", click, true);
    };
  }, [dirty]);

  if (error)
    return (
      <div className="mx-auto w-full max-w-3xl p-8">
        <ErrorBox error={error} onRetry={() => mutate()} />
        <Link href="/" className="mt-4 inline-flex items-center gap-1 text-[13px] text-brand">
          <ArrowLeft className="size-3.5" /> Back to bots
        </Link>
      </div>
    );
  if (!bot || !graph)
    return (
      <div className="flex flex-1 items-center justify-center gap-2 text-muted">
        <Spinner /> Loading bot…
      </div>
    );

  const color = bot.avatar_color || "#4f46e5";
  const publishedIsCurrent = bot.published_version != null && bot.published_version === bot.draft_version && !dirty;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border bg-panel px-3">
        <Link href="/" className="inline-flex size-7 items-center justify-center rounded-md text-muted hover:bg-panel-2 hover:text-fg" title="All bots">
          <ArrowLeft className="size-4" />
        </Link>
        <span className="flex size-7 items-center justify-center rounded-md text-[13px] font-semibold text-white" style={{ background: color }}>
          {bot.name.charAt(0).toUpperCase()}
        </span>
        <NameEditor bot={bot} onSaved={() => mutate()} />
        <Badge tone={statusTone(bot.status)}>{bot.status}</Badge>
        <Badge tone="gray" title="Latest saved draft version">
          v{bot.draft_version}
        </Badge>
        {bot.published_version != null && (
          <Badge tone="green" title="Live version">
            <CheckCircle2 className="size-3" /> live v{bot.published_version}
          </Badge>
        )}
        {dirty && (
          <span className="flex items-center gap-1 text-[12px] text-amber-600 dark:text-amber-400">
            <span className="size-1.5 rounded-full bg-amber-500" /> Unsaved changes
          </span>
        )}
        <div className="ml-auto flex items-center gap-1.5">
          <Button size="sm" variant="outline" onClick={validate} loading={busy === "validate"}>
            <ShieldCheck className="size-3.5" /> Validate
          </Button>
          <Button size="sm" variant="outline" onClick={save} loading={busy === "save"} disabled={!dirty} title="Save (⌘S)">
            <Save className="size-3.5" /> Save
          </Button>
          <Button size="sm" variant="primary" onClick={publish} loading={busy === "publish"} disabled={publishedIsCurrent} title={errorCount ? "Fix errors to publish" : "Publish the latest version"}>
            <Rocket className="size-3.5" /> {publishedIsCurrent ? "Published" : "Publish"}
          </Button>
        </div>
      </div>
      <div className="flex shrink-0 items-center border-b border-border bg-panel px-2">
        <Tabs
          value={tab}
          onChange={setTab}
          items={[
            {
              value: "flow",
              label: "Flow",
              icon: <Workflow className="size-3.5" />,
              badge: errorCount ? <span className="rounded-full bg-red-500/15 px-1.5 text-[10px] font-semibold text-red-600">{errorCount}</span> : undefined,
            },
            { value: "settings", label: "Settings", icon: <Settings className="size-3.5" /> },
            { value: "test", label: "Test call", icon: <PhoneCall className="size-3.5" /> },
            { value: "sessions", label: "Sessions", icon: <ListChecks className="size-3.5" /> },
            { value: "oracles", label: "Oracles & Demos", icon: <FlaskConical className="size-3.5" /> },
            { value: "versions", label: "Versions", icon: <History className="size-3.5" /> },
          ]}
        />
      </div>
      {tab === "flow" && (
        <FlowEditor history={history} issues={issues} issueSource={serverFresh ? "server" : "lint"} datasets={datasets ?? []} selection={selection} setSelection={setSelection} />
      )}
      {tab === "settings" && <SettingsTab bot={bot} onSaved={() => mutate()} />}
      {tab === "test" && <TestCallTab bot={bot} draftGraph={graph as FlowGraph} dirty={dirty} onSave={save} />}
      {tab === "sessions" && <SessionsTab botId={bot.id} onOpen={(sid) => router.push(`/sessions/${sid}`)} />}
      {tab === "oracles" && <OraclesTab bot={bot} graph={graph as FlowGraph} />}
      {tab === "versions" && (
        <VersionsTab
          bot={bot}
          dirty={dirty}
          onRestored={async () => {
            setLoadedVersion(null);
            setSavedJson(null);
            await mutate();
            setTab("flow");
          }}
          onPublished={() => mutate()}
        />
      )}
    </div>
  );
}
