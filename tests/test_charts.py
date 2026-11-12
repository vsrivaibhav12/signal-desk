"""Every idea can be drawn: where to enter, the stop, the target and the trail.  Run:  python -m pytest tests/test_charts.py"""
import os
import random
import shutil
import sys
import tempfile
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
pytest.importorskip("jsonschema")

from engine import charts, clock, contract, live  # noqa: E402
from engine.alerts import Alerts  # noqa: E402
from engine.clock import at, ts  # noqa: E402
from engine.strategies import sim  # noqa: E402
from engine.strategies.defs import BY_ID, STRATEGIES  # noqa: E402
from engine.strategies.desk import StrategyDesk  # noqa: E402
from engine.strategies.indicators import Series  # noqa: E402
from tests.test_segments import DeskBroker, MKT, StubMaster, StubResults  # noqa: E402
from tests.test_strategies import DAY, INST, flat_day, orb_day, walk, weekdays  # noqa: E402


def candles(n=12, base=100.0):
    return [[base + i * 0.1, base + i * 0.1 + 0.5, base + i * 0.1 - 0.4, base + i * 0.1 + 0.2] for i in range(n)]


# ── the trailing line is the line the exit rule uses ─────────────────────────────────────────────
def test_the_drive_trail_is_the_low_of_the_previous_candles_for_a_buy_and_the_high_for_a_sell():
    k = candles()
    buy = sim.decorate_chart({"k": k, "ov": {}}, {"dir": 1, "stop": 99.0, "target": None, "rule_exit": "drive_trail", "trail": 3})
    assert buy["tr"]["v"][:3] == [None, None, None]
    assert buy["tr"]["v"][5] == round(min(c[2] for c in k[2:5]), 2) and "below" in buy["tr"]["label"]
    sell = sim.decorate_chart({"k": k, "ov": {}}, {"dir": -1, "stop": 105.0, "target": None, "rule_exit": "drive_trail", "trail": 2})
    assert sell["tr"]["v"][4] == round(max(c[1] for c in k[2:4]), 2) and "above" in sell["tr"]["label"]
    assert (buy["dir"], buy["st"], buy["tg"]) == (1, 99.0, None)


def test_the_ema_pullback_exit_line_is_the_50_period_average_and_is_not_drawn_twice():
    ch = {"k": candles(3), "ov": {"ema20": [1, 2, 3], "ema50": [4, 5, 6]}}
    out = sim.decorate_chart(ch, {"dir": 1, "stop": 98.0, "target": 103.0, "rule_exit": "trend_fail"})
    assert out["tr"]["v"] == [4, 5, 6] and "ema50" not in out["ov"] and "ema20" in out["ov"] and out["tg"] == 103.0


def test_the_opening_range_breakout_has_no_trail_because_it_has_none():
    out = sim.decorate_chart({"k": candles(3), "ov": {}}, {"dir": 1, "stop": 99.0, "target": 103.0, "rule_exit": None})
    assert "tr" not in out and out["st"] == 99.0 and out["tg"] == 103.0


def test_every_chart_a_simulated_strategy_trade_gets_follows_the_chart_contract():
    cfg = sim.settings({})
    S = Series(walk(weekdays(24), random.Random(23), vol=0.003))
    made = {"drive": 0, "orb": 0, "ema_pullback": 0}
    for strat in STRATEGIES:
        for tr in sim.simulate(S, INST, strat, cfg, chart_from=S.days[0]):
            ch = tr["_chart"]
            contract.assert_valid("chart", ch)
            assert ch["st"] == tr["stop"] and ch["tg"] == tr["target"] and ch["dir"] == tr["dir"]
            assert ("tr" in ch) == (tr["rule_exit"] in ("drive_trail", "trend_fail"))
            if "tr" in ch:
                assert len(ch["tr"]["v"]) == len(ch["k"])
            made[strat["id"]] += 1
    assert made["orb"] and made["ema_pullback"]


# ── option charts are drawn on the index, with the rulebook's own levels ─────────────────────────
def option_trade(S):
    a = S.starts[DAY]
    return {"id": f"{DAY.isoformat()}-AAA-1", "date": DAY.isoformat(), "name": "AAA", "book": "Index options", "kind": "option", "status": "OPEN", "dir": 1,
            "entry_min": ts(at(DAY, "10:32")), "range_high": max(S.h[a:a + 6]), "range_low": min(S.l[a:a + 6]), "range_mid": 100.0,
            "underlying_entry": S.c[a + 15], "stop_underlying": 100.0, "target_underlying": 130.0, "entry_price": 210.0, "stop_price": 157.5}


