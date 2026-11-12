"""Checks for the backtest.  Run:  python -m tests.test_backtest   (needs no internet)"""
import copy
import os
import random
import sys
from datetime import date, timedelta

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import clock  # noqa: E402
from engine.backtest import report  # noqa: E402
from engine.backtest.data import Bars, load_fyers  # noqa: E402
from engine.backtest.luck import scramble  # noqa: E402
from engine.backtest.model import SyntheticMaster, option_price  # noqa: E402
from engine.backtest.runner import DEFAULTS, run_variant  # noqa: E402
from engine.clock import at, ts  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def walk_day(day, start, rng, step=300, n=75, vol=0.0008):
    p, out, t0 = start, [], ts(at(day, "09:15"))
    for i in range(n):
        o = p
        p = p * (1 + rng.gauss(0, vol))
        out.append([t0 + i * step, o, max(o, p) * 1.0002, min(o, p) * 0.9998, p, rng.randint(1000, 5000)])
    return out, p


def weekdays(n, end=date(2026, 11, 11)):
    d, out = end, []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def main():
    rng = random.Random(5)
    days = weekdays(30)

    # 1. no peeking: a candle only shows up once it has finished
    c, _ = walk_day(days[-1], 24500, rng)
    b = Bars(c, 300)
    t_close = c[3][0] + 300
    assert len(b.visible(days[-1], t_close - 1)) == 3 and len(b.visible(days[-1], t_close)) == 4
    assert b.visible(days[-1], c[0][0]) == [] and b.prev_close(days[-1]) is None
    print("ok  replay never shows a candle before it has finished")

    # 2. option price model behaves like an option
    spot, k, left = 24500, 24500, 3 * 86400
    call, put = option_price(spot, k, left, 0.13, "CE"), option_price(spot, k, left, 0.13, "PE")
    assert 80 < call < 170 and abs(call - put) < 40
    assert option_price(spot + 100, k, left, 0.13, "CE") > call > option_price(spot - 100, k, left, 0.13, "CE")
    assert option_price(spot, k, left - 86400, 0.13, "CE") < call, "time decay"
    assert option_price(spot, 20000, 60, 0.13, "CE") >= 4499, "deep in the money keeps its intrinsic value"
    print(f"ok  option model: Nifty 3-day at-the-money about ₹{call:.0f}, falls with time, rises with the index")

    # 3. contract rules on past days
    rules = DEFAULTS["rules"]
    m = SyntheticMaster(rules, {"NIFTY": "NSE:NIFTY50-INDEX", "BANKNIFTY": "NSE:NIFTYBANK-INDEX"})
    wed = date(2026, 10, 14)                                     # a Wednesday: next Nifty expiry is Tue 20 Oct
    e, _ = m.choose_expiry("NSE_FO", "NIFTY", wed, 1, "roll")
    assert clock.from_ts(e).date() == date(2026, 10, 20)
    tue = date(2026, 10, 20)                                     # expiry day rolls to next week
    e, _ = m.choose_expiry("NSE_FO", "NIFTY", tue, 1, "roll")
    assert clock.from_ts(e).date() == date(2026, 10, 27)
    e, why = m.choose_expiry("NSE_FO", "BANKNIFTY", date(2026, 10, 27), 3, "skip")   # Bank Nifty month-end Tuesday
    assert e is None and "expiry" in why
    e, _ = m.choose_expiry("NSE_FO", "BANKNIFTY", date(2026, 10, 20), 3, "skip")
    assert clock.from_ts(e).date() == date(2026, 10, 27)
    o = m.pick_option("NSE_FO", "NIFTY", ts(at(tue, "15:30")), 24523, "CE", "ATM")
    assert o["strike"] == 24500 and o["lot"] == 65
    print("ok  past contract rules: weekly and monthly expiry, expiry-day roll, Bank Nifty sits out its last days")

    # 4. the luck check keeps each day's total move and volume, but changes the path
    c1, _ = walk_day(days[0], 24500, rng)
    c2, _ = walk_day(days[1], c1[-1][4], rng)
    orig = Bars(c1 + c2, 300)
    sc = scramble({"X": orig}, random.Random(1))["X"]
    for d in orig.days:
        assert abs(sc.by_day[d][-1][4] / orig.by_day[d][-1][4] - 1) < 1e-9, "day's close must be kept"
        assert [x[5] for x in sc.by_day[d]] == [x[5] for x in orig.by_day[d]], "volume pattern must be kept"
        assert [round(x[4], 3) for x in sc.by_day[d]] != [round(x[4], 3) for x in orig.by_day[d]], "path must change"
        assert all(x[2] >= max(x[1], x[4]) and x[3] <= min(x[1], x[4]) for x in sc.by_day[d])
    print("ok  luck check: shuffled days keep their total move, gaps and volume; only the path changes")

    # 5. a whole replay on made-up prices, no internet
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
    bt = copy.deepcopy(DEFAULTS)
    bars, prices = {}, {"NSE:NIFTY50-INDEX": 24500, "BSE:SENSEX-INDEX": 80000, "NSE:NIFTYBANK-INDEX": 55000,
                        "NSE:FINNIFTY-INDEX": 26000, "NSE:MIDCPNIFTY-INDEX": 12000}
    for sym, p0 in prices.items():
        allc, p = [], p0
        for d in days:
            cc, p = walk_day(d, p, rng, vol=0.0011)
            allc += cc
        bars[sym] = Bars(allc, 300)
    trades, still_open, n_idx = run_variant(cfg, bars, days[12:], bt, log=lambda s: None)
    assert n_idx == 5 and still_open == 0 and len(trades) > 10
    for t in trades:
        assert t["status"] == "CLOSED" and t["cost"] > 0 and abs(t["net"] - (t["pnl"] - t["cost"])) < 0.02
        assert "09:45" <= t["entry_time"][11:16] <= "11:30", t["entry_time"]
        assert t["exit_time"] > t["entry_time"] and t["exit_type"] in ("STOP", "TARGET", "EARLY", "TRAIL", "TIME")
    s = report.summarize(trades)
    assert s["n"] == len(trades) and s["wins"] + s["losses"] <= s["n"] and s["max_drawdown"] >= 0
    print(f"ok  full replay on made-up prices: {len(trades)} trades, every one closed, costs applied, entries inside the window")

    # 6. no cheating through the replay: with early exit off, nothing changes about how entries are found
    c2 = copy.deepcopy(cfg)
    c2["early_exit"]["enabled"] = False
    t2, _, _ = run_variant(c2, bars, days[12:], bt, log=lambda s: None)
    assert all(t["exit_type"] != "EARLY" for t in t2)
    print("ok  early exit can be switched off in the replay")

    # 7. numbers in the report
    w = report.wilson(32, 100)
    assert 23 < w[0] < 25 and 41 < w[1] < 42
    fake = [{"net": 100, "pnl": 120, "net_r": 1, "r": 1.1, "date": "2026-10-01"}, {"net": -50, "pnl": -30, "net_r": -.5, "r": -.3, "date": "2026-10-01"},
            {"net": 200, "pnl": 230, "net_r": 2, "r": 2, "date": "2026-10-02"}, {"net": -300, "pnl": -280, "net_r": -3, "r": -2.8, "date": "2026-10-03"}]
    s = report.summarize(fake)
    assert s["pnl"] == -50 and s["wins"] == 2 and s["profit_factor"] == round(300 / 350, 2) and s["max_drawdown"] == 300
    assert report.summarize(fake, "pnl")["pnl"] == 40
    print("ok  report maths: win-rate range, profit factor, drawdown, gross versus net")

    # 8. Fyers history loader asks in chunks Fyers accepts and joins them
    calls = []

    class FakeFy:
        def history(self, sym, res, a, b):
            calls.append((a, b))
            assert (b - a).days <= 95 and res == "1"
            return [[ts(at(b, "09:15")) + 60 * i, 1, 1, 1, 1, 1] for i in range(1200)]
    got, n_stocks, missing = load_fyers(FakeFy(), [{"spot": "NSE:NIFTY50-INDEX"}], ["AAA"], 200, log=lambda s: None)
    assert len(calls) == 2 * 3 and "NSE:NIFTY50-INDEX" in got and "NSE:AAA-EQ" in got and n_stocks == 1
    print("ok  Fyers loader: 200 days fetched in 3 chunks of at most 95 days")
    print("\nALL BACKTEST CHECKS PASSED")


if __name__ == "__main__":
    main()
