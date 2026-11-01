"use client";

import { Archive, ArchiveRestore, Bot, Copy, MoreHorizontal, Plus, Search, Workflow } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import useSWR from "swr";
import { Badge, Button, Card, Dialog, EmptyState, ErrorBox, Field, Input, Select, Textarea, cn, statusTone, useConfirm, useToast } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import type { BotStats, BotSummary } from "@/lib/types";

const COLORS = ["#1C69D3", "#BB162B", "#0F766E", "#7C3AED", "#EA580C", "#0891B2", "#DB2777", "#4F46E5"];

function relTime(iso: string | null | undefined) {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function Chip({ k, v }: { k: string; v: string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded border border-border bg-panel-2 px-1.5 py-px text-[11px]">
      <span className="font-semibold text-subtle">{k}</span>
      <span className="text-fg">{v}</span>
    </span>
  );
}

function BotCard({ bot, onChanged }: { bot: BotSummary; onChanged: () => void }) {
  const router = useRouter();
  const confirm = useConfirm();
  const toast = useToast();
  const [menu, setMenu] = useState(false);
  const { data: stats } = useSWR<BotStats>(bot.session_count ? `stats:${bot.id}` : null, () => api.stats(bot.id));
  const color = bot.avatar_color || "#4f46e5";
  const rate = stats?.completion_rate ?? bot.completion_rate;
  const archived = bot.status === "archived";

  const duplicate = async () => {
    setMenu(false);
    try {
      const d = await api.duplicateBot(bot.id);
      toast(`Duplicated as “${d.name}”`, "success");
      onChanged();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const archive = async () => {
    setMenu(false);
    if (archived) {
      await api.patchBot(bot.id, { status: bot.published_version ? "published" : "draft" }).catch((e) => toast(e.message, "error"));
      onChanged();
      return;
    }
    const ok = await confirm({ title: `Archive ${bot.name}?`, message: "Archived bots are hidden from the dashboard and stop accepting published calls. You can restore them later.", confirmLabel: "Archive", danger: true });
    if (!ok) return;
    try {
      await api.deleteBot(bot.id);
      toast("Bot archived", "success");
      onChanged();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  return (
    <Card className={cn("group relative overflow-hidden transition-shadow hover:shadow-md", archived && "opacity-60")}>
      <div className="h-1" style={{ background: color }} />
      <button className="block w-full p-4 text-left" onClick={() => router.push(`/bots/${bot.id}`)}>
        <div className="flex items-start gap-3">
          <div className="flex size-10 shrink-0 items-center justify-center rounded-lg text-base font-semibold text-white shadow-sm" style={{ background: color }}>
            {bot.name.trim().charAt(0).toUpperCase()}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <div className="truncate text-sm font-semibold">{bot.name}</div>
              <Badge tone={statusTone(bot.status)}>{bot.status}</Badge>
            </div>
            <div className="mt-0.5 text-[12px] text-muted">
              {bot.industry || "General"} · <span className="font-mono">{bot.slug}</span>
            </div>
          </div>
        </div>
        <p className="mt-3 line-clamp-2 min-h-[2.5em] text-[12px] text-muted">{bot.description || "No description"}</p>
        <div className="mt-3 flex flex-wrap gap-1">
          <Chip k="STT" v={bot.config?.stt?.provider ?? "—"} />
          <Chip k="LLM" v={bot.config?.llm?.model ?? "—"} />
          <Chip k="TTS" v={bot.config?.tts?.provider ?? "—"} />
        </div>
        <div className="mt-4 grid grid-cols-3 gap-2 border-t border-border pt-3 text-[11px] text-muted">
          <div>
            <div className="text-[15px] font-semibold tabular-nums text-fg">{bot.node_count}</div>nodes
          </div>
          <div>
            <div className="text-[15px] font-semibold tabular-nums text-fg">{bot.session_count}</div>sessions
          </div>
          <div>
            <div className="text-[15px] font-semibold tabular-nums text-fg">{rate != null && bot.session_count ? `${Math.round(rate * 100)}%` : "—"}</div>completion
          </div>
        </div>
        <div className="mt-2 flex items-center justify-between text-[11px] text-subtle">
          <span>
            v{bot.draft_version ?? "?"} draft{bot.published_version ? ` · v${bot.published_version} live` : ""}
          </span>
          <span>Last call {relTime(bot.last_session_at)}</span>
        </div>
      </button>
      <div className="absolute right-2 top-3">
        <button
          className="inline-flex size-7 items-center justify-center rounded-md text-muted opacity-0 hover:bg-panel-2 hover:text-fg group-hover:opacity-100 data-[open=true]:opacity-100"
          data-open={menu}
          onClick={() => setMenu((m) => !m)}
          aria-label="Bot actions"
        >
          <MoreHorizontal className="size-4" />
        </button>
        {menu && (
          <>
            <div className="fixed inset-0 z-10" onClick={() => setMenu(false)} />
            <div className="absolute right-0 z-20 mt-1 w-40 rounded-lg border border-border bg-panel p-1 shadow-xl">
              <Link href={`/bots/${bot.id}`} className="flex items-center gap-2 rounded px-2 py-1.5 text-[13px] hover:bg-panel-2">
                <Workflow className="size-3.5" /> Open builder
              </Link>
              <button onClick={duplicate} className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-[13px] hover:bg-panel-2">
                <Copy className="size-3.5" /> Duplicate
              </button>
              <button onClick={archive} className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-[13px] text-red-600 hover:bg-panel-2">
                {archived ? <ArchiveRestore className="size-3.5" /> : <Archive className="size-3.5" />} {archived ? "Restore" : "Archive"}
              </button>
            </div>
          </>
        )}
      </div>
    </Card>
  );
}

function NewBotDialog({ open, onClose, bots }: { open: boolean; onClose: () => void; bots: BotSummary[] }) {
  const router = useRouter();
  const [form, setForm] = useState({ name: "", slug: "", description: "", industry: "", avatar_color: COLORS[0], template: "blank" });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const slugOk = !form.slug || /^[a-z0-9][a-z0-9-]*$/.test(form.slug);
  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      const b = await api.createBot({
        name: form.name.trim(),
        slug: form.slug || undefined,
        description: form.description || undefined,
        industry: form.industry || undefined,
        avatar_color: form.avatar_color,
        template: form.template,
      });
      onClose();
      router.push(`/bots/${b.id}`);
    } catch (e) {
      setErr(e instanceof ApiError && e.status === 409 ? "A bot with this slug already exists." : (e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="New bot"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" onClick={submit} loading={busy} disabled={!form.name.trim() || !slugOk}>
            Create bot
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div>
          <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">Start from</div>
          <div className="grid grid-cols-2 gap-2">
            {[{ slug: "blank", name: "Blank flow", industry: "Start → End" }, ...bots.filter((b) => b.status !== "archived")].map((t) => (
              <button
                key={t.slug}
                type="button"
                onClick={() => setForm((f) => ({ ...f, template: t.slug, industry: f.industry || (t.slug === "blank" ? "" : (t.industry ?? "")) }))}
                className={cn(
                  "rounded-lg border p-2.5 text-left transition-colors",
                  form.template === t.slug ? "border-brand bg-brand/5 ring-2 ring-[var(--ring)]" : "border-border hover:bg-panel-2",
                )}
              >
                <div className="text-[13px] font-medium">{t.name}</div>
                <div className="text-[11px] text-muted">{t.slug === "blank" ? "Minimal start → end" : `Clone · ${t.industry}`}</div>
              </button>
            ))}
          </div>
        </div>
        <Field label="Name">
          <Input autoFocus value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Acme Support" />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Slug" hint="Optional, auto-generated" error={!slugOk && "lowercase letters, digits, dashes"}>
            <Input value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value })} placeholder="acme-support" className="font-mono" />
          </Field>
          <Field label="Industry">
            <Input value={form.industry} onChange={(e) => setForm({ ...form, industry: e.target.value })} placeholder="Automotive" />
          </Field>
        </div>
        <Field label="Description">
          <Textarea rows={2} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
        </Field>
        <Field label="Brand color">
          <div className="flex gap-1.5">
            {COLORS.map((c) => (
              <button
                key={c}
                type="button"
                onClick={() => setForm({ ...form, avatar_color: c })}
                className={cn("size-6 rounded-md ring-offset-2 ring-offset-[var(--panel)]", form.avatar_color === c && "ring-2 ring-[var(--brand)]")}
                style={{ background: c }}
                aria-label={c}
              />
            ))}
          </div>
        </Field>
        {err && <ErrorBox error={err} />}
      </div>
    </Dialog>
  );
}

export default function Dashboard() {
  const { data, error, isLoading, mutate } = useSWR<BotSummary[]>("bots", api.listBots);
  const [q, setQ] = useState("");
  const [industry, setIndustry] = useState("");
  const [status, setStatus] = useState("active");
  const [open, setOpen] = useState(false);

  const industries = useMemo(() => [...new Set((data ?? []).map((b) => b.industry || "General"))].sort(), [data]);
  const filtered = useMemo(
    () =>
      (data ?? []).filter((b) => {
        if (status === "active" && b.status === "archived") return false;
        if (status && status !== "active" && b.status !== status) return false;
        if (industry && (b.industry || "General") !== industry) return false;
        const s = q.toLowerCase();
        return !s || [b.name, b.slug, b.description, b.industry].some((x) => x?.toLowerCase().includes(s));
      }),
    [data, q, industry, status],
  );

  return (
    <div className="mx-auto w-full max-w-7xl flex-1 px-6 py-6">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Voice agents</h1>
          <p className="text-[13px] text-muted">Design flows, configure providers, and test calls for every brand.</p>
        </div>
        <Button variant="primary" onClick={() => setOpen(true)}>
          <Plus className="size-4" /> New bot
        </Button>
      </div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="relative w-72">
          <Search className="absolute left-2 top-2 size-4 text-subtle" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search bots…" className="pl-7" />
        </div>
        <Select value={industry} onChange={(e) => setIndustry(e.target.value)} className="w-44">
          <option value="">All industries</option>
          {industries.map((i) => (
            <option key={i}>{i}</option>
          ))}
        </Select>
        <Select value={status} onChange={(e) => setStatus(e.target.value)} className="w-40">
          <option value="active">Active</option>
          <option value="">All statuses</option>
          <option value="draft">Draft</option>
          <option value="published">Published</option>
          <option value="archived">Archived</option>
        </Select>
        {data && <span className="ml-auto text-[12px] text-muted">{filtered.length} of {data.length} bots</span>}
      </div>

      {error && <ErrorBox error={error} onRetry={() => mutate()} />}
      {isLoading && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-64 animate-pulse rounded-lg border border-border bg-panel" />
          ))}
        </div>
      )}
      {data && filtered.length === 0 && (
        <Card>
          <EmptyState icon={<Bot className="size-8" />} title={data.length ? "No bots match your filters" : "No bots yet"}>
            {data.length ? "Try clearing the search or filters." : "Create your first voice agent from a blank flow or a template."}
            {!data.length && (
              <div className="mt-3">
                <Button variant="primary" onClick={() => setOpen(true)}>
                  <Plus className="size-4" /> New bot
                </Button>
              </div>
            )}
          </EmptyState>
        </Card>
      )}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {filtered.map((b) => (
          <BotCard key={b.id} bot={b} onChanged={() => mutate()} />
        ))}
      </div>
      {open && <NewBotDialog open={open} onClose={() => setOpen(false)} bots={data ?? []} />}
    </div>
  );
}
