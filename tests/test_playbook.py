"""Checks the Leader Playbook tests: pivot and pullback signals, the exits, the placebo and the sizing. Run:  python -m tests.test_playbook (offline)"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import daily as dl, playbook as pb  # noqa: E402
from tests.test_daily import candles, world  # noqa: E402
from tests.test_strategies import check  # noqa: E402

R = pb.R2


def climb(n, start=100.0, drift=0.0015, vol=2_000_000):
    rows, p = [], start
    for _ in range(n):
        c = p * (1 + drift)
        rows.append((p, c * 1.002, p * 0.998, c, vol))
        p = c
    return rows


def day_row(prev_close, o=None, h=None, l=None, c=None, v=2_000_000):
    o = prev_close if o is None else o
    c = o if c is None else c
    return (o, max(o, c) * 1.0005 if h is None else h, min(o, c) * 0.9995 if l is None else l, c, v)


def rs_for(D, value=95.0, key="X"):
    return {key: {d: value for d in D.d}}


def test_ema_and_costs():
    print("Playbook: averages, fills and costs")
    check("the exponential average", [round(x, 4) for x in pb.ema([1, 2, 3], 2)] == [1, 1.6667, 2.5556])
    base = climb(320)
    last = base[-1][3]

    def trade(after, stop_frac, exit_rule="trail", ref=None):
        D = dl.Daily(candles(base + after))
        i = len(base) - 1
        return pb.simulate2(D, i, last * stop_frac, exit_rule, ref or last, None), D

    t, D = trade([day_row(last, o=last), day_row(last)], 0.95)
    check("bought at the next open, with the given stop", t["entry"] == round(D.o[len(base)], 2) and abs(t["risk_pct"] - (1 - 0.95 * last / D.o[len(base)])) < 1e-9, t)
    check("the cost in R grows as the stop gets tighter", abs(t["gross_r"] - t["net_r"] - 0.004 / t["risk_pct"]) < 1e-3)
    tight, _ = trade([day_row(last)], 0.9975)
    check("a stop closer than 1% is skipped", tight is None)
    wide, _ = trade([day_row(last)], 0.88)
    check("a stop farther than 8% is skipped", wide is None)
    gap, _ = trade([day_row(last, o=last * 1.08)], 0.95)
    check("a gap more than 5% above the signal close is not bought", gap is None)
    down, D2 = trade([day_row(last), day_row(last, o=last * 0.90, h=last * 0.91, l=last * 0.85, c=last * 0.88), day_row(last * 0.88)], 0.95)
    check("a gap through the stop fills at the open", down["reason"] == "stop" and down["exit"] == round(last * 0.90, 2) and down["gross_r"] < -1, down)
    e = D2.o[len(base)]
    # trail: up one R then a close below the 21-day average ends it at the next open
    up, p = [], last
    for _ in range(25):
        p *= 1.012
        up.append(day_row(p / 1.012, c=p))
    drop = day_row(p, o=p, h=p, l=p * 0.88, c=p * 0.90)
    tr, _ = trade(up + [drop, day_row(p * 0.88, o=p * 0.88)], 0.95)
    check("the trail leaves at the next open after a close below the 21-day average", tr["reason"] == "trend break" and tr["gross_r"] > 0, tr)


def test_partial():
    print("Playbook: the partial-profit exit")
    base = climb(320)
    last = base[-1][3]
    entry = last
    stop = last * 0.95
    risk = 0.05

    def run(after, rule="partial"):
        D = dl.Daily(candles(base + [day_row(last)] + after))
        return pb.simulate2(D, len(base) - 1, stop, rule, last, None), D

    # +20% reached on day two, then the stock falls back to the entry: 40% banked, the rest stopped at breakeven
    rows = [day_row(last, o=last, h=last * 1.005, c=last * 1.005), day_row(last, o=last * 1.15, h=last * 1.21, l=last * 1.14, c=last * 1.18),
            day_row(last * 1.18, o=last * 1.0, h=last * 1.0, l=last * 0.99, c=last * 0.995)]
    t, D = run(rows)
    e = D.o[len(base) + 1]
    want = 0.4 * 0.20 / (1 - stop / e)
    check("40% is sold at +20%, the rest stops at the entry", t["reason"] == "stop" and abs(t["exit"] - round(e, 2)) < 0.02
          and abs(t["gross_r"] - want) < 0.02, (t["reason"], t["exit"], t["gross_r"], want))
    plain, _ = run(rows, "trail")
    check("without the partial exit the same path is a stop-out at the entry, not a gain", plain["gross_r"] < t["gross_r"], (plain["gross_r"], t["gross_r"]))
    gap_rows = [day_row(last, o=last, c=last), day_row(last, o=last * 1.30, h=last * 1.32, l=last * 1.29, c=last * 1.31)]
    g, D2 = run(gap_rows + [day_row(last * 1.31, o=last * 0.9, h=last * 0.9, l=last * 0.8, c=last * 0.85)])
    e2 = D2.o[len(base) + 1]
    want = (0.4 * 0.30 + 0.6 * (0.90 - 1.0)) / 0.05              # 40% sold at the gap-up open (+30%), the rest stopped at the entry but gapped to -10%
    check("a gap over the +20% level sells at the open", abs(g["gross_r"] - want) < 0.03 and g["reason"] == "stop", (g["gross_r"], want, g["reason"]))
    # a stop before the partial level loses everything
    s, _ = run([day_row(last, o=last, h=last, l=last * 0.93, c=last * 0.94)])
    check("a stop hit before +20% loses the whole position", s["reason"] == "stop" and s["gross_r"] < -0.9, s)


def make_pivot(tight=True, vol_mult=3.0):
    base = climb(320)
    p = base[-1][3]
    rows = list(base)
    for k in range(10):                                           # a 10-session base about 3% deep (or 8% if not tight)
        amp = 0.006 if tight else 0.07
        c = p * (1 + (amp if k % 2 else -amp))
        rows.append((p, max(p, c) * 1.001, min(p, c) * 0.999, c, 1_000_000))
        p = c
    hi = max(r[1] for r in rows[-10:])
    brk = p * 0 + hi * 1.02
    rows.append((p, brk * 1.001, p, brk, int(2_000_000 * vol_mult)))
    rows.append(day_row(brk))
    return dl.Daily(candles(rows)), len(rows) - 2, hi, min(r[2] for r in rows[-12:-2])


def test_signals():
    print("Playbook: pivot and pullback signals")
    D, i, hi, lo = make_pivot()
    rs = rs_for(D)
    sig = pb.pivot_signals(D, "X", None, rs)
    got = [s for s in sig if s["i"] == i]
    check("a tight base broken on volume is a signal, stopped at the base low", len(got) == 1 and abs(got[0]["stop"] - lo) < 1e-9 and got[0]["volx"] >= 1.2, got)
    check("it needs no earlier signal on the days around it", [s["i"] for s in sig] == [i], [s["i"] for s in sig])
    wide, wi, _, _ = make_pivot(tight=False)
    check("a base deeper than 5% is not a pivot", not [s for s in pb.pivot_signals(wide, "X", None, rs_for(wide)) if s["i"] == wi])
    quiet, qi, _, _ = make_pivot(vol_mult=0.7)
    check("a break on ordinary volume is not a signal", not [s for s in pb.pivot_signals(quiet, "X", None, rs_for(quiet)) if s["i"] == qi])
    check("a share that is not a relative-strength leader is not a signal", pb.pivot_signals(D, "X", None, rs_for(D, 50.0)) == [])
    check("nor one outside the universe that day", pb.pivot_signals(D, "X", {d: {"Y"} for d in D.d}, rs) == [])
    cut = dl.Daily(D.raw[:i + 3])
    check("signals on a truncated history match the full history", [s["i"] for s in pb.pivot_signals(cut, "X", None, rs_for(cut)) if s["i"] < i + 2] == [i])
    # pullback: an uptrend, three falling days, then a day that touches the 21-day average and closes above yesterday's high
    base = climb(330)
    rows = list(base)
    p = rows[-1][3]
    for _ in range(3):
        c = p * 0.991
        rows.append((p, p * 1.001, c * 0.998, c, 2_000_000))
        p = c
    rows.append((p, p * 1.02, p * 0.995, p, 2_000_000))
    k = len(rows) - 1
    D0 = dl.Daily(candles(rows))
    pb.ensure(D0)
    e = D0.ema21[k]
    prev_close, prev_high = rows[-2][3], rows[-2][1]
    close = max(prev_high * 1.012, e * 1.006)
    rows[-1] = (prev_close, close * 1.002, min(prev_close, e * 1.004), close, 2_000_000)
    rows.append(day_row(close))
    D1 = dl.Daily(candles(rows))
    pb.ensure(D1)
    ps = [s for s in pb.pullback_signals(D1, "X", None, rs_for(D1)) if s["i"] == k]
    check("a leader that dips to its 21-day average and turns up is a pullback signal", len(ps) == 1 and abs(ps[0]["stop"] - min(r[2] for r in rows[k - 4:k + 1])) < 1e-9, ps)
    check("a share that never dipped to the average is not", not [s for s in pb.pullback_signals(dl.Daily(candles(base + [day_row(base[-1][3])] * 2)), "X", None, rs_for(D1)) if s["i"] == len(base)])
    check("a pullback signal needs the relative-strength leader test too", pb.pullback_signals(D1, "X", None, rs_for(D1, 40.0)) == [])


def test_family():
    print("Playbook: the whole family")
    data, nifty = world()
    out = dl.research(data, nifty, log=lambda m: None, placebo_draws=5)
    p = out["playbook"]
    check("exactly the pre-registered tests are run", [e["id"] for e in p["experiments"]] == [x[0] for x in pb.PLAYBOOK], [e["id"] for e in p["experiments"]])
    check("each of the four structural tests has a random-entry placebo", [x["id"] for x in p["placebo"]] == ["pivot", "pivot_p", "pullback", "pullback_p"])
    b = p["bear_gauge"]
    check("the bear gauge reports how many days it flagged and what it would have changed", b["threshold"] == 0.2 and b["days_flagged"] <= b["days_measured"]
          and set(b["rows"]) == {"full", "full_t", "ctx_rs", "ctx_rs_t"}, b)
    check("sizing is reported at four risk levels", all([r["risk_pct"] for r in v] == [0.25, 0.5, 1.0, 1.5] for v in p["sizing"].values()), p["sizing"])
    for name, rows in p["sizing"].items():
        dd = [r.get("max_drawdown_pct") for r in rows if r.get("max_drawdown_pct") is not None]
        check(f"risking less never deepens the fall ({name})", dd == sorted(dd), dd)
    check("the rules are recorded", p["rules"]["bear_below"] == 0.2 and p["rules"]["partial_gain"] == 0.2)


def test_portfolio_sizing():
    print("Playbook: sizing in the portfolio")
    base = climb(320)
    last = base[-1][3]
    D = dl.Daily(candles(base + [day_row(last), day_row(last, o=last, h=last * 1.3, l=last, c=last * 1.25), day_row(last * 1.25)]))
    t = pb.simulate2(D, len(base) - 1, last * 0.95, "trail", last, None)
    t = dict(t, symbol="X", volx=2.0, exit_date=D.d[len(base) + 1], exit=D.c[len(base) + 1], exit_i=len(base) + 1)
    dates = D.d[len(base) - 1:]
    g = {}
    for risk in (0.005, 0.015):
        curve, taken = dl.portfolio([dict(t, exit=t["entry"] * 1.2)], {"X": D}, dates, risk)
        g[risk] = curve[-1] - 1
    check("a third of the risk gives a third of the gain", abs(g[0.005] * 3 - g[0.015]) < 1e-9, g)
    tight = dict(t, risk_pct=0.04, exit=t["entry"] * 1.2)
    wide = dict(t, risk_pct=0.08, exit=t["entry"] * 1.2)
    ct, _ = dl.portfolio([tight], {"X": D}, dates)
    cw, _ = dl.portfolio([wide], {"X": D}, dates)
    check("a tighter stop means a bigger position, so a bigger gain on the same move", abs((ct[-1] - 1) / (cw[-1] - 1) - 2.0) < 0.05, (ct[-1], cw[-1]))


def main():
    test_ema_and_costs()
    test_partial()
    test_signals()
    test_family()
    test_portfolio_sizing()
    print("\nALL PLAYBOOK CHECKS PASSED" if not check.failed else f"\n{check.failed} CHECK(S) FAILED")
    sys.exit(1 if check.failed else 0)


if __name__ == "__main__":
    main()
