"use client";

import { Loader2, X } from "lucide-react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ComponentProps,
  type ReactNode,
  type SelectHTMLAttributes,
} from "react";

export function cn(...c: (string | false | null | undefined)[]) {
  return c.filter(Boolean).join(" ");
}

/* ------------------------------------------------------------------ Button */

type BtnVariant = "primary" | "secondary" | "ghost" | "danger" | "outline";
const btnVariants: Record<BtnVariant, string> = {
  primary: "bg-brand text-brand-fg hover:opacity-90 shadow-sm",
  secondary: "bg-panel-2 text-fg hover:bg-border border border-border",
  outline: "bg-panel text-fg border border-border hover:bg-panel-2",
  ghost: "text-muted hover:text-fg hover:bg-panel-2",
  danger: "bg-red-600 text-white hover:bg-red-500",
};

export function Button({
  variant = "secondary",
  size = "md",
  loading,
  className,
  children,
  disabled,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: BtnVariant; size?: "xs" | "sm" | "md"; loading?: boolean }) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition-colors disabled:opacity-50 disabled:pointer-events-none whitespace-nowrap",
        size === "xs" && "h-6 px-1.5 text-[11px]",
        size === "sm" && "h-7 px-2 text-xs",
        size === "md" && "h-8 px-3 text-[13px]",
        btnVariants[variant],
        className,
      )}
      disabled={disabled || loading}
      {...rest}
    >
      {loading && <Loader2 className="size-3.5 animate-spin" />}
      {children}
    </button>
  );
}

export function IconButton({
  className,
  title,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { title: string }) {
  return (
    <button
      title={title}
      aria-label={title}
      className={cn(
        "inline-flex size-7 items-center justify-center rounded-md text-muted hover:bg-panel-2 hover:text-fg disabled:opacity-40",
        className,
      )}
      {...rest}
    />
  );
}

/* ------------------------------------------------------------------ Inputs */

const hasWidth = (c?: string) => !!c && /(^|\s)(w-|flex-1)/.test(c);
const inputBase =
  "rounded-md border border-border bg-panel px-2 text-[13px] text-fg placeholder:text-subtle focus:border-brand focus:outline-none focus:ring-2 focus:ring-[var(--ring)] disabled:opacity-60";

export function Input({ className, invalid, ...rest }: ComponentProps<"input"> & { invalid?: boolean }) {
  return <input className={cn(inputBase, !hasWidth(className) && "w-full", "h-8", invalid && "border-red-500", className)} {...rest} />;
}

export function Textarea({
  className,
  invalid,
  ...rest
}: ComponentProps<"textarea"> & { invalid?: boolean }) {
  return <textarea className={cn(inputBase, !hasWidth(className) && "w-full", "py-1.5 leading-relaxed", invalid && "border-red-500", className)} {...rest} />;
}

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cn(inputBase, !hasWidth(className) && "w-full", "h-8 pr-6", className)} {...rest}>
      {children}
    </select>
  );
}

export function Switch({
  checked,
  onChange,
  disabled,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  label?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-4.5 w-8 shrink-0 items-center rounded-full transition-colors disabled:opacity-50",
        checked ? "bg-brand" : "bg-border-strong",
      )}
    >
      <span
        className={cn(
          "inline-block size-3.5 rounded-full bg-white shadow transition-transform",
          checked ? "translate-x-4" : "translate-x-0.5",
        )}
      />
    </button>
  );
}

export function Field({
  label,
  hint,
  error,
  children,
  className,
  inline,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  children: ReactNode;
  className?: string;
  inline?: boolean;
}) {
  if (inline)
    return (
      <div className={cn("flex items-center justify-between gap-3 py-1", className)}>
        <div className="min-w-0">
          <div className="text-[12px] font-medium text-fg">{label}</div>
          {hint && <div className="text-[11px] text-muted">{hint}</div>}
          {error && <div className="text-[11px] text-red-500">{error}</div>}
        </div>
        {children}
      </div>
    );
  return (
    <label className={cn("block space-y-1", className)}>
      <div className="text-[11px] font-medium uppercase tracking-wide text-muted">{label}</div>
      {children}
      {hint && !error && <div className="text-[11px] text-muted">{hint}</div>}
      {error && <div className="text-[11px] text-red-500">{error}</div>}
    </label>
  );
}

export function Slider({
  value,
  min,
  max,
  step,
  onChange,
  format,
}: {
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  format?: (v: number) => string;
}) {
  return (
    <div className="flex items-center gap-2">
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="h-1 flex-1 cursor-pointer accent-[var(--brand)]"
      />
      <span className="w-14 text-right font-mono text-[12px] tabular-nums text-fg">{format ? format(value) : value}</span>
    </div>
  );
}

/* ------------------------------------------------------------------- Badges */

const tones = {
  gray: "bg-panel-2 text-muted border-border",
  green: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
  red: "bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/20",
  amber: "bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/20",
  blue: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20",
  violet: "bg-violet-500/10 text-violet-600 dark:text-violet-400 border-violet-500/20",
  brand: "bg-brand/10 text-brand border-brand/20",
};
export type Tone = keyof typeof tones;

export function Badge({ tone = "gray", children, className, title }: { tone?: Tone; children: ReactNode; className?: string; title?: string }) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-1.5 py-px text-[11px] font-medium leading-4 whitespace-nowrap",
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

export const statusTone = (s: string | null | undefined): Tone =>
  s === "published" || s === "completed"
    ? "green"
    : s === "active"
      ? "blue"
      : s === "transferred"
        ? "violet"
        : s === "error"
          ? "red"
          : s === "abandoned"
            ? "amber"
            : "gray";

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("size-4 animate-spin text-muted", className)} />;
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cn("rounded-lg border border-border bg-panel", className)}>{children}</div>;
}

