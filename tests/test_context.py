"""Checks relative strength, breadth, the monthly books and the index trend benchmark. Run:  python -m tests.test_context   (offline)"""
import os
import random
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import context as cx, daily as dl  # noqa: E402
from tests.test_daily import candles  # noqa: E402
from tests.test_strategies import check, weekdays  # noqa: E402


def trend(n, start, daily_ret, vol=1_000_000):
    rows, p = [], start
    for _ in range(n):
        c = p * (1 + daily_ret)
        rows.append((p, max(p, c) * 1.001, min(p, c) * 0.999, c, vol))
        p = c
    return dl.Daily(candles(rows))


def universe(n_days=330, shares=25):
    """25 shares drifting from -0.1% to +0.3% a day, so share k's strength rises with k."""
    data = {f"S{k:02d}": trend(n_days, 100.0, -0.001 + 0.004 * k / (shares - 1)) for k in range(shares)}
    members = {d: set(data) for D in data.values() for d in D.d}
    return data, members


def test_scores():
    print("Context: relative strength and breadth")
    D = trend(300, 100.0, 0.001)
    i = 299
    want = sum(w * (D.c[i] / D.c[i - k] - 1) for k, w in cx.CONTEXT["rs_weights"])
    check("the weighted score is the 40/20/20/20 mix of 3, 6, 9 and 12-month returns", abs(cx.rs_score(D, i) - want) < 1e-12
          and abs(sum(w for _, w in cx.CONTEXT["rs_weights"]) - 1.0) < 1e-12)
    check("the classic score skips the last month", abs(cx.classic_score(D, i) - (D.c[i - 21] / D.c[i - 252] - 1)) < 1e-12)
    check("a score needs a full year of history", cx.rs_score(D, 100) is None and cx.classic_score(D, 100) is None)
    data, members = universe()
    dates = sorted(members)[300:]
    rs = cx.rs_percentiles(data, members, dates)
    d = dates[-1]
    check("the strongest share is at the top and the weakest at the bottom", rs["S24"][d] == 100.0 and rs["S00"][d] == 0.0, (rs["S24"][d], rs["S00"][d]))
    check("percentiles run in order of strength", [rs[f"S{k:02d}"][d] for k in range(25)] == sorted(rs[f"S{k:02d}"][d] for k in range(25)))
    # no look-ahead: cutting the future off leaves earlier percentiles exactly as they were
    cut = {k: dl.Daily(v.raw[:320]) for k, v in data.items()}
    rs2 = cx.rs_percentiles(cut, members, dates[:15])
    check("percentiles on a prefix of the data equal those on all of it", all(rs2["S10"][x] == rs["S10"][x] for x in dates[:15] if x in rs2["S10"]))
    thin = cx.rs_percentiles(data, {d0: set(list(data)[:10]) for d0 in members}, dates)
    check("with fewer than 20 members there is no ranking", thin == {} or all(not v for v in thin.values()))
    br = cx.breadth_by_date(data, members, dates)
    check("breadth is the share of the universe above its 50-day average", abs(br[d] - 24 / 25) < 0.1 or 0.4 < br[d] < 1.0, br[d])
    up = {f"U{k}": trend(330, 100.0, 0.002) for k in range(25)}
    down = {f"D{k}": trend(330, 100.0, -0.002) for k in range(25)}
    mu = {dd: set(up) for dd in sorted(members)}
    md = {dd: set(down) for dd in sorted(members)}
    check("a rising market has breadth near one and a falling one near zero", cx.breadth_by_date(up, mu, dates)[d] > 0.95
          and cx.breadth_by_date(down, md, dates)[d] < 0.05)