def test_a_rulebook_option_chart_shows_the_index_levels_and_the_vwap_trail():
    S = Series(walk(weekdays(20), random.Random(41), start=24000, vol=0.002))
    ch = charts.option_trade_chart(S, option_trade(S))
    contract.assert_valid("chart", ch)
    assert (ch["st"], ch["tg"], ch["dir"], ch["sc"]) == (100.0, 130.0, 1, True)
    assert ch["tr"]["label"].startswith("VWAP") and len(ch["tr"]["v"]) == len(ch["k"]) and "vwap" not in ch["ov"]
    assert [l[0] for l in ch["ln"]] == ["Range high", "Range low"]                      # the stop is drawn from st, not as a second line
    assert "not the option's" in ch["tn"] and "157.5" in ch["tn"]


def test_no_candles_means_no_chart_rather_than_a_broken_one():
    S = Series(walk(weekdays(20), random.Random(41), start=24000, vol=0.002))
    assert charts.option_trade_chart(None, option_trade(S)) is None
    assert charts.option_trade_chart(S, {**option_trade(S), "entry_min": None}) is None
    assert charts.scan_chart(None, {"range_high": 1, "range_low": 0}, MKT, DAY) is None


def test_a_scanner_chart_shows_the_opening_range_and_where_price_is_now():
    S = Series(walk(weekdays(20), random.Random(41), start=24000, vol=0.002))
    a = S.starts[DAY]
    read = {"id": "NIFTY", "side": "CE", "range_high": max(S.h[a:a + 6]), "range_low": min(S.l[a:a + 6]), "spot": S.c[-1]}
    ch = charts.scan_chart(S, read, {"name": "Nifty 50", "range": ["09:15", "09:45"]}, S.day[-1])
    contract.assert_valid("chart", ch)
    assert ch["e"] == len(ch["k"]) - 1 and ch["el"] == "Now" and ch["st"] is None and ch["tg"] is None and ch["dir"] == 1
    assert ch["bx"][4] == "Opening range" and "option prices" in ch["tn"] and "above the range high" in ch["tn"]
    put = charts.scan_chart(S, {**read, "side": "PE"}, {"name": "Nifty 50", "range": ["09:15", "09:45"]}, S.day[-1])
    assert put["dir"] == -1 and "below the range low" in put["tn"]
    assert charts.scan_chart(S, {**read, "range_high": None}, {"name": "Nifty 50", "range": ["09:15", "09:45"]}, S.day[-1]) is None


# ── the plan in words, and where each chart is ───────────────────────────────────────────────────
def strategy_trade(**over):
    t = {"id": "drive|HAL|1", "strategy": "drive", "symbol": "NSE:HAL-EQ", "name": "HAL", "kind": "stock", "dir": 1, "date": "2026-11-11", "entry": 4000.0,
         "stop": 3960.0, "target": None, "qty": 12, "risk": 480.0, "status": "OPEN", "signal_t": "2026-11-11T09:55:00+05:30", "rule_exit": "drive_trail",
         "trail": 3, "exit_by": "15:15", "dec": {"g": "take", "f": [], "unknown": [], "cr": 0.1, "cv": 3, "nm": True}, "why": []}
    t.update(over)
    return t


def test_the_plan_for_each_strategy_says_where_to_enter_stop_target_and_trail():
    drive = live.strategy_plan(strategy_trade())
    assert drive["enter"].startswith("Buy near 4,000") and drive["stop"].startswith("Stop 3,960") and "No fixed target" in drive["target"]
    assert "closes below the low of the previous 3 candles" in drive["trail"] and drive["exit_by"] == "Leave by 15:15 whatever happens."
    sell = live.strategy_plan(strategy_trade(dir=-1))
    assert sell["enter"].startswith("Sell near") and "above the high" in sell["trail"]
    orb = live.strategy_plan(strategy_trade(strategy="orb", rule_exit=None, target=4080.0))
    assert orb["target"] == "Target 4,080." and orb["trail"].startswith("No trailing")
    ema = live.strategy_plan(strategy_trade(strategy="ema_pullback", rule_exit="trend_fail", target=4100.0))
    assert "50-period average" in ema["trail"]


