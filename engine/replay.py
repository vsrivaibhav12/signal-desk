"""Point-in-time replay of the whole decision process, not just the strategies.

    python -m engine.replay --source yahoo --cache <folder>          development and validation only (the holdout stays sealed)
    python -m engine.replay --source kotak --days 365 --final        also unseal the holdout, once

The older backtest ran the strategies over every share on every day. The live tool does not: it looks closely only at a watchlist.
This replays that selection, bar by bar, with only what was known at the time:
  1. results: NSE's own calendar for the past dates, grouped into just out / today / next session with the real holidays;
  2. volume: each share's normal volume from the ten sessions before, against the volume traded so far, on the same time-of-day
     curve the live tool uses, with the same promotion thresholds and the same limit on how many shares are promoted per candle;
  3. strategies, graded and filled exactly as live (next-candle open, costs, slippage, gaps);
  4. a trade counts for the full system only if its share was on the watchlist when the signal candle closed.
Then it asks what each piece adds: every signal, then volume promotion, then results, then both, then the grade on top, with
day-clustered confidence intervals, a split into development, validation and an untouched holdout, and sensitivity to costs.

What it cannot replay, and says so: news (no timestamped history exists), and which shares were in the Nifty 200 on each date
(today's list is used, which flatters results a little: survivorship).
"""
import argparse
import json
import os
import random
import sys
from collections import defaultdict
from datetime import datetime, timedelta

from . import clock, watch as watchmod
from .backtest import report
from .clock import at, ts
from .results import Results
from .schema import SCHEMA
from .strategies import backtest as stbt, risk, sim
from .strategies.defs import STRATEGIES
from .tradingcal import TradingCalendar, weekends_only

BARS = 75                                  # five-minute candles in a session, 09:15 to 15:30
SPLIT = (0.6, 0.2, 0.2)                    # development, validation, holdout (by session, in time order)


