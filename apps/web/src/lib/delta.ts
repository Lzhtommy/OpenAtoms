import type { Delta, DeltaOp } from "./types";

export function deltaToText(d: unknown): string {
  if (typeof d === "string") return d;
  if (!Array.isArray(d)) return "";
  return (d as DeltaOp[])
    .map((op) => {
      if ("insert" in op) {
        if (typeof op.insert === "string") return op.insert;
        if ("mentiontrigger" in op.insert) return `@${op.insert.mentiontrigger.value}`;
        if ("attachment" in op.insert) return `[${op.insert.attachment.name}]`;
      }
      return "";
    })
    .join("");
}

/** Append streamed delta ops: merge adjacent string inserts. */
export function appendDelta(base: unknown, add: Delta): Delta {
  const out: DeltaOp[] = Array.isArray(base) ? [...(base as DeltaOp[])] : typeof base === "string" ? [{ insert: base }] : [];
  for (const op of add) {
    const last = out[out.length - 1];
    if (last && "insert" in last && typeof last.insert === "string" && "insert" in op && typeof op.insert === "string") {
      out[out.length - 1] = { insert: last.insert + op.insert };
    } else out.push(op);
  }
  return out;
}

export type Segment =
  | { kind: "text"; text: string }
  | { kind: "mention"; role: string; name: string }
  | { kind: "action"; uuid: string }
  | { kind: "attachment"; name: string; url: string; mime?: string };

/** Group delta into renderable segments (consecutive text joined, mentions inline). */
export function segments(d: unknown): Segment[] {
  if (typeof d === "string") return [{ kind: "text", text: d }];
  if (!Array.isArray(d)) return [];
  const segs: Segment[] = [];
  for (const op of d as DeltaOp[]) {
    if ("action_data" in op) segs.push({ kind: "action", uuid: op.action_data.uuid });
    else if ("insert" in op) {
      if (typeof op.insert === "string") {
        const last = segs[segs.length - 1];
        if (last?.kind === "text") last.text += op.insert;
        else segs.push({ kind: "text", text: op.insert });
      } else if ("mentiontrigger" in op.insert) {
        // render mention inline as markdown-ish text token, keep separate for chip
        const last = segs[segs.length - 1];
        const token = `@${op.insert.mentiontrigger.value}`;
        if (last?.kind === "text") last.text += `**${token}**`;
        else segs.push({ kind: "text", text: `**${token}**` });
      } else if ("attachment" in op.insert) {
        segs.push({ kind: "attachment", ...op.insert.attachment });
      }
    }
  }
  return segs;
}

export function textDelta(text: string): Delta {
  return [{ insert: text }];
}
