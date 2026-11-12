"""The luck check.

The same rules are run again on the same prices, but with the order of the moves inside each
day shuffled. That wipes out any real pattern while keeping each day's size of movement,
gaps and volume. Whatever the rules "earn" on shuffled prices is what luck, or a flaw in the
test itself, can produce. A real edge has to beat that.
"""
import random
import statistics as st

from .data import Bars
from .runner import run_variant


def scramble(bars, rng):
    out = {}
    for sym, b in bars.items():
        candles = []
        for day in b.days:
            lst = b.by_day[day]
            feats, prev = [], lst[0][1]
            for t, o, h, l, c, v in lst:
                feats.append((c / prev - 1, (h - max(o, c)) / o, (min(o, c) - l) / o))
                prev = c
            rng.shuffle(feats)
            p = lst[0][1]
            for (t, o, h, l, c, v), (r, ho, lo) in zip(lst, feats):
                c2 = p * (1 + r)
                candles.append([t, p, max(p, c2) + ho * p, min(p, c2) - lo * p, c2, v])
                p = c2
        out[sym] = Bars(candles, b.res)
    return out


def per_trade(trades, kind):
    sub = [t for t in trades if t["kind"] == kind]
    return (sum(t["pnl"] for t in sub) / len(sub)) if sub else 0.0, len(sub)


def luck_check(cfg, bars, days, bt, real_trades, runs=5, fine_when_open=False, log=print):
    rows = []
    for i in range(runs):
        log(f"    luck check {i + 1} of {runs}")
        trades, _, _ = run_variant(cfg, scramble(bars, random.Random(9000 + i)), days, bt,
                                   fine_when_open=fine_when_open, log=lambda m: None)
        g = sum(t["pnl"] for t in trades)
        n = sum(t["net"] for t in trades)
        rows.append({"n": len(trades), "gross": g, "net": n, "gross_per_trade": g / len(trades) if trades else 0,
                     "opt": per_trade(trades, "option")[0], "stk": per_trade(trades, "stock")[0]})
    real_g = sum(t["pnl"] for t in real_trades)
    real_pt = real_g / len(real_trades) if real_trades else 0
    lo = min(r["gross_per_trade"] for r in rows)
    hi = max(r["gross_per_trade"] for r in rows)
    if real_pt > hi:
        read = "above"
    elif real_pt < lo:
        read = "below"
    else:
        read = "inside"
    return {
        "runs": runs, "avg_trades": round(st.mean(r["n"] for r in rows)),
        "avg_gross": round(st.mean(r["gross"] for r in rows)), "avg_net": round(st.mean(r["net"] for r in rows)),
        "gross_per_trade_avg": round(st.mean(r["gross_per_trade"] for r in rows), 1),
        "gross_per_trade_low": round(lo, 1), "gross_per_trade_high": round(hi, 1),
        "option_per_trade": round(st.mean(r["opt"] for r in rows), 1),
        "stock_per_trade": round(st.mean(r["stk"] for r in rows), 1),
        "real_gross_per_trade": round(real_pt, 1),
        "real_option_per_trade": round(per_trade(real_trades, "option")[0], 1),
        "real_stock_per_trade": round(per_trade(real_trades, "stock")[0], 1),
        "read": read,
    }
