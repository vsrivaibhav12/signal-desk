"""Checks the results calendar, the quotes-based watchlist, the options scanner, the alert policy and how they join up
in the strategy desk. Run:  python -m tests.test_segments    (offline)"""
import json
import os
import random
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import clock, optscan, watch as watchmod  # noqa: E402
from engine.alerts import Alerts  # noqa: E402
from engine.clock import at, ts  # noqa: E402
from engine.results import Results, next_trading_day, prev_trading_day  # noqa: E402
from engine.tradingcal import TradingCalendar  # noqa: E402
from engine.schema import SCHEMA  # noqa: E402
from engine.strategies.desk import StrategyDesk, candle_is_fresh  # noqa: E402
from engine.strategies.indicators import Series  # noqa: E402
from tests.test_strategies import DAY, check, flat_day, walk, weekdays  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def ev(sym, d, purpose="Financial Results"):
    return {"symbol": sym, "company": sym + " Ltd", "purpose": purpose, "bm_desc": "", "date": d}


def test_results():
    print("Results calendar")
    folder = tempfile.mkdtemp()
    raw = [ev("HAL", "12-Nov-2026"), ev("ABB", "13-Nov-2026"), ev("GRASIM", "13-Nov-2026"), ev("ITC", "11-Nov-2026"),
           ev("ZZZ", "12-Nov-2026"), ev("TCS", "12-Nov-2026", "Dividend"), ev("HAL", "12-Nov-2026"),
           ev("SBIN", "14-Nov-2026", "Financial Results/Dividend")]
    calls = []
    r = Results(folder, ["HAL", "ABB", "GRASIM", "ITC", "TCS", "SBIN"], fetch=lambda a, b: (calls.append((a, b)), raw)[1],
                extra=[{"symbol": "TCS", "date": "2026-11-12"}])
    now = datetime(2026, 11, 12, 9, 0)
    check("it reads the calendar the first time", r.refresh(now) and len(calls) == 1)
    check("and not again within three hours", not r.refresh(now + timedelta(hours=1)) and len(calls) == 1)
    check("it asks again once the copy is old", r.refresh(now + timedelta(hours=4)) and len(calls) == 2)
    syms = {(e["symbol"], e["date"]) for e in r.state["events"]}
    check("only Nifty 200 results count (not other companies, not dividends alone)", ("ZZZ", "2026-11-12") not in syms
          and not any(s == "TCS" and d == "2026-11-12" and e["source"] == "NSE" for (s, d), e in
                      [((x["symbol"], x["date"]), x) for x in r.state["events"]]), syms)
    check("a company listed twice appears once", len([e for e in r.state["events"] if e["symbol"] == "HAL"]) == 1)
    check("a name added by hand in config is kept", ("TCS", "2026-11-12") in syms)
    g = r.groups(date(2026, 11, 12))
    check("today, just out and next session are separated",
          {e["symbol"] for e in g["today"]} == {"HAL", "TCS"} and {e["symbol"] for e in g["last"]} == {"ITC"}
          and {e["symbol"] for e in g["next"]} == {"ABB", "GRASIM"}, g)
    w = r.watch(date(2026, 11, 12))
    check("watch order: reaction first, then today, then next", [x["kind"] for x in w][:1] == ["last"]
          and [x["kind"] for x in w] == sorted([x["kind"] for x in w], key=["last", "today", "next"].index), w)
    check("a label is given to a share with results", r.label("HAL", date(2026, 11, 12)) == "Results today"
          and r.label("ITC", date(2026, 11, 12)) == "Results just out" and r.label("NOPE", date(2026, 11, 12)) is None)
    # a Friday looks across the weekend to Monday; a Monday looks back across it
    check("trading days skip weekends", prev_trading_day(date(2026, 11, 16)) == date(2026, 11, 13)
          and next_trading_day(date(2026, 11, 13)) == date(2026, 11, 16))
    g2 = r.groups(date(2026, 11, 13))
    check("on Friday, Saturday results are due before the next session", "SBIN" in {e["symbol"] for e in g2["next"]}, g2["next"])
    # NSE failing keeps the last good copy and says so
    bad = Results(folder, ["HAL"], fetch=lambda a, b: (_ for _ in ()).throw(RuntimeError("blocked")))
    bad.refresh(now + timedelta(hours=9))
    check("a failed refresh keeps the saved events and reports the error", bad.state["events"] and bad.state["ok"] is False
          and "blocked" in bad.state["error"], bad.state.get("error"))
    attempts = []
    b2 = Results(tempfile.mkdtemp(), ["HAL"], fetch=lambda a, c: (attempts.append(1), (_ for _ in ()).throw(RuntimeError("x")))[1],
                 log=lambda m: None)
    t1 = datetime(2026, 11, 12, 9, 0)
    b2.refresh(t1)
    b2.refresh(t1 + timedelta(minutes=1)); b2.refresh(t1 + timedelta(minutes=9))
    check("a failed read is not retried every minute", len(attempts) == 1, len(attempts))
    b2.refresh(t1 + timedelta(minutes=11))
    check("it tries again after the back-off", len(attempts) == 2, len(attempts))
    b2.refresh(t1 + timedelta(minutes=11 + 19))
    check("and the wait doubles each time (20 minutes now)", len(attempts) == 2, len(attempts))
    b2.refresh(t1 + timedelta(minutes=11 + 21))
    check("so the third try comes after 20 minutes", len(attempts) == 3, len(attempts))
    check("the last good copy is kept apart from the last attempt", b2.state["fetched_at"] is None and b2.state["checked_at"])
    ok = Results(tempfile.mkdtemp(), ["HAL"], fetch=lambda a, c: [ev("HAL", "12-Nov-2026")], log=lambda m: None)
    started = ok.refresh_async(t1)
    ok._thread.join(5)
    check("the refresh can run in the background", started and ok.state["events"] and ok.state["ok"], ok.state)
    check("and is not started again while the copy is fresh", ok.refresh_async(t1 + timedelta(minutes=5)) is False)
    check("the saved copy is read back after a restart", Results(folder, ["HAL"]).state["events"] == bad.state["events"])
    pub = r.public(date(2026, 11, 12))
    check("the dashboard view has the groups and the age", {"today", "last", "next", "later", "fetched_at", "ok"} <= set(pub))
    # the real NSE reply shape, saved from a live call
    real = json.load(open(os.path.join(HERE, "fixtures", "event_calendar.json"), encoding="utf-8"))
    names = [x.split("|")[0] for x in open(os.path.join(os.path.dirname(HERE), "engine", "nifty200.txt"), encoding="utf-8")
             if x.strip() and not x.startswith("#")]
    rr = Results(tempfile.mkdtemp(), names, fetch=lambda a, b: real)
    rr.refresh(now)
    check("the real NSE reply is understood", len(rr.state["events"]) >= 1 and rr.state["ok"], rr.state["events"][:2])
    shutil.rmtree(folder, ignore_errors=True)


