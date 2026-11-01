"use client";

import { AlertTriangle, Plus, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Button, Field, IconButton, Input, Select, Switch, Textarea, cn } from "@/components/ui";
import { FAULT_PRESETS, faultLayers } from "@/lib/oracle";
import type { Dataset, FaultProfile, TextReplacement } from "@/lib/types";

function ReplacementList({ value, onChange, warnFor }: { value: TextReplacement[]; onChange: (v: TextReplacement[]) => void; warnFor?: (from: string) => string | null }) {
  return (
    <div className="space-y-1">
      {value.map((r, i) => {
        const warn = warnFor?.(r.from);
        return (
          <div key={i}>
            <div className="flex items-center gap-1.5">
              <Input value={r.from} placeholder="heard" className="font-mono text-[12px]" onChange={(e) => onChange(value.map((x, j) => (j === i ? { ...x, from: e.target.value } : x)))} />
              <span className="text-subtle">→</span>
              <Input value={r.to} placeholder="becomes" className="font-mono text-[12px]" onChange={(e) => onChange(value.map((x, j) => (j === i ? { ...x, to: e.target.value } : x)))} />
              <IconButton title="Remove" onClick={() => onChange(value.filter((_, j) => j !== i))}>
                <X className="size-3.5" />
              </IconButton>
            </div>
            {warn && (
              <div className="mt-0.5 flex items-center gap-1 text-[10.5px] text-amber-700 dark:text-amber-400">
                <AlertTriangle className="size-3" /> {warn}
              </div>
            )}
          </div>
        );
      })}
      <Button size="xs" variant="ghost" onClick={() => onChange([...value, { from: "", to: "" }])}>
        <Plus className="size-3" /> Add replacement
      </Button>
    </div>
  );
}

function Section({ title, on, onToggle, children }: { title: string; on: boolean; onToggle: (v: boolean) => void; children: ReactNode }) {
  return (
    <div className={cn("rounded-md border p-2.5", on ? "border-amber-500/30 bg-amber-500/5" : "border-border")}>
      <div className="flex items-center justify-between">
        <span className={cn("text-[11px] font-semibold uppercase tracking-wider", on ? "text-amber-700 dark:text-amber-400" : "text-muted")}>{title}</span>
        <Switch checked={on} onChange={onToggle} label={`${title} fault`} />
      </div>
      {on && <div className="mt-2 space-y-2">{children}</div>}
    </div>
  );
}

function RowsEditor({ value, onChange }: { value: Record<string, unknown>[]; onChange: (v: Record<string, unknown>[]) => void }) {
  const serialized = JSON.stringify(value, null, 2);
  const [text, setText] = useState(serialized);
  const [last, setLast] = useState(serialized);
  const [err, setErr] = useState<string | null>(null);
  if (serialized !== last) {
    setLast(serialized);
    if (!err) setText(serialized);
  }
  return (
    <div>
      <Textarea
        rows={5}
        value={text}
        spellCheck={false}
        invalid={!!err}
        className="font-mono text-[11px]"
        onChange={(e) => {
          setText(e.target.value);
          try {
            const v = JSON.parse(e.target.value);
            if (!Array.isArray(v) || v.some((r) => typeof r !== "object" || r === null || Array.isArray(r))) throw new Error("Must be a JSON array of row objects");
            setErr(null);
            setLast(JSON.stringify(v, null, 2));
            onChange(v);
          } catch (x) {
            setErr(x instanceof Error ? x.message : "Invalid JSON");
          }
        }}
      />
      {err && <div className="mt-0.5 text-[11px] text-red-500">{err}</div>}
    </div>
  );
}

