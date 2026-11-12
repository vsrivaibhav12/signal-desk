"""Checks the tests built from the Market research document: RSI(2) mean reversion, the momentum burst, the episodic pivot, risk-adjusted
momentum, the exit modifiers and pilot sizing. Run:  python -m tests.test_archetypes    (offline)"""
import os
import random
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import archetypes as ar, context as cx, daily as dl, results  # noqa: E402
from tests.test_daily import candles, world  # noqa: E402
from tests.test_playbook import climb, day_row, rs_for  # noqa: E402
from tests.test_strategies import check, weekdays  # noqa: E402

A = ar.A


def test_rsi():
    print("Archetypes: RSI(2)")
    up = ar.rsi([float(x) for x in range(1, 20)])
    dn = ar.rsi([float(x) for x in range(20, 1, -1)])
    check("a rising series has RSI 100 and a falling one 0", up[-1] == 100.0 and dn[-1] == 0.0, (up[-1], dn[-1]))
    check("the first values are not defined", up[0] is None and up[1] is None and up[2] is not None)
    mixed = ar.rsi([10, 11, 10.5, 10.4, 10.9, 10.2, 10.3, 9.9, 10.8])
    check("RSI stays between 0 and 100", all(0 <= v <= 100 for v in mixed if v is not None), mixed)
    # Wilder's recursion on a tiny case: gains 1, 0 then a loss of 1
    r = ar.rsi([10, 11, 11, 10])
    check("it follows Wilder's smoothing", abs(r[2] - 100.0) < 1e-9 and abs(r[3] - (100 - 100 / (1 + 0.25 / 0.5))) < 1e-9, r)


def dip_series(down_days=3, drop=0.02):
    rows = climb(330)
    p = rows[-1][3]
    for _ in range(down_days):
        c = p * (1 - drop)
        rows.append((p, p * 1.001, c * 0.999, c, 2_000_000))
        p = c
    return rows


def test_mean_reversion():
    print("Archetypes: Connors mean reversion")
    rows = dip_series()
    D = dl.Daily(candles(rows + [day_row(rows[-1][3])] * 2))
    ar.ensure3(D)
    i = len(rows) - 1
    sig = ar.mr_signals(D, "X", None, rs_for(D), 10)
    check("a dip to a very low RSI(2) in an uptrend is a signal", i in [s["i"] for s in sig] and D.rsi2[i] < 10, (D.rsi2[i], [s["i"] for s in sig]))
    check("a stricter threshold needs a deeper dip", i not in [s["i"] for s in ar.mr_signals(D, "X", None, rs_for(D), 0.5)])
    check("the relative-strength version needs a leader", i in [s["i"] for s in ar.mr_signals(D, "X", None, rs_for(D, 95.0), 10, True)]
          and ar.mr_signals(D, "X", None, rs_for(D, 40.0), 10, True) == [])
    falling = dl.Daily(candles(climb(150, drift=-0.003) + [day_row(100)] * 20))
    check("below the 200-day average there is no signal", ar.mr_signals(falling, "X", None, rs_for(falling), 10) == [])
    check("a share outside the universe that day is not a signal", ar.mr_signals(D, "X", {d: {"Y"} for d in D.d}, rs_for(D), 10) == [])
    # fills: signal on the last dip day, so the share is below its 5-day average when it is bought
    dip = dip_series()
    last = dip[-1][3]
    cost = dl.RULES["cost_round_trip"] * 100

    def trade(after, stop=0.12):
        Dx = dl.Daily(candles(dip + after))
        ar.ensure3(Dx)
        return ar.sim_mr(Dx, len(dip) - 1, stop), Dx

    rebound, Dr = trade([day_row(last, o=last, c=last * 0.995), day_row(last * 0.995, o=last * 0.995, c=last * 1.06), day_row(last * 1.06)])
    check("it buys the next open and leaves at the open after the first close above the 5-day average",
          rebound["reason"] == "5-day average" and rebound["entry"] == round(Dr.o[len(dip)], 2) and rebound["exit_i"] > rebound["entry_i"] + 1, rebound)
    check("costs are 0.40% of the position", abs(rebound["gross_pct"] - rebound["net_pct"] - cost) < 1e-6, rebound)
    slide = [day_row(last * (1 - 0.008 * k), o=last * (1 - 0.008 * k), c=last * (1 - 0.008 * (k + 1))) for k in range(0, 16)]
    slow, _ = trade(slide)
    check("after 10 sessions it leaves anyway", slow["reason"] == "time" and slow["hold"] == 10, slow)
    crash, _ = trade([day_row(last, o=last, c=last * 0.995), day_row(last * 0.995, o=last * 0.80, h=last * 0.81, l=last * 0.78, c=last * 0.79), day_row(last * 0.79)])
    check("a catastrophic gap fills at the open, worse than the stop", crash["reason"] == "stop" and crash["exit"] == round(last * 0.80, 2) and crash["net_pct"] < -19, crash)
    tight, _ = trade([day_row(last, o=last, h=last, l=last * 0.955, c=last * 0.97), day_row(last * 1.1)], stop=0.03)
    check("a hard 3% stop is hit on a 4% dip that the 12% stop would sit through", tight["reason"] == "stop" and tight["gross_pct"] < -2.9, tight)
    wide, _ = trade([day_row(last, o=last, h=last, l=last * 0.955, c=last * 0.97), day_row(last * 1.1, o=last * 0.99, c=last * 1.1)], stop=0.12)
    check("the same dip does not stop the 12% version", wide["reason"] != "stop", wide)


