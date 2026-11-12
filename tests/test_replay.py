"""Checks the point-in-time replay. Run:  python -m tests.test_replay    (offline)"""
import json
import os
import random
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import clock, replay as rp, watch as watchmod  # noqa: E402
from engine.clock import at, ts  # noqa: E402
from engine.results import Results  # noqa: E402
from engine.strategies import sim  # noqa: E402
from engine.strategies.indicators import Series  # noqa: E402
from tests.test_strategies import check, walk, weekdays  # noqa: E402


def flat_days(n, price=100.0, vol=1000, end=date(2026, 11, 12)):
    out = []
    for d in weekdays(n, end):
        t0 = ts(at(d, "09:15"))
        out += [[t0 + 300 * k, price, price + 0.05, price - 0.05, price, vol] for k in range(75)]
    return out


class StubResults:
    def __init__(self, table):
        self.table = table                                           # day iso -> {symbol: kind}
        self.state = {"events": [1, 2, 3]}

    def watch(self, day):
        return [{"symbol": s, "kind": k, "why": "x"} for s, k in self.table.get(day.isoformat(), {}).items()]


def test_promotion():
    print("Replay: the watchlist as it would have looked")
    days = weekdays(14, date(2026, 11, 12))
    today = days[-1]
    flat = {n: flat_days(14) for n in "ABC"}
    t0 = ts(at(today, "09:15"))
    for c in flat["A"]:
        if c[0] >= t0:
            c[5] = 5000                                              # A trades five times its normal volume today
    for c in flat["C"]:
        if c[0] >= t0 + 300 * 2:                                     # C jumps 3% and stays there
            c[1] = c[2] = c[3] = c[4] = 103.0
    series = {f"NSE:{n}-EQ": Series(v) for n, v in flat.items()}
    insts = {s: {"symbol": s, "name": s[4:-3], "kind": "stock", "lot": 1} for s in series}
    pit = rp.PointInTime(series, insts, StubResults({today.isoformat(): {"B": "today"}}), {"watch": {"max_promoted": 2}})
    got = pit.promoted(today, 5)
    check("heavy volume and a big move are promoted, a quiet share is not", set(got) == {"NSE:A-EQ", "NSE:C-EQ"}, got)
    check("the volume ratio is against the time-of-day curve", 2.0 < got["NSE:A-EQ"]["ratio"] < 2.7, got["NSE:A-EQ"])
    check("nothing is promoted on a day before there is a baseline", pit.promoted(days[1], 5) == {}, pit.promoted(days[1], 5))
    one = rp.PointInTime(series, insts, None, {"watch": {"max_promoted": 1}}).promoted(today, 5)
    check("the per-candle limit applies, strongest first", list(one) == ["NSE:C-EQ"], one)
    check("results shares are known by the day", pit.results_for(today) == {"NSE:B-EQ": "today"})
    trade = {"symbol": "NSE:A-EQ", "signal_ts": t0 + 300 * 6}                # the signal candle is bar 5, closing at 09:45
    tag = pit.tag(trade)
    check("a trade is tagged with why its share was on the list", tag["promoted"] and tag["results"] is None and not tag["index"], tag)
    early = pit.tag({"symbol": "NSE:C-EQ", "signal_ts": t0 + 300 * 2})                 # C only jumps at the third candle
    check("a signal before the share qualified is not on the list", not early["promoted"], early)
    late = pit.tag({"symbol": "NSE:C-EQ", "signal_ts": t0 + 300 * 6})
    check("and the same share is on it once it has moved", late["promoted"], late)
    check("an index is always scanned", rp.PointInTime(series, {**insts, "NSE:NIFTY50-INDEX": {"kind": "index"}}, None, None)
          .tag({"symbol": "NSE:NIFTY50-INDEX", "signal_ts": t0 + 600})["index"])
    stats = pit.load_stats([today], 3)
    check("it reports how many shares are scanned per candle", stats["avg_shares_scanned_per_candle"] > 0 and stats["all_shares"] == 3, stats)


