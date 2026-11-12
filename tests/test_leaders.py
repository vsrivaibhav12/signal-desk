"""Checks the relative-strength leaders and market health. Run:  python -m tests.test_leaders    (offline)"""
import json
import os
import random
import shutil
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import clock, leaders as ld  # noqa: E402
from engine.alerts import Alerts  # noqa: E402
from engine.clock import at, ts  # noqa: E402
from engine.strategies.desk import StrategyDesk  # noqa: E402
from tests.test_segments import DeskBroker, StubResults, MKT, StubMaster  # noqa: E402
from tests.test_strategies import DAY, check, walk, weekdays  # noqa: E402


def make_days(n_days=330, shares=60, crash=False, thin=()):
    """{date: rows} in the shape bhav.load_days gives. Share k drifts from -0.1% to +0.3% a day, so its strength rises with k."""
    days = weekdays(n_days, date(2026, 11, 11))
    out = {d: [] for d in days}
    for k in range(shares):
        p, drift = 100.0 + k, -0.001 + 0.004 * k / (shares - 1)
        sym, isin = f"S{k:02d}", f"INE{k:05d}"
        for j, d in enumerate(days):
            g = drift if not (crash and j >= n_days - 40) else -0.012                   # a market-wide crash over the last 40 sessions
            c = p * (1 + g)
            qty = 20 if sym in thin else 2_000_000
            out[d].append((isin, sym, "EQ", p, max(p, c), min(p, c), c, p, float(qty), c * qty))
            p = c
    return out


def test_compute():
    print("Leaders: ratings, leaders and market health")
    days = make_days()
    out = ld.compute(days)
    check("the last day and the size of the universe are reported", out["asof"] == "2026-11-11" and out["universe"] == 60, (out["asof"], out["universe"]))
    check("the strongest share is rated 100 and the weakest 0", out["rs"]["S59"] == 100.0 and out["rs"]["S00"] == 0.0, (out["rs"]["S59"], out["rs"]["S00"]))
    names = [x["symbol"] for x in out["leaders"]]
    check("leaders are the top fifth, strongest first", names[0] == "S59" and "S00" not in names and all(x["rs"] >= 80 for x in out["leaders"]) and len(names) <= 13,
          names[:3])
    check("a leader carries its 3-month return and how far it is below its high", out["leaders"][0]["ret3m_pct"] > 0 and out["leaders"][0]["off_high_pct"] >= 0)
    b = out["breadth"]
    check("market health is measured across the universe", 0 < b["above200"] < 1 and 0 <= b["above50"] <= 1 and not b["bear_phase"], b)
    check("a share too thin to matter is not rated", "S05" in ld.compute(days)["rs"] and "S05" not in ld.compute(make_days(thin=("S05",)))["rs"])
    crashed = ld.compute(make_days(crash=True))
    check("when nearly every share is below its 200-day average it is a bear phase and there are no leaders",
          crashed["breadth"]["bear_phase"] is True and crashed["n_leaders"] == 0, (crashed["breadth"], crashed["n_leaders"]))
    check("too little history gives no rating, not a wrong one", ld.compute(make_days(n_days=120)) is None)
    check("the file is plain data and small", json.loads(json.dumps(out)) == out and len(json.dumps(out)) < 60_000)


