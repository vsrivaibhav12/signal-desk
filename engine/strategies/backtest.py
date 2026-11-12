"""Backtest all ten strategies on past prices.

    python -m engine.strategies.backtest                       Kotak Neo or Fyers if available, otherwise Yahoo (free, ~60 days)
    python -m engine.strategies.backtest --source yahoo        free source only
    python -m engine.strategies.backtest --source fyers --days 365
"""
import argparse
import json
import math
import os
import sys
from collections import defaultdict

import yaml

from .. import clock
from ..schema import SCHEMA
from ..backtest import report
from . import data, sim, risk
from .defs import STRATEGIES

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CHARTS_KEPT = 100        # the latest trades of each strategy that get a chart
COLS = ["date", "name", "dir", "in", "out", "entry", "exit", "stop", "target", "qty", "risk", "gross", "cost",
        "net", "r", "how", "regime", "why", "id", "dec"]


def load_config():
    return yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))


def universe():
    with open(os.path.join(ROOT, "engine", "nifty200.txt"), encoding="utf-8") as f:
        return [x.split("|")[0] for x in f if x.strip() and not x.startswith("#")]


def t_stat(trades):
    """Session-clustered t-statistic on daily average R, not trade-level pseudo-replication."""
    by_day = {}
    for trade in trades:
        by_day.setdefault(trade["date"], []).append(float(trade.get("net_r", 0.0) or 0.0))
    daily = [sum(values) / len(values) for _, values in sorted(by_day.items()) if values]
    n = len(daily)
    if n < 3:
        return None
    mean = sum(daily) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in daily) / (n - 1))
    return round(mean / (sd / math.sqrt(n)), 2) if sd else None


def market_of(t):
    return "Stocks" if t["kind"] == "stock" else "Index futures"


def verdict(s, gross, halves, ts, name):
    """Plain-language reading of one strategy's result. Honest about luck and sample size."""
    n = s.get("n", 0)
    if n < 30:
        return "few", f"Only {n} trades were triggered, which is too few to say anything. A longer history is needed."
    net = s["pnl"]
    parts = []
    if net > 0:
        parts.append(f"After estimated costs {name} made ₹{net:,.0f} over {n} trades ({s['win_rate']}% winners).")
    else:
        parts.append(f"After estimated costs {name} lost ₹{-net:,.0f} over {n} trades ({s['win_rate']}% winners).")
    if gross > 0 and net <= 0:
        parts.append(f"Before costs it was ahead by ₹{gross:,.0f}, so trading costs took all of it.")
    elif gross <= 0:
        parts.append("It was behind even before costs.")
    h1, h2 = halves
    if h1.get("n", 0) >= 10 and h2.get("n", 0) >= 10:
        a, b = h1["pnl"], h2["pnl"]
        if a > 0 and b > 0:
            parts.append("Both the first and the second half of the period were profitable.")
        elif a <= 0 and b <= 0:
            parts.append("Neither half of the period was profitable.")
        else:
            parts.append(f"Results were not steady: the first half {'made' if a > 0 else 'lost'} "
                         f"₹{abs(a):,.0f} and the second half {'made' if b > 0 else 'lost'} ₹{abs(b):,.0f}.")
    if net > 0 and ts is not None and ts >= 2 and h1.get("pnl", 0) > 0 and h2.get("pnl", 0) > 0:
        parts.append("The average result per trade is large enough, compared with its variation, to look more than luck. "
                     "That is promising, but a longer history and a live record must confirm it.")
        level = "good"
    elif net > 0:
        parts.append("The profit is not yet large or steady enough to rule out luck.")
        level = "mixed"
    else:
        parts.append("There is no sign of an edge here yet.")
        level = "bad"
    if n < 150:
        parts.append("This is a short sample, so treat it as an early reading.")
    return level, " ".join(parts)


def thin(points, keep=240):
    if len(points) <= keep:
        return points
    step = len(points) / keep
    out = [points[int(i * step)] for i in range(keep)]
    out[-1] = points[-1]
    return out


def row(t, with_why):
    return [t["date"], t["name"], t["dir"], t["entry_t"][11:16], t["exit_t"][11:16], t["entry"], t["exit"],
            t["stop"], t["target"], t["qty"], t["risk"], t["gross"], t["cost"], t["net"], t["net_r"],
            t["exit_type"], t["regime"], t["why"] if with_why else None, t["id"], t["dec"]]


