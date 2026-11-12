"""Daily-timeframe research: base-and-breakout on the Nifty 200, over about ten years of daily prices.

    python -m engine.daily --cache <folder>               development and validation only (the holdout is cut away before anything runs)
    python -m engine.daily --cache <folder> --final       also unseal the holdout, once

Why: intraday trades pay about 0.14R in costs and slippage and the research so far shows no edge after them. A trade that holds for
weeks pays the same money in costs but risks far more per trade, so costs matter much less. This tests whether a published, simple
swing method (buy a volume breakout from a base in an uptrending stock, small fixed stop, let winners run) has an edge in Indian
shares. It does NOT copy any website's proprietary rules, and it is a research test, not a recommendation.

THE RULES BELOW WERE FIXED BEFORE ANY RESULT WAS LOOKED AT AND MUST NOT BE TUNED TO THE RESULT. If one is changed, say so and treat the
holdout as used. Every experiment that will be run is listed in EXPERIMENTS; there is nothing else to pick from.

How it avoids fooling itself:
  - entries and exits use only data up to the decision, filled at the next day's open; a stop is checked before a target; a gap
    through a stop fills at the open;
  - with the holdout sealed, the prices after the holdout starts are removed before any signal is computed, so nothing leaks;
  - a PLACEBO enters at random on days when the same stocks pass the same trend and liquidity filters, with the same exits, so the
    breakout has to beat random entry, not just beat zero;
  - confidence intervals resample whole months (trades in one month are not independent: a bull run lifts them all);
  - results are shown by year and by how concentrated they are in a few trades.

What it cannot fix: it uses today's Nifty 200 for every past year (survivorship: stocks that fell out of the index are missing, which
flatters a breakout method). A probe estimates how big that tilt is. Treat any positive result as an upper bound.
"""
import argparse
import json
import os
import random
import sys
from collections import defaultdict
from datetime import date, timedelta

from . import clock, context as cx
from .backtest import report
from .backtest.data import yahoo_candles, yahoo_stock
from .replay import day_bootstrap, split_sessions
from .schema import SCHEMA

# ── fixed in advance ─────────────────────────────────────────────────────────────────────────────
RULES = {
    "base_days": 40,            # the base is the previous 40 sessions; the pivot is its highest high
    "volume_days": 50,          # normal volume = average of the previous 50 sessions
    "volume_multiple": 1.5,     # the breakout day must trade at least this many times normal volume
    "max_extended": 0.07,       # ... and must close no more than 7% above the pivot (not already run away)
    "max_gap": 0.08,            # skip if tomorrow's open is more than 8% above the pivot
    "base_depth": 0.25,         # "tight base": the base fell no more than 25% from its high
    "stop": 0.08,               # initial stop 8% below the entry; 1R is 8%
    "target_r": 3.0,            # exit B: a target of 3R (24%)
    "time_stop": 60,            # exit B: or leave after 60 sessions
    "trail_sma": 20,            # exit A: leave at the next open after a close below the 20-day average
    "cooldown": 10,             # sessions to wait after an exit before the same share can be bought again
    "min_value_cr": 10.0,       # average daily traded value over 50 sessions, in crore rupees
    "warmup": 260,              # sessions of history needed before a signal
    "cost_round_trip": 0.004,   # 0.40% of the position: STT, stamp, charges and slippage on a delivery trade, both ways
    "risk_per_trade": 0.015,    # portfolio test: 1.5% of equity risked per trade
    "max_positions": 5,         # portfolio test: at most this many open at once
}
# (id, name, what it adds, flags that must all be true, exit rule). Cumulative, in this order. Nothing else is tested.
EXPERIMENTS = [
    ("base", "Breakout on volume", "Any close above the 40-day high on 1.5 times normal volume.", (), "trail"),
    ("trend", "+ uptrend", "Price above its 50, 150 and 200 day averages in order, and the 200 day rising.", ("trend",), "trail"),
    ("tight", "+ tight base", "The base fell no more than 25%.", ("trend", "tight"), "trail"),
    ("full", "+ Nifty above its 200-day average", "The market is in an uptrend.", ("trend", "tight", "regime"), "trail"),
    ("base_t", "Breakout on volume (3R target)", "As the first row, with the target exit.", (), "target"),
    ("trend_t", "+ uptrend (3R target)", "", ("trend",), "target"),
    ("tight_t", "+ tight base (3R target)", "", ("trend", "tight"), "target"),
    ("full_t", "+ Nifty above 200-day (3R target)", "", ("trend", "tight", "regime"), "target"),
]
# A second family, added before any context result was seen (see engine/context.py). Each is the "tight base" breakout plus a context filter.
CONTEXT_EXPERIMENTS = [
    ("ctx_rs", "Tight base + relative strength 80+", "The share is in the top fifth for 3/6/9/12-month strength.", ("trend", "tight", "rs_ok"), "trail"),
    ("ctx_breadth", "Tight base + broad market", "At least half the universe is above its 50-day average.", ("trend", "tight", "breadth_ok"), "trail"),
    ("ctx_both", "Tight base + both", "Relative strength 80+ and a broad market.", ("trend", "tight", "rs_ok", "breadth_ok"), "trail"),
    ("ctx_rs_t", "Tight base + relative strength 80+ (3R target)", "", ("trend", "tight", "rs_ok"), "target"),
    ("ctx_breadth_t", "Tight base + broad market (3R target)", "", ("trend", "tight", "breadth_ok"), "target"),
    ("ctx_both_t", "Tight base + both (3R target)", "", ("trend", "tight", "rs_ok", "breadth_ok"), "target"),
]
NIFTY = "^NSEI"


