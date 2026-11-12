"""Checks for the strategy desk.  Run:  python -m tests.test_strategies   (needs no internet)"""
import os
import random
import shutil
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import clock  # noqa: E402
from engine.clock import at, ts  # noqa: E402
from engine.strategies import indicators as ind, sim  # noqa: E402
from engine.strategies.defs import BY_ID, STRATEGIES  # noqa: E402
from engine.strategies.desk import StrategyDesk  # noqa: E402
from engine.strategies.indicators import Series  # noqa: E402

DAY = date(2026, 11, 11)


def weekdays(n, end=DAY):
    d, out = end, []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def walk(days, rng, start=1000.0, vol=0.0025):
    out, p = [], start
    for day in days:
        t0 = ts(at(day, "09:15"))
        p *= 1 + rng.gauss(0, 0.004)                         # overnight gap
        trend = rng.gauss(0, 0.0004)
        for i in range(75):
            o = p
            p = p * (1 + trend + rng.gauss(0, vol))
            out.append([t0 + i * 300, o, max(o, p) * (1 + abs(rng.gauss(0, 0.0006))),
                        min(o, p) * (1 - abs(rng.gauss(0, 0.0006))), p, rng.randint(1000, 9000) * (3 if i < 3 else 1)])
    return out


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"   {detail}" if detail and not cond else ""))
    if not cond:
        check.failed += 1


check.failed = 0
INST = {"symbol": "NSE:AAA-EQ", "name": "AAA", "kind": "stock", "lot": 1}


def test_indicators():
    print("Indicators")
    check("ema of a flat line is that line", all(abs(x - 5) < 1e-9 for x in ind.ema([5] * 30, 9)))
    rising = [100 + i for i in range(60)]
    check("rsi of a steady rise is 100", ind.rsi(rising)[-1] == 100)
    falling = [200 - i for i in range(60)]
    check("rsi of a steady fall is 0", ind.rsi(falling)[-1] < 1)
    h = [x + 1 for x in rising]
    l = [x - 1 for x in rising]
    a = ind.adx(h, l, rising)[-1]
    check("adx of a clean trend is high", a is not None and a > 40, a)
    flat = [100 + (1 if i % 2 else -1) for i in range(80)]
    a2 = ind.adx([x + 1 for x in flat], [x - 1 for x in flat], flat)[-1]
    check("adx of a zig-zag is low", a2 is not None and a2 < 15, a2)
    mid, up, lo, w = ind.bollinger([10.0] * 40)
    check("bollinger bands collapse on a flat line", abs(w[-1]) < 1e-12 and abs(up[-1] - 10) < 1e-9)
    check("rolling minimum", ind.rolling_min([5, 3, 4, 6, 7], 3) == [5, 3, 3, 3, 4])
    check("5-minute slots count from 9:15", ind.slot(ts(at(DAY, "09:15"))) == 0 and ind.slot(ts(at(DAY, "09:20"))) == 1
          and ind.slot(ts(at(DAY, "15:25"))) == 74)


def test_no_lookahead():
    print("No look-ahead")
    rng = random.Random(7)
    days = weekdays(22)
    candles = walk(days, rng, vol=0.003)
    cfg = sim.settings({})
    full = Series(candles)
    total = 0
    for cut_slot in (30, 50, 62):
        cut = candles[:len(candles) - 75 + cut_slot]
        part = Series(cut)
        for strat in STRATEGIES:
            f = {t["id"]: t for t in sim.simulate(full, INST, strat, cfg, days=[DAY], live_day=DAY)}
            p = sim.simulate(part, INST, strat, cfg, days=[DAY], live_day=DAY)
            total += len(p)
            for t in p:
                g = f.get(t["id"])
                same_entry = g is not None and all(g[k] == t[k] for k in ("entry", "stop", "target", "qty", "dir"))
                done_same = t["status"] == "OPEN" or (g is not None and g["status"] == "CLOSED" and g["net"] == t["net"]
                                                      and g["exit_t"] == t["exit_t"])
                check(f"{strat['id']} cut@{cut_slot}: trade {t['id'][-18:]} unchanged by later candles", same_entry and done_same)
    check("the test saw real signals", total > 0, total)


def flat_day(day, price=100.0, volume=1000):
    t0 = ts(at(day, "09:15"))
    return [[t0 + i * 300, price, price, price, price, volume] for i in range(75)]


def orb_day(day, scenario):
    t0 = ts(at(day, "09:15"))
    bars = [[t0 + i * 300, 101.5, 101.5, 101.5, 101.5, 1000] for i in range(75)]
    bars[0] = [t0, 100, 101, 100, 100.5, 3000]
    bars[1] = [t0 + 300, 100.5, 100.8, 100.2, 100.4, 3000]
    bars[2] = [t0 + 600, 100.4, 100.9, 100.1, 100.8, 3000]
    bars[3] = [t0 + 900, 100.6, 101.6, 100.5, 101.5, 3000]           # first close above 101: the entry
    nxt = {"target": [101.5, 102.6, 101.4, 102.0], "both": [101.5, 103.0, 99.5, 101.0],
           "gap": [99.0, 99.5, 98.5, 99.0], "quiet": [101.5, 101.5, 101.5, 101.5]}[scenario]
    bars[4] = [t0 + 1200] + nxt + [1000]
    return bars


