import type { Idea, Live } from "../src/generated/live";

export const NOW = Date.parse("2026-11-11T10:00:00+05:30");
export const iso = (secondsAgo: number) => new Date(NOW - secondsAgo * 1000).toISOString();

export function makeIdea(over: Partial<Idea> = {}): Idea {
  return {
    id: "strategy:orb|HAL|1", desk: "intraday_stocks", source: "strategy:orb", name: "HAL", symbol: "NSE:HAL-EQ", direction: 1, action: "BUY",
    grade: "take", status: "OPEN", signaled_at: iso(120), entry: 4000, stop: 3960, target: 4080, qty: 12, risk: 480, cost_ratio: 0.12,
    checks: [
      { id: "cost", label: "Stop wide enough for the costs", state: "pass" },
      { id: "vol", label: "Heavy volume", state: "pass" },
      { id: "mkt", label: "Nifty agrees", state: "pass" },
      { id: "conf", label: "No conflicting signal", state: "pass" },
    ],
    blockers: [], why: ["Closed above the opening range."], context: {},
    evidence: { headline: "Replay, after costs: about -0.11R per trade.", avg_r: -0.11, trades: 644, asof: "2026-11-12", source: "docs/STRATEGY_RESEARCH.md" },
    paper_position: true, quality: 80, ...over,
  };
}

export function makeLive(over: Partial<Live> = {}): Live {
  return {
    schema: 1, generated_at: iso(30), date: "2026-11-11", thresholds: { price_amber_s: 300, price_stale_s: 600, idea_expire_s: 900 },
    session: { phase: "watching", running: true, heartbeat: iso(30), session: "2026-11-11-day", updated: iso(30) },
    brokers: {},
    health: { decision: "OK", asof: iso(30), blocking: [], items: [] },
    freshness: { prices_asof: iso(60), last_candle_end: iso(90), quotes_scan_asof: iso(60), desk_updated: iso(60) },
    risk: { limits: { max_open_positions: 8, max_open_risk: 17500, max_daily_loss: 3500 }, open_positions: 2, open_risk: 1480, realized_pnl: 300,
            open_pnl: -120, day_pnl: 180 },
    desks: [{ id: "intraday_stocks", name: "Intraday stocks" }],
    ideas: [makeIdea()], positions: [], ended: [],
    market: { tiles: [], regime: null, movers: [], results: null, leaders: null, watchlist_count: 12 },
    evidence: {}, ...over,
  };
}
