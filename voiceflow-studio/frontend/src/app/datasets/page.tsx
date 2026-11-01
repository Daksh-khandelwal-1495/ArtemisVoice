"use client";

import { Database, Plus, Search, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import useSWR from "swr";
import { Badge, Button, Card, Dialog, EmptyState, ErrorBox, IconButton, Input, Spinner, cn, useConfirm, useToast } from "@/components/ui";
import { JsonEditor } from "@/components/ui/editors";
import { api } from "@/lib/api";
import type { Dataset, DatasetRowsResponse } from "@/lib/types";

const PAGE = 50;

function cell(v: unknown) {
  if (v == null) return <span className="text-subtle">—</span>;
  if (typeof v === "object") return <span className="font-mono text-[11px]">{JSON.stringify(v)}</span>;
  return String(v);
}

function RowsTable({ ds, onChanged }: { ds: Dataset; onChanged: () => void }) {
  const toast = useToast();
  const confirm = useConfirm();
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [page, setPage] = useState(0);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => {
      setDebounced(q);
      setPage(0);
    }, 250);
    return () => clearTimeout(t);
  }, [q]);

  const { data, error, isLoading, mutate } = useSWR<DatasetRowsResponse>(`rows:${ds.name}:${debounced}:${page}`, () => api.datasetRows(ds.name, debounced, PAGE, page * PAGE), {
    keepPreviousData: true,
  });
  const columns = data?.columns?.length ? data.columns : ds.columns;

  const add = async () => {
    if (!draft) return;
    setBusy(true);
    try {
      await api.addRow(ds.name, draft);
      toast("Row added", "success");
      setAdding(false);
      mutate();
      onChanged();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const del = async (rowId: string) => {
    if (!(await confirm({ title: "Delete row?", message: "Lookups using this dataset will no longer match it.", confirmLabel: "Delete", danger: true }))) return;
    try {
      await api.deleteRow(ds.name, rowId);
      mutate();
      onChanged();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        <div>
          <div className="flex items-center gap-2 text-[14px] font-semibold">
            <span className="font-mono">{ds.name}</span>
            <Badge>{data?.total ?? ds.row_count} rows</Badge>
          </div>
          {ds.description && <div className="text-[12px] text-muted">{ds.description}</div>}
        </div>
        <div className="relative ml-auto w-64">
          <Search className="absolute left-2 top-2 size-4 text-subtle" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search rows…" className="pl-7" />
        </div>
        <Button
          variant="primary"
          size="sm"
          onClick={() => {
            setDraft(Object.fromEntries(ds.columns.map((c) => [c, ""])));
            setAdding(true);
          }}
        >
          <Plus className="size-3.5" /> Add row
        </Button>
      </div>
      {error && (
        <div className="p-4">
          <ErrorBox error={error} onRetry={() => mutate()} />
        </div>
      )}
      <div className="flex-1 overflow-auto thin-scroll">
        {isLoading && !data && (
          <div className="flex items-center justify-center gap-2 py-10 text-muted">
            <Spinner /> Loading rows…
          </div>
        )}
        {data && data.items.length === 0 && <EmptyState icon={<Database className="size-7" />} title={debounced ? "No matching rows" : "No rows yet"} />}
        {data && data.items.length > 0 && (
          <table className="w-full text-[12px]">
            <thead className="sticky top-0 bg-panel-2 text-left text-[10.5px] uppercase tracking-wider text-muted">
              <tr>
                {columns.map((c) => (
                  <th key={c} className="whitespace-nowrap border-b border-border px-3 py-2 font-mono font-medium normal-case">
                    {c}
                  </th>
                ))}
                <th className="w-10 border-b border-border" />
              </tr>
            </thead>
            <tbody>
              {data.items.map((r) => (
                <tr key={r.id} className="group border-b border-border hover:bg-panel-2">
                  {columns.map((c) => (
                    <td key={c} className="max-w-[260px] truncate px-3 py-1.5" title={typeof r.data[c] === "string" ? (r.data[c] as string) : undefined}>
                      {cell(r.data[c])}
                    </td>
                  ))}
                  <td className="px-1">
                    <IconButton title="Delete row" onClick={() => del(r.id)} className="opacity-0 hover:text-red-500 group-hover:opacity-100">
                      <Trash2 className="size-3.5" />
                    </IconButton>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {data && data.total > PAGE && (
        <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-2 text-[12px] text-muted">
          {page * PAGE + 1}–{Math.min(data.total, (page + 1) * PAGE)} of {data.total}
          <Button size="sm" variant="outline" disabled={page === 0} onClick={() => setPage(page - 1)}>
            Prev
          </Button>
          <Button size="sm" variant="outline" disabled={(page + 1) * PAGE >= data.total} onClick={() => setPage(page + 1)}>
            Next
          </Button>
        </div>
      )}
      <Dialog
        open={adding}
        onClose={() => setAdding(false)}
        title={`Add row to ${ds.name}`}
        footer={
          <>
            <Button variant="outline" onClick={() => setAdding(false)}>
              Cancel
            </Button>
            <Button variant="primary" onClick={add} loading={busy} disabled={!draft}>
              Add row
            </Button>
          </>
        }
      >
        <div className="mb-2 text-[12px] text-muted">Row data as a JSON object. New keys become new columns.</div>
        <JsonEditor value={draft} onChange={setDraft} rows={14} allowNull={false} />
      </Dialog>
    </div>
  );
}

export default function DatasetsPage() {
  const { data, error, isLoading, mutate } = useSWR<Dataset[]>("datasets", api.datasets);
  const [selected, setSelected] = useState<string | null>(null);
  const current = data?.find((d) => d.name === selected) ?? data?.[0];

  return (
    <div className="flex min-h-0 flex-1">
      <aside className="w-72 shrink-0 overflow-y-auto border-r border-border bg-panel p-3 thin-scroll">
        <div className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-wider text-muted">Datasets</div>
        {error && <ErrorBox error={error} onRetry={() => mutate()} />}
        {isLoading && (
          <div className="flex items-center gap-2 p-2 text-muted">
            <Spinner /> Loading…
          </div>
        )}
        {data?.length === 0 && <div className="p-2 text-[12px] text-muted">No datasets. Seed them from the backend.</div>}
        <div className="space-y-0.5">
          {data?.map((d) => (
            <button
              key={d.id}
              onClick={() => setSelected(d.name)}
              className={cn("flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left", current?.name === d.name ? "bg-panel-2" : "hover:bg-panel-2")}
            >
              <Database className="mt-0.5 size-3.5 shrink-0 text-cyan-600" />
              <div className="min-w-0 flex-1">
                <div className="truncate font-mono text-[12.5px]">{d.name}</div>
                <div className="truncate text-[11px] text-muted">
                  {d.row_count} rows · {d.columns.length} cols
                </div>
              </div>
            </button>
          ))}
        </div>
      </aside>
      {current ? (
        <RowsTable key={current.name} ds={current} onChanged={() => mutate()} />
      ) : (
        !isLoading && (
          <Card className="m-6 flex-1">
            <EmptyState icon={<Database className="size-8" />} title="Select a dataset" />
          </Card>
        )
      )}
    </div>
  );
}
