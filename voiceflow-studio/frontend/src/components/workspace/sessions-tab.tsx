"use client";

import { ListChecks, RefreshCw } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { Badge, Button, Card, EmptyState, ErrorBox, Select, Spinner, statusTone } from "@/components/ui";
import { api } from "@/lib/api";
import type { SessionList } from "@/lib/types";

export const fmtDuration = (s: number | null | undefined) => (s == null ? "—" : s < 60 ? `${Math.round(s)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`);

const PAGE = 25;

export function SessionsTab({ botId, onOpen }: { botId: string; onOpen: (id: string) => void }) {
  const [status, setStatus] = useState("");
  const [mode, setMode] = useState("");
  const [page, setPage] = useState(0);
  const { data, error, isLoading, mutate } = useSWR<SessionList>(`sessions:${botId}:${status}:${mode}:${page}`, () =>
    api.sessions({ bot_id: botId, status: status || undefined, mode: mode || undefined, limit: PAGE, offset: page * PAGE }),
  );
  return (
    <div className="flex-1 overflow-y-auto p-6 thin-scroll">
      <div className="mx-auto max-w-6xl">
        <div className="mb-3 flex items-center gap-2">
          <Select value={status} onChange={(e) => (setStatus(e.target.value), setPage(0))} className="w-40">
            <option value="">All statuses</option>
            {["active", "completed", "transferred", "abandoned", "error"].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </Select>
          <Select value={mode} onChange={(e) => (setMode(e.target.value), setPage(0))} className="w-36">
            <option value="">All modes</option>
            <option value="draft">draft</option>
            <option value="published">published</option>
          </Select>
          <Button size="sm" variant="ghost" onClick={() => mutate()}>
            <RefreshCw className="size-3.5" /> Refresh
          </Button>
          {data && <span className="ml-auto text-[12px] text-muted">{data.total} sessions</span>}
        </div>
        {error && <ErrorBox error={error} onRetry={() => mutate()} />}
        <Card className="overflow-hidden">
          {isLoading && (
            <div className="flex items-center justify-center gap-2 py-12 text-muted">
              <Spinner /> Loading sessions…
            </div>
          )}
          {data && data.items.length === 0 && <EmptyState icon={<ListChecks className="size-7" />} title="No sessions yet">Run a test call to see it here.</EmptyState>}
          {data && data.items.length > 0 && (
            <table className="w-full text-[12.5px]">
              <thead className="border-b border-border bg-panel-2 text-left text-[11px] uppercase tracking-wider text-muted">
                <tr>
                  <th className="px-3 py-2 font-medium">Started</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 font-medium">End reason</th>
                  <th className="px-3 py-2 font-medium">Mode</th>
                  <th className="px-3 py-2 font-medium">Duration</th>
                  <th className="px-3 py-2 font-medium">Captured</th>
                  <th className="px-3 py-2 font-medium">Missing required</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((s) => {
                  const missing = Array.isArray(s.required_missing) ? s.required_missing : [];
                  return (
                    <tr key={s.id} onClick={() => onOpen(s.id)} className="cursor-pointer border-b border-border last:border-0 hover:bg-panel-2">
                      <td className="px-3 py-2 tabular-nums">{new Date(s.started_at).toLocaleString()}</td>
                      <td className="px-3 py-2">
                        <Badge tone={statusTone(s.status)}>{s.status}</Badge>
                      </td>
                      <td className="px-3 py-2 font-mono text-[11.5px] text-muted">{s.end_reason ?? "—"}</td>
                      <td className="px-3 py-2">
                        <Badge tone={s.mode === "published" ? "green" : "gray"}>{s.mode}</Badge>
                      </td>
                      <td className="px-3 py-2 tabular-nums">{fmtDuration(s.duration_secs)}</td>
                      <td className="px-3 py-2 tabular-nums">{s.captured_count}</td>
                      <td className="px-3 py-2">
                        {missing.length === 0 ? (
                          <span className="text-emerald-600 dark:text-emerald-400">✓ none</span>
                        ) : (
                          <span className="font-mono text-[11px] text-red-600 dark:text-red-400" title={missing.join(", ")}>
                            {missing.slice(0, 3).join(", ")}
                            {missing.length > 3 && ` +${missing.length - 3}`}
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </Card>
        {data && data.total > PAGE && (
          <div className="mt-3 flex items-center justify-end gap-2 text-[12px] text-muted">
            Page {page + 1} of {Math.ceil(data.total / PAGE)}
            <Button size="sm" variant="outline" disabled={page === 0} onClick={() => setPage(page - 1)}>
              Prev
            </Button>
            <Button size="sm" variant="outline" disabled={(page + 1) * PAGE >= data.total} onClick={() => setPage(page + 1)}>
              Next
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
