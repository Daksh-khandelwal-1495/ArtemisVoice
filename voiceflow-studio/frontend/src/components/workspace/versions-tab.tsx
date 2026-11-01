"use client";

import { CheckCircle2, Eye, History, RotateCcw, Rocket } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { ReadOnlyFlow } from "@/components/flow/flow-editor";
import { Badge, Button, Card, EmptyState, ErrorBox, Spinner, cn, useConfirm, useToast } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { normalizeGraph } from "@/lib/defaults";
import type { BotDetail, FlowValidationResult, VersionDetail, VersionInfo } from "@/lib/types";

export function VersionsTab({ bot, dirty, onRestored, onPublished }: { bot: BotDetail; dirty: boolean; onRestored: () => void; onPublished: () => void }) {
  const toast = useToast();
  const confirm = useConfirm();
  const { data, error, mutate } = useSWR<VersionInfo[]>(`versions:${bot.id}:${bot.draft_version}:${bot.published_version}`, () => api.versions(bot.id));
  const [viewId, setViewId] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const { data: view, error: viewErr } = useSWR<VersionDetail>(viewId ? `version:${bot.id}:${viewId}` : null, () => api.version(bot.id, viewId!));
  const [pubErr, setPubErr] = useState<FlowValidationResult | null>(null);

  const restore = async (v: VersionInfo) => {
    const ok = await confirm({
      title: `Restore v${v.version}?`,
      message: dirty ? "This copies the version as a new draft and DISCARDS your unsaved flow edits." : "This copies the version as a new latest draft. Nothing is lost.",
      confirmLabel: "Restore",
      danger: dirty,
    });
    if (!ok) return;
    setBusy(v.id);
    try {
      const r = await api.restoreVersion(bot.id, v.id);
      toast(`Restored as v${r.version}`, "success");
      await mutate();
      onRestored();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(null);
    }
  };

  const publish = async (v: VersionInfo) => {
    setBusy(v.id);
    setPubErr(null);
    try {
      await api.publish(bot.id, v.id);
      toast(`Published v${v.version}`, "success");
      await mutate();
      onPublished();
    } catch (e) {
      if (e instanceof ApiError && typeof e.detail === "object" && e.detail && "validation" in e.detail) setPubErr((e.detail as { validation: FlowValidationResult }).validation);
      toast((e as Error).message, "error");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="grid min-h-0 flex-1 grid-cols-[400px_1fr]">
      <div className="overflow-y-auto border-r border-border p-3 thin-scroll">
        {error && <ErrorBox error={error} onRetry={() => mutate()} />}
        {!data && !error && (
          <div className="flex items-center gap-2 p-4 text-muted">
            <Spinner /> Loading versions…
          </div>
        )}
        {data && data.length === 0 && <EmptyState icon={<History className="size-7" />} title="No versions" />}
        <div className="space-y-2">
          {data?.map((v, i) => (
            <Card key={v.id} className={cn("p-3", viewId === v.id && "border-brand ring-2 ring-[var(--ring)]")}>
              <div className="flex items-center gap-2">
                <span className="font-mono text-[13px] font-semibold">v{v.version}</span>
                {i === 0 && <Badge tone="blue">latest draft</Badge>}
                {v.is_published && (
                  <Badge tone="green">
                    <CheckCircle2 className="size-3" /> published
                  </Badge>
                )}
                <span className="ml-auto text-[11px] text-muted">{new Date(v.created_at).toLocaleString()}</span>
              </div>
              <div className="mt-1 text-[12px] text-muted">
                {v.note || <span className="italic">No note</span>} · {v.node_count} nodes
              </div>
              <div className="mt-2 flex gap-1.5">
                <Button size="xs" variant="outline" onClick={() => setViewId(v.id)}>
                  <Eye className="size-3" /> View
                </Button>
                {i !== 0 && (
                  <Button size="xs" variant="outline" onClick={() => restore(v)} loading={busy === v.id}>
                    <RotateCcw className="size-3" /> Restore
                  </Button>
                )}
                {!v.is_published && (
                  <Button size="xs" variant="outline" onClick={() => publish(v)} loading={busy === v.id}>
                    <Rocket className="size-3" /> Publish this
                  </Button>
                )}
              </div>
            </Card>
          ))}
        </div>
      </div>
      <div className="relative min-h-0">
        {pubErr && (
          <div className="absolute inset-x-3 top-3 z-10 rounded-lg border border-red-500/30 bg-panel p-3 text-[12px] shadow-lg">
            <div className="mb-1 font-semibold text-red-600">Cannot publish: validation errors</div>
            {pubErr.issues
              .filter((i) => i.severity === "error")
              .map((i, k) => (
                <div key={k} className="text-muted">
                  ● {i.message} {i.node_id && <span className="font-mono">({i.node_id})</span>}
                </div>
              ))}
          </div>
        )}
        {!viewId && <EmptyState icon={<Eye className="size-7" />} title="Select a version to preview">The canvas is read-only. Restore a version to edit it.</EmptyState>}
        {viewErr && <div className="p-4"><ErrorBox error={viewErr} /></div>}
        {viewId && !view && !viewErr && (
          <div className="flex h-full items-center justify-center gap-2 text-muted">
            <Spinner /> Loading…
          </div>
        )}
        {view && <ReadOnlyFlow graph={normalizeGraph(view.graph)} fitKey={view.id} />}
      </div>
    </div>
  );
}
