// Turns a stored chart (contract/schemas/chart.schema.json) into positions on a drawing. Pure functions: no DOM, easy to test.
import { price } from "./format";

export type Unit = "m5" | "day";

export interface NormalChart {
  times: number[];
  unit: Unit;
  k: number[][];
  e: number;
  x: number | null;
  ep: number;
  xp: number | null;
  ln: Array<[string, number]>;
  ov: Record<string, Array<number | null>>;
  bx: [number, number, number, number, string] | null;
  st: number | null;
  tg: number | null;
  dir: 1 | -1 | null;
  /** The stop acts on a candle CLOSE beyond it, not on a touch. */
  sc: boolean;
  tr: { label: string; v: Array<number | null> } | null;
  tn: string;
  el: string;
}

const finite = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n);
const numOrNull = (n: unknown): number | null => (finite(n) ? n : null);

/** Reads whatever was stored into a chart that is safe to draw, or null when it cannot be drawn. Never throws. */
export function normalizeChart(raw: unknown): NormalChart | null {
  if (!raw || typeof raw !== "object") return null;
  const c = raw as Record<string, unknown>;
  const k = c.k;
  if (!Array.isArray(k) || k.length < 2 || !k.every((r) => Array.isArray(r) && r.length === 4 && r.every(finite))) return null;
  const n = k.length;
  let times: number[] | null = null;
  if (Array.isArray(c.t) && c.t.length === n && c.t.every(finite)) times = c.t as number[];
  else if (finite(c.t0) && Array.isArray(c.d) && c.d.length === n && c.d.every(finite)) times = (c.d as number[]).map((d) => (c.t0 as number) + d * 300);
  if (!times) return null;
  if (!Number.isInteger(c.e) || (c.e as number) < 0 || (c.e as number) >= n || !finite(c.ep)) return null;

  const series = (v: unknown): Array<number | null> | null => (Array.isArray(v) && v.length === n ? v.map(numOrNull) : null);
  const ov: Record<string, Array<number | null>> = {};
  for (const [name, v] of Object.entries((c.ov as Record<string, unknown>) ?? {})) {
    const s = series(v);
    if (s) ov[name] = s;
  }
  const trRaw = c.tr as { label?: unknown; v?: unknown } | null | undefined;
  const trV = trRaw ? series(trRaw.v) : null;
  const bxRaw = c.bx;
  const bx = Array.isArray(bxRaw) && bxRaw.length === 5 && bxRaw.slice(0, 4).every(finite) ? (bxRaw as [number, number, number, number, string]) : null;
  const ln: Array<[string, number]> = Array.isArray(c.ln)
    ? (c.ln as unknown[]).filter((l): l is [string, number] => Array.isArray(l) && typeof l[0] === "string" && finite(l[1]))
    : [];
  return {
    times, unit: c.unit === "day" ? "day" : "m5", k: k as number[][], e: c.e as number, x: Number.isInteger(c.x) ? (c.x as number) : null, ep: c.ep as number,
    xp: numOrNull(c.xp), ln, ov, bx, st: numOrNull(c.st), tg: numOrNull(c.tg), dir: c.dir === 1 || c.dir === -1 ? c.dir : null, sc: c.sc === true,
    tr: trV ? { label: typeof trRaw?.label === "string" ? trRaw.label : "Trail", v: trV } : null,
    tn: typeof c.tn === "string" ? c.tn : "", el: typeof c.el === "string" && c.el ? c.el : "Trigger",
  };
}

export type LineKind = "entry" | "stop" | "target" | "level";

export interface HLine { kind: LineKind; y: number; value: number; label: string }
export interface Candle { x: number; yO: number; yC: number; yH: number; yL: number; up: boolean; title: string }
export interface Segment { points: Array<[number, number]> }

export interface ChartLayout {
  width: number;
  height: number;
  plot: { left: number; right: number; top: number; bottom: number };
  slot: number;
  bodyWidth: number;
  candles: Candle[];
  lines: HLine[];
  /** Right-hand text labels, nudged apart so none overlap. */
  labels: Array<{ y: number; text: string; kind: LineKind | "trail" }>;
  trail: Segment[];
  overlays: Array<{ name: string; segments: Segment[] }>;
  box: { x: number; y: number; w: number; h: number; label: string } | null;
  trigger: { x: number; label: string; markerY: number; pointsUp: boolean };
  exit: { x: number; y: number; gain: boolean | null } | null;
  /** A target too far from the candles to draw: where to point the arrow. */
  offTarget: { value: number; above: boolean } | null;
  ticks: Array<{ x: number; text: string }>;
  entryLabel: string;
}