class QBroker:
    """History for 'D' and quotes, like a broker."""

    def __init__(self, daily, quotes):
        self.daily, self.q, self.calls, self.cool = daily, quotes, [], 0

    def cooling(self):
        return self.cool

    def history(self, sym, res, d0, d1):
        self.calls.append((sym, res))
        return self.daily.get(sym, [])

    def quotes(self, syms):
        return {s: self.q[s] for s in syms if s in self.q}


def test_market_data_freshness():
    print("Market data freshness")
    now = 1_800_000_000
    check("a recent completed candle is accepted", candle_is_fresh(now - 120, now))
    check("a candle older than the configured freshness window is rejected", not candle_is_fresh(now - 601, now, 600))
    check("a candle timestamp too far in the future is rejected", not candle_is_fresh(now + 11, now, 600, 10))
    check("small clock skew is tolerated", candle_is_fresh(now + 5, now, 600, 10))
    check("invalid timestamps fail closed", not candle_is_fresh(None, now))
    day = DAY
    old_or_future_series = Series(flat_day(day - timedelta(days=1), price=100.0) + index_day(day, [100.4,100.6,100.2,100.8,100.5,100.7,101.4,102.0,102.6,103.2,103.6,104.0]))
    desk = StrategyDesk.__new__(StrategyDesk)
    desk.oscfg = optscan.settings({})
    desk.markets = [MKT]
    desk.series = {MKT["spot"]: old_or_future_series}
    desk.watch = None
    desk.results = None
    desk.state = {}
    desk._stale_symbols = []
    h = desk._health(at(day, "10:14"), day)
    check("a future-dated index candle puts the data-health decision on WATCH", h["decision"] == "WATCH" and "Nifty 50 candles" in h["blocking"], h)