# ── the watchlist, as it would have looked ───────────────────────────────────────────────────────
class PointInTime:
    def __init__(self, series, insts, results, cfg=None, warm=10):
        self.cfg = watchmod.settings(cfg)
        self.insts = insts
        self.results = results
        self.warm = warm
        self.days = sorted({d for S in series.values() for d in S.days})
        self.cum, self.total, self.close = {}, {}, {}     # symbol -> day -> per-bar running volume / close; total volume of the day
        self.t0 = {d: ts(at(d, "09:15")) for d in self.days}
        for sym, S in series.items():
            inst = insts.get(sym)
            if not inst or inst["kind"] != "stock":
                continue
            per, tot, cl = {}, {}, {}
            for d in S.days:
                run, vols, closes = 0.0, [None] * BARS, [None] * BARS
                for j in range(S.starts[d], S.ends[d] + 1):
                    k = (S.t[j] - self.t0[d]) // 300
                    if 0 <= k < BARS:
                        run += S.v[j]
                        vols[k], closes[k] = run, S.c[j]
                for k in range(1, BARS):                   # a bar with no trades keeps the running totals
                    if vols[k] is None:
                        vols[k], closes[k] = vols[k - 1], closes[k - 1]
                per[d], tot[d], cl[d] = vols, run, closes
            self.cum[sym], self.total[sym], self.close[sym] = per, tot, cl
        self.prev_close = {sym: series[sym].prev_close for sym in self.cum}
        self._sdays = {sym: sorted(per) for sym, per in self.cum.items()}
        self._rv = {}
        self._base = {}
        self._promoted = {}
        self._results_day = {}

    def baseline(self, sym, day):
        key = (sym, day)
        if key not in self._base:
            earlier = [self.total[sym][d] for d in sorted(self.total[sym]) if d < day][-self.warm:]
            earlier = [v for v in earlier if v > 0]
            self._base[key] = sum(earlier) / len(earlier) if len(earlier) >= 3 else None
        return self._base[key]

    def promoted(self, day, k):
        """{symbol: info} for the shares the quotes scan would have promoted when candle k (0 = 09:15) had just closed."""
        key = (day, k)
        if key in self._promoted:
            return self._promoted[key]
        mins = 5 * (k + 1) + 0.5                          # the live scan reads quotes about 30 seconds after the close
        frac, rows = watchmod.fraction_at(mins), []
        for sym, per in self.cum.items():
            vols = per.get(day)
            if not vols or vols[k] is None:
                continue
            pc = self.prev_close[sym].get(day)
            base = self.baseline(sym, day)
            if not pc or self.close[sym][day][k] is None:
                continue
            move = (self.close[sym][day][k] / pc - 1) * 100
            ratio = vols[k] / (base * frac) if base and frac >= 0.03 and vols[k] else None
            by_vol = ratio is not None and ratio >= self.cfg["min_volume_ratio"]
            by_move = abs(move) >= self.cfg["min_move_pct"]
            if by_vol or by_move:
                rows.append((max((ratio or 0) / self.cfg["min_volume_ratio"], abs(move) / self.cfg["min_move_pct"]), sym, ratio, move))
        rows.sort(key=lambda r: -r[0])
        out = {sym: {"ratio": None if ratio is None else round(ratio, 2), "move": round(move, 2)}
               for _, sym, ratio, move in rows[:self.cfg["max_promoted"]]}
        self._promoted[key] = out
        return out

    def top_rv(self, day, n=20, back=14, bars=2):
        """{symbol: ratio} for the `n` shares that traded the most volume in the first `bars` candles (10 minutes) against their previous
        `back` sessions: the paper's "stocks in play" (it uses the first 5 minutes; Yahoo reports zero volume for the 09:15 candle, so
        the first two candles are used). Known at 09:25."""
        key = (day, n)
        if key in self._rv:
            return self._rv[key]
        rows = []
        for sym, per in self.cum.items():
            vols = per.get(day)
            first = vols[bars - 1] if vols else None
            if not first:
                continue
            earlier = [self.cum[sym][d][bars - 1] for d in self._sdays[sym] if d < day][-back:]
            earlier = [v for v in earlier if v]
            if len(earlier) >= 5:
                rows.append((first / (sum(earlier) / len(earlier)), sym))
        rows.sort(reverse=True)
        self._rv[key] = {sym: round(r, 2) for r, sym in rows[:n]}
        return self._rv[key]

    def results_for(self, day):
        """{symbol: 'last' | 'today' | 'next'} for shares with results around this day."""
        if day not in self._results_day:
            self._results_day[day] = {f"NSE:{w['symbol']}-EQ": w["kind"] for w in self.results.watch(day)} if self.results else {}
        return self._results_day[day]

    def tag(self, trade):
        """What put this trade's share on the watchlist when the signal candle closed."""
        sym, day = trade["symbol"], clock.from_ts(trade["signal_ts"]).date()
        if self.insts[sym]["kind"] == "index":
            return {"index": True, "promoted": False, "results": None, "ratio": None, "rv20": False}
        k = (trade["signal_ts"] - self.t0[day]) // 300 - 1
        info = self.promoted(day, max(0, min(BARS - 1, k))).get(sym)
        return {"index": False, "promoted": info is not None, "results": self.results_for(day).get(sym),
                "ratio": info and info["ratio"], "move": info and info["move"], "rv20": sym in self.top_rv(day)}

    def load_stats(self, days, n_stocks):
        """How many shares the watchlist would have scanned per candle, against scanning all of them."""
        counts = []
        for d in days:
            res = len(self.results_for(d))
            for k in range(1, BARS - 4):
                counts.append(len(self.promoted(d, k)) + res)
        if not counts:
            return {}
        avg = sum(counts) / len(counts)
        return {"avg_shares_scanned_per_candle": round(avg, 1), "max": max(counts), "all_shares": n_stocks,
                "share_of_universe": round(avg / n_stocks * 100, 1) if n_stocks else None}


# ── numbers ──────────────────────────────────────────────────────────────────────────────────────
def day_bootstrap(trades, key="net_r", n=1000, seed=7):
    """95% interval for the average R per trade, resampling whole days (trades on the same day are not independent)."""
    by = defaultdict(lambda: [0.0, 0])
    for t in trades:
        by[t["date"]][0] += t[key]
        by[t["date"]][1] += 1
    days = list(by)
    if len(days) < 5:
        return None
    rng, out = random.Random(seed), []
    for _ in range(n):
        pick = rng.choices(days, k=len(days))
        cnt = sum(by[d][1] for d in pick)
        out.append(sum(by[d][0] for d in pick) / cnt if cnt else 0.0)
    out.sort()
    return [round(out[int(0.025 * n)], 3), round(out[int(0.975 * n) - 1], 3)]