def test_the_rulebook_plan_names_the_index_levels_and_the_options_own_stop():
    t = {**option_trade(Series(walk(weekdays(20), random.Random(41), start=24000, vol=0.002))), "contract": "NIFTY 25000 CE", "exit_by": "14:45"}
    plan = live.rulebook_plan(t, {"early_exit": {"enabled": True, "closes_back_inside_range": 2, "vwap_break": True}})
    assert plan["enter"] == "Buy NIFTY 25000 CE near ₹210." and "falls to ₹157.5" in plan["stop"] and "AAA closes a 5-minute candle below 100" in plan["stop"]
    assert "reaching 130" in plan["target"] and "book half" in plan["target"]
    assert "VWAP" in plan["trail"] and "2 closes back inside the opening range" in plan["trail"] and plan["exit_by"].startswith("Leave by 14:45")
    assert "Leave early" not in live.rulebook_plan(t, {})["trail"]


def test_the_chart_references_point_at_the_files_the_engine_writes():
    assert live.strategy_chart_ref(strategy_trade()) == {"file": "chartlive_drive.json", "key": "drive|HAL|1"}
    assert live.rulebook_chart_ref({"id": "d-N-1"}) == {"file": "chartlive_options.json", "key": "d-N-1"}
    idea = live.strategy_idea(strategy_trade(), clock.now().replace(year=2026, month=11, day=11, hour=10, minute=0))
    assert idea.chart == {"file": "chartlive_drive.json", "key": "drive|HAL|1"} and idea.plan["enter"]


# ── end to end: a whole morning through the desk, then every idea's chart is found ───────────────
def test_every_idea_in_live_json_has_a_chart_in_the_files_the_engine_wrote():
    names = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    days = weekdays(20)
    data = {f"NSE:{n}-EQ": walk(days, random.Random(i + 40), start=400 + 150 * i, vol=0.0035) for i, n in enumerate(names)}
    data["NSE:NIFTY50-INDEX"] = walk(days, random.Random(99), start=24000, vol=0.002)
    folder = tempfile.mkdtemp()
    sent = []
    try:
        S0 = Series(data["NSE:NIFTY50-INDEX"])
        a0 = S0.starts[DAY]
        rb = {"id": f"{DAY.isoformat()}-NIFTY-1", "date": DAY.isoformat(), "unit": "NIFTY", "name": "Nifty 50", "book": "Index options", "kind": "option",
              "status": "OPEN", "dir": 1, "entry_min": ts(at(DAY, "10:32")), "entry_time": at(DAY, "10:32").isoformat(), "range_high": max(S0.h[a0:a0 + 6]),
              "range_low": min(S0.l[a0:a0 + 6]), "range_mid": 24000.0, "underlying_entry": S0.c[a0 + 15], "stop_underlying": 24000.0,
              "target_underlying": 24300.0, "entry_price": 210.0, "stop_price": 157.5, "qty": 75, "symbol": "NSE_FO|1", "action": "BUY", "remaining": 75,
              "last_price": 212.0, "open_pnl": 150.0, "risk_amount": 3937.5, "contract": "NIFTY 25000 CE", "reasons": ["why"], "phase": "initial"}
        d = StrategyDesk({"strategies": {"portfolio": {"enabled": False}, "alert_grade": "all"}}, DeskBroker(data), sent.append, folder, names,
                         [{**MKT, "lot": 75}], results=StubResults("BBB"), master=StubMaster(), alerts=Alerts(sent.append, None),
                         options_trades=lambda: [rb])
        t = at(DAY, "09:20")
        while t <= at(DAY, "11:00"):
            now = t + timedelta(seconds=30)
            clock.set_fake(now)
            d.tick(now)
            t += timedelta(minutes=5)
        clock.set_fake(None)
        when = at(DAY, "11:00") + timedelta(seconds=30)
        doc = live.build(when, {}, {"status": {"phase": "watching"}, "trades": [rb]}, {**d.state, "updated": when.isoformat()})
        contract.assert_valid("live", doc)
        assert doc["ideas"]
        missing = []
        for idea in doc["ideas"]:
            ref = idea["chart"]
            assert idea["plan"]["enter"] and idea["plan"]["stop"] and idea["plan"]["trail"]
            chart = (d.charts.get(ref["file"].replace("chartlive_", "").replace(".json", "")) or {}).get(ref["key"])
            if chart is None:
                missing.append((idea["id"], ref))
                continue
            contract.assert_valid("chart", chart)
        assert not missing, missing
        kinds = {i["id"].split(":")[0] for i in doc["ideas"]}
        assert {"rulebook", "strategy"} <= kinds, kinds
        print("ideas checked:", len(doc["ideas"]), sorted(kinds))
    finally:
        clock.set_fake(None)
        shutil.rmtree(folder, ignore_errors=True)