def test_calendar():
    print("Trading calendar")
    folder = tempfile.mkdtemp()
    cal = TradingCalendar(folder, fetch=lambda: [date(2026, 11, 24)], log=lambda m: None)
    check("without a holiday list only weekends are known", not cal.known and cal.next(date(2026, 11, 23)) == date(2026, 11, 24))
    cal.refresh(datetime(2026, 11, 12, 9, 0))
    check("a published holiday is not a trading day", cal.known and not cal.is_trading_day(date(2026, 11, 24)))
    check("the next session skips the holiday", cal.next(date(2026, 11, 23)) == date(2026, 11, 25))
    check("the previous session skips it too", cal.prev(date(2026, 11, 25)) == date(2026, 11, 23))
    check("a long weekend is stepped over", cal.next(date(2026, 11, 20)) == date(2026, 11, 23))
    check("the list is remembered after a restart", TradingCalendar(folder).is_trading_day(date(2026, 11, 24)) is False)
    check("a holiday NSE missed can be added by hand", TradingCalendar(None, fetch=lambda: [], extra=["2026-11-26"]).is_trading_day(date(2026, 11, 26)) is False)
    n = []
    bad = TradingCalendar(tempfile.mkdtemp(), fetch=lambda: (n.append(1), (_ for _ in ()).throw(RuntimeError("blocked")))[1], log=lambda m: None)
    t = datetime(2026, 11, 12, 9, 0)
    bad.refresh(t); bad.refresh(t + timedelta(minutes=5))
    check("a failed holiday read backs off too", len(n) == 1, len(n))
    # results use it: with 24 Nov a holiday, results due on the 25th are "before the next session" on Monday 23rd
    raw = [ev("HAL", "25-Nov-2026"), ev("ABB", "24-Nov-2026")]
    with_cal = Results(tempfile.mkdtemp(), ["HAL", "ABB"], fetch=lambda a, b: raw, cal=cal, log=lambda m: None)
    without = Results(tempfile.mkdtemp(), ["HAL", "ABB"], fetch=lambda a, b: raw, log=lambda m: None)
    now = datetime(2026, 11, 23, 9, 0)
    with_cal.refresh(now); without.refresh(now)
    nxt = lambda r: {e["symbol"] for e in r.groups(date(2026, 11, 23))["next"]}
    check("with the holiday known, the 25th is the next session's result day", nxt(with_cal) == {"HAL", "ABB"}, nxt(with_cal))
    check("without it, the 25th would be missed", nxt(without) == {"ABB"}, nxt(without))


def test_watch():
    print("Watchlist and load control")
    f = watchmod.expected_fraction
    t0 = datetime(2026, 11, 12, 9, 15)
    vals = [f(t0 + timedelta(minutes=m)) for m in range(0, 380, 15)]
    check("the volume profile only ever rises, from 0 to 1", vals == sorted(vals) and vals[0] == 0 and vals[-1] == 1.0, vals[:4])
    check("about a tenth of the day's volume is traded in the first 15 minutes",
          0.08 < f(t0 + timedelta(minutes=15)) < 0.15)
    folder = tempfile.mkdtemp()
    day = date(2026, 11, 12)
    daily = {f"NSE:{n}-EQ": [[i, 1, 1, 1, 1, 1_000_000] for i in range(12)] for n in ("AAA", "BBB", "CCC", "DDD")}
    daily["NSE:DDD-EQ"] = []                                    # no history at all for one share
    quotes = {"NSE:AAA-EQ": {"lp": 101, "prev_close": 100, "volume": 400_000},     # 1% up, volume well above normal
              "NSE:BBB-EQ": {"lp": 103, "prev_close": 100, "volume": 20_000},      # 3% up on thin volume
              "NSE:CCC-EQ": {"lp": 100.1, "prev_close": 100, "volume": 50_000},    # quiet
              "NSE:DDD-EQ": {"lp": 100.2, "prev_close": 100, "volume": 5_000_000}}
    br = QBroker(daily, quotes)
    w = watchmod.Watch(folder, list(quotes), {"watch": {"baseline_per_pass": 2}}, log=lambda m: None)
    now = datetime(2026, 11, 12, 10, 15)                        # 60 minutes in: about 26% of a normal day
    board = w.scan(br, now)
    hist_calls = [c for c in br.calls if c[1] == "D"]
    check("only a few daily histories are read per pass", len(hist_calls) == 2, hist_calls)
    board = w.scan(br, now)
    check("the rest are read on the next pass", len(w.base.v) == 3, len(w.base.v))
    names = [r["name"] for r in board]
    check("a share on heavy volume is promoted", "AAA" in names, names)
    check("a big move is promoted even on thin volume", "BBB" in names, names)
    check("a quiet share is left alone", "CCC" not in names, names)
    check("a share with no baseline is judged on its move alone, not guessed", "DDD" not in names, names)
    a = next(r for r in board if r["name"] == "AAA")
    check("the volume ratio is against the time of day", 1.4 < a["volx"] < 1.8, a)
    br.calls.clear()
    br.cool = 10
    w2 = watchmod.Watch(tempfile.mkdtemp(), list(quotes), None, log=lambda m: None)
    w2.scan(br, now)
    check("no daily reads while the broker is cooling", not [c for c in br.calls if c[1] == "D"], br.calls)
    w3 = watchmod.Watch(folder, list(quotes), None, log=lambda m: None)
    check("the baseline survives a restart", len(w3.base.v) == 3)
    # a few shares that always fail must not starve the rest
    many = [f"NSE:S{i:02d}-EQ" for i in range(41)]
    br2 = QBroker({}, {})
    wq = watchmod.Watch(tempfile.mkdtemp(), many, {"watch": {"baseline_per_pass": 40}}, log=lambda m: None)
    wq.base.fill(br2, many, now, wq.cfg, lambda m: None)
    br2.calls.clear()
    wq.base.fill(br2, many, now + timedelta(minutes=1), wq.cfg, lambda m: None)
    check("the share after the first 40 is tried on the next pass", [("NSE:S40-EQ", "D")] == br2.calls, br2.calls)
    br2.calls.clear()
    wq.base.fill(br2, many, now + timedelta(minutes=40), wq.cfg, lambda m: None)
    check("failed shares come back after a back-off, not at once", len(br2.calls) == 40, len(br2.calls))
    br2.calls.clear()
    wq.base.fill(br2, many, now + timedelta(minutes=41), wq.cfg, lambda m: None)
    check("and the one that failed again waits longer", len(br2.calls) == 1 or len(br2.calls) == 0, br2.calls)
    check("the back-off is saved", watchmod.Baseline(wq.base.path.rsplit(os.sep, 1)[0]).tries == wq.base.tries and wq.base.tries)

    # A quote batch is only useful when each individual record is recent.
    qt = now.timestamp()
    freshness_quotes = {
        "NSE:FRESH-EQ": {"lp": 103, "prev_close": 100, "volume": 1, "ts": qt - 120, "ts_source": "exchange"},
        "NSE:STALE-EQ": {"lp": 104, "prev_close": 100, "volume": 1, "ts": qt - 720, "ts_source": "exchange"},
        "NSE:FUTURE-EQ": {"lp": 105, "prev_close": 100, "volume": 1, "ts": qt + 45, "ts_source": "exchange"},
        "NSE:FALLBACK-EQ": {"lp": 106, "prev_close": 100, "volume": 1, "received_ts": qt - 15},
        "NSE:NAN-TIME-EQ": {"lp": 107, "prev_close": 100, "volume": 1, "ts": float("nan"), "ts_source": "exchange"},
        "NSE:NAN-PRICE-EQ": {"lp": float("nan"), "prev_close": 100, "volume": 1, "ts": qt - 15, "ts_source": "exchange"},
        "NSE:BAD-EXCHANGE-TS-EQ": {"lp": 108, "prev_close": 100, "volume": 1, "ts": None, "received_ts": qt - 5,
                                   "ts_invalid": True, "ts_source": "invalid"},
    }
    fresh_symbols = list(freshness_quotes)
    wfresh = watchmod.Watch(tempfile.mkdtemp(), fresh_symbols,
                            {"watch": {"max_quote_age_s": 600, "max_future_skew_s": 10}}, log=lambda m: None)
    fresh_board = wfresh.scan(QBroker({}, freshness_quotes), now)
    qmeta = wfresh.public()["quality"]
    check("individual stale and future quotes are rejected", {"stale": qmeta["stale"], "future": qmeta["future"], "fresh": qmeta["fresh"]} == {"stale": 1, "future": 1, "fresh": 2}, qmeta)
    check("non-finite and explicitly invalid exchange timestamps and prices are rejected", qmeta["invalid"] == 3, qmeta)
    check("only usable quotes count toward timestamp-source coverage", qmeta["exchange_timestamps"] == 1 and qmeta["response_timestamps"] == 1, qmeta)
    check("only fresh quotes can promote a share", {r["name"] for r in fresh_board} == {"FRESH", "FALLBACK"}, fresh_board)
    check("the promoted candidate carries its quote age and timestamp source", all("quote_age_s" in r and "quote_timestamp_source" in r for r in fresh_board), fresh_board)
    shutil.rmtree(folder, ignore_errors=True)


