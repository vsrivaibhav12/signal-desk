"""Checks the daily base-and-breakout research. Run:  python -m tests.test_daily    (offline)"""
import json
import os
import random
import shutil
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import daily as dl  # noqa: E402
from engine.clock import at, ts  # noqa: E402
from tests.test_strategies import check, weekdays  # noqa: E402

R = dl.RULES


def candles(rows, end=date(2026, 11, 12)):
    """rows: (open, high, low, close, volume) per weekday, oldest first."""
    days = weekdays(len(rows), end)
    return [[ts(at(d, "09:15")), *r] for d, r in zip(days, rows)]


def quiet(n, price=100.0, vol=1_000_000, drift=0.0004, rng=None):
    """n sessions in a gentle uptrend with a small range (a base)."""
    rng, out, p = rng or random.Random(5), [], price
    for _ in range(n):
        o = p
        c = p * (1 + drift + rng.uniform(-0.004, 0.004))
        out.append((o, max(o, c) * 1.003, min(o, c) * 0.997, c, vol))
        p = c
    return out


def breakout_world(after, volume_mult=3.0):
    """300 quiet sessions, then a breakout day on big volume, then the `after` rows. Returns (Daily, index of the breakout day)."""
    base = quiet(300)
    last = base[-1][3]
    pivot = max(r[1] for r in base[-40:])
    c = pivot * 1.03
    brk = (last, c * 1.005, last, c, int(1_000_000 * volume_mult))           # closes 3% above the base high on 3x volume
    rows = base + [brk] + after
    return dl.Daily(candles(rows)), 300, pivot


def row_from(prev_close, o=None, h=None, l=None, c=None, v=1_000_000):
    o = prev_close if o is None else o
    c = o if c is None else c
    return (o, max(o, c) if h is None else h, min(o, c) if l is None else l, c, v)


