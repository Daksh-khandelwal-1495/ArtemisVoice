"use client";

import { AudioLines, Brain, Ear, KeyRound, Mic, Save, SlidersHorizontal, Undo2 } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import useSWR from "swr";
import { ENV_KEYS } from "@/components/app-shell";
import { Button, Card, ErrorBox, Field, Input, Select, Slider, Spinner, Switch, Textarea, cn, useToast } from "@/components/ui";
import { ChipsInput } from "@/components/ui/editors";
import { api, fieldErrors } from "@/lib/api";
import { mergeConfig } from "@/lib/defaults";
import type { BotConfig, BotDetail, ProviderCatalog, ProviderEntry } from "@/lib/types";

function Panel({ icon, title, subtitle, children }: { icon: ReactNode; title: string; subtitle?: string; children: ReactNode }) {
  return (
    <Card className="p-4">
      <div className="mb-3 flex items-center gap-2">
        <span className="flex size-7 items-center justify-center rounded-md bg-brand/10 text-brand">{icon}</span>
        <div>
          <div className="text-[13px] font-semibold">{title}</div>
          {subtitle && <div className="text-[11.5px] text-muted">{subtitle}</div>}
        </div>
      </div>
      <div className="space-y-3">{children}</div>
    </Card>
  );
}

function ProviderCards({ entries, value, onSelect }: { entries: ProviderEntry[]; value: string; onSelect: (p: ProviderEntry) => void }) {
  return (
    <div className="grid grid-cols-3 gap-2">
      {entries.map((p) => {
        const sel = p.id === value;
        return (
          <button
            key={p.id}
            type="button"
            disabled={!p.configured && !sel}
            onClick={() => onSelect(p)}
            title={!p.configured ? `Set ${ENV_KEYS[p.id] ?? "API key"} in backend/.env` : undefined}
            className={cn(
              "rounded-lg border p-2.5 text-left transition-colors disabled:cursor-not-allowed",
              sel ? "border-brand bg-brand/5 ring-2 ring-[var(--ring)]" : "border-border hover:bg-panel-2",
              !p.configured && "opacity-60",
            )}
          >
            <div className="text-[13px] font-medium">{p.name}</div>
            <div className="mt-0.5 text-[11px] text-muted">{p.models.length} models</div>
            {!p.configured && (
              <div className="mt-1 flex items-center gap-1 text-[10.5px] font-medium text-amber-600 dark:text-amber-400">
                <KeyRound className="size-3" /> key missing
              </div>
            )}
          </button>
        );
      })}
    </div>
  );
}