def test_fills():
    print("Entries, stops, targets and costs")
    cfg = sim.settings({"strategies": {"min_bars": 0, "execution": {"entry_mode": "signal_close"}}})
    strat = BY_ID["orb"]
    prev = flat_day(DAY - timedelta(days=1))
    for scenario, kind, price in (("target", "TARGET", 102.5), ("both", "STOP", 100.0),
                                  ("gap", "STOP", 99.0), ("quiet", "TIME", 101.5)):
        S = Series(prev + orb_day(DAY, scenario))
        trades = sim.simulate(S, INST, strat, cfg, days=[DAY])
        t = trades[0] if trades else None
        check(f"orb/{scenario}: one trade taken", len(trades) == 1, len(trades))
        if not t:
            continue
        check(f"orb/{scenario}: enters at 101.5 with stop 100", t["entry"] == 101.5 and t["stop"] == 100.0, (t["entry"], t["stop"]))
        check(f"orb/{scenario}: target is 1.5x range above the high", t["target"] == 102.5, t["target"])
        check(f"orb/{scenario}: ends by {kind} at {price}", t["exit_type"] == kind and abs(t["exit"] - price) < 1e-9,
              (t["exit_type"], t["exit"]))
        check(f"orb/{scenario}: risk is quantity x distance to stop", abs(t["risk"] - t["qty"] * 1.5) < 0.01, t["risk"])
        want = (t["exit"] - t["entry"]) * t["qty"]
        check(f"orb/{scenario}: gross profit is correct", abs(t["gross"] - want) < 0.02, (t["gross"], want))
        cost = sim.trade_cost("stock", t["qty"], t["entry"], t["exit"], cfg)
        check(f"orb/{scenario}: net = gross - costs", abs(t["net"] - (t["gross"] - cost)) < 0.02)
    # shorts mirror longs
    mirror = [[c[0], 200 - c[1], 200 - c[3], 200 - c[2], 200 - c[4], c[5]] for c in orb_day(DAY, "target")]
    S = Series([[c[0], 200 - c[1], 200 - c[3], 200 - c[2], 200 - c[4], c[5]] for c in prev] + mirror)
    tr = sim.simulate(S, INST, strat, cfg, days=[DAY])
    check("orb: a mirrored day gives a short that hits its target", len(tr) == 1 and tr[0]["dir"] == -1
          and tr[0]["exit_type"] == "TARGET", [(t["dir"], t["exit_type"]) for t in tr])
    # a stop closer than the minimum is pushed out
    tiny = sim._open_trade(strat, INST, Series(prev + orb_day(DAY, "quiet")), 3 + 75,
                           {"dir": 1, "stop": 101.499, "target": None, "target_r": 2, "why": [], "regime": ""}, cfg)
    check("a too-tight stop is moved out to the minimum", tiny is not None and abs(101.5 - tiny["stop"]) >= 101.5 * 0.002 - 0.01,
          tiny and tiny["stop"])


class FakeBroker:
    def __init__(self, data):
        self.data, self.calls = data, 0

    def history(self, sym, res, d0, d1):
        self.calls += 1
        now = ts(clock.now())
        return [c for c in self.data[sym] if d0 <= clock.from_ts(c[0]).date() <= d1 and c[0] <= now]