def burst_world(**kw):
    """A volatile share (daily range about 5%) with a quiet day before a 4% burst."""
    rng, rows, p = random.Random(3), [], 100.0
    for _ in range(320):
        c = p * (1 + 0.0008 + rng.uniform(-0.012, 0.012))
        rows.append((p, max(p, c) * 1.022, min(p, c) * 0.978, c, 2_000_000))
        p = c
    return rows, p


def test_burst():
    print("Archetypes: Stockbee momentum burst")
    rows, p = burst_world()
    base = list(rows)
    quiet = (p, p * 1.004, p * 0.997, p * 1.003, 1_000_000)               # yesterday: narrow and light (volume under the day before)
    prev_close = quiet[3]
    burst = (prev_close * 1.01, prev_close * 1.07, prev_close * 1.005, prev_close * 1.06, 3_000_000)
    D = dl.Daily(candles(base + [(p, p * 1.02, p * 0.98, p, 2_000_000), quiet, burst, day_row(burst[3])]))
    ar.ensure3(D)
    i = len(base) + 2
    sig = [s for s in ar.burst_signals(D, "X", None) if s["i"] == i]
    check("a 4% burst after a narrow, light day is a signal with the entry level and yesterday's low as the stop",
          len(sig) == 1 and abs(sig[0]["level"] - prev_close * 1.04) < 1e-9 and sig[0]["stop"] == D.l[i - 1], sig)
    check("the average daily range test is applied", D.adr[i] >= A["adr_min"], D.adr[i])
    flat = dl.Daily(candles(climb(320) + [(p, p * 1.002, p * 0.999, p, 1_000_000), quiet, burst]))
    ar.ensure3(flat)
    check("a share that does not move much each day is not eligible", not [s for s in ar.burst_signals(flat, "X", None) if s["i"] == len(flat.d) - 1])
    gap = dl.Daily(candles(base + [(p, p * 1.02, p * 0.98, p, 2_000_000), quiet, (prev_close * 1.10, prev_close * 1.12, prev_close * 1.09, prev_close * 1.11, 3_000_000)]))
    ar.ensure3(gap)
    check("a gap more than 8% above the close is skipped as extended", not [s for s in ar.burst_signals(gap, "X", None) if s["i"] == len(gap.d) - 1])
    loud = dl.Daily(candles(base + [(p, p * 1.02, p * 0.98, p, 2_000_000), (p, p * 1.03, p * 0.97, p * 1.03, 2_500_000), burst]))
    ar.ensure3(loud)
    check("a day before that was not narrow and quiet disqualifies it", not [s for s in ar.burst_signals(loud, "X", None) if s["i"] == len(loud.d) - 1])
    # trades
    entry = max(D.o[i], sig[0]["level"])
    t = ar.sim_r(D, i, entry, D.l[i - 1], "hold5", i)
    check("the stop-buy fills at the 4% level (or the open if higher), and the stop is yesterday's low", t is None or abs(t["entry"] - round(entry, 2)) < 0.011)
    after = [day_row(burst[3], o=burst[3], c=burst[3] * (1 + 0.004 * k)) for k in range(1, 8)]
    D2 = dl.Daily(candles(base + [(p, p * 1.02, p * 0.98, p, 2_000_000), quiet, burst] + after))
    ar.ensure3(D2)
    j = len(base) + 2
    e2 = max(D2.o[j], prev_close * 1.04)
    t5 = ar.sim_r(D2, j, e2, D2.l[j - 1], "hold5", j)
    if t5 is not None:
        check("hold5 leaves at the close of the fifth session", t5["reason"] == "5-session exit" and t5["hold"] == 4, t5)
        cr = dl.RULES["cost_round_trip"] / t5["risk_pct"]
        check("costs in R grow as the stop gets tighter", abs(t5["gross_r"] - t5["net_r"] - cr) < 1e-3, t5)
    sk = ar.sim_r(D2, j, e2, e2 * 0.999, "hold5", j)
    check("a stop closer than 1% is skipped", sk is None)
    wd = ar.sim_r(D2, j, e2, e2 * 0.85, "hold5", j)
    check("a stop farther than 8% is skipped", wd is None)


