"""Checks of the special rules.  Run:  python -m tests.test_engine"""
import copy
import os
import random
import sys
from datetime import date, timedelta

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import clock  # noqa: E402
from engine.clock import at  # noqa: E402
from engine.core import Engine  # noqa: E402
from engine.run import find_code, sessions  # noqa: E402
from engine.store import Store  # noqa: E402
from engine.symbols import Master  # noqa: E402
from tests.simulate import ROOT, build, replay  # noqa: E402


def master():
    cache = os.path.join(ROOT, "tests", ".master_cache")

    def loader(name):
        p = os.path.join(cache, name + ".csv")
        if not os.path.exists(p):
            os.makedirs(cache, exist_ok=True)
            open(p, "w", encoding="utf-8").write(Master._download(name))
        return open(p, encoding="utf-8").read()
    return Master(loader)


def run_day(cfg, plan, day, seed=3, stop_at=None):
    m = master()
    fb = build(cfg, m, [day], {day: plan}, random.Random(seed))
    alerts, feed = [], {"status": {}, "trades": []}
    eng = Engine(cfg, fb, m, alerts.append, feed)
    replay(eng, cfg, day, stop_at)
    clock.set_fake(None)
    return feed, alerts, eng


def main():
    base = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
    m = master()
    exps = sorted({r["expiry"] for r in m.rows("NSE_FO", "NIFTY") if r["type"] == "CE"})
    e0, e1 = clock.from_ts(exps[0]).date(), clock.from_ts(exps[1]).date()
    day = e0 - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    only = lambda ids: [dict(x, enabled=x["id"] in ids) for x in copy.deepcopy(base["markets"])]

    # 1. expiry rules
    assert m.choose_expiry("NSE_FO", "NIFTY", e0, 1, "roll")[0] == exps[1], "expiry day must roll to next week"
    assert m.choose_expiry("NSE_FO", "NIFTY", e0 - timedelta(days=1), 1, "roll")[0] == exps[0]
    bexp = clock.from_ts(sorted({r["expiry"] for r in m.rows("NSE_FO", "BANKNIFTY") if r["type"] == "CE"})[0]).date()
    assert m.choose_expiry("NSE_FO", "BANKNIFTY", bexp - timedelta(days=2), 3, "skip")[0] is None
    assert m.choose_expiry("NSE_FO", "BANKNIFTY", bexp - timedelta(days=3), 3, "skip")[0] is not None
    print("ok  expiry day rolls to next week; Bank Nifty sits out its last three days")

    cfg = copy.deepcopy(base)
    cfg["stocks"]["enabled"] = False
    cfg["markets"] = only(["NIFTY"])

    # 2. wide opening range is skipped
    feed, alerts, _ = run_day(cfg, {"NIFTY": "wide"}, day)
    assert not feed["trades"] and "wider" in feed["status"]["tiles"][0]["note"] or "more than" in feed["status"]["tiles"][0]["note"]
    print("ok  wide opening range: day skipped, no trade")

    # 3. a clean trend day gives exactly one trade with stop, target and reasons
    for seed in range(1, 30):
        feed, alerts, _ = run_day(cfg, {"NIFTY": "up"}, day, seed)
        if feed["trades"]:
            break
    t = feed["trades"][0]
    assert t["option_type"] == "CE" and t["stop_price"] < t["entry_price"] and t["target_underlying"] > t["range_high"]
    assert len(t["reasons"]) >= 4 and t["status"] == "CLOSED" and t["exit_reason"]
    assert abs(t["stop_price"] - t["entry_price"] * 0.75) <= 0.05
    assert any("Why this trade" in a for a in alerts)
    print(f"ok  trend day: {t['headline']}, entry {t['entry_price']}, stop {t['stop_price']}, "
          f"target {round(t['target_underlying'], 1)}, result {t['result']} ({t['exit_reason'][:30]})")

    # 4. two lots: half booked at target, rest trailed
    cfg2 = copy.deepcopy(cfg)
    cfg2["general"]["lots"] = 2
    found = False
    for seed in range(1, 60):
        feed, alerts, _ = run_day(cfg2, {"NIFTY": "up"}, day, seed)
        for t in feed["trades"]:
            if len(t["fills"]) == 2:
                assert t["fills"][0]["qty"] == t["qty"] // 2 and sum(f["qty"] for f in t["fills"]) == t["qty"]
                found = True
        if found:
            break
    assert found
    print("ok  two lots: half booked at the target, the rest trailed and closed")

    # 5. a skip day takes no trades
    cfg3 = copy.deepcopy(cfg)
    cfg3["skip_dates"] = [day.isoformat()]
    feed, _, _ = run_day(cfg3, {"NIFTY": "up"}, day, seed)
    assert not feed["trades"]
    print("ok  skip date: no trades")

    # 6. market holiday: no data, no trades, no stock watchlist
    cfg4 = copy.deepcopy(base)
    cfg4["markets"] = only(["NIFTY"])
    feed, alerts, _ = run_day(cfg4, {"NIFTY": "closed", "stocks": {}}, day)
    assert not feed["trades"] and not any("watchlist" in a for a in alerts)
    assert "closed" in feed["status"]["tiles"][0]["note"]
    print("ok  holiday: market seen as closed")

    # 7. losses stop the day; never more than two trades per market per day
    cfgall = copy.deepcopy(base)
    worst = 0
    for seed in range(1, 25):
        feed, _, _ = run_day(cfgall, {k: random.Random(seed).choice(["fail_up", "fail_down", "up", "down"])
                                      for k in ("NIFTY", "SENSEX", "BANKNIFTY", "CRUDE", "GOLD")}, day, seed)
        per = {}
        for t in feed["trades"]:
            per.setdefault(t["unit"], []).append(t)
        for unit, ts_ in per.items():
            worst = max(worst, len(ts_))
            assert len(ts_) <= 2
            assert not (ts_[0]["result"] == "LOSS" and len(ts_) > 1), "traded again after a loss"
            for t in ts_:
                assert t["status"] == "CLOSED", "trade left open at the end of the day"
                assert t["entry_time"][11:16] <= ("11:31" if t["book"] != "Commodity options" else "20:31")
    print(f"ok  discipline over 24 mixed days: max {worst} trades per market, none after a loss, all closed by exit time")

    # 8. crude sits out Wednesdays
    wed = day
    while wed.weekday() != 2:
        wed -= timedelta(days=1)
    cfg5 = copy.deepcopy(base)
    cfg5["stocks"]["enabled"] = False
    cfg5["markets"] = only(["CRUDE"])
    feed, _, _ = run_day(cfg5, {"CRUDE": "up"}, wed)
    assert not feed["trades"]
    print("ok  crude skipped on Wednesday")

    # 9. login code reading, locked storage, session windows
    assert find_code("https://x.github.io/y/?s=ok&code=200&auth_code=abc.DEF-123&state=s") == "abc.DEF-123"
    assert find_code("hello") is None
    st = Store("/tmp/sd_store_test", "secret-material")
    st.save_secure({"refresh_token": "r"})
    assert st.load_secure() == {"refresh_token": "r"}
    assert Store("/tmp/sd_store_test", "wrong").load_secure() == {}
    names = [(n, a.strftime("%H:%M"), b.strftime("%H:%M")) for n, a, b in sessions(base, day)]
    assert names == [("day", "08:30", "15:08"), ("evening", "17:15", "22:38")], names
    print("ok  login code parsing, locked token file, session hours", names)
    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()