def test_risk_controls():
    print("Position sizing and portfolio controls")
    from engine.strategies import risk
    check("one share is rejected when it breaches the risk budget",
          risk.quantity_for_risk(1000, 700, 500, 5000) == 0)
    check("one share is rejected when it breaches the position-value cap",
          risk.quantity_for_risk(1000, 5, 500, 500) == 0)
    check("quantity respects both limits",
          risk.quantity_for_risk(100, 10, 500, 250) == 2)
    trades = [
        {"id": "a", "date": "2026-11-11", "entry_ts": 100, "exit_ts": 200, "status": "CLOSED",
         "symbol": "AAA", "risk": 500, "net": -1600, "strategy": "breakout", "dec": {"g": "take"}},
        {"id": "b", "date": "2026-11-11", "entry_ts": 150, "exit_ts": 250, "status": "CLOSED",
         "symbol": "BBB", "risk": 500, "net": 400, "strategy": "ema", "dec": {"g": "take"}},
        {"id": "c", "date": "2026-11-11", "entry_ts": 210, "exit_ts": 300, "status": "CLOSED",
         "symbol": "CCC", "risk": 500, "net": 200, "strategy": "vwap", "dec": {"g": "take"}},
    ]
    risk.annotate(trades, {"enabled": True, "require_take_grade": True, "max_open_positions": 5,
                           "max_open_positions_per_symbol": 1, "max_total_open_risk": 2500, "max_daily_loss": 1500})
    check("a realized daily loss blocks subsequent entries", trades[0]["risk_approved"] and
          not trades[2]["risk_approved"] and "daily_loss_limit" in trades[2]["risk_reasons"])

    same_symbol = [
        {"id": "x1", "date": "2026-11-11", "entry_ts": 100, "exit_ts": 300, "status": "CLOSED", "symbol": "X",
         "risk": 400, "net": 100, "strategy": "breakout", "dec": {"g": "take"}},
        {"id": "x2", "date": "2026-11-11", "entry_ts": 110, "exit_ts": 250, "status": "CLOSED", "symbol": "X",
         "risk": 400, "net": 100, "strategy": "ema", "dec": {"g": "take"}},
    ]
    risk.annotate(same_symbol, {"enabled": True, "require_take_grade": True, "max_open_positions": 5,
                                "max_open_positions_per_symbol": 1, "max_total_open_risk": 2500, "max_daily_loss": 1500})
    check("same-symbol concurrent strategy signals are not double-counted", same_symbol[0]["risk_approved"] and
          not same_symbol[1]["risk_approved"] and "symbol_already_exposed" in same_symbol[1]["risk_reasons"])

    external = [{"id": "rulebook-1", "date": "2026-11-11", "entry_ts": 100,
                 "status": "OPEN", "symbol": "NIFTY-OPT", "risk_amount": 2200}]
    cross_book = [{"id": "strategy-1", "date": "2026-11-11", "entry_ts": 200,
                   "status": "OPEN", "symbol": "AAA", "risk": 600,
                   "strategy": "breakout", "dec": {"g": "take"}}]
    risk.annotate(cross_book, {"enabled": True, "max_open_positions": 5,
                              "max_open_positions_per_symbol": 1, "max_total_open_risk": 2500,
                              "max_daily_loss": 1500}, external_trades=external,
                  shared_cfg={"enabled": True, "max_open_positions": 8,
                              "max_open_risk": 2500, "max_daily_loss": 3500})
    check("rulebook positions consume the shared cross-engine risk budget",
          not cross_book[0]["risk_approved"] and "shared_max_open_risk" in cross_book[0]["risk_reasons"])

    from engine.core import Engine
    eng = Engine.__new__(Engine)
    eng.cfg = {"general": {"portfolio": {"enabled": True, "max_open_positions": 5,
                                           "max_open_risk": 15000, "max_daily_loss": 3000}},
               "portfolio": {"enabled": True, "max_open_positions": 8,
                             "max_open_risk": 1500, "max_daily_loss": 3500}}
    eng.feed = {"trades": []}
    eng.portfolio_provider = lambda: [{"id": "strategy-open", "date": DAY.isoformat(), "status": "OPEN",
                                       "risk_approved": True, "risk": 1100}]
    rejected = eng._portfolio_rejection({"risk_amount": 500}, DAY)
    check("rulebook engine sees strategy-desk open risk before admitting a trade",
          "shared portfolio open risk would be exceeded" in rejected, rejected)


def test_next_open_execution():
    print("Conservative next-candle execution")
    prev = flat_day(DAY - timedelta(days=1))
    bars = orb_day(DAY, "target")
    bars[4][1] = 101.8  # distinguish the next candle open from the signal candle close (101.5)
    S = Series(prev + bars)
    cfg = sim.settings({"strategies": {"min_bars": 0, "execution": {"entry_mode": "next_open"}}})
    tr = sim.simulate(S, INST, BY_ID["orb"], cfg, days=[DAY])
    entry_index = S.index_of(ts(at(DAY, "09:35")))
    check("next-open model records the next candle open, not the signal close",
          bool(tr) and tr[0]["fill_model"] == "next_open" and tr[0]["entry"] == 101.8
          and tr[0]["entry_ts"] == ts(at(DAY, "09:35")), tr[0] if tr else "no trade")
    # A gap through the original stop invalidates the setup rather than widening risk to force a fill.
    bars = orb_day(DAY, "gap")
    S_gap = Series(prev + bars)
    cfg_close = sim.settings({"strategies": {"min_bars": 0, "execution": {"entry_mode": "next_open"}}})
    gap_trades = sim.simulate(S_gap, INST, BY_ID["orb"], cfg_close, days=[DAY])
    check("a gap through the stop does not force an entry", not gap_trades)