def ep_world(vol=4.0, gap=0.12, red=False, deep=False, news_day=None):
    rng, rows, p = random.Random(9), [], 100.0
    for k in range(330):
        if k < 260:                                                   # a rise, then a 60-session base
            c = p * 1.003
        else:
            c = p * (1 + (-0.009 if deep else rng.uniform(-0.004, 0.004)))
        rows.append((p, max(p, c) * 1.003, min(p, c) * 0.997, c, 2_000_000))
        p = c
    o = p * (1 + gap)
    c = o * (0.97 if red else 1.03)
    rows.append((o, max(o, c) * 1.01, min(o, c) * 0.99, c, int(2_000_000 * vol)))
    rows.append(day_row(c))
    rows.append(day_row(c))
    return dl.Daily(candles(rows)), len(rows) - 3


def test_episodic():
    print("Archetypes: the episodic pivot (daily version)")
    D, i = ep_world()
    gd = D.d[i]
    sig = [s for s in ar.ep_signals(D, "ABC|1234", None, set()) if s["i"] == i]
    check("a 12% gap on heavy volume out of a flat base, holding its open, is a signal with the gap day's low as the stop",
          len(sig) == 1 and sig[0]["stop"] == D.l[i] and sig[0]["news"] is False, sig)
    check("a results announcement on the gap day marks it as news", ar.ep_signals(D, "ABC|1234", None, {("ABC", gd)})[0]["news"] is True)
    check("so does one the day before", ar.ep_signals(D, "ABC|1234", None, {("ABC", D.d[i - 1])})[0]["news"] is True)
    check("an announcement for another share does not", ar.ep_signals(D, "ABC|1234", None, {("XYZ", gd)})[0]["news"] is False)
    for label, kw in (("a gap bigger than 30%", {"gap": 0.40}), ("a gap smaller than 8%", {"gap": 0.05}), ("ordinary volume", {"vol": 1.5}),
                      ("a day that closes below its open", {"red": True}), ("a base deeper than 35%", {"deep": True})):
        Dx, ix = ep_world(**kw)
        check(f"{label} is not a signal", not [s for s in ar.ep_signals(Dx, "ABC|1234", None, set()) if s["i"] == ix])
    # the trade
    t = ar.sim_r(D, i, D.o[i + 1], D.l[i], "ep10", i + 1)
    check("it is bought at the next open and measured against the gap-day low", t is not None and abs(t["entry"] - round(D.o[i + 1], 2)) < 0.011
          and 0.01 <= t["risk_pct"] <= 0.12, t)
    far = ar.sim_r(D, i, D.o[i + 1], D.o[i + 1] * 0.8, "ep10", i + 1)
    check("a stop more than 12% away is skipped", far is None)