# ── data ─────────────────────────────────────────────────────────────────────────────────────────
def rolling_mean(x, n):
    out, run = [None] * len(x), 0.0
    for i, v in enumerate(x):
        run += v
        if i >= n:
            run -= x[i - n]
        if i >= n - 1:
            out[i] = run / n
    return out


class Daily:
    """One share's daily candles, with the indicators the rules use. Everything at index i uses data up to and including i."""

    def __init__(self, candles, ended=False):
        self.raw = candles
        self.ended = ended                       # True when this share stopped trading (delisted or merged) before the data ends
        by = {}
        for t, o, h, l, c, v in candles:
            by[clock.from_ts(t).date()] = (o, h, l, c, v)
        self.d = sorted(by)
        self.o, self.h, self.l, self.c, self.v = ([by[d][k] for d in self.d] for k in range(5))
        self.n = len(self.d)
        self.index = {d: i for i, d in enumerate(self.d)}
        self.sma20, self.sma50, self.sma150, self.sma200 = (rolling_mean(self.c, k) for k in (20, 50, 150, 200))
        val = [c * v for c, v in zip(self.c, self.v)]
        self.vol_prev = [None] * self.n          # average volume of the previous 50 sessions (not including today)
        self.val_prev = [None] * self.n          # average traded value (rupees) of the previous 50 sessions
        vm, am = rolling_mean(self.v, RULES["volume_days"]), rolling_mean(val, RULES["volume_days"])
        for i in range(1, self.n):
            self.vol_prev[i], self.val_prev[i] = vm[i - 1], am[i - 1]
        b = RULES["base_days"]
        self.pivot = [None] * self.n             # highest high of the previous 40 sessions
        self.base_low = [None] * self.n
        for i in range(b, self.n):
            self.pivot[i], self.base_low[i] = max(self.h[i - b:i]), min(self.l[i - b:i])

    def truncated(self, before):
        """The same share with every session from `before` on removed (used to seal the holdout)."""
        keep = [c for c in self.raw if clock.from_ts(c[0]).date() < before]
        return self if len(keep) == len(self.raw) else Daily(keep, ended=False)


def load_daily(symbols, cache=None, years=10, log=print):
    """{symbol: Daily} from Yahoo Finance (split-adjusted prices). A symbol with too little history is left out."""
    out, missing = {}, []
    if cache:
        os.makedirs(cache, exist_ok=True)
    for n, sym in enumerate(symbols):
        ysym = NIFTY if sym == NIFTY else yahoo_stock(sym)
        path = os.path.join(cache, f"d_{years}y_" + ysym.replace("^", "_").replace("%", "_") + ".json") if cache else None
        try:
            if path and os.path.exists(path):
                candles = json.load(open(path, encoding="utf-8"))
            else:
                candles = yahoo_candles(ysym, "1d", f"{years}y")
                if path:
                    json.dump(candles, open(path, "w", encoding="utf-8"))
            if len(candles) < RULES["warmup"] + 60:
                raise RuntimeError("too little history")
            out[sym] = Daily(candles)
        except Exception as e:
            missing.append(sym)
            log(f"  no daily history for {sym}: {e}")
        if (n + 1) % 50 == 0:
            log(f"  loaded {n + 1} of {len(symbols)} daily histories")
    return out, missing