def graded(trades):
    """How the trades the tool would call 'take' did, against all of them."""
    tk = [t for t in trades if t["dec"]["g"] == "take"]
    mid = len(tk) // 2
    out = {"n": len(tk), "summary": report.summarize(tk) if tk else {"n": 0},
           "halves": [report.summarize(tk[:mid]) if mid else {"n": 0}, report.summarize(tk[mid:]) if mid else {"n": 0}]}
    if tk:
        out["summary"]["gross_pnl"] = round(sum(t["gross"] for t in tk), 2)
        out["summary"]["gross_r"] = round(sum(t["gross"] / t["risk"] for t in tk if t["risk"]) / len(tk), 3)
    return out


def analyse(strat, trades, keep_rows=300, keep_why=100):
    s = report.summarize(trades) if trades else {"n": 0}
    gross = sum(t["gross"] for t in trades)
    mid = len(trades) // 2
    halves = [report.summarize(trades[:mid]) if mid else {"n": 0}, report.summarize(trades[mid:]) if mid else {"n": 0}]
    ts = t_stat(trades)
    level, text = verdict(s, gross, halves, ts, strat["name"])
    if s.get("n"):
        s["gross_pnl"] = round(gross, 2)
        s["total_r"] = round(sum(t["net_r"] for t in trades), 1)
        s["t_stat"] = ts
        s["cost_total"] = round(sum(t["cost"] for t in trades), 2)
        s["gross_r"] = round(sum(t["gross"] / t["risk"] for t in trades if t["risk"]) / len(trades), 3)
        s["avg_risk"] = round(sum(t["risk"] for t in trades) / len(trades), 0)
        s["avg_cost"] = round(s["cost_total"] / len(trades), 0)
    curve, eq = [], 0.0
    for t in trades:
        eq += t["net"]
        curve.append(round(eq, 2))
    shown = trades[-keep_rows:]
    cut = len(shown) - keep_why
    return {
        "id": strat["id"], "name": strat["name"], "source": strat["source"], "best_for": strat["best_for"],
        "risk": strat["risk"], "rules": strat["rules"], "window": strat["window"],
        "summary": s, "halves": halves, "verdict": text, "level": level,
        "by_market": report.group(trades, market_of), "by_exit": report.group(trades, lambda t: t["exit_type"]),
        "by_side": report.group(trades, lambda t: "Buy" if t["dir"] == 1 else "Sell"),
        "by_month": report.group(trades, report.month_of, order=None),
        "curve": thin(curve),
        "take": graded(trades), "trades_total": len(trades),
        "trades": [row(t, i >= cut) for i, t in enumerate(shown)],
    }


def chronological_validation(trades):
    """Simple chronological 60/20/20 diagnostic split on risk-approved closed trades.

    This is a reporting split, not a parameter-training process. It must not be
    described as an untouched holdout if a researcher has already viewed the data.
    """
    eligible = [t for t in trades if t.get("risk_approved") and t.get("status") == "CLOSED"]
    sessions = sorted({t["date"] for t in eligible})
    if len(sessions) < 5:
        return {"sessions": len(sessions), "warning": "Too few sessions for a useful chronological split.",
                "train": {"sessions": 0, "trades": 0}, "validation": {"sessions": 0, "trades": 0},
                "test": {"sessions": 0, "trades": 0}}
    a = max(1, int(len(sessions) * 0.6))
    b = max(a + 1, int(len(sessions) * 0.8))
    b = min(b, len(sessions) - 1)
    cuts = {"train": set(sessions[:a]), "validation": set(sessions[a:b]), "test": set(sessions[b:])}
    out = {"sessions": len(sessions), "warning": "Chronological diagnostics only; not a parameter-optimised walk-forward test."}
    for name, dates in cuts.items():
        subset = [t for t in eligible if t["date"] in dates]
        out[name] = {"sessions": len(dates), "trades": len(subset), "summary": report.summarize(subset) if subset else {"n": 0}}
    return out