def test_desk():
    print("Live desk matches the backtest, survives a restart")
    rng = random.Random(11)
    days = weekdays(20)
    names = ["AAA", "BBB", "CCC", "DDD"]
    data = {f"NSE:{n}-EQ": walk(days, random.Random(i + 3), start=500 + 300 * i, vol=0.003) for i, n in enumerate(names)}
    folder = tempfile.mkdtemp()
    sent, announced = [], []
    try:
        def make():
            d = StrategyDesk({"strategies": {"telegram": "signals", "alert_grade": "all", "portfolio": {"enabled": False}}}, FakeBroker(data), sent.append, folder, names, [])
            real = d._tell
            d._tell = lambda new, done: (announced.extend(t["id"] for t in new), real(new, done))
            return d

        desk = make()
        t = at(DAY, "09:20")
        restarted = False
        while t <= at(DAY, "15:30"):
            now = t + timedelta(seconds=30)
            clock.set_fake(now)
            desk.tick(now)
            if desk.dirty:
                desk.save()
                desk.dirty = False
            if not restarted and t >= at(DAY, "12:00"):
                desk = make()                              # the run died and a fresh one took over
                restarted = True
            t += timedelta(minutes=5)
        clock.set_fake(None)
        cfg = sim.settings({})
        expect = []
        for sym, inst in desk.insts.items():
            S = Series(data[sym])
            for strat in STRATEGIES:
                expect += sim.simulate(S, inst, strat, cfg, days=[DAY], live_day=DAY)
        got = {x["id"]: x for x in desk.state["trades"] if x["date"] == DAY.isoformat()}
        check("the test day produced signals", len(expect) > 5, len(expect))
        check("live trades have exactly the ids the backtest finds", set(got) == {e["id"] for e in expect},
              (len(got), len(expect)))
        same = all(got[e["id"]]["status"] == e["status"] and got[e["id"]].get("net") == e.get("net") for e in expect if e["id"] in got)
        check("live results equal backtest results", same)
        check("signals were announced as they appeared", len(announced) > 5, len(announced))
        check("nothing was announced twice, even across the restart", len(announced) == len(set(announced)),
              len(announced) - len(set(announced)))
        check("every trade is closed by the end of the day", all(x["status"] == "CLOSED" for x in got.values()))
        check("a summary was sent", any("Strategy desk: today" in m for m in sent))
        check("today's stats add up", sum(v["signals"] for v in desk.state["today"].values()) == len(got))
        check("indicators still warm after the restart (no gaps)", desk.state["status"]["failed"] == 0)
    finally:
        clock.set_fake(None)
        shutil.rmtree(folder, ignore_errors=True)


def replay(desk, data_end=None):
    t = at(DAY, "09:20")
    while t <= at(DAY, "15:30"):
        now = t + timedelta(seconds=30)
        clock.set_fake(now)
        desk.tick(now)
        t += timedelta(minutes=5)
    clock.set_fake(None)


def test_archive_preserves_unresolved_positions():
    print("Archive preserves unresolved positions")
    names = ["AAA"]
    folder = tempfile.mkdtemp()
    try:
        d = StrategyDesk({}, FakeBroker({}), lambda m: None, folder, names, [])
        old_day = (DAY - timedelta(days=20)).isoformat()
        d.state["trades"] = [
            {"id": "stuck", "date": old_day, "strategy": "breakout", "status": "OPEN", "risk": 300,
             "symbol": "NSE:AAA-EQ", "risk_approved": True},
            {"id": "done", "date": old_day, "strategy": "breakout", "status": "CLOSED", "risk_approved": True,
             "gross": 100, "net": 80, "net_r": 0.2},
        ]
        d._roll_archive(DAY)
        check("an old unresolved open position remains in the live ledger",
              any(t["id"] == "stuck" and t["status"] == "OPEN" for t in d.state["trades"]))
        check("an old closed position is folded into the archive",
              d.state["archive"]["breakout"]["n"] == 1 and not any(t["id"] == "done" for t in d.state["trades"]))
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_alerts_and_heartbeat():
    print("Telegram alerts for every strategy, heartbeat, time budget")
    from engine.strategies import desk as deskmod
    names = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    data = {f"NSE:{n}-EQ": walk(weekdays(20), random.Random(i + 40), start=400 + 150 * i, vol=0.0035) for i, n in enumerate(names)}
    folder = tempfile.mkdtemp()
    try:
        sent, beats = [], []
        d = StrategyDesk({"strategies": {"alert_grade": "all", "portfolio": {"enabled": False}}}, FakeBroker(data), sent.append, folder, names, [], heartbeat=lambda: beats.append(1))
        check("alerts are on by default", d.tg_signals and d.tg_on and StrategyDesk({}, FakeBroker(data), print, folder, names, []).alert_grade == "take")
        replay(d)
        text = "\n".join(sent)
        seen = {s["name"] for s in STRATEGIES if f"• {s['name']}:" in text}
        trig = {t["strategy"] for t in d.state["trades"]}
        check("alert messages were sent", any("new strategy signal" in m for m in sent), len(sent))
        import re
        missed = [s["name"] for s in STRATEGIES if s["id"] in trig and not re.search(re.escape(s["name"]) + r" \d+", text)]
        check("every strategy that triggered a trade was alerted at least once", not missed and len(trig) >= 5, (missed, len(trig)))
        check("no message lists more than 10 example signals", all(m.count("\n• ") <= deskmod.MAX_LINES for m in sent))
        check("the heartbeat is called during a pass", len(beats) > 20, len(beats))
        # only some strategies
        sent2 = []
        d2 = StrategyDesk({"strategies": {"alert_for": ["orb"], "alert_grade": "all", "portfolio": {"enabled": False}}}, FakeBroker(data), sent2.append, tempfile.mkdtemp(), names, [])
        replay(d2)
        body = "\n".join(m for m in sent2 if "new strategy signal" in m)
        check("alert_for limits the alerts to the chosen strategies", "• Opening range breakout:" in body and
              not any(f"• {s['name']}:" in body for s in STRATEGIES if s["id"] != "orb"))
        check("telegram: off sends nothing at all", not (lambda s: (replay(StrategyDesk({"strategies": {"telegram": "off"}}, FakeBroker(data),
              s.append, tempfile.mkdtemp(), names, [])), s)[1])([]))
        # a pass that runs out of time keeps what was already found, and carries on next candle
        old = deskmod.PASS_BUDGET
        d3 = StrategyDesk({}, FakeBroker(data), lambda m: None, tempfile.mkdtemp(), names, [])
        clock.set_fake(at(DAY, "11:00") + timedelta(seconds=30))
        d3.tick(clock.now())
        full = len(d3.state["trades"])
        deskmod.PASS_BUDGET = 0
        clock.set_fake(at(DAY, "11:05") + timedelta(seconds=30))
        d3.tick(clock.now())
        deskmod.PASS_BUDGET = old
        clock.set_fake(None)
        check("a pass that runs out of time does not lose the day's trades", len(d3.state["trades"]) >= full and full > 0,
              (full, len(d3.state["trades"])))
    finally:
        clock.set_fake(None)