def index_day(day, closes, hi_bump=0.3):
    """A day of 5-minute index candles from the given closes, starting 09:15, volume constant."""
    t0 = ts(at(day, "09:15"))
    out, prev = [], closes[0]
    for k, c in enumerate(closes):
        o = prev
        out.append([t0 + 300 * k, o, max(o, c) + hi_bump, min(o, c) - hi_bump, c, 1000])
        prev = c
    return out


def test_candle_normalization():
    print("Candle normalization")
    t0 = ts(at(DAY, "09:15"))
    rows = [
        [t0, 100, 103, 99, 101, 1000],
        [t0 + 300, 100, 98, 99, 101, 1000],  # impossible OHLC: high below open/close
        [float("nan"), 100, 103, 99, 101, 1000],  # timestamp cannot be attributed to a day
        [t0 + 600, 101, 104, 100, 102, float("nan")],  # price survives; volume becomes zero
        [t0, 101, 104, 100, 102, 500],  # duplicate timestamp: last returned bar wins
    ]
    series = Series(rows)
    check("invalid OHLC and invalid timestamp bars are excluded", series.n == 2 and series.invalid_candles == 2,
          {"n": series.n, "invalid": series.invalid_candles})
    check("duplicate candle timestamps keep the last returned row", series.c[0] == 102 and series.v[0] == 500, (series.c, series.v))
    check("bad volume cannot inflate unusual-volume calculations", series.v[1] == 0 and series.invalid_volume_candles == 1, series.v)
    check("known-day corruption is attributed and unknown timestamps remain visible",
          DAY in series.invalid_candle_days and series.invalid_unknown_candles == 1,
          (series.invalid_candle_days, series.invalid_unknown_candles))
    empty = Series([])
    check("an empty history creates a valid empty series", empty.n == 0 and empty.invalid_candles == 0)