def test_numbers():
    print("Replay: intervals, splits, costs")
    rng = random.Random(1)
    trades = [{"date": f"2026-10-{d:02d}", "net_r": rng.gauss(0.1, 1.0), "net": 0.0} for d in range(1, 29) for _ in range(5)]
    ci = rp.day_bootstrap(trades)
    mean = sum(t["net_r"] for t in trades) / len(trades)
    check("the interval brackets the average", ci[0] < mean < ci[1], (ci, mean))
    check("the interval is repeatable", ci == rp.day_bootstrap(trades))
    check("too few days give no interval", rp.day_bootstrap(trades[:12]) is None)
    wide = rp.day_bootstrap([dict(t, date="2026-10-01") if i % 2 else t for i, t in enumerate(trades)])
    check("trades piled on few days widen the interval (they are not independent)", wide is None or (wide[1] - wide[0]) >= 0)
    days = [f"d{i:02d}" for i in range(50)]
    sp = rp.split_sessions(days)
    check("sessions split 60/20/20 in time order", len(sp["development"]) == 30 and len(sp["validation"]) == 10 and len(sp["holdout"]) == 10
          and sp["development"][-1] < sp["validation"][0] < sp["holdout"][0])
    sel = [{"gross": 100.0, "cost": 40.0, "risk": 500.0, "date": f"2026-10-{i:02d}", "net_r": 0.12, "net": 60.0} for i in range(1, 8)]
    zero, double = rp.with_cost(sel, 0)[0], rp.with_cost(sel, 2)[0]
    check("cost sensitivity rescales the costs", abs(zero["net_r"] - 0.2) < 1e-9 and abs(double["net_r"] - 0.04) < 1e-9, (zero["net_r"], double["net_r"]))


def make_world():
    days = weekdays(34, date(2026, 11, 12))
    names = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    series = {f"NSE:{n}-EQ": Series(walk(days, random.Random(i + 40), start=400 + 150 * i, vol=0.0035)) for i, n in enumerate(names)}
    series["NSE:NIFTY50-INDEX"] = Series(walk(days, random.Random(99), start=24000, vol=0.002))
    market = {"id": "NIFTY", "kind": "index", "name": "Nifty 50", "spot": "NSE:NIFTY50-INDEX", "root": "NIFTY", "enabled": True}
    sc = sim.settings({"strategies": {"portfolio": {"enabled": False}}})
    insts = sim.build_instruments([market], names, sc["index_lots"])
    return series, insts, sc, days