def test_books():
    print("Context: the monthly books")
    one = {"A": trend(330, 100.0, 0.001)}
    dates = one["A"].d[260:]
    members = {d: {"A"} for d in dates}
    seen = []

    def pick(prev, d):
        seen.append((prev, d))
        return ["A"]

    curve, made = cx.run_book(one, dates, pick)
    px0, px1 = one["A"].c[260 + 0], one["A"].c[-1]
    first_reb = next(d for d in dates[1:] if d.month != dates[0].month)
    check("a share is only picked on the first session of a month, from the day before", all(p < d for p, d in seen) and len(seen) >= 3
          and seen[0][1] == first_reb, seen[:2])
    held_from = one["A"].c[one["A"].index[first_reb]]
    want = (px1 / held_from) * (1 - cx.CONTEXT["cost_side"])
    check("it buys at the rebalance close, pays costs once, then follows the share", abs(curve[-1] - want) / want < 1e-6, (curve[-1], want))
    check("the curve is flat in cash before the first rebalance", all(v == 1.0 for v in curve[:dates.index(first_reb)]))
    # a share that stops trading is sold at its last price and the cash waits
    ended = {"A": dl.Daily(one["A"].raw[:300], ended=True), "B": one["A"]}
    dts = ended["B"].d[260:]
    curve2, made2 = cx.run_book(ended, dts, lambda prev, d: ["A"])
    check("a delisted share is bought, then sold at its last price when it stops trading", made2 == 2, made2)
    gone = next(k for k, d in enumerate(dts) if d > ended["A"].d[-1])
    check("after the sale the money waits in cash, unchanged", all(abs(v - curve2[gone]) < 1e-12 for v in curve2[gone:]), curve2[gone - 1:gone + 3])
    # momentum picker: the strongest shares, and cash when Nifty is weak
    data, mem = universe()
    nif = trend(330, 10000.0, 0.001)
    top = cx.momentum_picker(data, mem, nif, cx.classic_score)(sorted(mem)[-2], sorted(mem)[-1])
    check("it holds the strongest twenty", len(top) == cx.CONTEXT["top"] and "S24" in top and "S00" not in top, top[:3])
    weak = trend(330, 10000.0, -0.002)
    check("with Nifty below its 200-day average it holds cash", cx.momentum_picker(data, mem, weak, cx.classic_score, True)(sorted(mem)[-2], sorted(mem)[-1]) == []
          and len(cx.momentum_picker(data, mem, nif, cx.classic_score, True)(sorted(mem)[-2], sorted(mem)[-1])) == cx.CONTEXT["top"])
    check("equal weight holds every member", cx.equal_weight_picker(data, mem)(sorted(mem)[-2], sorted(mem)[-1]) == sorted(data))
    # the index trend benchmark
    up = trend(400, 10000.0, 0.001)
    dts = up.d[250:]
    curve3, sw = cx.index_trend_hold(up, dts)
    bh = cx.buy_and_hold(up, dts)
    check("held while above its 200-day average: close to buy and hold, less one switching cost", sw == 1 and 0.97 < curve3[-1] / bh[-1] < 1.0, (sw, curve3[-1], bh[-1]))
    down = trend(400, 10000.0, -0.001)
    c4, sw4 = cx.index_trend_hold(down, down.d[250:])
    check("a falling index is never held", sw4 == 0 and all(v == 1.0 for v in c4), sw4)
    check("buy and hold starts at one", abs(bh[0] - 1.0) < 1e-12)


def test_registration():
    print("Context: the settings are the pre-registered ones")
    check("settings", cx.CONTEXT["rs_min"] == 80.0 and cx.CONTEXT["breadth_min"] == 0.5 and cx.CONTEXT["top"] == 20
          and cx.CONTEXT["cost_side"] == 0.002 and cx.CONTEXT["classic_skip"] == 21)
    check("the second family has exactly six rows", [e[0] for e in dl.CONTEXT_EXPERIMENTS] == ["ctx_rs", "ctx_breadth", "ctx_both", "ctx_rs_t", "ctx_breadth_t", "ctx_both_t"])
    check("sector rotation is stated as not done, with the reason", "SECTOR ROTATION" in cx.__doc__ and "point-in-time" in cx.__doc__)


def test_research_output():
    print("Context: the research output")
    from tests.test_daily import world
    data, nifty = world()
    out = dl.research(data, nifty, log=lambda m: None, placebo_draws=5)
    check("the context experiments are reported", [e["id"] for e in out["context_experiments"]] == [e[0] for e in dl.CONTEXT_EXPERIMENTS])
    check("every book is reported with its curve statistics", {"mom_classic", "mom_classic_regime", "mom_ms", "mom_ms_regime", "equal_weight",
          "nifty_trend_hold", "nifty_buy_hold"} <= set(out["books"]), sorted(out["books"]))
    b = out["books"]["mom_classic"]
    check("a book has CAGR, drawdown, years and the two periods", {"cagr_pct", "max_drawdown_pct", "years", "development", "validation"} <= set(b), sorted(b))
    check("the settings used are recorded", out["context_settings"]["rs_min"] == 80.0)


def main():
    test_scores()
    test_books()
    test_registration()
    test_research_output()
    print("\nALL CONTEXT CHECKS PASSED" if not check.failed else f"\n{check.failed} CHECK(S) FAILED")
    sys.exit(1 if check.failed else 0)


if __name__ == "__main__":
    main()