def test_basics():
    print("Daily: indicators and signals use only the past")
    check("rolling mean", dl.rolling_mean([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0])
    D, i, pivot = breakout_world([row_from(103)] * 3)
    sig = dl.signals(D, {})
    got = [s for s in sig if s["i"] == i]
    check("the breakout day is found, with its pivot and volume ratio", len(got) == 1 and abs(got[0]["pivot"] - pivot) < 1e-9
          and 2.9 < got[0]["volx"] < 3.1, got)
    check("an ordinary day is not a breakout", all(s["i"] == i for s in sig), [s["i"] for s in sig][:5])
    # no look-ahead: the signals on a prefix of the data are exactly the signals the full data gives for those days
    full = dl.signals(D, {})
    m = i + 3
    cut = dl.Daily(D.raw[:m])
    pre = dl.signals(cut, {})
    check("signals on a truncated history match the full history", [(s["i"], s["pivot"], s["trend"], s["tight"]) for s in pre
          if s["i"] < m - 1] == [(s["i"], s["pivot"], s["trend"], s["tight"]) for s in full if s["i"] < m - 1])
    check("indicators on a prefix equal the full run's", cut.sma200[m - 1] == D.sma200[m - 1] and cut.pivot[m - 1] == D.pivot[m - 1])
    low_vol, _, _ = breakout_world([row_from(103)], volume_mult=1.2)
    check("a breakout on ordinary volume is not a signal", not [s for s in dl.signals(low_vol, {}) if s["i"] == 300])
    far = quiet(300)
    last = far[-1][3]
    far.append((last, last * 1.2, last, last * 1.15, 3_000_000))                # already 15% above: too extended
    check("a close far above the pivot is not chased", not [s for s in dl.signals(dl.Daily(candles(far + [row_from(last * 1.15)])), {}) if s["i"] == 300])
    thin = dl.Daily(candles(quiet(300, vol=100) + [(100, 110, 100, 108, 300), row_from(108)]))
    check("an illiquid share is skipped", dl.signals(thin, {}) == [])
    tr = dl.trend_ok(D, i)
    check("a steady climb passes the trend template", tr is True, tr)


def entry_of(D, i, pivot):
    return D.o[i + 1]


def test_fills():
    print("Daily: entries, stops, targets and costs")
    cost_r = R["cost_round_trip"] / R["stop"]
    # entry at tomorrow's open, then a stop-out
    D, i, pivot = breakout_world([row_from(103.0, o=103.0, c=103.0), row_from(103.0, c=100.0, l=90.0), row_from(95)])
    t = dl.simulate(D, i, pivot, "trail")
    stop = t["entry"] * (1 - R["stop"])
    check("bought at the next open", abs(t["entry"] - round(D.o[i + 1], 2)) < 1e-9)
    check("the stop is hit at the stop price", t["reason"] == "stop" and abs(t["exit"] - round(stop, 2)) < 0.011, t)
    check("a stop-out is minus one R, less costs", abs(t["gross_r"] + 1) < 0.02 and abs(t["net_r"] - (t["gross_r"] - cost_r)) < 1e-6, (t["gross_r"], t["net_r"]))
    # a gap through the stop fills at the open, worse than the stop
    D2, i2, p2 = breakout_world([row_from(103.0), row_from(103.0, o=85.0, h=86.0, l=80.0, c=82.0), row_from(82)])
    t2 = dl.simulate(D2, i2, p2, "trail")
    check("a gap through the stop fills at the open", t2["reason"] == "stop" and t2["exit"] == 85.0 and t2["gross_r"] < -1.5, t2)
    # target: stop is checked first on a bar that touches both
    D3, i3, p3 = breakout_world([row_from(103.0), row_from(103.0, o=103.0, h=140.0, l=90.0, c=120.0)])
    both = dl.simulate(D3, i3, p3, "target")
    check("a bar that touches the stop and the target counts as the stop", both["reason"] == "stop", both)
    D4, i4, p4 = breakout_world([row_from(103.0), row_from(103.0, o=104.0, h=140.0, l=104.0, c=130.0)])
    hit = dl.simulate(D4, i4, p4, "target")
    tgt = hit["entry"] * (1 + R["target_r"] * R["stop"])
    check("the target pays three R, less costs", hit["reason"] == "target" and abs(hit["exit"] - round(tgt, 2)) < 0.011
          and abs(hit["net_r"] - (3 - cost_r)) < 0.02, hit)
    gap_up = breakout_world([row_from(103.0), row_from(103.0, o=150.0, h=152.0, l=149.0, c=151.0)])
    ug = dl.simulate(gap_up[0], gap_up[1], gap_up[2], "target")
    check("a gap above the target fills at the open", ug["exit"] == 150.0 and ug["gross_r"] > 3, ug)
    # time exit
    flat = [row_from(103.0)] * (R["time_stop"] + 2)
    D5, i5, p5 = breakout_world(flat)
    tm = dl.simulate(D5, i5, p5, "target")
    check("the target exit leaves after 60 sessions", tm["reason"] == "time" and tm["hold"] == R["time_stop"] - 1, (tm["reason"], tm["hold"]))
    # trail: runs, then closes below the 20-day average and leaves at the next open
    up = []
    p = 103.0
    for _ in range(25):
        p *= 1.012
        up.append(row_from(p / 1.012, c=p))
    drop = row_from(p, o=p, h=p, l=p * 0.88, c=p * 0.90)
    D6, i6, p6 = breakout_world(up + [drop, row_from(p * 0.88, o=p * 0.88)])
    tl = dl.simulate(D6, i6, p6, "trail")
    check("the trailing exit leaves at the next open after the trend breaks", tl["reason"] == "trend break" and tl["gross_r"] > 0
          and tl["exit_date"] > tl["entry_date"], tl)
    # a share that gaps far above the pivot is skipped
    D7, i7, p7 = breakout_world([row_from(103.0, o=p7 * 1.2 if False else 150.0, c=150.0)])
    check("a gap more than 8% above the pivot is not bought", dl.simulate(D7, i7, p7, "trail") is None)
    # open at the end of the data
    D8, i8, p8 = breakout_world([row_from(103.0), row_from(104.0)])
    check("a trade still open at the end is flagged", dl.simulate(D8, i8, p8, "trail")["open"] is True)


def world(seed=3, n=800, shares=30):
    """A small market: a walk with drift per share, volume spikes, and a Nifty to follow."""
    rng = random.Random(seed)
    data = {}
    for k in range(shares):
        rows, p = [], 100.0 + 20 * k
        for _ in range(n):
            o = p
            c = p * (1 + rng.gauss(0.0006, 0.014))
            v = 1_000_000 * (3.0 if rng.random() < 0.03 else rng.uniform(0.6, 1.3))
            rows.append((o, max(o, c) * 1.004, min(o, c) * 0.996, c, int(v)))
            p = c
        data[f"S{k}"] = dl.Daily(candles(rows))
    rows, p = [], 20000.0
    for _ in range(n):
        o = p
        c = p * (1 + rng.gauss(0.0004, 0.009))
        rows.append((o, max(o, c) * 1.003, min(o, c) * 0.997, c, 0))
        p = c
    return data, dl.Daily(candles(rows))


def test_experiments():
    print("Daily: experiments, seal, placebo, portfolio")
    data, nifty = world()
    out = dl.research(data, nifty, log=lambda m: None, placebo_draws=10)
    ids = [e["id"] for e in out["experiments"]]
    check("exactly the pre-registered experiments are run", ids == [e[0] for e in dl.EXPERIMENTS], ids)
    n = {e["id"]: e["all"].get("n", 0) for e in out["experiments"]}
    check("each filter can only remove trades (trail)", n["base"] >= n["trend"] >= n["tight"] >= n["full"], n)
    check("each filter can only remove trades (target)", n["base_t"] >= n["trend_t"] >= n["tight_t"] >= n["full_t"], n)
    check("there are trades to look at", n["base"] > 20, n)
    check("the holdout is sealed: not reported, and the run ends before it", "holdout" not in out["experiments"][0]
          and out["period"]["to"] < out["splits"]["holdout"]["from"], (out["period"], out["splits"]["holdout"]))
    # the seal really cuts the data: wrecking every price after the holdout starts changes nothing
    cut = date.fromisoformat(out["splits"]["holdout"]["from"])
    wrecked = {}
    for sym, D in data.items():
        rows = [list(c) for c in D.raw]
        for r in rows:
            if date.fromtimestamp(r[0]) >= cut:
                r[1], r[2], r[3], r[4], r[5] = 1.0, 1.0, 1.0, 1.0, 1
        wrecked[sym] = dl.Daily(rows)
    nrows = [list(c) for c in nifty.raw]
    for r in nrows:
        if date.fromtimestamp(r[0]) >= cut:
            r[1], r[2], r[3], r[4] = 1.0, 1.0, 1.0, 1.0
    out2 = dl.research(wrecked, dl.Daily(nrows), log=lambda m: None, placebo_draws=10)
    same = ([e["all"] for e in out["experiments"]] == [e["all"] for e in out2["experiments"]]
            and [e["all"] for e in out["context_experiments"]] == [e["all"] for e in out2["context_experiments"]]
            and out["books"] == out2["books"] and out["playbook"] == out2["playbook"] and out["archetypes"] == out2["archetypes"])
    check("prices after the holdout starts cannot change any result (no leak)", same)
    opened = dl.research(data, nifty, log=lambda m: None, final=True, placebo_draws=5)
    check("with --final the holdout is reported and more sessions are covered", "holdout" in opened["experiments"][0]
          and opened["period"]["to"] >= out["splits"]["holdout"]["to"] and opened["holdout"]["unsealed"])
    check("every trade closed or flagged open: open trades are not counted in the averages",
          all("open" in e["all"] for e in out["experiments"] if e["all"].get("n")))
    plc = out["placebo"]
    check("the placebo is reported for the breakout rows and the context rows", [p["id"] for p in plc] == ["trend", "trend_t", "full", "full_t",
          "ctx_rs", "ctx_breadth", "ctx_both", "ctx_rs_t", "ctx_breadth_t", "ctx_both_t"], [p["id"] for p in plc])
    check("and says whether the breakout beat random entry", all(p["breakout_beats_random"] in (True, False, None) for p in plc))
    pb = next(p["placebo"] for p in plc if p["placebo"])
    check("the placebo has an interval", pb["ci"][0] <= pb["mean"] <= pb["ci"][1] and pb["draws"] > 0, pb)
    check("the report is plain data", json.loads(json.dumps(out)) == out)
    check("the survivor probe and the market benchmark are reported", out["survivor_probe"].get("shares") == len(data) and out["nifty_buy_and_hold"].get("cagr_pct") is not None)
    check("the limits are stated", any("Survivorship" in a for a in out["assumptions"]) and any("fixed before any result" in a for a in out["assumptions"]))
    check("every result has a by-year view and a concentration view", "by_year_full_system" in out and "concentration_full_system" in out)


def test_portfolio():
    print("Daily: the portfolio")
    D, i, pivot = breakout_world([row_from(103.0, o=104.0, h=140.0, l=104.0, c=130.0), row_from(130)])
    t = dl.simulate(D, i, pivot, "target")
    tr = [dict(t, symbol="X", volx=3.0)]
    dates = D.d[i:]
    curve, taken = dl.portfolio(tr, {"X": D}, dates)
    gain = (t["exit"] / t["entry"] - 1) * (R["risk_per_trade"] / R["stop"]) - R["cost_round_trip"] * (R["risk_per_trade"] / R["stop"])
    check("one winner at 1.5% risk adds about 4.4% after costs", taken == 1 and abs(curve[-1] - (1 + gain)) < 1e-6, (taken, curve[-1], 1 + gain))
    check("the curve starts at one", abs(curve[0] - 1.0) < 0.05)
    many = [dict(t, symbol="X", volx=float(k)) for k in range(9)]
    c2, taken2 = dl.portfolio(many, {"X": D}, dates)
    check("no more than five positions are open at once", taken2 == R["max_positions"], taken2)
    st = dl.curve_stats([1.0, 1.1, 0.9, 1.2] + [1.2] * 40, [date(2020, 1, 1) + (date(2020, 1, 2) - date(2020, 1, 1)) * k for k in range(44)])
    check("drawdown is measured from the peak", abs(st["max_drawdown_pct"] - 18.2) < 0.1, st)


def test_guard():
    print("Daily: the holdout cannot be used twice")
    d = tempfile.mkdtemp()
    try:
        path = os.path.join(d, "daily_research.json")
        json.dump({"holdout": {"unsealed": True}}, open(path, "w"))
        try:
            dl.main(["--final", "--out", path])
            check("a second --final is refused", False)
        except SystemExit as e:
            check("a second --final is refused", "already unsealed" in str(e), str(e))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    test_basics()
    test_fills()
    test_experiments()
    test_portfolio()
    test_guard()
    print("\nALL DAILY CHECKS PASSED" if not check.failed else f"\n{check.failed} CHECK(S) FAILED")
    sys.exit(1 if check.failed else 0)


if __name__ == "__main__":
    main()