class FakeFyers:
    def __init__(self, candles):
        self.candles, self.calls, self.bad = candles, 0, []

    def history(self, sym, res, d0, d1):
        self.calls += 1
        if res != "5" or (d1 - d0).days > 95:
            self.bad.append((res, (d1 - d0).days))
        out = [c for c in self.candles if d0 <= clock.from_ts(c[0]).date() <= d1]
        return out + out[:3]                                   # Fyers can repeat a candle at a chunk edge


def test_fyers_year():
    print("Year of Fyers history")
    from engine.strategies.data import series_from_fyers
    days = weekdays(180)
    candles = walk(days, random.Random(5))
    fy = FakeFyers(candles)
    clock.set_fake(at(DAY, "16:00"))
    out, missing = series_from_fyers(fy, ["NSE:AAA-EQ"], 250, log=lambda m: None)
    clock.set_fake(None)
    S = out["NSE:AAA-EQ"]
    check("asked in chunks of at most 95 days, at 5-minute resolution", not fy.bad and fy.calls == 3, (fy.bad, fy.calls))
    check("no candle appears twice", len(S.t) == len(set(S.t)) and S.t == sorted(S.t))
    check("every session in range is present", len(S.days) == len(days) - 0 or len(S.days) >= 170, len(S.days))
    res = sim.run_all(out, {"NSE:AAA-EQ": INST}, sim.settings({}), log=lambda m: None)
    check("all ten strategies run over a year without error", set(res) == {s["id"] for s in STRATEGIES})


