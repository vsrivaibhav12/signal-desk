import { useMemo } from "preact/hooks";
import { layoutChart, normalizeChart, OVERLAY_NAMES, type NormalChart } from "../lib/chart";
import { price } from "../lib/format";

const KIND_COLOR = { entry: "var(--text)", stop: "var(--bad)", target: "var(--good)", level: "var(--muted)", trail: "var(--trail)" } as const;
const KIND_DASH = { entry: "1 3", stop: "6 4", target: "6 4", level: "2 4" } as const;
const OVERLAY_COLORS = ["#8b5cf6", "#0891b2", "#d946ef", "#65a30d"];

/** The stored chart, ready to draw. Exported so callers can check a chart is drawable before reserving space for it. */
export function useNormalChart(raw: unknown): NormalChart | null {
  return useMemo(() => normalizeChart(raw), [raw]);
}

export function TradeChart({ chart, name, compact = false, showNote = true }: { chart: NormalChart; name: string; compact?: boolean; showNote?: boolean }) {
  const g = useMemo(() => layoutChart(chart, { compact }), [chart, compact]);
  const buy = chart.dir !== -1;
  const levelsText = [
    `${g.entryLabel.toLowerCase()} near ${price(chart.ep)}`,
    chart.st !== null ? `stop ${price(chart.st)}` : null,
    chart.tg !== null ? `target ${price(chart.tg)}` : "no fixed target",
    chart.tr ? "with a trailing exit line" : null,
  ].filter(Boolean).join(", ");
  return (
    <figure class="chart">
      <svg viewBox={`0 0 ${g.width} ${g.height}`} role="img" aria-label={`Chart of ${name}: ${chart.k.length} ${chart.unit === "day" ? "daily" : "five-minute"} candles. ${buy ? "Buy" : "Sell"}; ${levelsText}.`}>
        {g.box && (
          <>
            <rect x={g.box.x} y={g.box.y} width={g.box.w} height={g.box.h} fill="var(--accent)" opacity=".1" />
            <text x={g.box.x + 4} y={g.box.y + g.box.h + 12} font-size="10" fill="var(--muted)">{g.box.label}</text>
          </>
        )}
        <rect x={g.trigger.x - g.slot / 2} y={g.plot.top} width={g.slot} height={g.plot.bottom - g.plot.top} fill="var(--accent)" opacity=".13" />
        {g.lines.map((l) => (
          <line key={`${l.kind}-${l.label}-${l.value}`} x1={g.plot.left} x2={g.plot.right} y1={l.y} y2={l.y} stroke={KIND_COLOR[l.kind]} stroke-width={l.kind === "stop" || l.kind === "target" ? 1.5 : 1}
            stroke-dasharray={KIND_DASH[l.kind]} />
        ))}
        {g.overlays.map((o, i) => o.segments.map((s, j) => (
          <polyline key={`${o.name}-${j}`} points={s.points.map(([px, py]) => `${px.toFixed(1)},${py.toFixed(1)}`).join(" ")} fill="none" stroke={OVERLAY_COLORS[i % OVERLAY_COLORS.length]} stroke-width="1.5" stroke-linejoin="round" />
        )))}
        {g.trail.map((s, j) => (
          <polyline key={`trail-${j}`} points={s.points.map(([px, py]) => `${px.toFixed(1)},${py.toFixed(1)}`).join(" ")} fill="none" stroke="var(--trail)" stroke-width="2.2" stroke-linejoin="round" stroke-dasharray="7 3" />
        ))}
        {g.candles.map((c, i) => (
          <g key={i}>
            <title>{c.title}</title>
            <line x1={c.x} x2={c.x} y1={c.yH} y2={c.yL} stroke={c.up ? "var(--good)" : "var(--bad)"} stroke-width="1.2" />
            <rect x={c.x - g.bodyWidth / 2} y={Math.min(c.yO, c.yC)} width={g.bodyWidth} height={Math.max(1.2, Math.abs(c.yO - c.yC))} fill={c.up ? "var(--good)" : "var(--bad)"} />
          </g>
        ))}
        <polygon
          points={g.trigger.pointsUp
            ? `${g.trigger.x},${g.trigger.markerY - 7} ${g.trigger.x - 6},${g.trigger.markerY + 4} ${g.trigger.x + 6},${g.trigger.markerY + 4}`
            : `${g.trigger.x},${g.trigger.markerY + 7} ${g.trigger.x - 6},${g.trigger.markerY - 4} ${g.trigger.x + 6},${g.trigger.markerY - 4}`}
          fill="var(--accent)" stroke="var(--surface)" stroke-width="1.2" />
        <text x={g.trigger.x} y={g.plot.top + 9} font-size="10" font-weight="600" fill="var(--accent)" text-anchor="middle">{g.trigger.label}</text>
        {g.exit && (
          <circle cx={g.exit.x} cy={g.exit.y} r="5.5" fill={g.exit.gain === null ? "var(--muted)" : g.exit.gain ? "var(--good)" : "var(--bad)"} stroke="var(--surface)" stroke-width="2" />
        )}
        {g.labels.map((l, i) => (
          <text key={i} x={g.plot.right + 6} y={l.y + 3.5} font-size="10.5" fill={KIND_COLOR[l.kind]}>{l.text}</text>
        ))}
        {g.ticks.map((t) => <text key={t.x} x={t.x} y={g.height - 8} font-size="10" fill="var(--muted)" text-anchor="middle">{t.text}</text>)}
      </svg>
      <figcaption class="chart-legend">
        <span><i class="k-entry" />{g.entryLabel}</span>
        {chart.st !== null && <span><i class="k-stop" />{chart.sc ? "Stop (acts on a candle close)" : "Stop"}</span>}
        {chart.tg !== null && <span><i class="k-target" />Target</span>}
        {chart.tr && <span><i class="k-trail" />{chart.tr.label}</span>}
        {g.overlays.map((o, i) => <span key={o.name}><i style={{ background: OVERLAY_COLORS[i % OVERLAY_COLORS.length] }} />{OVERLAY_NAMES[o.name] ?? o.name}</span>)}
      </figcaption>
      {showNote && chart.tn && <p class="muted small chart-note">{chart.tn}</p>}
    </figure>
  );
}

export function ChartPlaceholder({ state }: { state: "loading" | "missing" }) {
  return (
    <div class="chart chart-empty" role="status">
      {state === "loading" ? "Loading the chart…" : "No chart is stored for this idea yet. The numbers above are still correct; the chart appears after the engine's next cycle."}
    </div>
  );
}