MKT = {"id": "NIFTY", "kind": "index", "name": "Nifty 50", "spot": "NSE:NIFTY50-INDEX", "root": "NIFTY", "master": "NSE_FO", "range": ["09:15", "09:45"],
       "entry": ["09:45", "11:30"], "premium_stop_pct": 25, "min_days_to_expiry": 1, "near_expiry": "roll"}


class StubMaster:
    def choose_expiry(self, name, root, day, min_days, near):
        return (ts(at(day, "15:30")), None) if root != "NOEXP" else (None, "no contracts")

    def pick_option(self, name, root, expiry, price, cp, choice):
        return {"desc": f"{root}{cp}", "symbol": f"nse_fo|{cp}", "lot": 75, "strike": round(price / 50) * 50}


class OBroker:
    """An option quote the way Kotak gives it: last, bid, ask, size at the ask, and when it was updated."""

    def __init__(self, ask, now, bid=None, ts=None, ask_qty=1000, bid_qty=500, oi=120000, none=False):
        self.q = None if none else {"lp": ask, "bid": bid if bid is not None else round(ask * 0.98, 2), "ask": ask,
                                    "ts": ts if ts is not None else int(now.timestamp()) - 20, "ask_qty": ask_qty, "bid_qty": bid_qty,
                                    "oi": oi, "prev_close": None, "volume": None}

    def quotes(self, syms):
        return {s: dict(self.q) for s in syms} if self.q else {}


