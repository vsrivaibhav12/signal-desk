"""Backtest all ten strategies on past prices.

    python -m engine.strategies.backtest                       Fyers if logged in, otherwise Yahoo (free, ~60 days)
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
from ..backtest import report
from . import data, sim
from .defs import STRATEGIES

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
COLS = ["date", "name", "dir", "in", "out", "entry", "exit", "stop", "target", "qty", "risk", "gross", "cost",
        "net", "r", "how", "regime", "why"]


def load_config():
    return yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))


def universe():
    with open(os.path.join(ROOT, "engine", "nifty200.txt"), encoding="utf-8") as f:
        return [x.split("|")[0] for x in f if x.strip() and not x.startswith("#")]


def t_stat(trades):
    n = len(trades)
    if n < 3:
        return None
    rs = [t["net_r"] for t in trades]
    mean = sum(rs) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in rs) / (n - 1))
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
            t["exit_type"], t["regime"], t["why"] if with_why else None]


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
        "trades_total": len(trades),
        "trades": [row(t, i >= cut) for i, t in enumerate(shown)],
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["auto", "yahoo", "fyers"], default="auto")
    ap.add_argument("--days", type=int, default=250, help="calendar days of history to ask Fyers for")
    ap.add_argument("--out", default=os.path.join(ROOT, "_data", "strategies_backtest.json"))
    ap.add_argument("--cache", default=None)
    ap.add_argument("--stocks", type=int, default=0, help="only the first N stocks (quick tests)")
    a = ap.parse_args(argv)
    log = lambda m: print(m, flush=True)
    cfg = load_config()
    sc = sim.settings(cfg)
    uni = universe()[:a.stocks] if a.stocks else universe()
    markets = [m for m in cfg["markets"] if m.get("kind") == "index" and m.get("enabled", True)]
    insts = sim.build_instruments(markets, uni, sc["index_lots"])
    series, source, missing = {}, "", []
    fy = data.quiet_fyers_login(ROOT, log) if a.source in ("auto", "fyers") else None
    if a.source == "fyers" and not fy:
        sys.exit("Fyers is not logged in. Run the Trade engine first so a login is saved, then try again.")
    if fy:
        log(f"Loading {a.days} days of 5-minute candles from Fyers ...")
        series, missing = data.series_from_fyers(fy, list(insts), a.days, log)
        source = "Fyers, 5-minute candles"
        short = [s for s in missing]
        if short and a.source == "auto":
            log(f"Fyers had no data for {len(short)} symbols; filling them in from Yahoo Finance.")
            y, _ = data.series_from_yahoo(insts, markets, [i["name"] for s_, i in insts.items() if s_ in short and i["kind"] == "stock"],
                                          a.cache, log)
            series.update({k: v for k, v in y.items() if k in short})
            source += " (some symbols from Yahoo Finance)"
    else:
        if a.source == "auto":
            log("Fyers login not available, using free Yahoo Finance data instead.")
        log("Loading about 60 days of 5-minute candles from Yahoo Finance ...")
        series, missing = data.series_from_yahoo(insts, markets, uni, a.cache, log)
        source = "Yahoo Finance, 5-minute candles"
    if not series:
        sys.exit("No price data could be loaded.")
    ref = next((s for s in series.values() if len(s.days) > 20), next(iter(series.values())))
    days = ref.days
    log(f"Replaying {len(days)} sessions ({days[0]} to {days[-1]}) on {len(series)} symbols ...")
    results = sim.run_all(series, insts, sc, log=log)
    out_s = []
    for strat in STRATEGIES:
        r = analyse(strat, results[strat["id"]])
        out_s.append(r)
        s = r["summary"]
        log(f"  {strat['name']:28s} {s.get('n', 0):5d} trades  net ₹{s.get('pnl', 0):>10,.0f}  win {s.get('win_rate')}%")
    out = {
        "generated": clock.now().isoformat(timespec="minutes"), "source": source,
        "period": {"from": days[0].isoformat(), "to": days[-1].isoformat(), "days": len(days)},
        "universe": {"stocks": sum(1 for s in series if s.endswith("-EQ")), "indices": [i["name"] for s, i in insts.items()
                                                                                    if i["kind"] == "index" and s in series]},
        "cols": COLS, "strategies": out_s,
        "assumptions": [
            "Each trade risks about ₹500 on a share (position capped at ₹40,000) or one lot of an index future, so results are comparable between strategies.",
            "Shares are bought or sold intraday. Index trades use the index level as a stand-in for the futures price.",
            f"Costs deducted: ₹{sc['costs']['stock_per_trade']:.0f} plus {sc['costs']['stock_pct']}% of turnover and "
            f"{sc['costs']['stock_slippage_pct']}% slippage per leg on shares; ₹{sc['costs']['index_per_trade']:.0f} plus "
            f"{sc['costs']['index_pct']}% and {sc['costs']['index_slippage_pct']}% slippage per leg on index futures.",
            "A signal is acted on at the close of the candle that produced it. When one candle touches both the stop and the target, the stop is assumed to have been hit first.",
            "Indicators use only past candles. Index volume does not exist, so volume checks are skipped for indices and their average price line is a plain average.",
            "No setting was tuned to this data. Every number is the one set out on the strategy's tab.",
            f"All open trades are closed by {sc['exit_by']}.",
        ],
    }
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    log(f"\nWritten to {a.out} ({os.path.getsize(a.out) // 1024} KB)")
    if os.path.isdir(os.path.join(ROOT, "_data", ".git")) and os.path.abspath(a.out).startswith(os.path.join(ROOT, "_data")):
        from ..store import Store
        Store(os.path.join(ROOT, "_data"), "unused").publish(own={"strategies_backtest.json"})
        log("Published to your dashboard.")
    return out


if __name__ == "__main__":
    main()