def stability_blocks(trades, blocks=5):
    """Non-overlapping chronological blocks that expose regime/period instability."""
    eligible = [t for t in trades if t.get("risk_approved") and t.get("status") == "CLOSED"]
    sessions = sorted({t["date"] for t in eligible})
    if not sessions:
        return []
    out = []
    for i in range(min(blocks, len(sessions))):
        lo = round(i * len(sessions) / min(blocks, len(sessions)))
        hi = round((i + 1) * len(sessions) / min(blocks, len(sessions)))
        dates = set(sessions[lo:hi])
        subset = [t for t in eligible if t["date"] in dates]
        out.append({"from": min(dates), "to": max(dates), "sessions": len(dates),
                    "summary": report.summarize(subset) if subset else {"n": 0}})
    return out


def load_series(source, days, insts, markets, uni, cache, log):
    """Five-minute candles for every instrument: Kotak Neo or Fyers if available, otherwise Yahoo. Returns (series, source text)."""
    a = argparse.Namespace(source=source, days=days, cache=cache)
    series, source, missing = {}, "", []
    fy, label = None, ""
    if a.source in ("auto", "kotak"):
        fy, label = data.quiet_kotak(log), "Kotak Neo"
    if fy is None and a.source in ("auto", "fyers"):
        fy, label = data.quiet_fyers_login(ROOT, log), "Fyers"
    if a.source == "kotak" and not fy:
        sys.exit("Kotak Neo prices are not available. Check that the KOTAK_CONSUMER_KEY secret is set and correct.")
    if a.source == "fyers" and not fy:
        sys.exit("Fyers is not logged in. Run the Trade engine first so a login is saved, then try again.")
    if fy:
        log(f"Loading {a.days} days of 5-minute candles from {label} ...")
        series, missing = data.series_from_broker(fy, list(insts), a.days, log, label)
        source = f"{label}, 5-minute candles"
        short = [s for s in missing]
        if short and a.source == "auto":
            log(f"{label} had no data for {len(short)} symbols; filling them in from Yahoo Finance.")
            y, _ = data.series_from_yahoo(insts, markets, [i["name"] for s_, i in insts.items() if s_ in short and i["kind"] == "stock"],
                                          a.cache, log)
            series.update({k: v for k, v in y.items() if k in short})
            source += " (some symbols from Yahoo Finance)"
    else:
        if a.source == "auto":
            log("No broker prices available, using free Yahoo Finance data instead.")
        log("Loading about 60 days of 5-minute candles from Yahoo Finance ...")
        series, missing = data.series_from_yahoo(insts, markets, uni, a.cache, log)
        source = "Yahoo Finance, 5-minute candles"
    return series, source


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["auto", "yahoo", "kotak", "fyers"], default="auto")
    ap.add_argument("--days", type=int, default=365, help="calendar days of history to ask the broker for (about 250 market sessions)")
    ap.add_argument("--out", default=os.path.join(ROOT, "_data", "strategies_backtest.json"))
    ap.add_argument("--cache", default=None)
    ap.add_argument("--stocks", type=int, default=0, help="only the first N stocks (quick tests)")
    ap.add_argument("--entry-mode", choices=["next_open", "signal_close"], default=None,
                    help="execution assumption; next_open is the recommended conservative default")
    a = ap.parse_args(argv)
    log = lambda m: print(m, flush=True)
    cfg = load_config()
    sc = sim.settings(cfg)
    if a.entry_mode:
        sc["execution"]["entry_mode"] = a.entry_mode
    uni = universe()[:a.stocks] if a.stocks else universe()
    markets = [m for m in cfg["markets"] if m.get("kind") == "index" and m.get("enabled", True)]
    insts = sim.build_instruments(markets, uni, sc["index_lots"])
    series, source = load_series(a.source, a.days, insts, markets, uni, a.cache, log)
    if not series:
        sys.exit("No price data could be loaded.")
    ref = next((s for s in series.values() if len(s.days) > 20), next(iter(series.values())))
    days = ref.days
    log(f"Replaying {len(days)} sessions ({days[0]} to {days[-1]}) on {len(series)} symbols ...")
    chart_from = days[-12] if len(days) > 12 else days[0]          # charts are drawn only for the latest sessions
    results = sim.run_all(series, insts, sc, log=log, chart_from=chart_from)
    out_s = []
    out_dir = os.path.dirname(a.out)
    os.makedirs(out_dir, exist_ok=True)
    chart_files = set()
    for strat in STRATEGIES:
        trades = results[strat["id"]]
        r = analyse(strat, trades)
        out_s.append(r)
        charts = {t["id"]: t["_chart"] for t in trades[-CHARTS_KEPT:] if "_chart" in t}
        name = f"chartbt_{strat['id']}.json"
        json.dump(charts, open(os.path.join(out_dir, name), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
        chart_files.add(name)
        for t in trades:
            t.pop("_chart", None)
        s = r["summary"]
        log(f"  {strat['name']:28s} {s.get('n', 0):5d} trades  net ₹{s.get('pnl', 0):>10,.0f}  win {s.get('win_rate')}%")
    everything = sorted((t for v in results.values() for t in v), key=lambda t: t["entry_ts"])
    sessions = len({t["date"] for t in everything}) or 1
    take_all = graded(everything)
    portfolio_summary = risk.summary(everything)
    portfolio_validation = chronological_validation(everything)
    stability = stability_blocks(everything)
    decision = {"all": {"n": len(everything), "summary": report.summarize(everything) if everything else {"n": 0}},
                "take": take_all, "take_per_day": round(take_all["n"] / sessions, 1),
                "rules": {"cost": f"Round-trip cost is at most {int(sc['decide']['max_cost_ratio'] * 100)}% of the amount risked",
                          "vol": f"Volume is at least {sc['decide']['min_volume']:g}x normal for this time of day (indices pass)",
                          "mkt": "Nifty is on the same side of its VWAP as the trade",
                          "conf": "No other strategy is already on the opposite side of the same stock today"}}
    out = {
        "decision": decision,
        "schema": SCHEMA,
        "generated": clock.now().isoformat(timespec="minutes"), "source": source,
        "period": {"from": days[0].isoformat(), "to": days[-1].isoformat(), "days": len(days)},
        "universe": {"stocks": sum(1 for s in series if s.endswith("-EQ")), "indices": [i["name"] for s, i in insts.items()
                                                                                    if i["kind"] == "index" and s in series]},
        "cols": COLS, "strategies": out_s,
        "portfolio": {"limits": sc.get("portfolio", {}), "performance": portfolio_summary,
                      "chronological_validation": portfolio_validation, "stability_blocks": stability},
        "assumptions": [
            "Each trade risks about ₹500 on a share (position capped at ₹40,000) or one lot of an index future, so results are comparable between strategies.",
            "Shares are bought or sold intraday. Index trades use the index level as a stand-in for the futures price.",
            f"Costs deducted: ₹{sc['costs']['stock_per_trade']:.0f} plus {sc['costs']['stock_pct']}% of turnover and "
            f"{sc['costs']['stock_slippage_pct']}% slippage per leg on shares; ₹{sc['costs']['index_per_trade']:.0f} plus "
            f"{sc['costs']['index_pct']}% and {sc['costs']['index_slippage_pct']}% slippage per leg on index futures.",
            ("A signal is executed at the next five-minute candle open; a gap through the stop invalidates the trade."
             if sc.get("execution", {}).get("entry_mode") == "next_open" else
             "A signal is executed at the signal candle close; this is an optimistic reference model, not the recommended fill assumption."),
            "Indicators use only past candles. Index volume does not exist, so volume checks are skipped for indices and their average price line is a plain average.",
            "No setting was tuned to this data. Every number is the one set out on the strategy's tab.",
            f"All open trades are closed by {sc['exit_by']}.",
            "Strategy tabs show standalone candidate results. The portfolio report separately applies TAKE-grade admission, one concurrent position per underlying, aggregate open-risk, position-count and daily-loss limits.",
            "The chronological 60/20/20 split and stability blocks are diagnostic slices only; no parameter optimisation or untouched holdout is claimed.",
            "Session-clustered t-statistics use daily average R to reduce trade-level dependence; they are descriptive, not proof of an edge.",
        ],
    }
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    log(f"\nWritten to {a.out} ({os.path.getsize(a.out) // 1024} KB)")
    if os.path.isdir(os.path.join(ROOT, "_data", ".git")) and os.path.abspath(a.out).startswith(os.path.join(ROOT, "_data")):
        from ..store import Store
        Store(os.path.join(ROOT, "_data"), "unused").publish(own={"strategies_backtest.json"}, own_prefix=("chartbt_",))
        log("Published to your dashboard.")
    return out


if __name__ == "__main__":
    main()