def split_sessions(days, holdout_from=None):
    """Development, validation and holdout sessions, in time order. With `holdout_from` (an ISO date) the holdout is every
    session from that date on and the rest is split 75/25, so a fixed future start keeps the holdout fresh as data grows."""
    if holdout_from:
        rest = [d for d in days if d < holdout_from]
        a = int(len(rest) * 0.75)
        return {"development": rest[:a], "validation": rest[a:], "holdout": [d for d in days if d >= holdout_from]}
    n = len(days)
    a, b = int(n * SPLIT[0]), int(n * (SPLIT[0] + SPLIT[1]))
    return {"development": days[:a], "validation": days[a:b], "holdout": days[b:]}


def brief(trades):
    """The few numbers that matter."""
    if not trades:
        return {"n": 0}
    s = report.summarize(trades)
    return {"n": s["n"], "avg_r": s["avg_r"], "win_rate": s["win_rate"], "net": s["pnl"], "profit_factor": s["profit_factor"],
            "max_drawdown": s["max_drawdown"], "days": s["days_traded"], "ci_avg_r": day_bootstrap(trades)}


def with_cost(trades, factor):
    return [dict(t, net_r=(t["gross"] - factor * t["cost"]) / t["risk"] if t["risk"] else 0.0,
                 net=t["gross"] - factor * t["cost"]) for t in trades]


EXPERIMENTS = [
    ("all", "Every signal on every share", "What the old backtest measured: no watchlist, no grade.", lambda t: True),
    ("volume", "On the watchlist by volume or move", "Only shares the quotes scan would have promoted at that moment.",
     lambda t: t["pit"]["promoted"]),
    ("results", "On the watchlist by results", "Only shares with results just out, today or before the next session.",
     lambda t: t["pit"]["results"] is not None),
    ("watchlist", "On the watchlist (either)", "Volume or move, or results.", lambda t: t["pit"]["promoted"] or t["pit"]["results"] is not None),
    ("rv20", "The paper's selection: top 20 by opening volume", "The 20 shares with the heaviest volume in the first 10 minutes against their previous 14 sessions.",
     lambda t: t["pit"].get("rv20", False)),
    ("grade", "Graded worth taking, any share", "The four checks alone, without the watchlist.", lambda t: t["dec"]["g"] == "take"),
    ("full", "The full system", "On the watchlist and graded worth taking.",
     lambda t: (t["pit"]["promoted"] or t["pit"]["results"] is not None) and t["dec"]["g"] == "take"),
]


def evaluate(trades, days_by_split, final):
    """All the experiments, each split into development, validation and (if unsealed) the holdout."""
    stocks = [t for t in trades if not t["pit"]["index"]]
    out = []
    for eid, name, desc, pick in EXPERIMENTS:
        sel = [t for t in stocks if pick(t)]
        row = {"id": eid, "name": name, "desc": desc, "all": brief(sel)}
        for part, days in days_by_split.items():
            if part == "holdout" and not final:
                continue
            ds = set(days)
            row[part] = brief([t for t in sel if t["date"] in ds])
        row["by_strategy"] = {sid: brief([t for t in sel if t["strategy"] == sid]) for sid in sorted({t["strategy"] for t in stocks})}
        out.append(row)
    return out


def breakdowns(trades):
    """The full system's trades by month, hour of entry, side and exit."""
    sel = [t for t in trades if not t["pit"]["index"] and (t["pit"]["promoted"] or t["pit"]["results"] is not None)
           and t["dec"]["g"] == "take"]
    by = lambda keyf: {str(k): brief([t for t in sel if keyf(t) == k]) for k in sorted({keyf(t) for t in sel})}
    return {"month": by(lambda t: t["date"][:7]), "hour": by(lambda t: t["entry_t"][11:13]),
            "side": by(lambda t: "Buy" if t["dir"] == 1 else "Sell"), "exit": by(lambda t: t["exit_type"]),
            "why_on_list": {
                "results just out": brief([t for t in sel if t["pit"]["results"] == "last"]),
                "results today": brief([t for t in sel if t["pit"]["results"] == "today"]),
                "results next session": brief([t for t in sel if t["pit"]["results"] == "next"]),
                "volume or move only": brief([t for t in sel if t["pit"]["results"] is None])}}