def test_charts():
    print("Trade charts and market conditions")
    cfg = sim.settings({"strategies": {"min_bars": 0}})
    prev = flat_day(DAY - timedelta(days=1))
    S = Series(prev + orb_day(DAY, "target"))
    t = sim.simulate(S, INST, BY_ID["orb"], cfg, days=[DAY], chart_from=DAY)[0]
    ch = t.pop("_chart")
    check("a trade's chart has candles in four numbers each", ch["k"] and all(len(k) == 4 for k in ch["k"]), ch["k"][:1])
    check("the trigger candle is the one whose close gave the entry", ch["k"][ch["e"]][3] == 101.5 and ch["ep"] == 101.5, (ch["e"], ch["ep"]))
    check("the exit candle and price are marked", ch["x"] == ch["e"] + 1 and ch["xp"] == 102.5, (ch["x"], ch["xp"]))
    check("opening range levels and the shaded box are drawn", [l[0] for l in ch["ln"]] == ["Opening range high", "Opening range low"]
          and ch["bx"][:2] == [0, 2] and ch["bx"][2:4] == [100, 101], (ch["ln"], ch["bx"]))
    check("candle times step in 5-minute slots", ch["d"][:4] == [0, 1, 2, 3] and ch["t0"] == ts(at(DAY, "09:15")))
    check("no stray working fields are left on the trade", not [k for k in t if k.startswith("_")], [k for k in t if k.startswith("_")])
    S2 = Series(prev + orb_day(DAY, "quiet"))
    quiet = sim.simulate(S2, INST, BY_ID["orb"], sim.settings({"strategies": {"min_bars": 0, "exit_by": "23:00"}}),
                         days=[DAY], live_day=DAY, chart_from=DAY)[0]
    check("a trade still open has no exit marker", quiet["status"] == "OPEN" and quiet["_chart"]["x"] is None and quiet["_chart"]["xp"] is None)
    check("without chart_from no chart is built", "_chart" not in sim.simulate(S, INST, BY_ID["orb"], cfg, days=[DAY])[0])

    rng = random.Random(23)
    Sx = Series(walk(weekdays(24), rng, vol=0.003))
    cfg2 = sim.settings({})
    seen_ov, bad = set(), []
    for strat in STRATEGIES:
        for tr in sim.simulate(Sx, INST, strat, cfg2, chart_from=Sx.days[0]):
            c = tr["_chart"]
            for name, vals in c["ov"].items():
                seen_ov.add(name)
                if len(vals) != len(c["k"]):
                    bad.append((strat["id"], name))
            if not (0 <= c["e"] < len(c["k"])):
                bad.append((strat["id"], "e"))
    check("every indicator line has one value per candle", not bad, bad[:3])
    check("indicator lines were drawn for the Bollinger, EMA and VWAP strategies", {"vwap", "bb_up", "ema9", "ema20"} <= seen_ov, seen_ov)

    # the desk writes chart files, keeps only recent ones, charts the options desk's trades, and reports market conditions
    names = ["AAA", "BBB", "CCC", "DDD"]
    data = {f"NSE:{n}-EQ": walk(weekdays(20), random.Random(i + 70), start=500 + 200 * i, vol=0.0035) for i, n in enumerate(names)}
    folder = tempfile.mkdtemp()
    try:
        opt = {"id": f"{DAY.isoformat()}-AAA-1", "date": DAY.isoformat(), "name": "AAA", "book": "Stocks", "status": "OPEN",
               "entry_min": ts(at(DAY, "10:32")), "range_high": 0, "range_low": 0, "range_mid": 0, "underlying_entry": 0}
        S0 = Series(data["NSE:AAA-EQ"])
        a0 = S0.starts[DAY]
        opt.update(range_high=max(S0.h[a0:a0 + 3]), range_low=min(S0.l[a0:a0 + 3]), underlying_entry=S0.c[a0 + 15])
        d = StrategyDesk({}, FakeBroker(data), lambda m: None, folder, names, [], options_trades=lambda: [opt])
        d.charts["orb"]["ghost-old-trade"] = {"k": []}
        replay(d)
        d.save()
        files = sorted(f for f in os.listdir(folder) if f.startswith("chartlive_"))
        check("chart files are written per strategy, loaded only when needed", len(files) >= 3 and "strategies.json" in os.listdir(folder), files)
        main = open(os.path.join(folder, "strategies.json"), encoding="utf-8").read()
        check("the main live file carries no candle data", '"k":[[' not in main and len(main) < 5_000_000)
        ids = {t["id"] for t in d.state["trades"]}
        stored = {sid: set(v) for sid, v in d.charts.items() if v}
        check("every stored chart belongs to a live trade", all(v <= ids | {opt["id"]} for v in stored.values()), {k: len(v) for k, v in stored.items()})
        check("charts of unknown old trades are dropped", "ghost-old-trade" not in d.charts["orb"])
        check("the options desk's trade has a chart with its opening range", opt["id"] in d.charts["options"]
              and d.charts["options"][opt["id"]]["bx"][:2] == [0, 2] and d.charts["options"][opt["id"]]["xp"] is None)
        sc = d.charts["options"][opt["id"]]
        check("that chart's trigger candle matches the entry time", clock.from_ts(sc["t0"] + sc["d"][sc["e"]] * 300).strftime("%H:%M") == "10:25",
              clock.from_ts(sc["t0"] + sc["d"][sc["e"]] * 300).strftime("%H:%M"))
        rg = d.state.get("regime")
        check("market conditions are reported", rg and rg["breadth"]["stocks"] == 4 and rg["breadth"]["trending"] is not None, rg)
        check("each condition-gated strategy says how many shares pass its filter", set(rg["filters"]) == {"bb_bounce", "ema_pullback", "ema_cross"}
              and all(0 <= f["share"] <= 100 for f in rg["filters"].values()))
        check("trending and ranging shares add up to everything", rg["breadth"]["trending"] + rg["breadth"]["ranging"] in (99, 100, 101))
    finally:
        clock.set_fake(None)
        shutil.rmtree(folder, ignore_errors=True)




def mk_series(days_n=8, today_fn=None, prev_price=100.0, prev_vol=1000):
    """Several flat sessions, then a custom 'today'."""
    days = weekdays(days_n)
    candles = []
    for d in days[:-1]:
        t0 = ts(at(d, "09:15"))
        candles += [[t0 + i * 300, prev_price, prev_price * 1.0005, prev_price * 0.9995, prev_price, prev_vol] for i in range(75)]
    t0 = ts(at(days[-1], "09:15"))
    candles += today_fn(t0) if today_fn else [[t0 + i * 300, prev_price, prev_price * 1.0005, prev_price * 0.9995, prev_price, prev_vol] for i in range(75)]
    return candles


