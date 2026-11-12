import { useEffect } from "preact/hooks";
import { findChart, type ChartRef } from "../lib/data";
import type { AppState } from "../state";
import { ChartPlaceholder, TradeChart, useNormalChart } from "./TradeChart";

/** An idea's chart, loading its file on demand. Shows a plain message while loading and when there is none. */
export function ChartBlock({ app, chartRef, name, compact = false, showNote = true }: {
  app: AppState; chartRef: ChartRef | null | undefined; name: string; compact?: boolean; showNote?: boolean;
}) {
  const file = chartRef && chartRef.file !== "swing.json" ? chartRef.file : null;
  const sha = file ? app.snap.manifest?.files.find((f) => f.path === file)?.sha256 : undefined;
  useEffect(() => { if (file) app.ensureChartFile(file); }, [file, sha]);
  const chart = useNormalChart(findChart(app.snap, app.chartFiles, chartRef));
  if (chart) return <TradeChart chart={chart} name={name} compact={compact} showNote={showNote} />;
  const loading = file !== null && app.chartFiles[file] === undefined;
  return <ChartPlaceholder state={loading ? "loading" : "missing"} />;
}