def cost_sensitivity(trades):
    sel = [t for t in trades if not t["pit"]["index"] and (t["pit"]["promoted"] or t["pit"]["results"] is not None)
           and t["dec"]["g"] == "take"]
    return [{"cost_factor": f, **brief(with_cost(sel, f))} for f in (0, 0.5, 1.0, 1.5, 2.0)]


# ── the run ──────────────────────────────────────────────────────────────────────────────────────
def replay(series, insts, cfg, results, log=print, final=False, warm=12, holdout_from=None, strategies=None):
    """Returns the report dict. `cfg` is the sim settings; `results` is a Results holding the past calendar (or None)."""
    sc = cfg
    all_days = sorted({d for S in series.values() for d in S.days})
    pit = PointInTime(series, insts, results, sc)
    log("Running every strategy on every share (the signals the watchlist will then be applied to) ...")
    res = sim.run_all(series, insts, sc, log=log, strategies=strategies)
    trades = [t for v in res.values() for t in v]
    traded_days = all_days[warm:]                                   # the first sessions only teach the indicators and baselines
    splits = split_sessions([d.isoformat() for d in traded_days], holdout_from)
    # SEALED means sealed: unless --final, the holdout sessions are removed before ANY number is computed, so no total,
    # breakdown or cost table can leak them.
    shown = set(splits["development"]) | set(splits["validation"]) | (set(splits["holdout"]) if final else set())
    total_sessions = len(traded_days)
    traded_days = [d for d in traded_days if d.isoformat() in shown]
    trades = [t for t in trades if t["date"] in shown]
    for t in trades:
        t["pit"] = pit.tag(t)
    n_stocks = sum(1 for i in insts.values() if i["kind"] == "stock")
    experiments = evaluate(trades, splits, final)
    full = [t for t in trades if not t["pit"]["index"] and (t["pit"]["promoted"] or t["pit"]["results"] is not None) and t["dec"]["g"] == "take"]
    admitted = [dict(t) for t in sorted(full, key=lambda x: x["entry_ts"])]
    risk.annotate(admitted, sc.get("portfolio"))
    adm = [t for t in admitted if t.get("risk_approved")]
    return {
        "schema": SCHEMA, "generated": clock.now().isoformat(timespec="minutes"),
        "period": {"from": traded_days[0].isoformat(), "to": traded_days[-1].isoformat(), "sessions": len(traded_days),
                   "sessions_incl_holdout": total_sessions, "warm_up_sessions": warm},
        "splits": {k: {"sessions": len(v), "from": v[0] if v else None, "to": v[-1] if v else None} for k, v in splits.items()},
        "holdout": {"unsealed": bool(final), "note": "Counted once. Looking at it again after changing the rules makes it a development set."
                    if final else f"Sealed: the last {len(splits['holdout'])} sessions are left out of every number above. Run with --final once, when the rules are fixed."},
        "experiments": experiments,
        "full_system_with_portfolio_limits": brief(adm),
        "cost_sensitivity": cost_sensitivity(trades),
        "breakdowns": breakdowns(trades),
        "index_trades": brief([t for t in trades if t["pit"]["index"]]),
        "watchlist_load": pit.load_stats(traded_days, n_stocks),
        "results_calendar": {"events_in_universe": len(results.state["events"]) if results else 0},
        "trades_total": len(trades),
        "assumptions": [
            "Next-candle-open fills, estimated costs and slippage, gaps through the stop dropped: the same as the live desk.",
            "Results come from NSE's published calendar for the past dates, grouped by real trading sessions. A share with results is "
            "watched from the day before to the day after, as live.",
            "The volume promotion uses candle volume as a stand-in for the quote volume the live tool reads, on the same time-of-day curve and "
            "with the same thresholds and per-candle limit.",
            "News is NOT replayed: there is no timestamped news history, so the news part of the watchlist is missing here.",
            "Today's Nifty 200 list is used for every date (survivorship bias flatters results a little).",
            "Confidence intervals resample whole days, because trades on one day are not independent.",
        ]}