def test_pilot_and_momentum():
    print("Archetypes: pilot sizing and risk-adjusted momentum")
    base = climb(330)
    D = dl.Daily(candles(base))
    dates = D.d[300:]
    ar.ensure3(D)

    def tr(k0, k1, gain, vx=1.0):
        e = D.c[k0]
        return {"symbol": "X", "entry": e, "exit": e * (1 + gain), "entry_date": D.d[k0], "exit_date": D.d[k1], "volx": vx, "net_r": gain / 0.08, "entry_i": k0, "exit_i": k1, "open": False}

    one = [tr(305, 310, 0.20)]
    full, _ = ar.portfolio_pilot(one, {"X": D}, dates, adaptive=False)
    ref, _ = dl.portfolio(one, {"X": D}, dates)
    check("with adaptive sizing off it is the ordinary portfolio", abs(full[-1] - ref[-1]) < 1e-12, (full[-1], ref[-1]))
    pilot, _ = ar.portfolio_pilot(one, {"X": D}, dates, adaptive=True)
    check("the first trade is a pilot: 0.4% risk instead of 1.5%", abs((pilot[-1] - 1) * 1.5 / 0.4 - (full[-1] - 1)) < 1e-9, (pilot[-1], full[-1]))
    wins = [tr(301, 303, 0.10), tr(304, 306, 0.10), tr(308, 312, 0.20)]
    pw, _ = ar.portfolio_pilot(wins, {"X": D}, dates, adaptive=True)
    fw, _ = ar.portfolio_pilot(wins, {"X": D}, dates, adaptive=False)
    third_pilot = (pw[-1])
    check("two wins in a row move the next trade to full size, so the pilot book catches up", pw[-1] > 1 and third_pilot < fw[-1], (pw[-1], fw[-1]))
    losses = [tr(301, 303, -0.08), tr(304, 306, -0.08), tr(307, 309, 0.30), tr(320, 324, 0.30)]
    pl, taken = ar.portfolio_pilot(losses, {"X": D}, dates, adaptive=True)
    check("two losses in a row pause new trades for 14 days", taken == 3, taken)
    # risk-adjusted momentum: a smooth climb scores higher than a jumpy one with the same return
    rng = random.Random(1)
    srows, sp = [], 100.0
    for _ in range(260):
        sc = sp * (1 + 0.002 + rng.uniform(-0.002, 0.002))
        srows.append((sp, max(sp, sc) * 1.001, min(sp, sc) * 0.999, sc, 2_000_000))
        sp = sc
    smooth = dl.Daily(candles(srows))
    rows, p = [], 100.0
    for _ in range(260):
        c = p * (1 + 0.002 + rng.uniform(-0.03, 0.03))
        rows.append((p, max(p, c) * 1.01, min(p, c) * 0.99, c, 2_000_000))
        p = c
    jumpy = dl.Daily(candles(rows))
    check("the score is the 126-session return divided by the 20-day volatility: smooth beats jumpy", ar.riskadj_score(smooth, 259) > ar.riskadj_score(jumpy, 259))
    check("a score needs 126 sessions of history", ar.riskadj_score(smooth, 100) is None)
    data = {"S": smooth, "J": jumpy}
    members = {d: {"S", "J"} for d in smooth.d}
    pick = ar.riskadj_picker(data, members, smooth, False)
    check("the picker holds only shares above their 200-day average, best score first", pick(smooth.d[258], smooth.d[259])[:1] == ["S"])
    weak = dl.Daily(candles(climb(300, drift=-0.002)))
    check("with Nifty below its 200-day average it holds cash", ar.riskadj_picker(data, members, weak, True)(smooth.d[258], smooth.d[259]) == [])


