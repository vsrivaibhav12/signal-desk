import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChartBlock } from "../src/components/ChartBlock";
import { IdeaCard } from "../src/components/IdeaCard";
import { IdeaSheet } from "../src/components/IdeaSheet";
import { PlanList } from "../src/components/PlanList";
import { TradeChart } from "../src/components/TradeChart";
import { fetchChartFile, findChart } from "../src/lib/data";
import { layoutChart, normalizeChart, tickText } from "../src/lib/chart";
import { effectiveIdea } from "../src/lib/ideas";
import { assessTrust } from "../src/lib/trust";
import { makeApp, makeChart, makeIdea, makeLive, NOW } from "./helpers";

afterEach(() => cleanup());

const chart = (over = {}) => normalizeChart(makeChart(over))!;
const PLAN = { enter: "Buy near 101.3.", stop: "Stop 99.5. If price touches it, leave.", target: "Target 103.", trail: "Trail: leave on a close below the line.", exit_by: "Leave by 15:15." };

describe("reading a stored chart", () => {
  it("accepts the engine's intraday and daily shapes", () => {
    const m = chart();
    expect(m.times[1]! - m.times[0]!).toBe(300);
    expect(m.unit).toBe("m5");
    const day = normalizeChart({ ...makeChart(), t0: undefined, d: undefined, t: Array.from({ length: 12 }, (_, i) => 1794368700 + i * 86400), unit: "day" })!;
    expect(day.unit).toBe("day");
    expect(day.times[1]! - day.times[0]!).toBe(86400);
  });

  it("refuses anything it cannot draw rather than drawing it wrongly", () => {
    const bad = [null, 7, {}, { k: [] }, { ...makeChart(), k: [[1, 2, 3]] }, { ...makeChart(), e: 99 }, { ...makeChart(), ep: "x" }, { ...makeChart(), d: [1, 2] },
                 { ...makeChart(), t0: undefined }, { ...makeChart(), k: [[1, 2, 3, NaN], [1, 2, 3, 4]] }];
    for (const b of bad) expect(normalizeChart(b)).toBeNull();
  });

  it("drops an indicator line or trail whose length does not match the candles, but keeps the chart", () => {
    const m = normalizeChart({ ...makeChart(), ov: { vwap: [1, 2] }, tr: { label: "x", v: [1] } })!;
    expect(m.ov).toEqual({}) ;
    expect(m.tr).toBeNull();
  });

  it("older stored charts, with no stop, target or trail, still draw", () => {
    const m = normalizeChart({ ...makeChart(), st: undefined, tg: undefined, tr: undefined, dir: undefined, sc: undefined })!;
    expect(m).toMatchObject({ st: null, tg: null, tr: null, dir: null, sc: false, el: "Trigger" });
  });
});

