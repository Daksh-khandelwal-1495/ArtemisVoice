"use client";

import { ArrowDown, ArrowUp, Check, ChevronDown, ChevronRight, Lock, Plus, Trash2, X } from "lucide-react";
import { useState } from "react";
import { Button, Field, IconButton, Input, Select, Switch, Textarea, cn } from "@/components/ui";
import { ChipsInput } from "@/components/ui/editors";
import { newField } from "@/lib/defaults";
import { FIELD_TYPES, SLUG_RE, type CaptureField, type FieldValidation } from "@/lib/types";

const num = (v: string) => (v === "" ? null : Number(v));

function RegexTester({ regex }: { regex: string }) {
  const [sample, setSample] = useState("");
  let re: RegExp | null = null;
  let err: string | null = null;
  try {
    re = new RegExp(regex);
  } catch (e) {
    err = (e as Error).message;
  }
  if (err) return <div className="text-[11px] text-red-500">Invalid regex: {err}</div>;
  const ok = sample ? re!.test(sample) : null;
  return (
    <div className="flex items-center gap-1.5">
      <Input value={sample} onChange={(e) => setSample(e.target.value)} placeholder="Test a value…" className="h-7 text-[12px]" />
      {ok === true && <Check className="size-4 shrink-0 text-emerald-500" />}
      {ok === false && <X className="size-4 shrink-0 text-red-500" />}
    </div>
  );
}