# ── signals and trades ───────────────────────────────────────────────────────────────────────────
def regime_map(nifty):
    """{date: True} when Nifty closed above its own 200-day average that day."""
    return {nifty.d[i]: nifty.sma200[i] is not None and nifty.c[i] > nifty.sma200[i] for i in range(nifty.n)}


def trend_ok(D, i):
    s50, s150, s200, prev = D.sma50[i], D.sma150[i], D.sma200[i], D.sma200[i - 20] if i >= 20 else None
    return None not in (s50, s150, s200, prev) and D.c[i] > s50 > s150 > s200 and s200 > prev


def liquid(D, i):
    v = D.val_prev[i]
    return v is not None and v >= RULES["min_value_cr"] * 1e7


def signals(D, regime, members=None, key=None, rs_map=None, breadth=None):
    """Every breakout day, with the flags each experiment may require. Uses only data up to the day itself.
    With `members` ({date: set of keys}) a day only counts if the share was in the universe that day."""
    out = []
    for i in range(RULES["warmup"], D.n - 1):
        piv, vp = D.pivot[i], D.vol_prev[i]
        if piv is None or not vp or not liquid(D, i):
            continue
        if members is not None and key not in members.get(D.d[i], ()):
            continue
        if not (D.c[i] > piv and D.v[i] >= RULES["volume_multiple"] * vp and D.c[i] <= piv * (1 + RULES["max_extended"])):
            continue
        rs = rs_map.get(key, {}).get(D.d[i]) if rs_map is not None else None
        br = breadth.get(D.d[i]) if breadth is not None else None
        out.append({"i": i, "date": D.d[i], "pivot": piv, "volx": D.v[i] / vp, "trend": trend_ok(D, i),
                    "tight": (piv - D.base_low[i]) / piv <= RULES["base_depth"], "regime": bool(regime.get(D.d[i], False)),
                    "rs": rs, "rs_ok": rs is not None and rs >= cx.CONTEXT["rs_min"],
                    "breadth_ok": br is not None and br >= cx.CONTEXT["breadth_min"]})
    return out


def simulate(D, i_sig, pivot, exit_rule, R=RULES):
    """One trade from a signal on day i_sig: bought at the next open, 8% stop, exited by the chosen rule. None if it is skipped."""
    e = i_sig + 1
    if e >= D.n:
        return None
    entry = D.o[e]
    if pivot is not None and entry > pivot * (1 + R["max_gap"]):
        return None
    stop, st = entry * (1 - R["stop"]), R["stop"]
    target = entry * (1 + R["target_r"] * st)
    breakeven = False

    def done(k, px, reason, still_open=False):
        gross = (px / entry - 1) / st
        return {"entry_i": e, "exit_i": k, "entry_date": D.d[e], "exit_date": D.d[k], "entry": round(entry, 2), "exit": round(px, 2),
                "reason": reason, "hold": k - e, "gross_r": round(gross, 3), "net_r": round(gross - R["cost_round_trip"] / st, 3),
                "open": still_open}

    for k in range(e, D.n):
        if D.l[k] <= stop:                                         # the stop is checked first, as the worst case
            return done(k, D.o[k] if D.o[k] < stop else stop, "stop")
        if exit_rule == "target":
            if D.h[k] >= target:
                return done(k, D.o[k] if D.o[k] > target else target, "target")
            if k - e + 1 >= R["time_stop"]:
                return done(k, D.c[k], "time")
        else:
            if not breakeven and D.c[k] >= entry * (1 + st):       # up one R: the stop moves to the entry from tomorrow
                breakeven, stop = True, max(stop, entry)
            s20 = D.sma20[k]
            if k > e and s20 is not None and D.c[k] < s20:
                if k + 1 < D.n:
                    return done(k + 1, D.o[k + 1], "trend break")
                return done(k, D.c[k], "delisted or merged" if D.ended else "end of data", not D.ended)
    # the data ran out: a share that stopped trading is closed at its last price, never silently dropped
    return done(D.n - 1, D.c[-1], "delisted or merged" if D.ended else "end of data", not D.ended)