const istClock = new Intl.DateTimeFormat("en-IN", { hour: "numeric", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
const istDay = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" });
export const tickText = (epochSeconds: number, unit: Unit) => (unit === "day" ? istDay : istClock).format(epochSeconds * 1000);

const ENTRY_LABEL: Record<string, string> = { Trigger: "Entry", Entry: "Entry", Signal: "Signal close", Now: "Price now" };

export function layoutChart(ch: NormalChart, opts: { compact?: boolean } = {}): ChartLayout {
  const n = ch.k.length;
  const width = 760;
  const height = opts.compact ? 230 : 340;
  const left = 8, right = opts.compact ? 150 : 180, top = 16, bottom = 24;
  const pw = width - left - right, ph = height - top - bottom;

  let lo = Math.min(...ch.k.map((c) => c[2]!));
  let hi = Math.max(...ch.k.map((c) => c[1]!));
  const band = hi - lo || Math.abs(hi) * 0.01 || 1;
  const near = (v: number) => v > lo - 0.8 * band && v < hi + 0.8 * band;
  const farTarget = ch.tg !== null && !near(ch.tg);
  for (const v of [ch.ep, ch.st, farTarget ? null : ch.tg]) if (v !== null) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  const levels = ch.ln.filter(([, v]) => near(v));
  for (const [, v] of levels) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  const pad = (hi - lo) * 0.07 || 1;
  lo -= pad; hi += pad;

  const slot = pw / n;
  const x = (i: number) => left + (i + 0.5) * slot;
  const y = (v: number) => top + ((hi - v) / (hi - lo)) * ph;

  const candles: Candle[] = ch.k.map((c, i) => ({
    x: x(i), yO: y(c[0]!), yC: y(c[3]!), yH: y(c[1]!), yL: y(c[2]!), up: c[3]! >= c[0]!,
    title: `${tickText(ch.times[i]!, ch.unit)}  open ${price(c[0])}, high ${price(c[1])}, low ${price(c[2])}, close ${price(c[3])}`,
  }));

  const entryLabel = ENTRY_LABEL[ch.el] ?? "Entry";
  const lines: HLine[] = [];
  for (const [name, v] of levels) lines.push({ kind: "level", y: y(v), value: v, label: name.replace(/^Opening range /, "Range ") });
  if (ch.st !== null) lines.push({ kind: "stop", y: y(ch.st), value: ch.st, label: ch.sc ? "Stop (on a close)" : "Stop" });
  if (ch.tg !== null && !farTarget) lines.push({ kind: "target", y: y(ch.tg), value: ch.tg, label: "Target" });
  lines.push({ kind: "entry", y: y(ch.ep), value: ch.ep, label: entryLabel });

  const labels: ChartLayout["labels"] = lines.map((l) => ({ y: l.y, text: `${l.label} ${price(l.value)}`, kind: l.kind }));
  if (farTarget && ch.tg !== null) {
    const above = ch.tg > hi;
    labels.push({ y: above ? top + 6 : height - bottom - 6, text: `Target ${price(ch.tg)} ${above ? "↑" : "↓"}`, kind: "target" });
  }
  labels.sort((a, b) => a.y - b.y);
  for (let i = 1; i < labels.length; i++) if (labels[i]!.y - labels[i - 1]!.y < 12) labels[i]!.y = labels[i - 1]!.y + 12;

  const segments = (vals: Array<number | null>): Segment[] => {
    const out: Segment[] = [];
    let run: Array<[number, number]> = [];
    const flush = () => { if (run.length > 1) out.push({ points: run }); run = []; };
    vals.forEach((v, i) => { if (v === null || v < lo || v > hi) flush(); else run.push([x(i), y(v)]); });
    flush();
    return out;
  };

  const e = ch.k[ch.e]!;
  const buy = ch.dir !== -1;
  const markerY = buy ? y(e[2]!) + 15 : y(e[1]!) - 15;
  let exit: ChartLayout["exit"] = null;
  if (ch.x !== null && ch.xp !== null && ch.x >= 0 && ch.x < n) {
    const entry = ch.ep, dir = ch.dir ?? 1;
    const gain = ch.xp === entry ? null : (ch.xp - entry) * dir > 0;
    exit = { x: x(ch.x), y: y(ch.xp), gain };
  }

  const step = Math.max(1, Math.ceil(n / 8));
  const ticks: ChartLayout["ticks"] = [];
  for (let i = 0; i < n; i += step) ticks.push({ x: x(i), text: tickText(ch.times[i]!, ch.unit) });

  return {
    width, height, plot: { left, right: width - right, top, bottom: height - bottom }, slot, bodyWidth: Math.max(2, slot * 0.62), candles, lines, labels,
    trail: ch.tr ? segments(ch.tr.v) : [],
    overlays: Object.entries(ch.ov).map(([name, vals]) => ({ name, segments: segments(vals) })),
    box: ch.bx ? { x: x(ch.bx[0]) - slot / 2, y: y(ch.bx[3]), w: (ch.bx[1] - ch.bx[0] + 1) * slot, h: Math.max(1, y(ch.bx[2]) - y(ch.bx[3])), label: ch.bx[4] } : null,
    trigger: { x: x(ch.e), label: ch.el, markerY, pointsUp: buy },
    exit, offTarget: farTarget && ch.tg !== null ? { value: ch.tg, above: ch.tg > hi } : null, ticks, entryLabel,
  };
}

/** Indicator lines get readable names; anything unknown keeps its own. */
export const OVERLAY_NAMES: Record<string, string> = {
  vwap: "VWAP", ema9: "9-period average", ema20: "20-period average", ema21: "21-period average", ema50: "50-period average",
  bb_up: "Upper band", bb_mid: "Middle band", bb_lo: "Lower band",
};