describe("laying a chart out", () => {
  it("puts higher prices higher up, and the stop below a buy's entry and the target above it", () => {
    const g = layoutChart(chart());
    const by = (kind: string) => g.lines.find((l) => l.kind === kind)!;
    expect(by("target").y).toBeLessThan(by("entry").y);
    expect(by("entry").y).toBeLessThan(by("stop").y);
    for (const c of g.candles) expect(c.yH).toBeLessThanOrEqual(c.yL);
    for (const l of g.lines) expect(l.y).toBeGreaterThanOrEqual(g.plot.top);
  });

  it("draws every level the reader needs, with plain labels", () => {
    const text = layoutChart(chart()).labels.map((l) => l.text).join(" | ");
    expect(text).toContain("Entry 101.3");
    expect(text).toContain("Stop 99.5");
    expect(text).toContain("Target 103");
    expect(text).toContain("Range high 100.8");
  });

  it("says when the stop acts on a candle close, and names the entry by what it is", () => {
    expect(layoutChart(chart({ sc: true })).labels.some((l) => l.text.startsWith("Stop (on a close)"))).toBe(true);
    expect(layoutChart(chart({ el: "Now" })).entryLabel).toBe("Price now");
    expect(layoutChart(chart({ el: "Signal" })).entryLabel).toBe("Signal close");
  });

  it("keeps the right-hand labels apart", () => {
    const g = layoutChart(chart({ ep: 101.3, st: 101.25, tg: 101.35, ln: [["a", 101.28], ["b", 101.32]] }));
    const ys = g.labels.map((l) => l.y);
    for (let i = 1; i < ys.length; i++) expect(ys[i]! - ys[i - 1]!).toBeGreaterThanOrEqual(12);
  });

  it("points an arrow at a target too far away to draw, instead of squashing the candles", () => {
    const far = layoutChart(chart({ tg: 140 }));
    expect(far.offTarget).toEqual({ value: 140, above: true });
    expect(far.lines.some((l) => l.kind === "target")).toBe(false);
    expect(far.labels.some((l) => l.text.startsWith("Target 140") && l.text.endsWith("↑"))).toBe(true);
    const below = layoutChart(chart({ tg: 60, dir: -1 }));
    expect(below.offTarget).toEqual({ value: 60, above: false });
    expect(layoutChart(chart()).offTarget).toBeNull();
  });

  it("breaks the trail where it has no value, and draws nothing for a chart with no trail", () => {
    expect(layoutChart(chart()).trail).toHaveLength(1);
    const gap = layoutChart(chart({ tr: { label: "t", v: [100, 100.1, null, 100.2, 100.3, 100.4, null, 100.5, 100.6, 100.7, 100.8, 100.9] } }));
    expect(gap.trail.length).toBe(3);
    expect(layoutChart(chart({ tr: null })).trail).toEqual([]);
  });

  it("a sell's trigger arrow points down and a buy's points up", () => {
    expect(layoutChart(chart({ dir: 1 })).trigger.pointsUp).toBe(true);
    expect(layoutChart(chart({ dir: -1 })).trigger.pointsUp).toBe(false);
  });

  it("marks the exit, green for a gain and red for a loss on a buy", () => {
    expect(layoutChart(chart({ x: 9, xp: 102.5 })).exit).toMatchObject({ gain: true });
    expect(layoutChart(chart({ x: 9, xp: 100.2 })).exit).toMatchObject({ gain: false });
    expect(layoutChart(chart({ x: 9, xp: 99.5, dir: -1 })).exit).toMatchObject({ gain: true });
    expect(layoutChart(chart()).exit).toBeNull();
  });

  it("compact charts are shorter, and time ticks read as India clock times or dates", () => {
    expect(layoutChart(chart(), { compact: true }).height).toBeLessThan(layoutChart(chart()).height);
    expect(tickText(1794368700, "m5")).toMatch(/^\d{1,2}[:.]\d{2}$/);
    expect(tickText(1794368700, "day")).toMatch(/Nov/);
  });

  it("copes with a flat chart and a single repeated price", () => {
    const flat = normalizeChart({ ...makeChart(), k: Array.from({ length: 12 }, () => [100, 100, 100, 100]), ep: 100, st: null, tg: null, tr: null, ln: [], ov: {}, bx: null })!;
    const g = layoutChart(flat);
    expect(g.candles.every((c) => Number.isFinite(c.yO) && Number.isFinite(c.yH))).toBe(true);
  });
});

describe("the drawing", () => {
  it("shows every level and the trail, and describes the chart for a screen reader", () => {
    const { container } = render(<TradeChart chart={chart()} name="HAL" />);
    const text = container.textContent ?? "";
    for (const word of ["Entry 101.3", "Stop 99.5", "Target 103"]) expect(text).toContain(word);
    expect(text).toContain("Trail: a close below this line ends the trade");           // in the legend
    expect(container.querySelectorAll("polyline").length).toBeGreaterThan(0);
    expect(container.querySelectorAll("g title").length).toBe(12);                       // one tooltip per candle
    const label = container.querySelector("svg")!.getAttribute("aria-label")!;
    expect(label).toMatch(/Chart of HAL: 12 five-minute candles. Buy;/);
    expect(label).toContain("stop 99.5");
    expect(label).toContain("target 103");
    expect(label).toContain("trailing exit line");
    expect(screen.getByText("A note about the chart.")).toBeTruthy();
  });

  it("a chart with no target says so", () => {
    const { container } = render(<TradeChart chart={chart({ tg: null, tr: null })} name="HAL" />);
    expect(container.querySelector("svg")!.getAttribute("aria-label")).toContain("no fixed target");
    expect(container.textContent).not.toContain("Trail:");
  });
});

describe("finding an idea's chart", () => {
  const swing = { charts: { "BRK:2026-11-10": makeChart({ unit: "day" }) } } as never;

  it("looks in the engine's chart file, or inside swing.json", () => {
    const files = { "chartlive_orb.json": { "orb|HAL|1": { k: 1 } } };
    expect(findChart({ swing: undefined }, files, { file: "chartlive_orb.json", key: "orb|HAL|1" })).toEqual({ k: 1 });
    expect(findChart({ swing: undefined }, files, { file: "chartlive_orb.json", key: "nope" })).toBeUndefined();
    expect(findChart({ swing: undefined }, files, { file: "chartlive_drive.json", key: "x" })).toBeUndefined();
    expect(findChart({ swing }, {}, { file: "swing.json", in: "charts", key: "BRK:2026-11-10" })).toBeDefined();
    expect(findChart({ swing }, {}, null)).toBeUndefined();
  });

  it("fetches a chart file, refusing anything that is not a map of charts", async () => {
    const ok = vi.fn(async () => new Response(JSON.stringify({ a: 1 }), { status: 200 })) as unknown as typeof fetch;
    const src = { kind: "live" as const, base: "https://x/", label: "x" };
    expect(await fetchChartFile(src, "chartlive_orb.json", NOW, ok)).toEqual({ a: 1 });
    const list = (async () => new Response("[1]", { status: 200 })) as unknown as typeof fetch;
    await expect(fetchChartFile(src, "f.json", NOW, list)).rejects.toThrow(/not valid data/);
    const missing = (async () => new Response("", { status: 404 })) as unknown as typeof fetch;
    await expect(fetchChartFile(src, "f.json", NOW, missing)).rejects.toThrow(/not published yet/);
  });
});