def run_experiment(data, sigs, flags, exit_rule, regime_ok=None):
    """All trades for one experiment: one position per share at a time, with a cool-off after each exit."""
    trades = []
    for sym, D in data.items():
        free_from = -1
        for s in sigs.get(sym, []):
            if not all(s[f] for f in flags) or s["i"] + 1 <= free_from:
                continue
            t = simulate(D, s["i"], s["pivot"], exit_rule)
            if t is None:
                continue
            free_from = t["exit_i"] + RULES["cooldown"]
            trades.append({**t, "symbol": sym, "sdate": s["date"].isoformat(), "date": s["date"].isoformat()[:7],
                           "volx": round(s["volx"], 2), "net": t["net_r"]})
    trades.sort(key=lambda t: (t["entry_date"], t["symbol"]))
    return trades


def placebo(data, window, n_real, exit_rule, draws=40, seed=11, members=None, extra=None):
    """Random entry on days when the same shares pass the trend and liquidity filters (and are in the universe), same exits."""
    pool = []
    for sym, D in data.items():
        for i in range(RULES["warmup"], D.n - 1):
            if window[0] <= D.d[i].isoformat() <= window[1] and liquid(D, i) and trend_ok(D, i)                     and (members is None or sym in members.get(D.d[i], ())) and (extra is None or extra(sym, D.d[i])):
                pool.append((sym, i))
    if not pool or not n_real:
        return None
    rng, means = random.Random(seed), []
    for _ in range(draws):
        got = [t for t in (simulate(data[s], i, None, exit_rule) for s, i in rng.sample(pool, min(n_real, len(pool)))) if t and not t["open"]]
        if got:
            means.append(sum(t["net_r"] for t in got) / len(got))
    if not means:
        return None
    means.sort()
    return {"mean": round(sum(means) / len(means), 3), "ci": [round(means[int(0.025 * len(means))], 3), round(means[int(0.975 * len(means)) - 1], 3)],
            "draws": len(means), "pool": len(pool)}


# ── a portfolio, marked to market daily ──────────────────────────────────────────────────────────
def portfolio(trades, data, dates, risk=None):
    """Equity of a book that risks `risk` (1.5% by default) of equity per trade, holds at most 5 positions, and pays costs. Starts at 1.0.
    A trade may carry its own stop distance in "risk_pct" (the playbook tests do); otherwise it is the 8% stop."""
    st, cost = RULES["stop"], RULES["cost_round_trip"]
    risk = RULES["risk_per_trade"] if risk is None else risk
    by_entry, by_exit = defaultdict(list), defaultdict(list)
    for t in trades:
        by_entry[t["entry_date"]].append(t)
        by_exit[t["exit_date"]].append(t)
    cash, held, curve, taken = 1.0, {}, [], 0
    last_close = {}
    for d in dates:
        # Entries first, then exits: a trade that is stopped out or hits its target on its entry day must be entered to be closed,
        # and a slot freed today is not reused today (the conservative choice).
        for t in sorted(by_entry.get(d, []), key=lambda x: -x["volx"]):
            if len(held) >= RULES["max_positions"]:
                break
            equity = cash + sum(p["shares"] * last_close.get(p["sym"], p["entry"]) for p in held.values())
            size = equity * risk / t.get("risk_pct", st)                      # money in the position
            if size > cash:
                continue
            held[id(t)] = {"sym": t["symbol"], "shares": size / t["entry"], "entry": t["entry"]}
            cash -= size
            taken += 1
        for t in by_exit.get(d, []):
            pos = held.pop(id(t), None)
            if pos:
                cash += pos["shares"] * t["exit"] - pos["shares"] * pos["entry"] * cost
        for p in held.values():
            D = data[p["sym"]]
            j = D.index.get(d)
            if j is not None:
                last_close[p["sym"]] = D.c[j]
        curve.append(cash + sum(p["shares"] * last_close.get(p["sym"], p["entry"]) for p in held.values()))
    return curve, taken


def curve_stats(curve, dates):
    if len(curve) < 30:
        return {}
    years = max((dates[-1] - dates[0]).days / 365.25, 1e-9)
    peak, dd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        dd = max(dd, (peak - v) / peak)
    return {"final": round(curve[-1], 3), "cagr_pct": round(((curve[-1] / curve[0]) ** (1 / years) - 1) * 100, 1),
            "max_drawdown_pct": round(dd * 100, 1), "years": round(years, 1)}


