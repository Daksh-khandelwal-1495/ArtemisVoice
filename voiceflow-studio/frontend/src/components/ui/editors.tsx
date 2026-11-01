"use client";

import { Plus, X } from "lucide-react";
import { useState } from "react";
import { Button, IconButton, Input, Textarea, cn } from "./index";

export function ChipsInput({
  value,
  onChange,
  placeholder = "Type and press Enter",
  className,
}: {
  value: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
  className?: string;
}) {
  const [draft, setDraft] = useState("");
  const add = (raw: string) => {
    const parts = raw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean)
      .filter((s) => !value.includes(s));
    if (parts.length) onChange([...value, ...parts]);
    setDraft("");
  };
  return (
    <div
      className={cn(
        "flex min-h-8 flex-wrap items-center gap-1 rounded-md border border-border bg-panel px-1.5 py-1 focus-within:border-brand",
        className,
      )}
    >
      {value.map((v) => (
        <span key={v} className="inline-flex items-center gap-0.5 rounded bg-panel-2 px-1.5 py-0.5 text-[12px]">
          {v}
          <button type="button" className="text-muted hover:text-red-500" onClick={() => onChange(value.filter((x) => x !== v))} aria-label={`Remove ${v}`}>
            <X className="size-3" />
          </button>
        </span>
      ))}
      <input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            add(draft);
          } else if (e.key === "Backspace" && !draft && value.length) onChange(value.slice(0, -1));
        }}
        onBlur={() => draft && add(draft)}
        placeholder={value.length ? "" : placeholder}
        className="min-w-[80px] flex-1 bg-transparent px-1 text-[13px] outline-none placeholder:text-subtle"
      />
    </div>
  );
}

export function KVEditor({
  value,
  onChange,
  keyPlaceholder = "key",
  valuePlaceholder = "value",
  keyOptions,
  valueOptions,
}: {
  value: Record<string, string>;
  onChange: (v: Record<string, string>) => void;
  keyPlaceholder?: string;
  valuePlaceholder?: string;
  keyOptions?: string[];
  valueOptions?: string[];
}) {
  const entries = Object.entries(value);
  const set = (i: number, k: string, v: string) => {
    const next = entries.map((e, j) => (j === i ? [k, v] : e));
    onChange(Object.fromEntries(next));
  };
  const listId = (s: string) => `kv-${s}-${Math.abs(hash(String(keyOptions) + String(valueOptions)))}`;
  return (
    <div className="space-y-1.5">
      {entries.map(([k, v], i) => (
        <div key={i} className="flex items-center gap-1.5">
          <Input value={k} placeholder={keyPlaceholder} list={keyOptions ? listId("k") : undefined} onChange={(e) => set(i, e.target.value, v)} className="font-mono text-[12px]" />
          <span className="text-subtle">→</span>
          <Input value={v} placeholder={valuePlaceholder} list={valueOptions ? listId("v") : undefined} onChange={(e) => set(i, k, e.target.value)} className="font-mono text-[12px]" />
          <IconButton title="Remove" onClick={() => onChange(Object.fromEntries(entries.filter((_, j) => j !== i)))}>
            <X className="size-3.5" />
          </IconButton>
        </div>
      ))}
      <Button
        size="sm"
        variant="ghost"
        onClick={() => {
          let k = keyOptions?.find((o) => !(o in value)) ?? "";
          if (k in value) k = `${k}_${entries.length}`;
          onChange({ ...value, [k]: "" });
        }}
      >
        <Plus className="size-3.5" /> Add
      </Button>
      {keyOptions && (
        <datalist id={listId("k")}>
          {keyOptions.map((o) => (
            <option key={o} value={o} />
          ))}
        </datalist>
      )}
      {valueOptions && (
        <datalist id={listId("v")}>
          {valueOptions.map((o) => (
            <option key={o} value={o} />
          ))}
        </datalist>
      )}
    </div>
  );
}

function hash(s: string) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return h;
}

/** JSON textarea that only commits parsed objects; shows parse errors inline. */
export function JsonEditor({
  value,
  onChange,
  rows = 6,
  placeholder = "{ }",
  allowNull = true,
}: {
  value: unknown;
  onChange: (v: Record<string, unknown> | null) => void;
  rows?: number;
  placeholder?: string;
  allowNull?: boolean;
}) {
  const serialized = value == null ? "" : JSON.stringify(value, null, 2);
  const [text, setText] = useState(serialized);
  const [error, setError] = useState<string | null>(null);
  const [lastSerialized, setLastSerialized] = useState(serialized);
  // Sync external changes (e.g. node switch) without clobbering in-progress invalid edits.
  if (serialized !== lastSerialized) {
    setLastSerialized(serialized);
    if (!error) setText(serialized);
  }
  return (
    <div>
      <Textarea
        rows={rows}
        value={text}
        placeholder={placeholder}
        spellCheck={false}
        invalid={!!error}
        className="font-mono text-[12px]"
        onChange={(e) => {
          const t = e.target.value;
          setText(t);
          if (!t.trim()) {
            setError(allowNull ? null : "Required");
            if (allowNull) {
              setLastSerialized("");
              onChange(null);
            }
            return;
          }
          try {
            const parsed = JSON.parse(t);
            if (typeof parsed !== "object" || Array.isArray(parsed) || parsed === null) throw new Error("Must be a JSON object");
            setError(null);
            setLastSerialized(JSON.stringify(parsed, null, 2));
            onChange(parsed);
          } catch (err) {
            setError(err instanceof Error ? err.message : "Invalid JSON");
          }
        }}
      />
      {error && <div className="mt-1 text-[11px] text-red-500">{error}</div>}
    </div>
  );
}
