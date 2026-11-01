"use client";

import { useCallback, useState } from "react";
import type { FlowGraph } from "./types";

interface H {
  past: FlowGraph[];
  present: FlowGraph | null;
  future: FlowGraph[];
}

const LIMIT = 100;

export type GraphUpdater = FlowGraph | ((g: FlowGraph) => FlowGraph);

/** Undo/redo history for the flow graph. `record=false` mutates the present without a history entry (e.g. mid-drag). */
export function useGraphHistory() {
  const [h, setH] = useState<H>({ past: [], present: null, future: [] });

  const set = useCallback((u: GraphUpdater, record = true) => {
    setH((s) => {
      if (!s.present) return s;
      const next = typeof u === "function" ? u(s.present) : u;
      if (next === s.present) return s;
      return record ? { past: [...s.past, s.present].slice(-LIMIT), present: next, future: [] } : { ...s, present: next };
    });
  }, []);

  /** Push the current state as an undo point (call before a series of record=false updates). */
  const checkpoint = useCallback(() => {
    setH((s) => (s.present ? { past: [...s.past, s.present].slice(-LIMIT), present: s.present, future: [] } : s));
  }, []);

  const reset = useCallback((g: FlowGraph) => setH({ past: [], present: g, future: [] }), []);

  const undo = useCallback(
    () =>
      setH((s) => {
        if (!s.past.length || !s.present) return s;
        return { past: s.past.slice(0, -1), present: s.past[s.past.length - 1], future: [s.present, ...s.future] };
      }),
    [],
  );
  const redo = useCallback(
    () =>
      setH((s) => {
        if (!s.future.length || !s.present) return s;
        return { past: [...s.past, s.present], present: s.future[0], future: s.future.slice(1) };
      }),
    [],
  );

  return { graph: h.present, set, checkpoint, reset, undo, redo, canUndo: h.past.length > 0, canRedo: h.future.length > 0 };
}

export type GraphHistory = ReturnType<typeof useGraphHistory>;