export function SettingsTab({ bot, onSaved }: { bot: BotDetail; onSaved: () => void }) {
  const toast = useToast();
  const { data: catalog, error: catErr } = useSWR<ProviderCatalog>("providers", api.providers);
  const original = useMemo(() => mergeConfig(bot.config), [bot.config]);
  const [cfg, setCfg] = useState<BotConfig>(original);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const dirty = JSON.stringify(cfg) !== JSON.stringify(original);

  const set = <K extends keyof BotConfig>(k: K, patch: Partial<BotConfig[K]>) => setCfg((c) => ({ ...c, [k]: { ...c[k], ...patch } }));
  const err = (path: string) => errors[path];

  const save = async () => {
    setBusy(true);
    setErrors({});
    try {
      await api.patchBot(bot.id, { config: cfg });
      toast("Settings saved", "success");
      onSaved();
    } catch (e) {
      const fe = fieldErrors(e);
      setErrors(fe);
      toast(Object.keys(fe).length ? `Invalid settings: ${Object.entries(fe).map(([k, v]) => `${k} ${v}`).join("; ")}` : (e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  if (catErr) return <div className="p-6"><ErrorBox error={catErr} /></div>;
  if (!catalog)
    return (
      <div className="flex flex-1 items-center justify-center gap-2 text-muted">
        <Spinner /> Loading providers…
      </div>
    );

  const stt = catalog.stt.find((p) => p.id === cfg.stt.provider);
  const llm = catalog.llm.find((p) => p.id === cfg.llm.provider);
  const tts = catalog.tts.find((p) => p.id === cfg.tts.provider);
  const opts = (list: string[] | undefined, current: string) => [...new Set([...(list ?? []), current].filter(Boolean))];

  return (
    <div className="flex-1 overflow-y-auto thin-scroll">
      <div className="sticky top-0 z-10 flex items-center justify-between border-b border-border bg-bg/90 px-6 py-2 backdrop-blur">
        <div className="text-[12px] text-muted">Provider & behaviour configuration for <b className="text-fg">{bot.name}</b>. Applies to new calls.</div>
        <div className="flex gap-2">
          <Button size="sm" variant="ghost" disabled={!dirty} onClick={() => (setCfg(original), setErrors({}))}>
            <Undo2 className="size-3.5" /> Discard
          </Button>
          <Button size="sm" variant="primary" disabled={!dirty} loading={busy} onClick={save}>
            <Save className="size-3.5" /> Save settings
          </Button>
        </div>
      </div>
      <div className="mx-auto grid max-w-6xl grid-cols-1 gap-4 p-6 lg:grid-cols-2">
        <Panel icon={<Ear className="size-4" />} title="Speech-to-text" subtitle="Transcribes the caller">
          <ProviderCards entries={catalog.stt} value={cfg.stt.provider} onSelect={(p) => set("stt", { provider: p.id as BotConfig["stt"]["provider"], model: p.models[0], language: p.languages?.includes(cfg.stt.language) ? cfg.stt.language : (p.languages?.[0] ?? cfg.stt.language) })} />
          <div className="grid grid-cols-2 gap-3">
            <Field label="Model" error={err("stt.model")}>
              <Select value={cfg.stt.model} onChange={(e) => set("stt", { model: e.target.value })}>
                {opts(stt?.models, cfg.stt.model).map((m) => (
                  <option key={m}>{m}</option>
                ))}
              </Select>
            </Field>
            <Field label="Language" error={err("stt.language")}>
              <Select value={cfg.stt.language} onChange={(e) => set("stt", { language: e.target.value })}>
                {opts(stt?.languages, cfg.stt.language).map((m) => (
                  <option key={m}>{m}</option>
                ))}
              </Select>
            </Field>
          </div>
          <Field label="Keywords" hint="Boost brand & product words (e.g. Nexon, Seltos)" error={err("stt.keywords")}>
            <ChipsInput value={cfg.stt.keywords} onChange={(k) => set("stt", { keywords: k })} />
          </Field>
        </Panel>

        <Panel icon={<Brain className="size-4" />} title="Language model" subtitle="Drives the conversation">
          <ProviderCards entries={catalog.llm} value={cfg.llm.provider} onSelect={(p) => set("llm", { provider: p.id as "openai", model: p.models[0] })} />
          <Field label="Model" error={err("llm.model")}>
            <Select value={cfg.llm.model} onChange={(e) => set("llm", { model: e.target.value })}>
              {opts(llm?.models, cfg.llm.model).map((m) => (
                <option key={m}>{m}</option>
              ))}
            </Select>
          </Field>
          <Field label="Temperature" error={err("llm.temperature")}>
            <Slider value={cfg.llm.temperature} min={0} max={2} step={0.05} onChange={(v) => set("llm", { temperature: v })} format={(v) => v.toFixed(2)} />
          </Field>
          <Field label="Max tokens" error={err("llm.max_tokens")}>
            <Slider value={cfg.llm.max_tokens ?? 300} min={16} max={4096} step={16} onChange={(v) => set("llm", { max_tokens: v })} />
          </Field>
          <Field label="Base URL" hint="Optional OpenAI-compatible endpoint" error={err("llm.base_url")}>
            <Input value={cfg.llm.base_url ?? ""} onChange={(e) => set("llm", { base_url: e.target.value || null })} placeholder="https://api.openai.com/v1" className="font-mono text-[12px]" />
          </Field>
        </Panel>

        <Panel icon={<AudioLines className="size-4" />} title="Text-to-speech" subtitle="The bot's voice">
          <ProviderCards
            entries={catalog.tts}
            value={cfg.tts.provider}
            onSelect={(p) => set("tts", { provider: p.id as BotConfig["tts"]["provider"], model: p.models[0], voice: p.voices?.[0] ?? cfg.tts.voice, language: p.languages?.includes(cfg.tts.language) ? cfg.tts.language : (p.languages?.[0] ?? "en") })}
          />
          <div className="grid grid-cols-3 gap-3">
            <Field label="Model" error={err("tts.model")}>
              <Select value={cfg.tts.model} onChange={(e) => set("tts", { model: e.target.value })}>
                {opts(tts?.models, cfg.tts.model).map((m) => (
                  <option key={m}>{m}</option>
                ))}
              </Select>
            </Field>
            <Field label="Voice" error={err("tts.voice")}>
              <Select value={cfg.tts.voice} onChange={(e) => set("tts", { voice: e.target.value })}>
                {opts(tts?.voices, cfg.tts.voice).map((m) => (
                  <option key={m}>{m}</option>
                ))}
              </Select>
            </Field>
            <Field label="Language" error={err("tts.language")}>
              <Select value={cfg.tts.language} onChange={(e) => set("tts", { language: e.target.value })}>
                {opts(tts?.languages, cfg.tts.language).map((m) => (
                  <option key={m}>{m}</option>
                ))}
              </Select>
            </Field>
          </div>
          <Field label="Speed" error={err("tts.speed")}>
            <Slider value={cfg.tts.speed} min={0.5} max={2} step={0.05} onChange={(v) => set("tts", { speed: v })} format={(v) => `${v.toFixed(2)}×`} />
          </Field>
          <Field label="Sample rate" error={err("tts.sample_rate")}>
            <Select value={String(cfg.tts.sample_rate ?? "")} onChange={(e) => set("tts", { sample_rate: e.target.value ? Number(e.target.value) : null })}>
              <option value="">provider default</option>
              {[8000, 16000, 22050, 24000, 44100, 48000].map((r) => (
                <option key={r} value={r}>
                  {r} Hz
                </option>
              ))}
            </Select>
          </Field>
        </Panel>

        <Panel icon={<Mic className="size-4" />} title="Voice activity detection" subtitle="Turn-taking sensitivity (Silero VAD)">
          <Field label="Stop after silence (s)" hint="How long the caller must pause before the bot replies" error={err("vad.stop_secs")}>
            <Slider value={cfg.vad.stop_secs} min={0.1} max={3} step={0.05} onChange={(v) => set("vad", { stop_secs: v })} format={(v) => `${v.toFixed(2)}s`} />
          </Field>
          <Field label="Start speech after (s)" error={err("vad.start_secs")}>
            <Slider value={cfg.vad.start_secs} min={0.05} max={2} step={0.05} onChange={(v) => set("vad", { start_secs: v })} format={(v) => `${v.toFixed(2)}s`} />
          </Field>
          <Field label="Confidence" error={err("vad.confidence")}>
            <Slider value={cfg.vad.confidence} min={0} max={1} step={0.05} onChange={(v) => set("vad", { confidence: v })} format={(v) => v.toFixed(2)} />
          </Field>
        </Panel>

        <div className="lg:col-span-2">
          <Panel icon={<SlidersHorizontal className="size-4" />} title="Behavior" subtitle="Call-level rules">
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <div className="space-y-3">
                <Field label="Greeting" hint="Spoken at call start unless the start node has say_on_enter" error={err("behavior.greeting")}>
                  <Textarea rows={3} value={cfg.behavior.greeting ?? ""} onChange={(e) => set("behavior", { greeting: e.target.value || null })} />
                </Field>
                <Field label="Fallback message" hint="Spoken on provider errors" error={err("behavior.fallback_message")}>
                  <Textarea rows={2} value={cfg.behavior.fallback_message} onChange={(e) => set("behavior", { fallback_message: e.target.value })} />
                </Field>
              </div>
              <div className="space-y-2">
                <Field label="Idle timeout (s)" hint="User silence before a nudge" error={err("behavior.idle_timeout_secs")}>
                  <Slider value={cfg.behavior.idle_timeout_secs} min={5} max={300} step={1} onChange={(v) => set("behavior", { idle_timeout_secs: v })} format={(v) => `${v}s`} />
                </Field>
                <Field label="Max nudges before hang-up" error={err("behavior.idle_max_nudges")}>
                  <Slider value={cfg.behavior.idle_max_nudges} min={0} max={10} step={1} onChange={(v) => set("behavior", { idle_max_nudges: v })} />
                </Field>
                <Field label="Max call duration" error={err("behavior.max_call_duration_secs")}>
                  <Slider value={cfg.behavior.max_call_duration_secs} min={30} max={7200} step={30} onChange={(v) => set("behavior", { max_call_duration_secs: v })} format={(v) => `${Math.floor(v / 60)}m${v % 60 ? ` ${v % 60}s` : ""}`} />
                </Field>
                <div className="divide-y divide-border rounded-md border border-border px-3">
                  <Field inline label="Allow interruptions" hint="Caller can barge in while the bot speaks">
                    <Switch checked={cfg.behavior.allow_interruptions} onChange={(c) => set("behavior", { allow_interruptions: c })} />
                  </Field>
                  <Field inline label="Record transcript">
                    <Switch checked={cfg.behavior.record_transcript} onChange={(c) => set("behavior", { record_transcript: c })} />
                  </Field>
                  <Field inline label="Mask sensitive fields in logs">
                    <Switch checked={cfg.behavior.mask_sensitive_in_logs} onChange={(c) => set("behavior", { mask_sensitive_in_logs: c })} />
                  </Field>
                </div>
              </div>
            </div>
          </Panel>
        </div>
        {Object.keys(errors).length > 0 && (
          <div className="lg:col-span-2">
            <ErrorBox error={Object.entries(errors).map(([k, v]) => `${k}: ${v}`).join(" · ")} />
          </div>
        )}
      </div>
    </div>
  );
}