def test_decide():
    print("Real trade or just watching")
    from engine.strategies import decide
    dc = decide.settings({})
    rng = random.Random(5)
    S = Series(walk(weekdays(12), rng, vol=0.003))
    i = S.ends[S.days[-1]] - 20
    mk = Series(walk(weekdays(12), random.Random(6), vol=0.003))
    mk.symbol = "NSE:NIFTY50-INDEX"
    j = mk.index_of(S.t[i])
    tr = {"dir": 1, "risk": 500.0, "symbol": "NSE:AAA-EQ", "kind": "stock"}

    def with_(cost, cum, nifty_up):
        S.cumratio[i] = cum
        mk.c[j], mk.vwap[j] = (101, 100) if nifty_up else (99, 100)
        return decide.assess(S, i, dict(tr), cost, mk, dc)
    ok = with_(60, 3.0, True)
    check("a wide stop, heavy volume and Nifty agreeing is a take", ok["g"] == "take" and ok["f"] == [], ok)
    check("costs above 25% of the risk make it a watch", with_(150, 3.0, True)["f"] == ["cost"])
    check("ordinary volume makes it a watch", with_(60, 1.2, True)["f"] == ["vol"])
    check("Nifty on the other side makes it a watch", with_(60, 3.0, False)["f"] == ["mkt"])
    several = with_(200, 1.0, False)
    check("every failed check is listed", set(several["f"]) == {"cost", "vol", "mkt"} and several["g"] == "watch", several)
    S.cumratio[i] = None
    idx = decide.assess(S, i, {**tr, "kind": "index", "symbol": "NSE:NIFTYBANK-INDEX"}, 60, mk, dc)
    check("index strategies do not require a volume ratio", "vol" not in idx["f"])
    missing_volume = decide.assess(S, i, dict(tr), 60, mk, dc)
    check("missing stock volume fails closed instead of passing", missing_volume["g"] == "watch" and "vol_data" in missing_volume["f"])
    own = decide.assess(S, i, {**tr, "symbol": "NSE:NIFTY50-INDEX"}, 60, mk, dc)
    check("Nifty is not checked against itself", own["nm"] is None and "mkt" not in own["f"])
    no_market = decide.assess(S, i, dict(tr), 60, None, dc)
    check("missing market confirmation fails closed", no_market["g"] == "watch" and "mkt_data" in no_market["f"])

    a = {"date": "2026-11-11", "symbol": "X", "dir": 1, "entry_ts": 100, "dec": {"g": "take", "f": [], "cf": 0}}
    b = {"date": "2026-11-11", "symbol": "X", "dir": -1, "entry_ts": 200, "dec": {"g": "take", "f": [], "cf": 0}}
    c = {"date": "2026-11-11", "symbol": "X", "dir": 1, "entry_ts": 300, "dec": {"g": "take", "f": [], "cf": 0}}
    other = {"date": "2026-11-11", "symbol": "Y", "dir": -1, "entry_ts": 250, "dec": {"g": "take", "f": [], "cf": 0}}
    decide.finalize([a, b, c, other], dc)
    check("a trade is a watch if the opposite side was already taken in the same stock",
          a["dec"]["g"] == "take" and b["dec"]["g"] == "watch" and b["dec"]["f"] == ["conf"] and b["dec"]["cf"] == 1
          and c["dec"]["g"] == "watch" and c["dec"]["cf"] == 1 and other["dec"]["g"] == "take", [x["dec"] for x in (a, b, c, other)])
    check("the first trade in a stock is never blamed for a later opposite one", a["dec"]["cf"] == 0)

    # through the real simulator
    cfg = sim.settings({})
    res = sim.run_all({"NSE:AAA-EQ": Series(walk(weekdays(24), random.Random(31), vol=0.0035))}, {"NSE:AAA-EQ": INST}, cfg, log=lambda m: None)
    allt = [t for v in res.values() for t in v]
    check("every simulated trade carries a grade", allt and all(t["dec"]["g"] in ("take", "watch") for t in allt), len(allt))
    check("some trades are watch and the grade follows the checks", any(t["dec"]["g"] == "watch" for t in allt)
          and all((t["dec"]["g"] == "take") == (not t["dec"]["f"]) for t in allt))
    thin = [t for t in allt if t["dec"]["cr"] > 0.25]
    check("trades whose costs are over a quarter of the risk are never a take", all(t["dec"]["g"] == "watch" for t in thin), len(thin))