def test_optscan():
    print("Options scanner")
    day = DAY
    cfg = optscan.settings({})
    prev = flat_day(day - timedelta(days=1), price=100.0)
    # range 09:15-09:45 around 100-101, then a climb that breaks out
    up = [100.4, 100.6, 100.2, 100.8, 100.5, 100.7, 101.4, 102.0, 102.6, 103.2, 103.6, 104.0]
    S = Series(prev + index_day(day, up))
    now = at(day, "10:16")                                   # one minute after the last candle finished
    nf = optscan.nifty_vwap_side(S, day, now)
    check("Nifty's VWAP side is read with the time it is from", nf["side"] == 1 and nf["as_of"] == S.t[-1] + 300, nf)
    r = optscan.read_index(S, day, MKT, now, nifty=nf)
    check("it sees the call-side breakout and scores it", r["state"] == "Call side broke out" and r["side"] == "CE" and r["score"] >= 4, r)
    check("it reports the range and the distance to each side", r["range_high"] > r["range_low"] and "up" in r["to_break"])
    before_last_close = at(day, "10:14")
    future_read = optscan.read_index(S, day, MKT, before_last_close, nifty=nf)
    check("an index candle timestamp in the future is rejected", future_read["state"].startswith("Data timestamp is in the future"), future_read)
    check("a future-dated Nifty reading cannot confirm another index", optscan.nifty_vwap_side(S, day, before_last_close) is None)

    def idea(read, market=MKT, at_=now, **kw):
        optscan.suggest(read, market, StubMaster(), OBroker(kw.pop("ask", 120.0), at_, **kw), day, cfg, at_)
        return read

    r = idea(r)
    i = r["idea"]
    check("the contract is priced at the ask, with stop, target and risk per lot", i["premium"] == 120.0 and i["stop"] == 90.0
          and i["target"] > i["premium"] and i["risk_per_lot"] == 2250 and i["lot"] == 75 and i["bid"] < i["ask"], i)
    check("the quote's age is recorded", i["quote_age_s"] == 20, i["quote_age_s"])
    check("the spread is part of the cost", 0.1 < i["cost_ratio"] < 0.25, i["cost_ratio"])
    g, why = optscan.grade(r, MKT, now, cfg, [])
    check("a clean setup in the window is worth taking", g == "take" and not why, (g, why))
    g, why = optscan.grade(r, MKT, at(day, "13:00"), cfg, [])
    check("the same setup after the entry window is only watched", g == "watch" and any("entry window" in w for w in why), why)
    grade = lambda read, market=MKT: optscan.grade(read, market, now, cfg, [])
    # the contract's price must be usable
    for label, kw, word in (("stale", {"ts": int(now.timestamp()) - 400}, "old"), ("without a timestamp", {"ts": 0}, "timestamp"),
                            ("in the future", {"ts": int(now.timestamp()) + 3600}, "future"),
                            ("with a wide spread", {"bid": 100.0}, "spread"), ("with too little offered", {"ask_qty": 20}, "offered"),
                            ("without ask depth", {"ask_qty": None}, "ask depth"),
                            ("without bid depth", {"bid_qty": None}, "bid depth"),
                            ("with a non-finite quote age", {"ts": float("nan")}, "timestamp"),
                            ("with non-finite ask depth", {"ask_qty": float("nan")}, "ask depth"),
                            ("with non-finite bid depth", {"bid_qty": float("inf")}, "bid depth"),
                            ("with non-finite open interest", {"oi": float("nan")}, "open interest"),
                            ("without open interest", {"oi": None}, "open interest"),
                            ("with low open interest", {"oi": 10}, "below the minimum")):
        x = idea(optscan.read_index(S, day, MKT, now, nifty=nf), **kw)
        g, why = grade(x)
        check(f"a contract price {label} is only watched", g == "watch" and any(word in w for w in why), why)
    x = idea(optscan.read_index(S, day, MKT, now, nifty=nf), none=True)
    check("no price at all is never treated as a pass", grade(x)[0] == "watch", grade(x))
    x = idea(optscan.read_index(S, day, MKT, now, nifty=nf), ask=2.0)
    g, why = grade(x)
    check("costs that swamp the risk make it watch only", g == "watch" and any("costs" in w for w in why), (g, why))
    g, _ = optscan.grade(r, MKT, now, cfg, ["HDFCBANK"])
    check("heavyweight results add a warning", r["warnings"] == ["HDFCBANK reports results"], r.get("warnings"))
    # the other indices need Nifty: PASS, FAIL or UNKNOWN, and only PASS can be a take
    bn = {**MKT, "id": "BANKNIFTY", "name": "Bank Nifty"}
    r_pass = idea(optscan.read_index(S, day, bn, now, nifty=nf), bn)
    check("Bank Nifty with Nifty agreeing can be taken", r_pass["market_agrees"] is True and grade(r_pass, bn)[0] == "take", grade(r_pass, bn))
    r_fail = idea(optscan.read_index(S, day, bn, now, nifty={"side": -1, "as_of": nf["as_of"]}), bn)
    g, why = grade(r_fail, bn)
    check("Nifty on the other side of VWAP is a fail", r_fail["market_agrees"] is False and g == "watch" and any("other side" in w for w in why), why)
    r_none = idea(optscan.read_index(S, day, bn, now, nifty=None), bn)
    g, why = grade(r_none, bn)
    check("MISSING Nifty data is unknown, and unknown is never a take", r_none["market_agrees"] is None and g == "watch"
          and any("missing" in w for w in why), (r_none["market_agrees"], g, why))
    stale_nifty = optscan.nifty_vwap_side(S, day, now + timedelta(minutes=30))
    check("a Nifty reading from long ago is dropped, not used", stale_nifty is None, stale_nifty)
    skew = optscan.read_index(S, day, bn, now, nifty={"side": 1, "as_of": nf["as_of"] - 1800})
    check("a Nifty reading from a different time than this index is unknown", skew["market_agrees"] is None, skew["market_agrees"])
    check("Nifty itself does not need Nifty", optscan.read_index(S, day, MKT, now, nifty=None)["market_agrees"] is True)
    # the index's own candles must be recent
    old = optscan.read_index(S, day, MKT, now + timedelta(minutes=30), nifty=nf)
    g, why = optscan.grade(old, MKT, now + timedelta(minutes=30), cfg, [])
    check("old index candles give an out-of-date state and watch", old["state"] == "Data is out of date" and g == "watch", (old["state"], g))
    early = optscan.read_index(Series(prev + index_day(day, up[:3])), day, MKT, at(day, "09:31"), nifty=nf)
    check("while the opening range forms it waits", early["state"].startswith("Opening range forms")
          and optscan.grade(early, MKT, at(day, "09:31"), cfg, [])[0] == "wait", early["state"])
    inside = optscan.read_index(Series(prev + index_day(day, up[:6] + [100.6, 100.5])), day, MKT, at(day, "10:01"), nifty=nf)
    check("inside the range there is no breakout state", inside["state"] == "Inside the opening range", inside["state"])
    gi, wi = optscan.grade(inside, MKT, at(day, "10:01"), cfg, [])
    check("a high score inside the range is still not a take", gi == "watch" and any("inside the opening range" in w for w in wi), (inside["score"], gi, wi))
    down = [99.6, 99.4, 99.8, 99.2, 99.5, 99.3, 98.6, 98.0, 97.4, 96.8, 96.4, 96.0]
    Sd = Series(prev + index_day(day, down))
    pr = optscan.read_index(Sd, day, MKT, now, nifty=optscan.nifty_vwap_side(Sd, day, now))
    check("a breakdown scores a put", pr["side"] == "PE" and pr["state"] == "Put side broke out" and pr["score"] >= 4, pr)


def test_alerts():
    print("Alert policy")
    sent = []
    a = Alerts(sent.append, {"alerts": {"movers": True}})
    a.send("take", "trades"); a.send("chatter", "info"); a.send("trend", "movers"); a.send("news digest", "news")
    check("only wanted kinds go out; movers can be switched on", sent == ["take", "trend"], sent)
    sent.clear()
    a.send("prices down", "faults"); a.send("prices down", "faults")
    check("the same fault is sent once", sent == ["prices down"], sent)
    got = []
    s = Alerts(lambda t: got.append(t), None).sender()
    s("x", "options"); s("y", "news")
    check("a sender that names the kind follows the policy", got == ["x"] and s.takes_kind, got)
    check("nothing chatty is on by default", not any(Alerts(print).on[k] for k in ("info", "news", "movers")))