def yearly(curve, dates):
    """Return of each calendar year from an equity curve."""
    by, out = {}, {}
    for v, d in zip(curve, dates):
        by.setdefault(d.year, [v, v])[1] = v
    prev_end = None
    for y in sorted(by):
        start = prev_end if prev_end is not None else by[y][0]
        out[str(y)] = round((by[y][1] / start - 1) * 100, 1)
        prev_end = by[y][1]
    return out


def book_stats(curve, dates, splits):
    """Whole-period, development and validation statistics of an equity curve."""
    out = {**curve_stats(curve, dates), "years": yearly(curve, dates)}
    for part in ("development", "validation"):
        ds = set(splits[part])
        idx = [k for k, d in enumerate(dates) if d.isoformat() in ds]
        if len(idx) > 30:
            out[part] = curve_stats([curve[k] for k in idx], [dates[k] for k in idx])
    return out


# ── numbers ──────────────────────────────────────────────────────────────────────────────────────
def brief(trades):
    closed = [t for t in trades if not t["open"]]
    if not closed:
        return {"n": 0}
    s = report.summarize(closed)
    return {"n": s["n"], "avg_r": s["avg_r"], "gross_avg_r": round(sum(t["gross_r"] for t in closed) / len(closed), 3),
            "win_rate": s["win_rate"], "profit_factor": s["profit_factor"], "ci_avg_r": day_bootstrap(closed),
            "avg_hold": round(sum(t["hold"] for t in closed) / len(closed), 1), "open": len(trades) - len(closed)}


def concentration(trades):
    closed = sorted((t for t in trades if not t["open"]), key=lambda t: -t["net_r"])
    if len(closed) < 30:
        return {}
    total = sum(t["net_r"] for t in closed)
    rest = closed[10:]
    by_sym = defaultdict(float)
    for t in closed:
        by_sym[t["symbol"]] += t["net_r"]
    top5 = sorted(by_sym.values(), reverse=True)[:5]
    return {"total_r": round(total, 1), "top10_trades_r": round(sum(t["net_r"] for t in closed[:10]), 1),
            "avg_r_without_top10": round(sum(t["net_r"] for t in rest) / len(rest), 3), "top5_shares_r": round(sum(top5), 1),
            "shares_traded": len(by_sym)}


