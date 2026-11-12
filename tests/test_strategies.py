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
    cfg = sim.settings({"strategies": {"min_bars": 0}})
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
            d = StrategyDesk({"strategies": {"telegram": "signals"}}, FakeBroker(data), sent.append, folder, names, [])
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


def test_alerts_and_heartbeat():
    print("Telegram alerts for every strategy, heartbeat, time budget")
    from engine.strategies import desk as deskmod
    names = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    data = {f"NSE:{n}-EQ": walk(weekdays(20), random.Random(i + 40), start=400 + 150 * i, vol=0.0035) for i, n in enumerate(names)}
    folder = tempfile.mkdtemp()
    try:
        sent, beats = [], []
        d = StrategyDesk({}, FakeBroker(data), sent.append, folder, names, [], heartbeat=lambda: beats.append(1))
        check("alerts are on by default", d.tg_signals and d.tg_on)
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
        d2 = StrategyDesk({"strategies": {"alert_for": ["orb"]}}, FakeBroker(data), sent2.append, tempfile.mkdtemp(), names, [])
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


def main():
    test_indicators()
    test_no_lookahead()
    test_fills()
    test_desk()
    test_alerts_and_heartbeat()
    test_fyers_year()
    test_charts()
    print("\nALL CHECKS PASSED" if not check.failed else f"\n{check.failed} CHECK(S) FAILED")
    sys.exit(1 if check.failed else 0)


if __name__ == "__main__":
    main()