describe("a chart where an idea is shown", () => {
  const ref = { file: "chartlive_orb.json", key: "orb|HAL|1" };
  const live = makeLive();
  const rank = (over = {}) => {
    const idea = makeIdea({ chart: ref, plan: PLAN, ...over });
    return { idea, eff: effectiveIdea(idea, assessTrust(live, NOW), NOW) };
  };

  it("loads the file the idea points at, and draws the chart once it has arrived", () => {
    const app = makeApp(live);
    const { rerender } = render(<ChartBlock app={app} chartRef={ref} name="HAL" />);
    expect(screen.getByText("Loading the chart…")).toBeTruthy();
    expect(app.ensureChartFile).toHaveBeenCalledWith("chartlive_orb.json");
    rerender(<ChartBlock app={makeApp(live, {}, { "chartlive_orb.json": { "orb|HAL|1": makeChart() } })} chartRef={ref} name="HAL" />);
    expect(document.querySelector("svg")).toBeTruthy();
  });

  it("says plainly when there is no chart, and never leaves a blank", () => {
    render(<ChartBlock app={makeApp(live, {}, { "chartlive_orb.json": {} })} chartRef={ref} name="HAL" />);
    expect(screen.getByText(/No chart is stored for this idea yet/)).toBeTruthy();
    cleanup();
    render(<ChartBlock app={makeApp(live, {}, { "chartlive_orb.json": null })} chartRef={ref} name="HAL" />);
    expect(screen.getByText(/No chart is stored/)).toBeTruthy();
    cleanup();
    render(<ChartBlock app={makeApp(live)} chartRef={null} name="HAL" />);
    expect(screen.getByText(/No chart is stored/)).toBeTruthy();
  });

  it("a stored chart that cannot be drawn is reported as missing, not as a crash", () => {
    render(<ChartBlock app={makeApp(live, {}, { "chartlive_orb.json": { "orb|HAL|1": { k: "garbage" } } })} chartRef={ref} name="HAL" />);
    expect(screen.getByText(/No chart is stored/)).toBeTruthy();
  });

  it("a TAKE card shows its chart and its trail; a WATCH card does not", () => {
    const files = { "chartlive_orb.json": { "orb|HAL|1": makeChart() } };
    render(<IdeaCard r={rank()} app={makeApp(live, {}, files)} onOpen={() => {}} />);
    expect(document.querySelector(".idea svg")).toBeTruthy();
    expect(screen.getByText("Trail: leave on a close below the line.")).toBeTruthy();
    cleanup();
    render(<IdeaCard r={rank({ grade: "watch" })} app={makeApp(live, {}, files)} onOpen={() => {}} />);
    expect(document.querySelector(".idea svg")).toBeNull();
    expect(screen.queryByText("Trail: leave on a close below the line.")).toBeNull();
  });

  it("an expired card is not decorated with a chart that would invite acting on it", () => {
    const files = { "chartlive_orb.json": { "orb|HAL|1": makeChart() } };
    render(<IdeaCard r={rank({ signaled_at: new Date(NOW - 3_000_000).toISOString() })} app={makeApp(live, {}, files)} onOpen={() => {}} />);
    expect(screen.getByText("EXPIRED")).toBeTruthy();
    expect(document.querySelector(".idea svg")).toBeNull();
  });

  it("the details sheet shows the chart and the whole plan: enter, stop, target, trail, time limit", () => {
    const files = { "chartlive_orb.json": { "orb|HAL|1": makeChart() } };
    render(<IdeaSheet r={rank()} app={makeApp(live, {}, files)} onClose={() => {}} />);
    expect(document.querySelector("[role=dialog] svg")).toBeTruthy();
    expect([...document.querySelectorAll(".plan dt")].map((x) => x.textContent)).toEqual(["Enter", "Stop loss", "Target", "Trail / exit", "Time limit"]);
    expect([...document.querySelectorAll(".plan dd")].map((x) => x.textContent)).toEqual(Object.values(PLAN));
  });

  it("the plan skips what an idea does not have", () => {
    const { container } = render(<PlanList plan={{ ...PLAN, exit_by: null }} />);
    expect(container.querySelectorAll("dt")).toHaveLength(4);
    cleanup();
    expect(render(<PlanList plan={undefined} />).container.textContent).toBe("");
  });
});