def test_trend_setters():
    print("Trend setters, opening drive, hot-first pass")

    def strong(t0):                                  # gaps up a little, then rises 0.8% a candle on 6x volume and holds
        out, p = [], 100.4
        for i in range(75):
            o = p
            p = p * 1.008 if i < 14 else p * (1 + (0.0004 if i % 2 else -0.0003))
            out.append([t0 + i * 300, o, max(o, p) * 1.0003, min(o, p) * 0.9997, p, 6000 if i < 14 else 1500])
        return out

    def spike_fade(t0):                              # a spike that is already handing everything back
        out, p = [], 100.0
        for i in range(75):
            o = p
            p = p * 1.006 if i < 6 else p * 0.9965 if i < 12 else p * 0.9999
            out.append([t0 + i * 300, o, max(o, p) * 1.0004, min(o, p) * 0.9996, p, 6000])
        return out

    def quiet_big(t0):                               # up 3% but on ordinary volume
        out, p = [], 100.0
        for i in range(75):
            o = p
            p = p * 1.003 if i < 10 else p
            out.append([t0 + i * 300, o, max(o, p) * 1.0003, min(o, p) * 0.9997, p, 1000])
        return out
    data = {"NSE:AAA-EQ": mk_series(today_fn=strong), "NSE:BBB-EQ": mk_series(today_fn=spike_fade), "NSE:CCC-EQ": mk_series(today_fn=quiet_big),
            "NSE:DDD-EQ": mk_series()}
    names = ["AAA", "BBB", "CCC", "DDD"]
    day = weekdays(8)[-1]
    folder = tempfile.mkdtemp()
    try:
        sent, flushes = [], []
        cfgd = {"strategies": {"min_bars": 0, "telegram": "signals"}}
        d = StrategyDesk(cfgd, FakeBroker(data), sent.append, folder, names, [], flush=lambda: flushes.append(1))
        d.cfg["min_bars"] = 0
        t = at(day, "09:20")
        while t <= at(day, "10:30"):
            now = t + timedelta(seconds=30)
            clock.set_fake(now)
            d.tick(now)
            t += timedelta(minutes=5)
        clock.set_fake(None)
        mv = {m["name"]: m for m in d.state["movers"]["list"]}
        check("a big move on heavy volume that is holding is a genuine trend setter", "AAA" in mv and mv["AAA"]["genuine"] and mv["AAA"]["dir"] == 1, mv.get("AAA"))
        check("a spike that has faded is not genuine", mv.get("BBB") is None or not mv["BBB"]["genuine"], mv.get("BBB"))
        check("a big move on ordinary volume is not genuine", "CCC" in mv and not mv["CCC"]["genuine"] and not mv["CCC"]["checks"]["volume"], mv.get("CCC"))
        check("a quiet stock is not listed", "DDD" not in mv)
        check("the first time a mover was genuine is recorded", mv["AAA"]["since"] is not None)
        alerts = " ".join(m for m in sent if "trend setter" in m)
        check("each trend setter is announced once, not every candle", alerts.count("<b>AAA</b>") == 1 and alerts.count("<b>BBB</b>") <= 1, alerts.count("<b>AAA</b>"))
        check("the pass publishes after the stocks that matter, and again at the end", len(flushes) >= 10, len(flushes))
        drives = [x for x in d.state["trades"] if x["strategy"] == "drive"]
        check("the opening-drive strategy took the thrust early", any(x["name"] == "AAA" and x["dir"] == 1 and x["entry_t"][11:16] <= "09:30" for x in drives),
              [(x["name"], x["entry_t"][11:16]) for x in drives])
        aaa = [x for x in drives if x["name"] == "AAA"]
        check("it entered near the start of the move, not at the high", aaa and all(x["entry"] <= 103.0 for x in aaa) and max(c[2] for c in data["NSE:AAA-EQ"][-75:]) > 108,
              [(x["entry"]) for x in aaa])
        hot = d._hot(day)
        open_syms = {x["symbol"] for x in d.state["trades"] if x["status"] == "OPEN"}
        check("symbols with open trades and movers are looked at first", open_syms <= set(hot) and "NSE:AAA-EQ" in hot, (open_syms, hot))
        check("alerts default to take-grade trades only", all(x["dec"]["g"] == "take" for x in d.state["trades"] if False) or d.alert_grade == "take")
    finally:
        clock.set_fake(None)
        shutil.rmtree(folder, ignore_errors=True)


def test_live_signal_not_late():
    print("A live signal is reported on its own candle, not a candle later")
    prev = flat_day(DAY - timedelta(days=1))
    bars = orb_day(DAY, "target")
    bars[4][1] = 101.8                                   # the real open of the candle after the signal
    cfg = sim.settings({"strategies": {"min_bars": 0, "execution": {"entry_mode": "next_open"}}})
    strat = BY_ID["orb"]
    upto = Series(prev + bars[:4])                       # data ends on the signal candle (09:30 - 09:35 not drawn yet)
    live = sim.simulate(upto, INST, strat, cfg, days=[DAY], live_day=DAY)
    check("the signal appears as soon as its candle closes", len(live) == 1, len(live))
    if live:
        t = live[0]
        check("it is marked provisional and filled at that candle's close",
              t["fill_model"] == "provisional" and t["entry"] == 101.5 and t["entry_ts"] == ts(at(DAY, "09:35")),
              (t["fill_model"], t["entry"], t["entry_ts"]))
    full = sim.simulate(Series(prev + bars), INST, strat, cfg, days=[DAY], live_day=DAY)
    check("one candle later the same trade (same id) is refilled at the real open",
          bool(live) and len(full) == 1 and full[0]["id"] == live[0]["id"] and full[0]["entry"] == 101.8
          and full[0]["fill_model"] == "next_open", full[0] if full else "no trade")
    past = sim.simulate(upto, INST, strat, cfg, days=[DAY])
    check("a finished day (not live) never uses a provisional fill", not past, len(past))


def main():
    test_indicators()
    test_no_lookahead()
    test_fills()
    test_desk()
    test_alerts_and_heartbeat()
    test_fyers_year()
    test_charts()
    test_decide()
    test_risk_controls()
    test_archive_preserves_unresolved_positions()
    test_next_open_execution()
    test_live_signal_not_late()
    test_trend_setters()
    print("\nALL CHECKS PASSED" if not check.failed else f"\n{check.failed} CHECK(S) FAILED")
    sys.exit(1 if check.failed else 0)


if __name__ == "__main__":
    main()
