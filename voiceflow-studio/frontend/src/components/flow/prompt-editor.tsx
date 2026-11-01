"use client";

import { AlertTriangle } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { Textarea, cn } from "@/components/ui";
import { placeholders } from "@/lib/lint";

export interface VarOption {
  name: string;
  source: string;
}

/** Textarea with `{{var}}` autocomplete from the vars available to this node. */
export function PromptEditor({
  value,
  onChange,
  vars,
  rows = 5,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  vars: VarOption[];
  rows?: number;
  placeholder?: string;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [query, setQuery] = useState<string | null>(null);
  const [active, setActive] = useState(0);

  const matches = useMemo(
    () => (query === null ? [] : vars.filter((v) => v.name.toLowerCase().includes(query.toLowerCase())).slice(0, 8)),
    [query, vars],
  );
  const unknown = useMemo(() => {
    const known = new Set(vars.map((v) => v.name));
    return [...new Set(placeholders(value).filter((p) => !known.has(p)))];
  }, [value, vars]);

  const detect = (el: HTMLTextAreaElement) => {
    const before = el.value.slice(0, el.selectionStart);
    const m = /\{\{\s*([A-Za-z0-9_]*)$/.exec(before);
    setQuery(m ? m[1] : null);
    setActive(0);
  };

  const insert = (name: string) => {
    const el = ref.current;
    if (!el) return;
    const pos = el.selectionStart;
    const before = value.slice(0, pos).replace(/\{\{\s*[A-Za-z0-9_]*$/, "");
    const after = value.slice(pos).replace(/^[A-Za-z0-9_]*\s*\}\}/, "");
    const token = `{{${name}}}`;
    const next = before + token + after;
    onChange(next);
    setQuery(null);
    requestAnimationFrame(() => {
      el.focus();
      const c = before.length + token.length;
      el.setSelectionRange(c, c);
    });
  };

  return (
    <div className="relative">
      <Textarea
        ref={ref}
        rows={rows}
        value={value}
        placeholder={placeholder}
        onChange={(e) => {
          onChange(e.target.value);
          detect(e.target);
        }}
        onKeyUp={(e) => ["ArrowLeft", "ArrowRight"].includes(e.key) && detect(e.currentTarget)}
        onClick={(e) => detect(e.currentTarget)}
        onBlur={() => setTimeout(() => setQuery(null), 150)}
        onKeyDown={(e) => {
          if (!matches.length) return;
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setActive((a) => (a + 1) % matches.length);
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((a) => (a - 1 + matches.length) % matches.length);
          } else if (e.key === "Enter" || e.key === "Tab") {
            e.preventDefault();
            insert(matches[active].name);
          } else if (e.key === "Escape") setQuery(null);
        }}
        className="text-[12.5px]"
      />
      {matches.length > 0 && (
        <div className="absolute left-2 right-2 z-30 mt-1 overflow-hidden rounded-md border border-border bg-panel shadow-xl">
          {matches.map((v, i) => (
            <button
              key={v.name}
              type="button"
              onMouseDown={(e) => {
                e.preventDefault();
                insert(v.name);
              }}
              className={cn("flex w-full items-center justify-between px-2 py-1 text-left text-[12px]", i === active ? "bg-brand/10" : "hover:bg-panel-2")}
            >
              <span className="font-mono">{`{{${v.name}}}`}</span>
              <span className="text-[10.5px] text-muted">{v.source}</span>
            </button>
          ))}
        </div>
      )}
      <div className="mt-1 flex flex-wrap items-center gap-1">
        <span className="text-[10.5px] text-subtle">Type {"{{"} to insert:</span>
        {vars.slice(0, 10).map((v) => (
          <button
            key={v.name}
            type="button"
            title={`from ${v.source}`}
            onClick={() => {
              const t = `{{${v.name}}}`;
              onChange(value ? `${value}${value.endsWith(" ") ? "" : " "}${t}` : t);
            }}
            className={cn(
              "rounded px-1 font-mono text-[10.5px]",
              v.source === "built-in" ? "bg-panel-2 text-muted" : "bg-blue-500/10 text-blue-700 dark:text-blue-300",
            )}
          >
            {v.name}
          </button>
        ))}
        {vars.length > 10 && <span className="text-[10.5px] text-subtle">+{vars.length - 10}</span>}
      </div>
      {unknown.length > 0 && (
        <div className="mt-1 flex items-center gap-1 text-[11px] text-amber-600 dark:text-amber-400">
          <AlertTriangle className="size-3" /> Not captured upstream: {unknown.map((u) => `{{${u}}}`).join(", ")}
        </div>
      )}
    </div>
  );
}