export function FaultProfileEditor({
  value,
  onChange,
  fields,
  datasets,
  keywords,
}: {
  value: FaultProfile;
  onChange: (v: FaultProfile) => void;
  fields: string[];
  datasets: Dataset[];
  keywords: string[];
}) {
  const set = <K extends keyof FaultProfile>(k: K, v: FaultProfile[K] | undefined) => {
    const next = { ...value };
    if (v === undefined) delete next[k];
    else next[k] = v;
    onChange(next);
  };
  const kw = new Set(keywords.map((k) => k.toLowerCase()));
  const active = faultLayers(value);

  return (
    <div className="space-y-2">
      <div>
        <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted">Tata demo presets</div>
        <div className="flex flex-wrap gap-1">
          {FAULT_PRESETS.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => onChange(structuredClone(p.profile))}
              className="rounded-md border border-border bg-panel-2 px-1.5 py-0.5 text-[11px] hover:border-amber-500/50 hover:text-amber-700 dark:hover:text-amber-400"
            >
              {p.label}
            </button>
          ))}
          <button type="button" onClick={() => onChange({})} className="rounded-md px-1.5 py-0.5 text-[11px] text-muted hover:text-fg">
            Clear
          </button>
        </div>
        <div className="mt-1 text-[11px] text-muted">{active.length ? `Active faults: ${active.join(", ")}` : "No faults — runs the clean flow."}</div>
      </div>

      <Section title="STT" on={!!value.stt} onToggle={(on) => set("stt", on ? { replacements: [{ from: "411001", to: "41100I" }], keyword_protected: true } : undefined)}>
        <ReplacementList
          value={value.stt?.replacements ?? []}
          onChange={(r) => set("stt", { ...value.stt, replacements: r })}
          warnFor={(from) => (value.stt?.keyword_protected && from && kw.has(from.toLowerCase()) ? `“${from}” is already a bot STT keyword — protected, so this replacement is skipped.` : null)}
        />
        <Field inline label="keyword_protected" hint="Skip a replacement when the source word is in bot STT keywords (lets the keyterm-boost patch fix it).">
          <Switch checked={!!value.stt?.keyword_protected} onChange={(v) => set("stt", { ...value.stt, keyword_protected: v })} label="keyword_protected" />
        </Field>
      </Section>

      <Section title="Endpointing" on={!!value.endpointing} onToggle={(on) => set("endpointing", on ? { truncate_field: "pincode", keep_chars: 4 } : undefined)}>
        <div className="grid grid-cols-[1fr_90px] gap-2">
          <Field label="Truncate field">
            <Select value={value.endpointing?.truncate_field ?? ""} onChange={(e) => set("endpointing", { keep_chars: value.endpointing?.keep_chars ?? 4, truncate_field: e.target.value })}>
              {[...new Set(["pincode", ...fields])].map((f) => (
                <option key={f}>{f}</option>
              ))}
            </Select>
          </Field>
          <Field label="Keep chars">
            <Input type="number" min={0} value={value.endpointing?.keep_chars ?? 4} onChange={(e) => set("endpointing", { truncate_field: value.endpointing?.truncate_field ?? "pincode", keep_chars: Math.max(0, Number(e.target.value) || 0) })} />
          </Field>
        </div>
      </Section>

      <Section title="LLM" on={!!value.llm} onToggle={(on) => set("llm", on ? { swap_field: { field: "pincode", with_value_of: "phone" } } : undefined)}>
        <div className="grid grid-cols-2 gap-2">
          <Field label="Field">
            <Select value={value.llm?.swap_field?.field ?? ""} onChange={(e) => set("llm", { swap_field: { with_value_of: value.llm?.swap_field?.with_value_of ?? "phone", field: e.target.value } })}>
              {[...new Set(["pincode", ...fields])].map((f) => (
                <option key={f}>{f}</option>
              ))}
            </Select>
          </Field>
          <Field label="← value of">
            <Select value={value.llm?.swap_field?.with_value_of ?? ""} onChange={(e) => set("llm", { swap_field: { field: value.llm?.swap_field?.field ?? "pincode", with_value_of: e.target.value } })}>
              {[...new Set(["phone", ...fields])].map((f) => (
                <option key={f}>{f}</option>
              ))}
            </Select>
          </Field>
        </div>
      </Section>

      <Section title="Tools" on={!!value.tools} onToggle={(on) => set("tools", on ? { lookup_override: null, api_error: true } : undefined)}>
        <Field inline label="Simulated API error" hint="Lookups / api_calls fail (mock 5xx).">
          <Switch checked={!!value.tools?.api_error} onChange={(v) => set("tools", { ...value.tools, api_error: v })} label="api_error" />
        </Field>
        <Field inline label="Override lookup rows" hint="Return these rows instead of the dataset snapshot (wrong dealer).">
          <Switch
            checked={!!value.tools?.lookup_override}
            onChange={(v) => set("tools", { ...value.tools, lookup_override: v ? (FAULT_PRESETS.find((p) => p.id === "tools-dealer")!.profile.tools!.lookup_override ?? null) : null })}
            label="lookup_override"
          />
        </Field>
        {value.tools?.lookup_override && (
          <>
            <Field label="Dataset">
              <Select
                value={value.tools.lookup_override.dataset ?? ""}
                onChange={(e) => set("tools", { ...value.tools, lookup_override: { rows: value.tools?.lookup_override?.rows ?? [], dataset: e.target.value || null } })}
              >
                <option value="">(every lookup)</option>
                {[...new Set([value.tools.lookup_override.dataset, ...datasets.map((d) => d.name)])]
                  .filter((d): d is string => !!d)
                  .map((d) => (
                    <option key={d}>{d}</option>
                  ))}
              </Select>
            </Field>
            <div className="space-y-1">
              <div className="text-[11px] font-medium uppercase tracking-wide text-muted">Rows (JSON)</div>
              <RowsEditor value={value.tools.lookup_override.rows} onChange={(rows) => set("tools", { ...value.tools, lookup_override: { dataset: value.tools?.lookup_override?.dataset ?? "", rows } })} />
            </div>
          </>
        )}
      </Section>

      <Section title="TTS" on={!!value.tts} onToggle={(on) => set("tts", on ? { replacements: [{ from: "Nexon", to: "Nixon" }] } : undefined)}>
        <ReplacementList value={value.tts?.replacements ?? []} onChange={(r) => set("tts", { replacements: r })} />
      </Section>
    </div>
  );
}