function FieldRow({
  field,
  onChange,
  onDelete,
  onMove,
  first,
  last,
  duplicate,
  defaultOpen,
}: {
  field: CaptureField;
  onChange: (f: CaptureField) => void;
  onDelete: () => void;
  onMove: (d: -1 | 1) => void;
  first: boolean;
  last: boolean;
  duplicate: boolean;
  defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [name, setName] = useState(field.name);
  const [lastName, setLastName] = useState(field.name);
  if (field.name !== lastName) {
    setLastName(field.name);
    setName(field.name);
  }
  const v = field.validation;
  const setV = (patch: Partial<FieldValidation>) => onChange({ ...field, validation: { ...v, ...patch } });
  const nameErr = !SLUG_RE.test(name) ? "snake_case, starts with a letter" : duplicate ? "Duplicate field name" : null;
  const enumErr = field.type === "enum" && !(v.options ?? []).length;

  return (
    <div className={cn("rounded-md border bg-panel", nameErr || enumErr ? "border-red-500/50" : "border-border")}>
      <div className="flex items-center gap-1 px-1.5 py-1">
        <button type="button" onClick={() => setOpen(!open)} className="flex min-w-0 flex-1 items-center gap-1.5 text-left">
          {open ? <ChevronDown className="size-3.5 text-muted" /> : <ChevronRight className="size-3.5 text-muted" />}
          <span className="truncate font-mono text-[12px] font-medium">{field.name}</span>
          {field.required && <span className="text-red-500">*</span>}
          {field.sensitive && <Lock className="size-3 text-muted" />}
          <span className="ml-auto rounded bg-panel-2 px-1 text-[10.5px] text-muted">{field.type}</span>
        </button>
        <IconButton title="Move up" disabled={first} onClick={() => onMove(-1)} className="size-6">
          <ArrowUp className="size-3" />
        </IconButton>
        <IconButton title="Move down" disabled={last} onClick={() => onMove(1)} className="size-6">
          <ArrowDown className="size-3" />
        </IconButton>
        <IconButton title="Delete field" onClick={onDelete} className="size-6 hover:text-red-500">
          <Trash2 className="size-3" />
        </IconButton>
      </div>
      {open && (
        <div className="space-y-2.5 border-t border-border p-2.5">
          <div className="grid grid-cols-2 gap-2">
            <Field label="Name" error={nameErr}>
              <Input
                value={name}
                invalid={!!nameErr}
                className="font-mono text-[12px]"
                onChange={(e) => {
                  setName(e.target.value);
                  if (SLUG_RE.test(e.target.value)) onChange({ ...field, name: e.target.value });
                }}
              />
            </Field>
            <Field label="Label">
              <Input value={field.label} onChange={(e) => onChange({ ...field, label: e.target.value })} />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Type">
              <Select value={field.type} onChange={(e) => onChange({ ...field, type: e.target.value as CaptureField["type"] })}>
                {FIELD_TYPES.map((t) => (
                  <option key={t}>{t}</option>
                ))}
              </Select>
            </Field>
            <Field label="Max retries">
              <Input type="number" min={0} max={10} value={field.max_retries} onChange={(e) => onChange({ ...field, max_retries: Number(e.target.value) })} />
            </Field>
          </div>
          <Field label="Description" hint="Shown to the LLM to explain what to capture">
            <Textarea rows={2} value={field.description} onChange={(e) => onChange({ ...field, description: e.target.value })} />
          </Field>
          <Field label="Example">
            <Input value={field.example ?? ""} onChange={(e) => onChange({ ...field, example: e.target.value || null })} placeholder="e.g. MH12AB1234" />
          </Field>
          <div className="grid grid-cols-3 gap-2 rounded-md bg-panel-2 px-2 py-1.5">
            <label className="flex items-center gap-1.5 text-[12px]">
              <Switch checked={field.required} onChange={(c) => onChange({ ...field, required: c })} label="Required" /> Required
            </label>
            <label className="flex items-center gap-1.5 text-[12px]" title="Bot reads the value back and asks for a yes">
              <Switch checked={field.confirm} onChange={(c) => onChange({ ...field, confirm: c })} label="Confirm" /> Confirm
            </label>
            <label className="flex items-center gap-1.5 text-[12px]" title="Masked in logs and the UI">
              <Switch checked={field.sensitive} onChange={(c) => onChange({ ...field, sensitive: c })} label="Sensitive" /> Sensitive
            </label>
          </div>

          <div className="space-y-2 rounded-md border border-dashed border-border p-2">
            <div className="text-[10.5px] font-semibold uppercase tracking-wider text-muted">Validation</div>
            {field.type === "enum" && (
              <Field label="Options" error={enumErr && "Enum fields need at least one option"}>
                <ChipsInput value={v.options ?? []} onChange={(o) => setV({ options: o })} placeholder="Add option, Enter" />
              </Field>
            )}
            {field.type !== "boolean" && field.type !== "enum" && (
              <>
                <Field label="Regex">
                  <Input value={v.regex ?? ""} onChange={(e) => setV({ regex: e.target.value || null })} placeholder="^[A-Z]{2}\d{2}" className="font-mono text-[12px]" />
                </Field>
                {v.regex && <RegexTester regex={v.regex} />}
                <div className="grid grid-cols-2 gap-2">
                  <Field label={["number", "integer", "amount"].includes(field.type) ? "Min" : "Min length"}>
                    <Input type="number" value={v.min ?? ""} onChange={(e) => setV({ min: num(e.target.value) })} />
                  </Field>
                  <Field label={["number", "integer", "amount"].includes(field.type) ? "Max" : "Max length"} error={v.min != null && v.max != null && v.min > v.max && "min > max"}>
                    <Input type="number" value={v.max ?? ""} onChange={(e) => setV({ max: num(e.target.value) })} />
                  </Field>
                </div>
              </>
            )}
            {field.type === "date" && (
              <div className="flex gap-4">
                <label className="flex items-center gap-1.5 text-[12px]">
                  <Switch checked={!!v.future_only} onChange={(c) => setV({ future_only: c, past_only: c ? false : v.past_only })} /> Future only
                </label>
                <label className="flex items-center gap-1.5 text-[12px]">
                  <Switch checked={!!v.past_only} onChange={(c) => setV({ past_only: c, future_only: c ? false : v.future_only })} /> Past only
                </label>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export function FieldsEditor({
  fields,
  onChange,
  otherFieldNames,
}: {
  fields: CaptureField[];
  onChange: (f: CaptureField[]) => void;
  otherFieldNames: string[];
}) {
  const [lastAdded, setLastAdded] = useState<string | null>(null);
  const names = fields.map((f) => f.name);
  return (
    <div className="space-y-1.5">
      {fields.map((f, i) => (
        <FieldRow
          key={`${i}-${f.name === lastAdded ? "new" : "f"}`}
          field={f}
          first={i === 0}
          last={i === fields.length - 1}
          defaultOpen={f.name === lastAdded}
          duplicate={names.filter((n) => n === f.name).length > 1}
          onChange={(nf) => onChange(fields.map((x, j) => (j === i ? nf : x)))}
          onDelete={() => onChange(fields.filter((_, j) => j !== i))}
          onMove={(d) => {
            const next = [...fields];
            [next[i], next[i + d]] = [next[i + d], next[i]];
            onChange(next);
          }}
        />
      ))}
      {fields.length === 0 && <div className="rounded-md border border-dashed border-red-500/40 p-3 text-center text-[12px] text-red-500">Collect nodes need at least one field.</div>}
      <Button
        size="sm"
        variant="outline"
        onClick={() => {
          const f = newField([...names, ...otherFieldNames]);
          setLastAdded(f.name);
          onChange([...fields, f]);
        }}
      >
        <Plus className="size-3.5" /> Add field
      </Button>
    </div>
  );
}
