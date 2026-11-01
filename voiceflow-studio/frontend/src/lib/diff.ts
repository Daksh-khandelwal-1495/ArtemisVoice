export type DiffOp = { op: "same" | "del" | "add"; text: string };

/** Word-level LCS diff (inputs are short utterances, O(n·m) is fine). Whitespace tokens are kept. */
export function wordDiff(a: string, b: string): DiffOp[] {
  const A = a.split(/(\s+)/).filter((t) => t !== "");
  const B = b.split(/(\s+)/).filter((t) => t !== "");
  const n = A.length;
  const m = B.length;
  const L: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) L[i][j] = A[i] === B[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const out: DiffOp[] = [];
  const push = (op: DiffOp["op"], text: string) => {
    const last = out[out.length - 1];
    if (last && last.op === op) last.text += text;
    else out.push({ op, text });
  };
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (A[i] === B[j]) {
      push("same", A[i++]);
      j++;
    } else if (L[i + 1][j] >= L[i][j + 1]) push("del", A[i++]);
    else push("add", B[j++]);
  }
  while (i < n) push("del", A[i++]);
  while (j < m) push("add", B[j++]);
  return out;
}

export interface StateChange {
  key: string;
  before: unknown;
  after: unknown;
  kind: "added" | "removed" | "changed" | "same";
}

export function stateDiff(before: Record<string, unknown> = {}, after: Record<string, unknown> = {}): StateChange[] {
  const keys = Array.from(new Set([...Object.keys(before), ...Object.keys(after)]));
  return keys.map((key) => {
    const inB = key in before;
    const inA = key in after;
    const same = JSON.stringify(before[key]) === JSON.stringify(after[key]);
    return { key, before: before[key], after: after[key], kind: !inB ? "added" : !inA ? "removed" : same ? "same" : "changed" };
  });
}