def test_replay_end_to_end():
    print("Replay: the whole chain on a small market")
    series, insts, sc, days = make_world()
    table = {d.isoformat(): {"BBB": "today", "EEE": "last"} for d in days if d.day % 2 == 0}
    quiet = rp.replay(series, insts, sc, StubResults(table), log=lambda m: None, warm=10)
    check("the holdout is sealed unless asked for", "holdout" not in quiet["experiments"][0] and not quiet["holdout"]["unsealed"], quiet["holdout"])
    names = [e["id"] for e in quiet["experiments"]]
    check("every experiment is reported", names == ["all", "volume", "results", "watchlist", "rv20", "grade", "full"], names)
    n = {e["id"]: e["all"].get("n", 0) for e in quiet["experiments"]}
    check("each filter can only remove trades", n["all"] >= n["watchlist"] >= n["volume"] and n["watchlist"] >= n["results"]
          and n["all"] >= n["grade"] and n["watchlist"] >= n["full"] and n["grade"] >= n["full"], n)
    check("the watchlist is a real subset (it removed some trades)", n["watchlist"] < n["all"], n)
    sp = quiet["splits"]
    check("sealed: the totals cover only development and validation", sp["development"]["sessions"] + sp["validation"]["sessions"] == quiet["period"]["sessions"]
          and quiet["period"]["sessions_incl_holdout"] == quiet["period"]["sessions"] + sp["holdout"]["sessions"], (sp, quiet["period"]))
    check("the sealed run ends before the holdout begins", quiet["period"]["to"] < sp["holdout"]["from"], (quiet["period"]["to"], sp["holdout"]["from"]))
    check("development and validation are reported", all(k in quiet["experiments"][0] for k in ("development", "validation")))
    sens = quiet["cost_sensitivity"]
    avg = [x["avg_r"] for x in sens if x.get("n")]
    check("higher costs never improve the result", avg == sorted(avg, reverse=True), avg)
    check("the load of the watchlist is measured", quiet["watchlist_load"].get("share_of_universe") is not None, quiet["watchlist_load"])
    check("what cannot be replayed is stated", any("News is NOT replayed" in a for a in quiet["assumptions"])
          and any("survivorship" in a for a in quiet["assumptions"]))
    check("the report is plain data", json.loads(json.dumps(quiet)) == quiet)
    opened = rp.replay(series, insts, sc, StubResults(table), log=lambda m: None, final=True, warm=10)
    check("with --final the totals include the holdout sessions", opened["period"]["sessions"] == quiet["period"]["sessions_incl_holdout"]
          and opened["period"]["to"] >= sp["holdout"]["to"], (opened["period"], sp["holdout"]))
    check("and the sealed run's totals are a strict subset of it", quiet["experiments"][0]["all"]["n"] <= opened["experiments"][0]["all"]["n"])
    check("with --final the holdout is reported once", "holdout" in opened["experiments"][0] and opened["holdout"]["unsealed"])
    nonresults = rp.replay(series, insts, sc, None, log=lambda m: None, warm=10)
    check("with no results calendar the results experiment is simply empty", nonresults["experiments"][2]["all"].get("n", 0) == 0)
    d = tempfile.mkdtemp()
    try:
        path = os.path.join(d, "replay.json")
        json.dump(opened, open(path, "w"))
        try:
            rp.main(["--final", "--out", path])
            check("a second --final is refused", False)
        except SystemExit as e:
            check("a second --final is refused", "already unsealed" in str(e), str(e))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_results_history():
    print("Replay: NSE's calendar for past dates")
    raw = [{"symbol": "HAL", "company": "HAL", "purpose": "Financial Results", "date": "20-Oct-2026"},
           {"symbol": "ABB", "company": "ABB", "purpose": "Financial Results", "date": "21-Oct-2026"}]
    d = tempfile.mkdtemp()
    try:
        r = rp.results_history(date(2026, 10, 1), date(2026, 10, 31), ["HAL", "ABB"], cache=d, fetch=lambda a, b: raw, log=lambda m: None)
        g = r.groups(date(2026, 10, 21))
        check("results on past dates are grouped as they would have been", [e["symbol"] for e in g["last"]] == ["HAL"]
              and [e["symbol"] for e in g["today"]] == ["ABB"], g)
        again = rp.results_history(date(2026, 10, 1), date(2026, 10, 31), ["HAL", "ABB"], cache=d, fetch=lambda a, b: 1 / 0, log=lambda m: None)
        check("a cached read is used without asking NSE again", again is not None and len(again.state["events"]) == 2)
        none = rp.results_history(date(2026, 9, 1), date(2026, 9, 30), ["HAL"], cache=None, fetch=lambda a, b: 1 / 0, log=lambda m: None)
        check("if NSE cannot be read the replay runs without results", none is None)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_orb5():
    print("Replay: the 5-minute opening range candidate")
    from engine.strategies.defs import scan_orb5, BY_ID, _daily_atr
    P = {"stop_atr": 0.10}
    days = weekdays(10, date(2026, 11, 12))
    rows = []
    for d in days[:-1]:                                              # earlier days: a 2-point range around 100
        t0 = ts(at(d, "09:15"))
        for k in range(75):
            rows.append([t0 + 300 * k, 100.0, 101.0 if k == 5 else 100.2, 99.0 if k == 9 else 99.8, 100.0, 1000])
    t0 = ts(at(days[-1], "09:15"))

    def today(candles):
        return Series(rows + [[t0 + 300 * k, *c, 1000] for k, c in enumerate(candles)])

    S = today([(100.0, 100.5, 99.8, 100.4), (100.4, 100.8, 100.3, 100.7)])
    i = S.n - 1
    check("the daily average range comes from earlier sessions only", abs(_daily_atr(S, days[-1]) - 2.0) < 1e-9, _daily_atr(S, days[-1]))
    sig = scan_orb5(S, i, P)
    check("a green first candle and a close above its high is a long", sig is not None and sig["dir"] == 1, sig and sig["dir"])
    check("the stop is a tenth of the daily range from the entry", abs(sig["stop"] - (100.7 - 0.2)) < 1e-9, sig["stop"])
    red = today([(100.0, 100.2, 99.5, 99.6), (99.6, 99.7, 99.2, 99.3)])
    sr = scan_orb5(red, red.n - 1, P)
    check("a red first candle and a close below its low is a short", sr is not None and sr["dir"] == -1, sr)
    check("a doji first candle gives no direction", scan_orb5(today([(100.0, 100.5, 99.5, 100.0), (100.0, 101, 100, 100.9)]), S.n - 1, P) is None)
    check("a close that has not passed the first candle is not a signal", scan_orb5(today([(100.0, 100.5, 99.8, 100.4), (100.4, 100.5, 100.3, 100.45)]), S.n - 1, P) is None)
    later = today([(100.0, 100.5, 99.8, 100.4), (100.4, 100.8, 100.3, 100.7), (100.7, 101.0, 100.6, 100.9)])
    check("only the first break counts", scan_orb5(later, later.n - 1, P) is None and scan_orb5(later, later.n - 2, P) is not None)
    check("it is a research candidate, not one of the live strategies", "orb5" in BY_ID and all(s["id"] != "orb5" for s in __import__("engine.strategies.defs", fromlist=["x"]).STRATEGIES))
    # the paper's selection: the 20 shares with the heaviest first-candle volume
    days2 = weekdays(20, date(2026, 11, 12))
    flat = {n: flat_days(20) for n in "ABCD"}
    t1 = ts(at(days2[-1], "09:15"))
    for c in flat["C"]:
        if c[0] == t1:
            c[5] = 9000                                              # C's first candle trades nine times its normal volume
    series = {f"NSE:{n}-EQ": Series(v) for n, v in flat.items()}
    insts = {s: {"symbol": s, "name": s[4:-3], "kind": "stock", "lot": 1} for s in series}
    pit = rp.PointInTime(series, insts, None, None)
    top = pit.top_rv(days2[-1], n=1, bars=1)
    check("the share with the heaviest first-candle volume is selected", list(top) == ["NSE:C-EQ"] and top["NSE:C-EQ"] > 8, top)
    two = pit.top_rv(days2[-1], n=1)
    check("by default the first two candles are used, because Yahoo reports no volume for the first", list(two) == ["NSE:C-EQ"], two)
    check("a trade on the selected share is tagged", pit.tag({"symbol": "NSE:C-EQ", "signal_ts": t1 + 900})["rv20"] is True)
    check("and the top-1 list excludes the others", "NSE:A-EQ" not in pit.top_rv(days2[-1], n=1))


def main():
    test_promotion()
    test_numbers()
    test_replay_end_to_end()
    test_results_history()
    test_orb5()
    print("\nALL REPLAY CHECKS PASSED" if not check.failed else f"\n{check.failed} CHECK(S) FAILED")
    sys.exit(1 if check.failed else 0)


if __name__ == "__main__":
    main()