class DeskBroker:
    """Candles for the desk, daily candles and quotes for the watchlist."""

    def __init__(self, data):
        self.data, self.calls = data, []

    def cooling(self):
        return 0

    def stats_snapshot(self):
        return {"since": "x", "total": len(self.calls), "by_method": {}, "history_rate": 2.5, "cooling": 0}

    def history(self, sym, res, d0, d1):
        now = ts(clock.now())
        if res == "D":
            by = {}
            for c in self.data[sym]:
                by.setdefault(clock.from_ts(c[0]).date(), []).append(c)
            return [[ts(at(d, "09:15")), v[0][1], 0, 0, v[-1][4], sum(x[5] for x in v)] for d, v in sorted(by.items()) if d0 <= d <= d1]
        self.calls.append(sym)
        return [c for c in self.data[sym] if d0 <= clock.from_ts(c[0]).date() <= d1 and c[0] <= now]

    def quotes(self, syms):
        now = ts(clock.now())
        out = {}
        for s in syms:
            if s not in self.data:
                continue
            today = [c for c in self.data[s] if clock.from_ts(c[0]).date() == DAY and c[0] <= now]
            before = [c for c in self.data[s] if clock.from_ts(c[0]).date() < DAY]
            if today and before:
                busy = s in ("NSE:AAA-EQ", "NSE:EEE-EQ")                     # two shares trade on heavy volume, the rest are quiet
                out[s] = {"lp": today[-1][4], "prev_close": before[-1][4], "volume": sum(c[5] for c in today) * (3 if busy else 0.3)}
        return out


class StubResults:
    def __init__(self, sym):
        self.sym = sym
        self.state = {"ok": True}

    def watch(self, today):
        return [{"symbol": self.sym, "company": self.sym, "why": "Results today", "kind": "today", "date": today.isoformat()}]

    def heavyweights(self, today):
        return []

    def public(self, today):
        return {"today": [{"symbol": self.sym}], "last": [], "next": [], "later": [], "ok": True, "fetched_at": None}


def test_desk_joins_up():
    print("Desk: results, watchlist and options scan work together")
    names = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    days = weekdays(20)
    data = {f"NSE:{n}-EQ": walk(days, random.Random(i + 40), start=400 + 150 * i, vol=0.0035) for i, n in enumerate(names)}
    idx = walk(days, random.Random(99), start=24000, vol=0.002)
    data["NSE:NIFTY50-INDEX"] = idx
    folder = tempfile.mkdtemp()
    sent = []
    try:
        al = Alerts(sent.append, None)
        wt = watchmod.Watch(folder, [f"NSE:{n}-EQ" for n in names], {"watch": {"min_move_pct": 50}}, log=lambda m: None)
        mkt = {**MKT, "lot": 75}
        br = DeskBroker(data)
        d = StrategyDesk({"strategies": {"portfolio": {"enabled": False}}}, br, sent.append, folder, names, [mkt],
                         results=StubResults("BBB"), watch=wt, master=StubMaster(), alerts=al, catalysts=lambda: ["CCC"])
        t = at(DAY, "09:20")
        while t <= at(DAY, "11:00"):
            now = t + timedelta(seconds=30)
            clock.set_fake(now)
            d.tick(now)
            t += timedelta(minutes=5)
        clock.set_fake(None)
        wl = {w["name"]: w["why"] for w in d.state["watchlist"]}
        check("the results share is on the watchlist, with the reason", wl.get("BBB") == "Results today", wl)
        check("a share with a news catalyst is on it too", "CCC" in wl and wl["CCC"] == "In the news", wl)
        check("the index is always on it", "Nifty 50" in wl, wl)
        check("quiet shares are not on the watchlist", "DDD" not in wl and "FFF" not in wl and "AAA" in wl and "EEE" in wl, wl)
        check("and are never asked for candles", "NSE:DDD-EQ" not in br.calls and "NSE:FFF-EQ" not in br.calls, sorted(set(br.calls)))
        check("the results view is saved for the dashboard", d.state.get("results", {}).get("today") == [{"symbol": "BBB"}])
        check("the scan summary is saved", "scan" in d.state and d.state["scan"]["of"] == len(names))
        check("the options scan is saved", d.state.get("options_scan", {}).get("items") and d.state["options_scan"]["items"][0]["id"] == "NIFTY",
              d.state.get("options_scan"))
        tagged = [t for t in d.state["trades"] if (t.get("dec") or {}).get("ip")]
        check("trades on in-play shares carry why they are in play", all(t["dec"]["ip"] for t in tagged))
        check("alerts are only for things to act on", not any("restarted" in m or "Watching" in m for m in sent))
        check("the broker requests of each candle are counted", d.state["status"].get("cycle_calls", -1) >= 0 and d.state.get("api", {}).get("total", -1) >= 0,
              (d.state["status"].get("cycle_calls"), d.state.get("api")))
        h = d.state.get("health", {})
        check("data health is published with a decision", h.get("decision") in ("OK", "WATCH") and h.get("items"), h)
        check("each item says what it is and how fresh", all({"name", "status", "detail"} <= set(x) for x in h.get("items", [])))
    finally:
        clock.set_fake(None)
        shutil.rmtree(folder, ignore_errors=True)


