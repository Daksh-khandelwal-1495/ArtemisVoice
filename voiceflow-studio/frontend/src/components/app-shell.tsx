"use client";

import { AlertTriangle, AudioWaveform, Database, LayoutGrid, Moon, Sun, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";
import useSWR, { SWRConfig } from "swr";
import { API_URL, MOCK, fetcher } from "@/lib/api";
import type { Health } from "@/lib/types";
import { useIsDark } from "@/lib/use-theme";
import { ConfirmProvider, ToastProvider, cn } from "./ui";

export const ENV_KEYS: Record<string, string> = {
  deepgram: "DEEPGRAM_API_KEY",
  openai: "OPENAI_API_KEY",
  soniox: "SONIOX_API_KEY",
};

function ThemeToggle() {
  const dark = useIsDark();
  const toggle = () => {
    const next = !dark;
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("vf-theme", next ? "dark" : "light");
    } catch {}
  };
  return (
    <button
      onClick={toggle}
      className="inline-flex size-7 items-center justify-center rounded-md text-muted hover:bg-panel-2 hover:text-fg"
      title="Toggle theme"
      aria-label="Toggle theme"
    >
      {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </button>
  );
}

export function HealthBanner() {
  const { data, error } = useSWR<Health>("/health", fetcher, { refreshInterval: 15000, shouldRetryOnError: true, errorRetryInterval: 8000 });
  const [dismissed, setDismissed] = useState(false);
  if (error)
    return (
      <div className="flex items-center gap-2 border-b border-red-500/30 bg-red-500/10 px-4 py-1.5 text-[12px] text-red-700 dark:text-red-300">
        <AlertTriangle className="size-3.5" />
        Backend unreachable at <code className="font-mono">{API_URL}</code>. Start it with
        <code className="rounded bg-red-500/10 px-1 font-mono">cd backend && uv run uvicorn app.main:app --port 8000</code>
        or run the UI with <code className="font-mono">NEXT_PUBLIC_MOCK=1</code>.
      </div>
    );
  if (!data || dismissed) return null;
  const missing = Object.entries(data.providers ?? {})
    .filter(([, ok]) => !ok)
    .map(([p]) => ENV_KEYS[p] ?? p);
  if (data.db === false || missing.length)
    return (
      <div className="flex items-center gap-2 border-b border-amber-500/30 bg-amber-500/10 px-4 py-1.5 text-[12px] text-amber-800 dark:text-amber-300">
        <AlertTriangle className="size-3.5 shrink-0" />
        <span className="flex-1">
          {data.db === false && <b className="mr-2">Database is not connected.</b>}
          {missing.length > 0 && (
            <>
              Missing provider keys: {missing.map((m) => <code key={m} className="mr-1 rounded bg-amber-500/15 px-1 font-mono">{m}</code>)}— voice calls using
              these providers will fail.
            </>
          )}
        </span>
        <button onClick={() => setDismissed(true)} aria-label="Dismiss" className="opacity-70 hover:opacity-100">
          <X className="size-3.5" />
        </button>
      </div>
    );
  return null;
}

function NavLink({ href, icon, children }: { href: string; icon: ReactNode; children: ReactNode }) {
  const path = usePathname();
  const active = href === "/" ? path === "/" || path.startsWith("/bots") || path.startsWith("/sessions") : path.startsWith(href);
  return (
    <Link
      href={href}
      className={cn(
        "flex h-7 items-center gap-1.5 rounded-md px-2 text-[13px] font-medium",
        active ? "bg-panel-2 text-fg" : "text-muted hover:text-fg",
      )}
    >
      {icon}
      {children}
    </Link>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <SWRConfig value={{ fetcher, revalidateOnFocus: false }}>
      <ToastProvider>
        <ConfirmProvider>
          <div className="flex h-full min-h-screen flex-col">
            <header className="flex h-11 shrink-0 items-center gap-4 border-b border-border bg-panel px-4">
              <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight">
                <span className="flex size-6 items-center justify-center rounded-md bg-gradient-to-br from-indigo-500 to-violet-600 text-white">
                  <AudioWaveform className="size-3.5" />
                </span>
                VoiceFlow Studio
              </Link>
              <nav className="flex items-center gap-1">
                <NavLink href="/" icon={<LayoutGrid className="size-3.5" />}>
                  Bots
                </NavLink>
                <NavLink href="/datasets" icon={<Database className="size-3.5" />}>
                  Datasets
                </NavLink>
              </nav>
              <div className="ml-auto flex items-center gap-2">
                {MOCK && (
                  <span className="rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-px text-[11px] font-medium text-amber-700 dark:text-amber-400">
                    MOCK MODE
                  </span>
                )}
                <ThemeToggle />
              </div>
            </header>
            <HealthBanner />
            <main className="flex min-h-0 flex-1 flex-col">{children}</main>
          </div>
        </ConfirmProvider>
      </ToastProvider>
    </SWRConfig>
  );
}