def test_rolling_pf():
    print("Archetypes: rolling profit factor")
    def tr(month, r):
        return {"date": month, "net_r": r, "open": False}
    months = [f"2020-{m:02d}" for m in range(1, 13)] + [f"2021-{m:02d}" for m in range(1, 13)] + [f"2022-{m:02d}" for m in range(1, 7)]
    good = [tr(m, r) for m in months for r in (2.0, -1.0, -1.0, 2.0, 1.0)]
    out = ar.rolling_pf(good)
    check("a steady winner has a profit factor above the bar in every window", out["windows"] == 19 and out["share_above_bar"] == 1.0 and out["median_pf"] > 1.25, out)
    bad = [tr(m, r) for m in months for r in (1.0, -1.0, -1.0, 1.0, -0.5)]
    check("a loser never clears it", ar.rolling_pf(bad)["share_above_bar"] == 0.0 and ar.rolling_pf(bad)["share_above_1"] == 0.0)
    check("too few trades in a window gives no windows", ar.rolling_pf([tr("2020-01", 1.0)] * 5) == {"windows": 0} or ar.rolling_pf([tr("2020-01", 1.0)] * 5) == {})


def test_results_loader():
    print("Archetypes: the results history")
    import tempfile, shutil
    calls = []
    raw = [{"symbol": "HAL", "purpose": "Financial Results", "date": "20-Oct-2020"}, {"symbol": "HAL", "purpose": "Dividend", "date": "21-Oct-2020"}]

    def fake(frm, to):
        calls.append((frm, to))
        if len(calls) == 2:
            raise RuntimeError("blocked")
        return raw
    real = results.nse_fetch
    results.nse_fetch = fake
    d = tempfile.mkdtemp()
    try:
        days, missed = ar.load_results_days(date(2020, 10, 1), date(2021, 1, 20), d, log=lambda m: None)
        check("announcements are collected by symbol and date, other purposes ignored", ("HAL", date(2020, 10, 20)) in days and ("HAL", date(2020, 10, 21)) not in days, days)
        check("a piece that cannot be read is counted, not hidden", missed == 1 and len(calls) == 2, (missed, len(calls)))
        n = len(calls)
        again, missed2 = ar.load_results_days(date(2020, 10, 1), date(2021, 1, 20), d, log=lambda m: None)
        check("a piece already read is not asked for again; only the missing one is retried", len(calls) == n + 1 and missed2 == 0, (len(calls) - n, missed2))
    finally:
        results.nse_fetch = real
        shutil.rmtree(d, ignore_errors=True)


def test_family():
    print("Archetypes: the whole family")
    data, nifty = world()
    out = dl.research(data, nifty, log=lambda m: None, placebo_draws=4, results_days=set())
    a = out["archetypes"]
    check("every family is reported", {"mean_reversion", "burst", "episodic", "exits", "pilot_sizing", "momentum_books", "rolling_profit_factor", "rules"} <= set(a), sorted(a))
    check("the pre-registered mean-reversion tests are run", [e["id"] for e in a["mean_reversion"]["experiments"]] == [m[0] for m in ar.MR])
    check("the burst, episodic and exit tests are run", [e["id"] for e in a["burst"]["experiments"]] == ["b1", "b2", "b3"]
          and [e["id"] for e in a["episodic"]["experiments"]] == ["e1", "e2", "e3", "e4"] and [e["id"] for e in a["exits"]["experiments"]] == ["x0", "t1", "x1"])
    check("each entry test has a random-entry placebo", [p["id"] for p in a["mean_reversion"]["placebo"]] == [m[0] for m in ar.MR]
          and [p["id"] for p in a["burst"]["placebo"]] == ["b1", "b2", "b3"])
    check("the pilot and fixed books are reported", set(a["pilot_sizing"]) == {"fixed_full", "pilot"} and set(a["momentum_books"]) == {"v1", "v2"})
    check("the rules are recorded", a["rules"]["rsi_period"] == 2 and a["rules"]["burst_up"] == 0.04)
    import json
    check("it is plain data", json.loads(json.dumps(a)) == a)


def main():
    test_rsi()
    test_mean_reversion()
    test_burst()
    test_episodic()
    test_pilot_and_momentum()
    test_rolling_pf()
    test_results_loader()
    test_family()
    print("\nALL ARCHETYPE CHECKS PASSED" if not check.failed else f"\n{check.failed} CHECK(S) FAILED")
    sys.exit(1 if check.failed else 0)


if __name__ == "__main__":
    main()