def test_leaders_class():
    print("Leaders: what the rest of the tool asks")
    out = ld.compute(make_days())
    L = ld.Leaders(data=out)
    check("a rating is looked up by share name, unknown names give none", L.rs("S59") == 100.0 and L.rs("NOPE") is None)
    check("is_leader uses the strength threshold", L.is_leader("S59") and not L.is_leader("S10") and not L.is_leader("NOPE"))
    check("a fresh list is ok, an old one is not", L.ok(date(2026, 11, 12)) and not L.ok(date(2026, 11, 20)))
    line = L.health_line()
    check("the brief line states the 200-day and 50-day shares and the leader count", "200-day" in line and "50-day" in line and str(out["n_leaders"]) in line, line)
    bear = ld.Leaders(data=ld.compute(make_days(crash=True)))
    check("the brief line says so in a bear phase", "bear phase" in bear.health_line())
    empty = ld.Leaders(tempfile.mkdtemp())
    check("with no file there is simply nothing to show", empty.rs("X") is None and empty.health_line() is None and not empty.ok(date(2026, 11, 12))
          and empty.public() == {"ok": False})
    d = tempfile.mkdtemp()
    try:
        json.dump(out, open(os.path.join(d, "leaders.json"), "w"))
        check("it reads the file the Leaders job wrote", ld.Leaders(d).rs("S59") == 100.0)
        pub = L.public(date(2026, 11, 12))
        check("the dashboard view carries health, the top leaders and freshness", pub["ok"] and len(pub["leaders"]) <= 25 and "above200" in pub["breadth"])
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_desk_flags():
    print("Leaders: flagged on the watchlist and on trades, never a gate")
    names = ["AAA", "BBB", "CCC", "DDD"]
    days = weekdays(20)
    data = {f"NSE:{n}-EQ": walk(days, random.Random(i + 40), start=400 + 150 * i, vol=0.0035) for i, n in enumerate(names)}
    data["NSE:NIFTY50-INDEX"] = walk(days, random.Random(99), start=24000, vol=0.002)
    folder = tempfile.mkdtemp()
    sent = []
    try:
        L = ld.Leaders(data={"asof": DAY.isoformat(), "universe": 900, "rs_min": 80.0, "n_leaders": 1, "leaders": [],
                             "breadth": {"above50": 0.5, "above200": 0.5, "advancing": 0.5, "bear_phase": False},
                             "rs": {"AAA": 95.0, "BBB": 40.0}})
        from engine.strategies import sim
        sc = {"strategies": {"portfolio": {"enabled": False}, "alert_grade": "all"}}
        mkt = {**MKT, "lot": 75}
        d = StrategyDesk(sc, DeskBroker(data), sent.append, folder, names, [mkt], results=StubResults("CCC"), master=StubMaster(),
                         alerts=Alerts(sent.append, {"alerts": {"trades": True}}), leaders=L)
        t = at(DAY, "09:20")
        while t <= at(DAY, "13:00"):
            now = t + timedelta(seconds=30)
            clock.set_fake(now)
            d.tick(now)
            t += timedelta(minutes=5)
        clock.set_fake(None)
        wl = {w["name"]: w.get("rs") for w in d.state["watchlist"]}
        check("the watchlist carries each share's rating", wl.get("AAA", 95.0) == 95.0 and wl.get("BBB", 40.0) == 40.0 and wl.get("CCC") is None
              and any(v is not None for v in wl.values()), wl)
        tagged = [x for x in d.state["trades"] if x["name"] == "AAA"]
        check("trades on a rated share carry its rating", all(x["dec"]["rs"] == 95.0 for x in tagged), [x["dec"].get("rs") for x in tagged][:3])
        check("trades on an unrated share carry none", all(x["dec"]["rs"] is None for x in d.state["trades"] if x["name"] in ("CCC", "DDD")))
        check("the leaders summary is saved for the dashboard", d.state["leaders"]["ok"] and d.state["leaders"]["universe"] == 900)
        plain = StrategyDesk(sc, DeskBroker(data), lambda m: None, tempfile.mkdtemp(), names, [mkt])
        clock.set_fake(at(DAY, "09:50") + timedelta(seconds=30))
        plain.tick(at(DAY, "09:50") + timedelta(seconds=30))
        clock.set_fake(None)
        check("with no leaders file the desk works exactly as before", "leaders" not in plain.state and all(w.get("rs") is None for w in plain.state.get("watchlist", [])))
        alert_lines = [ln for m in sent for ln in m.split("\n") if "AAA" in ln and ("BUY" in ln or "SELL" in ln)]
        check("alerts say RS leader on a long trade in a leader, and not on a short", alert_lines and all(("RS leader" in ln) == ("BUY" in ln) for ln in alert_lines), alert_lines[:3])
    finally:
        clock.set_fake(None)
        shutil.rmtree(folder, ignore_errors=True)


def main():
    test_compute()
    test_leaders_class()
    test_desk_flags()
    print("\nALL LEADERS CHECKS PASSED" if not check.failed else f"\n{check.failed} CHECK(S) FAILED")
    sys.exit(1 if check.failed else 0)


if __name__ == "__main__":
    main()