def test_option_quote_tape():
    print("Prospective options quote tape")
    folder = tempfile.mkdtemp()
    desk = StrategyDesk.__new__(StrategyDesk)
    desk.state = {}
    desk.option_tape_path = os.path.join(folder, "options_tape.jsonl")
    now = at(DAY, "10:15")
    items = [
        {"id": "NIFTY", "spot": 24500, "side": "CE", "score": 5, "grade": "take", "why_not": [],
         "idea": {"symbol": "nse_fo|123", "contract": "NIFTYCE", "strike": 24500, "expiry": DAY.isoformat(), "lot": 75,
                  "bid": 100, "ask": 101, "bid_qty": 150, "ask_qty": 150, "oi": 50000, "quote_ts": int(now.timestamp()),
                  "quote_age_s": 0, "premium": 101, "stop": 75.75, "target": 151.5, "risk_per_lot": 1894, "cost_ratio": .12}},
        {"id": "BANKNIFTY", "spot": 52000, "side": "PE", "score": 3, "grade": "watch", "why_not": ["no quote"],
         "idea": {"symbol": "nse_fo|456", "bid": None, "ask": None}},
    ]
    desk._record_option_quotes(now, items)
    records = [json.loads(x) for x in open(desk.option_tape_path, encoding="utf-8")]
    check("only complete bid/ask observations are written", len(records) == 1, records)
    check("the prospective tape retains quote depth and decision context", records[0]["bid_qty"] == 150 and records[0]["ask_qty"] == 150
          and records[0]["open_interest"] == 50000 and records[0]["grade"] == "take", records[0])
    check("the tape is stamped as a prospective observation", records[0]["observed_at"] == now.isoformat(timespec="seconds") and records[0]["schema"] == 1, records[0])
    check("successful tape writes publish their capture status", desk.state["options_tape_status"]["ok"] is True)
    # A filesystem problem must not interrupt the scan or trade-decision cycle.
    broken = StrategyDesk.__new__(StrategyDesk)
    broken.option_tape_path = folder  # an existing directory cannot be opened as a JSONL file
    broken.state = {}
    broken._record_option_quotes(now, items)
    check("tape write failures are surfaced without crashing the strategy desk",
          broken.state.get("options_tape_status", {}).get("ok") is False and "could not write" in broken.state["options_tape_status"]["detail"],
          broken.state)
    shutil.rmtree(folder, ignore_errors=True)


def test_clean_start():
    print("A file from an older layout is replaced, not read")
    folder = tempfile.mkdtemp()
    try:
        json.dump({"trades": [{"id": "old", "date": "2026-11-01", "status": "CLOSED"}], "archive": {"x": 1}},
                  open(os.path.join(folder, "strategies.json"), "w"))
        json.dump({"old": 1}, open(os.path.join(folder, "chartlive_orb.json"), "w"))
        d = StrategyDesk({}, DeskBroker({}), print, folder, ["AAA"], [])
        check("old trades are gone", d.state["trades"] == [] and d.state["schema"] == SCHEMA)
        check("old charts are gone", not os.path.exists(os.path.join(folder, "chartlive_orb.json")))
        d.save()
        d2 = StrategyDesk({}, DeskBroker({}), print, folder, ["AAA"], [])
        check("a file in the current layout is kept", d2.state["schema"] == SCHEMA)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_reset():
    print("Reset data")
    from engine import reset
    folder = tempfile.mkdtemp()
    try:
        for name in ("feed.json", "strategies.json", "strategies_backtest.json", "backtest.json", "chartlive_orb.json", "chartbt_orb.json",
                     "secure.bin", "results.json", "volbase.json"):
            open(os.path.join(folder, name), "w").write('{"old": 1}')
        reset.reset(folder)
        left = set(os.listdir(folder))
        check("every log, chart and backtest result is gone", not ({"strategies.json", "strategies_backtest.json", "backtest.json",
              "chartlive_orb.json", "chartbt_orb.json"} & left), left)
        check("the Telegram link, calendar and volume baseline are kept", {"secure.bin", "results.json", "volbase.json"} <= left)
        feed = json.load(open(os.path.join(folder, "feed.json")))
        check("the feed is emptied and stamped with the current layout", feed == {"status": {}, "trades": [], "schema": SCHEMA}, feed)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def main():
    test_results()
    test_calendar()
    test_candle_normalization()
    test_watch()
    test_optscan()
    test_alerts()
    test_desk_joins_up()
    test_clean_start()
    test_reset()
    print("\nALL SEGMENT CHECKS PASSED" if not check.failed else f"\n{check.failed} CHECK(S) FAILED")
    sys.exit(1 if check.failed else 0)


if __name__ == "__main__":
    main()