def results_history(frm, to, universe, cache=None, fetch=None, log=print):
    """A Results object holding NSE's calendar for the past dates (read once, cached in `cache` if given)."""
    import tempfile
    from .results import nse_fetch
    path = os.path.join(cache, f"results_{frm}_{to}.json") if cache else None
    raw = None
    if path and os.path.exists(path):
        raw = json.load(open(path, encoding="utf-8"))
    if raw is None:
        try:
            raw = (fetch or nse_fetch)(frm, to)
        except Exception as e:
            log(f"  Could not read NSE's past results calendar ({type(e).__name__}); the replay will have no results watchlist.")
            return None
        if path:
            os.makedirs(cache, exist_ok=True)
            json.dump(raw, open(path, "w", encoding="utf-8"))
    r = Results(tempfile.mkdtemp(), universe, fetch=lambda a, b: raw, log=lambda m: None)
    r.refresh(datetime.combine(to, datetime.min.time()), force=True)
    return r


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["auto", "yahoo", "kotak", "fyers"], default="auto")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--cache", default=None)
    ap.add_argument("--stocks", type=int, default=0)
    ap.add_argument("--out", default=os.path.join(stbt.ROOT, "_data", "replay.json"))
    ap.add_argument("--strategies", default=None, help="comma-separated strategy ids to replay instead of the active three, e.g. orb,orb5")
    ap.add_argument("--holdout-from", default=None, help="fix the holdout to the sessions from this date (YYYY-MM-DD) on")
    ap.add_argument("--final", action="store_true", help="unseal the holdout (do this once, with the rules fixed)")
    ap.add_argument("--force-final", action="store_true", help="unseal it again although it was already used")
    a = ap.parse_args(argv)
    log = lambda m: print(m, flush=True)
    if a.final and os.path.exists(a.out) and not a.force_final:
        try:
            if json.load(open(a.out, encoding="utf-8")).get("holdout", {}).get("unsealed"):
                sys.exit("The holdout was already unsealed by an earlier run. Using it again would make it a development set. "
                         "Pass --force-final if you really mean to.")
        except (OSError, ValueError):
            pass
    cfg = stbt.load_config()
    sc = sim.settings(cfg)
    sc["watch"] = cfg.get("watch")
    uni = stbt.universe()[:a.stocks] if a.stocks else stbt.universe()
    markets = [m for m in cfg["markets"] if m.get("kind") == "index" and m.get("enabled", True)]
    insts = sim.build_instruments(markets, uni, sc["index_lots"])
    series, source = stbt.load_series(a.source, a.days, insts, markets, uni, a.cache, log)
    if not series:
        sys.exit("No price data could be loaded.")
    days = sorted({d for S in series.values() for d in S.days})
    log(f"{len(series)} symbols, {len(days)} sessions ({days[0]} to {days[-1]}).")
    results = results_history(days[0] - timedelta(days=5), days[-1] + timedelta(days=5), uni, a.cache, log=log)
    from .strategies.defs import BY_ID
    pool = [BY_ID[x.strip()] for x in a.strategies.split(",")] if a.strategies else None
    out = replay(series, insts, sc, results, log, final=a.final, holdout_from=a.holdout_from, strategies=pool)
    out["source"] = source
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print_summary(out, log)
    log(f"Written to {a.out}")
    root = stbt.ROOT
    if os.path.isdir(os.path.join(root, "_data", ".git")) and os.path.abspath(a.out).startswith(os.path.join(root, "_data")):
        from .store import Store
        Store(os.path.join(root, "_data"), "unused").publish(own={"replay.json"})
        log("Published to your dashboard.")


def print_summary(out, log=print):
    log(f"\nPeriod {out['period']['from']} to {out['period']['to']}, {out['period']['sessions']} sessions after warm-up. {out['holdout']['note']}")
    log(f"{'':44s}{'trades':>7s}{'avg R':>8s}{'95% interval':>18s}{'win %':>7s}{'net Rs':>10s}")
    for e in out["experiments"]:
        for part in ("all", "development", "validation", "holdout"):
            if part not in e:
                continue
            b = e[part]
            if not b.get("n"):
                log(f"{e['name'][:34]:34s} {part[:9]:9s}{0:>7d}")
                continue
            ci = b["ci_avg_r"]
            log(f"{e['name'][:34]:34s} {part[:9]:9s}{b['n']:7d}{b['avg_r']:8.3f}{(f'[{ci[0]:+.2f}, {ci[1]:+.2f}]' if ci else 'n/a'):>18s}{(b['win_rate'] or 0):7.1f}{b['net']:10,.0f}")
    log("\nWatchlist load: " + json.dumps(out["watchlist_load"]))


if __name__ == "__main__":
    main()