def survivor_probe(data, nifty, start, end):
    """If the stocks that are in the index TODAY earned far more than the index did, the list is tilted towards winners."""
    def cagr(D):
        a = next((i for i, d in enumerate(D.d) if d >= start), None)
        b = max((i for i, d in enumerate(D.d) if d <= end), default=None)
        if a is None or b is None or b - a < 250 or not D.c[a]:
            return None
        yrs = (D.d[b] - D.d[a]).days / 365.25
        return ((D.c[b] / D.c[a]) ** (1 / yrs) - 1) * 100
    each = sorted(c for c in (cagr(D) for D in data.values()) if c is not None)
    nf = cagr(nifty)
    if not each or nf is None:
        return {}
    return {"nifty_cagr_pct": round(nf, 1), "median_share_cagr_pct": round(each[len(each) // 2], 1),
            "average_share_cagr_pct": round(sum(each) / len(each), 1), "shares": len(each),
            "note": "Shares in today's index that grew far faster than the index show the list is tilted towards past winners."}


# ── the run ──────────────────────────────────────────────────────────────────────────────────────
def research(data, nifty, log=print, final=False, holdout_from=None, placebo_draws=40, members=None, universe="today's Nifty 200", results_days=None):
    # dates after the warm-up, in order; split them before touching any signal
    dates = [d for d in nifty.d[RULES["warmup"]:]]
    iso = [d.isoformat() for d in dates]
    splits = split_sessions(iso, holdout_from)
    cut = date.fromisoformat(splits["holdout"][0]) if splits["holdout"] and not final else None
    if cut:                                                   # the holdout is removed before any signal is computed
        data = {s: D.truncated(cut) for s, D in data.items()}
        nifty = nifty.truncated(cut)
        dates = [d for d in dates if d < cut]
    regime = regime_map(nifty)
    shown = set(splits["development"]) | set(splits["validation"]) | (set(splits["holdout"]) if final else set())
    members_eff = members if members is not None else cx.universe_by_date(data)
    log("Computing relative strength and breadth ...")
    rs_map = cx.rs_percentiles(data, members_eff, dates)
    breadth = cx.breadth_by_date(data, members_eff, dates)
    log("Finding every breakout day ...")
    sigs = {sym: signals(D, regime, members, sym, rs_map, breadth) for sym, D in data.items()}
    n_sig = sum(len(v) for v in sigs.values())
    log(f"  {n_sig} breakout days on {len(data)} shares.")
    exp_out, runs = [], {}
    for eid, name, desc, flags, rule in EXPERIMENTS:
        tr = [t for t in run_experiment(data, sigs, flags, rule) if t["sdate"] in shown]
        runs[eid] = tr
        row = {"id": eid, "name": name, "desc": desc, "exit": rule, "all": brief(tr)}
        for part in ("development", "validation") + (("holdout",) if final else ()):
            ds = set(splits[part])
            row[part] = brief([t for t in tr if t["sdate"] in ds])
        exp_out.append(row)
    ctx_out = []
    for eid, name, desc, flags, rule in CONTEXT_EXPERIMENTS:
        tr = [t for t in run_experiment(data, sigs, flags, rule) if t["sdate"] in shown]
        runs[eid] = tr
        row = {"id": eid, "name": name, "desc": desc, "exit": rule, "all": brief(tr)}
        for part in ("development", "validation") + (("holdout",) if final else ()):
            ds = set(splits[part])
            row[part] = brief([t for t in tr if t["sdate"] in ds])
        ctx_out.append(row)
    window = (iso[0], max(shown))
    plc = []
    for eid, rule in (("trend", "trail"), ("trend_t", "target"), ("full", "trail"), ("full_t", "target")):
        n_real = len([t for t in runs[eid] if not t["open"]])
        plc.append({"id": eid, "exit": rule, "real": next(r["all"] for r in exp_out if r["id"] == eid),
                    "placebo": placebo(data, window, n_real, rule, placebo_draws, members=members)})
    need = {"ctx_rs": ("rs",), "ctx_breadth": ("breadth",), "ctx_both": ("rs", "breadth")}
    for eid, rule in (("ctx_rs", "trail"), ("ctx_breadth", "trail"), ("ctx_both", "trail"),
                      ("ctx_rs_t", "target"), ("ctx_breadth_t", "target"), ("ctx_both_t", "target")):
        key = eid.replace("_t", "")
        conds = need[key]
        extra = (lambda sym, d, c=conds: ("rs" not in c or rs_map.get(sym, {}).get(d, -1) >= cx.CONTEXT["rs_min"])
                 and ("breadth" not in c or breadth.get(d, 0) >= cx.CONTEXT["breadth_min"]))
        n_real = len([t for t in runs[eid] if not t["open"]])
        plc.append({"id": eid, "exit": rule, "real": next(r["all"] for r in ctx_out if r["id"] == eid),
                    "placebo": placebo(data, window, n_real, rule, placebo_draws, members=members, extra=extra)})
    for p in plc:                                           # how often random entry on the same shares did as well
        pb = p["placebo"]
        p["breakout_beats_random"] = None if not pb or not p["real"].get("n") else p["real"]["avg_r"] > pb["ci"][1]
    port = {}
    shown_dates = [d for d in dates if d.isoformat() in shown]
    for eid in ("full", "full_t", "trend", "trend_t"):
        curve, taken = portfolio([t for t in runs[eid] if not t["open"]], data, shown_dates)
        port[eid] = {**curve_stats(curve, shown_dates), "trades_taken": taken}
    bench = curve_stats([nifty.c[nifty.index[d]] / nifty.c[nifty.index[shown_dates[0]]] for d in shown_dates if d in nifty.index],
                        shown_dates) if shown_dates else {}
    books = {}
    if shown_dates:
        pickers = {
            "mom_classic": cx.momentum_picker(data, members_eff, nifty, cx.classic_score),
            "mom_classic_regime": cx.momentum_picker(data, members_eff, nifty, cx.classic_score, True),
            "mom_ms": cx.momentum_picker(data, members_eff, nifty, cx.rs_score),
            "mom_ms_regime": cx.momentum_picker(data, members_eff, nifty, cx.rs_score, True),
            "equal_weight": cx.equal_weight_picker(data, members_eff)}
        for name, pick in pickers.items():
            curve, made = cx.run_book(data, shown_dates, pick)
            books[name] = {**book_stats(curve, shown_dates, splits), "trades": made}
        curve, sw = cx.index_trend_hold(nifty, shown_dates)
        books["nifty_trend_hold"] = {**book_stats(curve, shown_dates, splits), "trades": sw}
        books["nifty_buy_hold"] = book_stats(cx.buy_and_hold(nifty, shown_dates), [d for d in shown_dates if d in nifty.index], splits)
    from . import playbook
    breadth200 = cx.breadth_by_date(data, members_eff, dates, "sma200")
    pb = playbook.run(data, members_eff, rs_map, breadth200, sigs, runs, splits, shown, window, final, brief, portfolio, shown_dates, placebo_draws)
    from . import archetypes
    arch = archetypes.run(data, members_eff, rs_map, sigs, runs, nifty, splits, shown, window, final, results_days or set(), brief, shown_dates, dates, placebo_draws)
    full = runs["full"]
    years = {}
    for y in sorted({t["sdate"][:4] for t in full}):
        years[y] = brief([t for t in full if t["sdate"][:4] == y])
    return {
        "schema": SCHEMA, "generated": clock.now().isoformat(timespec="minutes"), "rules": RULES,
        "period": {"from": iso[0], "to": max(shown), "shares": len(data), "breakout_days": n_sig},
        "splits": {k: {"sessions": len(v), "from": v[0] if v else None, "to": v[-1] if v else None} for k, v in splits.items()},
        "holdout": {"unsealed": bool(final),
                    "note": "Counted once. Looking at it again after changing the rules makes it a development set." if final else
                    f"Sealed: {len(splits['holdout'])} sessions from {splits['holdout'][0] if splits['holdout'] else 'n/a'} on were removed before any "
                    "signal was computed. Run with --final once, when the rules are fixed."},
        "experiments": exp_out, "context_experiments": ctx_out, "books": books, "playbook": pb, "archetypes": arch,
        "context_settings": json.loads(json.dumps(cx.CONTEXT)),
        "breadth_coverage": {"dates": len(breadth), "average": round(sum(breadth.values()) / len(breadth), 3) if breadth else None},
        "placebo": plc, "portfolio": port, "nifty_buy_and_hold": bench,
        "by_year_full_system": years, "concentration_full_system": concentration(full),
        "ended_shares": {"shares_that_stopped_trading": sum(1 for D in data.values() if D.ended),
                        "trades_closed_at_delisting": brief([t for t in full if t["reason"] == "delisted or merged"])},
        "universe": universe,
        "survivor_probe": survivor_probe(data, nifty, shown_dates[0], shown_dates[-1]) if shown_dates else {},
        "assumptions": [
            f"Universe: {universe}.",
            "Survivorship: with today's Nifty 200, shares that left the index are missing, which flatters a breakout method. With the NSE "
            "point-in-time universe every share that traded is included, and one that stops trading is closed at its last price.",
            "Entry at the next open, a stop checked before any exit, a gap through the stop filled at the open, a 0.40 percent round-trip cost "
            "(taxes, charges and slippage) on the position, 8 percent stop so about 0.05R of cost per trade.",
            "One position per share at a time, with a 10-session cool-off after an exit. A trade still open at the end of the data is not counted.",
            "The rules were fixed before any result was seen. Nothing was tuned. The experiments are exactly those listed.",
            "Confidence intervals resample whole months because trades in one month are not independent.",
            "The portfolio risks 1.5% of equity per trade with at most 5 open positions; its drawdown is marked to market daily.",
            "This is a research test of a simple published method, not a recommendation, and not a copy of any website's rules.",
        ]}


def print_summary(out, log=print):
    log(f"\n{out['period']['from']} to {out['period']['to']}, {out['period']['shares']} shares, {out['period']['breakout_days']} breakout days. {out['holdout']['note']}")
    log(f"{'':40s}{'part':11s}{'trades':>7s}{'avg R':>8s}{'95% range':>17s}{'win %':>7s}{'hold':>6s}")
    for e in out["experiments"]:
        for part in ("all", "development", "validation", "holdout"):
            b = e.get(part)
            if not b or not b.get("n"):
                continue
            ci = b["ci_avg_r"]
            log(f"{e['name'][:39]:39s} {part[:10]:11s}{b['n']:7d}{b['avg_r']:8.3f}{(f'[{ci[0]:+.2f}, {ci[1]:+.2f}]' if ci else 'n/a'):>17s}{(b['win_rate'] or 0):7.1f}{b['avg_hold']:6.1f}")
    log("\nPlacebo (random entry, same shares, same filters, same exits):")
    for p in out["placebo"]:
        pb = p["placebo"]
        log(f"  {p['id']:8s} breakout {p['real'].get('avg_r')}R  vs random {pb['mean'] if pb else 'n/a'}R {pb['ci'] if pb else ''}  beats random: {p.get('breakout_beats_random')}")
    log("\nPortfolio (1.5% risk, 5 positions): " + json.dumps(out["portfolio"]))
    log("Nifty buy and hold: " + json.dumps(out["nifty_buy_and_hold"]))
    log("Survivor probe: " + json.dumps(out["survivor_probe"]))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=None, help="Yahoo daily price cache (universe: yahoo)")
    ap.add_argument("--universe", choices=["yahoo", "nse"], default="nse",
                    help="yahoo = today's Nifty 200 (survivors only); nse = the 200 most traded shares on each date, from NSE's daily files")
    ap.add_argument("--bhav", default=None, help="folder of NSE daily files made by python -m engine.bhav (universe: nse)")
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--stocks", type=int, default=0)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_data", "daily_research.json"))
    ap.add_argument("--holdout-from", default=None)
    ap.add_argument("--final", action="store_true")
    ap.add_argument("--force-final", action="store_true")
    a = ap.parse_args(argv)
    log = lambda m: print(m, flush=True)
    if a.final and os.path.exists(a.out) and not a.force_final:
        try:
            if json.load(open(a.out, encoding="utf-8")).get("holdout", {}).get("unsealed"):
                sys.exit("The holdout was already unsealed by an earlier run. Pass --force-final if you really mean to use it again.")
        except (OSError, ValueError):
            pass
    from .strategies import backtest as stbt
    nf, _ = load_daily([NIFTY], a.cache, a.years, log)
    members, label = None, "today's Nifty 200 (survivors only)"
    if a.universe == "yahoo":
        uni = stbt.universe()[:a.stocks] if a.stocks else stbt.universe()
        data, _ = load_daily(uni, a.cache, a.years, log)
    else:
        from . import bhav
        if not a.bhav or not os.path.isdir(a.bhav):
            sys.exit("Give --bhav <folder> made by:  python -m engine.bhav --cache <folder>")
        log("Reading NSE's daily files ...")
        candles, meta, _ = bhav.build(bhav.load_days(a.bhav), log=log)
        data = {isin: Daily(c, ended=meta[isin]["ended"]) for isin, c in candles.items()}
        names = {isin: m["symbol"] for isin, m in meta.items()}
        members = bhav.point_in_time_members(data)
        label = "the 200 most traded shares on each date, from NSE's own daily files, including shares later delisted or merged"
        data = {names[i] + "|" + i[-4:]: D for i, D in data.items()}
        members = {d: {names[i] + "|" + i[-4:] for i in s} for d, s in members.items()}
    if NIFTY not in nf or not data:
        sys.exit("Could not load the daily prices.")
    results_days, missed = set(), 0
    if a.universe == "nse":
        from . import archetypes
        d0 = max(min(D.d[0] for D in data.values()), nf[NIFTY].d[0])
        results_days, missed = archetypes.load_results_days(d0, nf[NIFTY].d[-1], a.cache, log)
        log(f"  {len(results_days)} results announcements read" + (f" ({missed} pieces missing)" if missed else ""))
    out = research(data, nf[NIFTY], log, final=a.final, holdout_from=a.holdout_from, members=members, universe=label, results_days=results_days)
    out["archetypes"]["results_calendar"] = {"announcements": len(results_days), "missing_pieces": missed}
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print_summary(out, log)
    log(f"Written to {a.out}")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isdir(os.path.join(root, "_data", ".git")) and os.path.abspath(a.out).startswith(os.path.join(root, "_data")):
        from .store import Store
        Store(os.path.join(root, "_data"), "unused").publish(own={"daily_research.json"})
        log("Published to your dashboard.")


if __name__ == "__main__":
    main()