export function EmptyState({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-14 text-center">
      {icon && <div className="text-subtle">{icon}</div>}
      <div className="text-sm font-medium">{title}</div>
      {children && <div className="max-w-sm text-[12px] text-muted">{children}</div>}
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border border-red-500/30 bg-red-500/5 px-3 py-2 text-[12px] text-red-600 dark:text-red-400">
      <span>{msg}</span>
      {onRetry && (
        <Button size="sm" variant="outline" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}

export function SectionTitle({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-2 flex items-center justify-between">
      <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted">{children}</h3>
      {right}
    </div>
  );
}

/* ------------------------------------------------------------------ Dialog */

export function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
  width = "max-w-lg",
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  width?: string;
}) {
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 pt-[12vh] backdrop-blur-[2px]" onMouseDown={onClose}>
      <div
        className={cn("w-full rounded-xl border border-border bg-panel shadow-2xl", width)}
        onMouseDown={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal
      >
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div className="text-sm font-semibold">{title}</div>
          <IconButton title="Close" onClick={onClose}>
            <X className="size-4" />
          </IconButton>
        </div>
        <div className="max-h-[65vh] overflow-y-auto p-4 thin-scroll">{children}</div>
        {footer && <div className="flex justify-end gap-2 border-t border-border px-4 py-3">{footer}</div>}
      </div>
    </div>
  );
}

/** Slide-over drawer from the right. */
export function Drawer({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/25" onMouseDown={onClose}>
      <div className="flex h-full w-[480px] max-w-full flex-col border-l border-border bg-panel shadow-2xl" onMouseDown={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div className="text-sm font-semibold">{title}</div>
          <IconButton title="Close" onClick={onClose}>
            <X className="size-4" />
          </IconButton>
        </div>
        <div className="flex-1 overflow-y-auto p-4 thin-scroll">{children}</div>
      </div>
    </div>
  );
}

/* --------------------------------------------------------- Confirm dialog */

type ConfirmOpts = { title: string; message?: ReactNode; confirmLabel?: string; danger?: boolean };
const ConfirmCtx = createContext<(o: ConfirmOpts) => Promise<boolean>>(async () => false);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [opts, setOpts] = useState<ConfirmOpts | null>(null);
  const resolver = useRef<(v: boolean) => void>(() => {});
  const confirm = useCallback((o: ConfirmOpts) => {
    setOpts(o);
    return new Promise<boolean>((res) => (resolver.current = res));
  }, []);
  const close = (v: boolean) => {
    resolver.current(v);
    setOpts(null);
  };
  return (
    <ConfirmCtx.Provider value={confirm}>
      {children}
      <Dialog
        open={!!opts}
        onClose={() => close(false)}
        title={opts?.title}
        width="max-w-sm"
        footer={
          <>
            <Button variant="outline" onClick={() => close(false)}>
              Cancel
            </Button>
            <Button variant={opts?.danger ? "danger" : "primary"} onClick={() => close(true)} autoFocus>
              {opts?.confirmLabel ?? "Confirm"}
            </Button>
          </>
        }
      >
        <div className="text-[13px] text-muted">{opts?.message}</div>
      </Dialog>
    </ConfirmCtx.Provider>
  );
}

export const useConfirm = () => useContext(ConfirmCtx);

/* ------------------------------------------------------------------ Toasts */

type Toast = { id: number; text: ReactNode; tone: "info" | "success" | "error" };
const ToastCtx = createContext<(text: ReactNode, tone?: Toast["tone"]) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((text: ReactNode, tone: Toast["tone"] = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4200);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={cn(
              "pointer-events-auto max-w-sm rounded-lg border px-3 py-2 text-[13px] shadow-lg",
              t.tone === "error" && "border-red-500/30 bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300",
              t.tone === "success" && "border-emerald-500/30 bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
              t.tone === "info" && "border-border bg-panel text-fg",
            )}
          >
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);

/* --------------------------------------------------------------- Tabs bar */

export function Tabs<T extends string>({
  value,
  onChange,
  items,
  className,
}: {
  value: T;
  onChange: (v: T) => void;
  items: { value: T; label: ReactNode; icon?: ReactNode; badge?: ReactNode }[];
  className?: string;
}) {
  return (
    <div className={cn("flex items-center gap-0.5", className)} role="tablist">
      {items.map((it) => (
        <button
          key={it.value}
          role="tab"
          aria-selected={value === it.value}
          onClick={() => onChange(it.value)}
          className={cn(
            "relative flex h-9 items-center gap-1.5 px-3 text-[13px] font-medium transition-colors",
            value === it.value ? "text-fg" : "text-muted hover:text-fg",
          )}
        >
          {it.icon}
          {it.label}
          {it.badge}
          {value === it.value && <span className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-brand" />}
        </button>
      ))}
    </div>
  );
}

export function Segmented<T extends string>({
  value,
  onChange,
  items,
}: {
  value: T;
  onChange: (v: T) => void;
  items: { value: T; label: ReactNode; disabled?: boolean }[];
}) {
  return (
    <div className="inline-flex rounded-md border border-border bg-panel-2 p-0.5">
      {items.map((it) => (
        <button
          key={it.value}
          type="button"
          disabled={it.disabled}
          onClick={() => onChange(it.value)}
          className={cn(
            "h-6 rounded px-2.5 text-[12px] font-medium disabled:opacity-40",
            value === it.value ? "bg-panel text-fg shadow-sm" : "text-muted hover:text-fg",
          )}
        >
          {it.label}
        </button>
      ))}
    </div>
  );
}
