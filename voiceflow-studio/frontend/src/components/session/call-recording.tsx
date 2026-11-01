"use client";

import { AudioLines, Download } from "lucide-react";
import { useState } from "react";
import { Card } from "@/components/ui";
import { API_URL } from "@/lib/api";

/** Whole-conversation player: the backend stitches each turn's caller + bot audio into one WAV. */
export function CallRecording({ sessionId, mode }: { sessionId: string; mode: string }) {
  const [missing, setMissing] = useState(false);
  const src = `${API_URL}/api/sessions/${encodeURIComponent(sessionId)}/recording.wav`;
  const simulated = mode === "simulation" || mode === "replay";
  return (
    <Card className="mb-4 p-4">
      <div className="mb-2 flex items-center gap-2">
        <AudioLines className="size-4 text-brand" />
        <h2 className="text-[13px] font-semibold">Call recording</h2>
        <span className="text-[11.5px] text-muted">
          {simulated ? "· synthesized voices (simulation)" : "· caller and bot audio, in turn order"}
        </span>
        {!missing && (
          <a href={src} download={`call-${sessionId.slice(0, 8)}.wav`} className="ml-auto inline-flex items-center gap-1 text-[12px] text-muted hover:text-brand">
            <Download className="size-3.5" /> Download WAV
          </a>
        )}
      </div>
      {missing ? (
        <div className="text-[12.5px] text-muted">No audio recorded for this session.</div>
      ) : (
        <audio controls preload="metadata" src={src} onError={() => setMissing(true)} className="w-full" />
      )}
    </Card>
  );
}
