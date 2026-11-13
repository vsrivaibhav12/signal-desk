"""Builds the sample data the dashboard shows with ?demo, using the engine's own code, and checks it against the contract.

    python -m tests.make_web_fixtures

It writes web/public/demo/: manifest.json, live.json, swing.json, intelligence.json, the ledger, and an encrypted journal (passphrase:
"correct horse battery staple"). Everything is sample data, made up, and labelled as such in the page.
"""
import json
import os
import shutil
import tempfile
from datetime import date, datetime

from engine import charts, clock, contract, journal, ledger, live, manifest
from engine.clock import at
from engine.run import load_config
from engine.strategies import sim
from engine.strategies.indicators import Series
from tests import test_swing as ts
from tests.simulate import main as simulate
from tests.test_strategies import DAY, flat_day

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "web", "public", "demo")
PASSPHRASE = "correct horse battery staple"
SWING_END = date(2026, 11, 10)             # the previous evening's close
NOW = datetime.fromisoformat("2026-11-11T13:05:30+05:30")


def write(name, obj):
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def nifty_session(base=25300.0, until="13:00"):
    """Yesterday (quiet) and this morning on made-up Nifty prices: a tight opening range, a breakout above it, then a trend with pullbacks."""
    import math
    t0 = clock.ts(at(DAY, "09:15"))
    n = (clock.ts(at(DAY, until)) - t0) // 300
    prev, out = flat_day(DAY.replace(day=DAY.day - 1), price=base - 40, volume=1000), []
    p = base
    for i in range(n):
        c = base + 18 * math.sin(i * 1.3) if i < 6 else base - 6 + 4 * (i - 6) if i < 10 else base + 34 + 7 * (i - 10) + 18 * math.sin((i - 10) / 3.0)
        o = p
        out.append([t0 + i * 300, o, max(o, c) + 4 + 3 * abs(math.sin(i)), min(o, c) - 4 - 3 * abs(math.cos(i)), c, 1000])
        p = c
    return prev + out


def sample_charts(doc, feed, desk, S):
    """Charts for the sample ideas, made by the engine's own functions. Returns {file name: {key: chart}}."""
    files, trades = {}, {t["id"]: t for t in desk["trades"]}
    for item in doc["ideas"] + doc["positions"]:
        ref = item.get("chart")
        if not ref or ref["file"] == "chartlive_options.json":
            continue
        sid = ref["file"][len("chartlive_"):-len(".json")]
        src_path = os.path.join(ROOT, "tests", f"chartlive_{sid}.json")
        if not os.path.exists(src_path):
            continue
        ch = json.load(open(src_path, encoding="utf-8")).get(ref["key"])
        if ch and ref["key"] in trades:
            files.setdefault(ref["file"], {})[ref["key"]] = sim.decorate_chart(ch, trades[ref["key"]])
    options = {}
    for t in feed["trades"]:
        if t["status"] == "OPEN" and t["date"] == DAY.isoformat():
            ch = charts.option_trade_chart(S, t)
            if ch:
                options[t["id"]] = ch
    return files, options


def swing_sample(folder):
    """Three nights of the swing desk on made-up prices: one position already open, one setup triggered tonight, one near its pivot."""
    ts.END = SWING_END
    base_open = ts.brk_rows()
    open_rows = ts.brk_rows(ts.closes_after_breakout(base_open, ts.path(3, 0.008)))                 # broke out three nights ago
    tonight = ts.brk_rows(n_pre=ts.N_PRE + 3)                                                        # breaks out on the last night
    near = ts.brk_rows(n_pre=ts.N_PRE + 3)
    last = near[-1]
    near[-1] = (last[0], last[1] * 0.95, last[2], last[0] * 1.012, ts.NORMAL_QTY)                    # no breakout tonight: still below its pivot
    days, by_day = ts.make_days(open_rows, extra={"TONIGHT": tonight, "NEARPIVOT": near})
    n = ts.nifty(days, 0.001)
    book, doc, led = None, None, ledger.Ledger(folder, ledger.SWING_PREFIX)
    for length in range(ts.BREAKOUT + 1, len(days) + 1):
        doc, book = ts.night(days, by_day, length, n, book, led)
    return doc


def main():
    os.makedirs(OUT, exist_ok=True)
    for name in os.listdir(OUT):
        os.remove(os.path.join(OUT, name))
    cfg = load_config()

    feed, _ = simulate(verbose=False)
    with open(os.path.join(ROOT, "tests", "strategies_live_demo.json"), encoding="utf-8") as f:
        desk = json.load(f)
    stamp = lambda secs: datetime.fromtimestamp(NOW.timestamp() - secs, clock.IST).isoformat(timespec="seconds")
    feed["status"].update({"phase": "watching", "running": True, "heartbeat": stamp(20), "updated": stamp(40), "session": "2026-11-11-day",
                           "brokers": {"primary": {"name": "Kotak Neo", "status": "connected"},
                                       "commodity": {"name": "Fyers", "status": "outside_session"}}})
    desk["updated"] = stamp(45)
    desk.setdefault("status", {}).update({"cycle_at": stamp(45), "bar_end": "2026-11-11T13:00"})
    desk["health"] = {"asof": stamp(45), "decision": "OK", "blocking": [], "items": [
        {"name": "Nifty 50 candles", "status": "fresh", "detail": "newest candle finished 1 min ago"},
        {"name": "Quotes scan", "status": "fresh", "detail": "1 min ago"},
        {"name": "Quote coverage", "status": "complete", "detail": "196 of 200 fresh quotes"},
        {"name": "Normal-volume baseline", "status": "complete", "detail": "205 of 205 shares"},
        {"name": "Results calendar", "status": "fresh", "detail": "2026-11-11 08:31"},
        {"name": "Broker", "status": "ok", "detail": "412 requests this run, history 2.5/s"}]}
    S = Series(nifty_session())
    a = S.starts[DAY]
    hi, lo = max(S.h[a:a + 6]), min(S.l[a:a + 6])
    nifty_trade = next((t for t in feed["trades"] if t["date"] == DAY.isoformat() and t["name"] == "Nifty 50"), None)
    if nifty_trade is None:                                         # the simulated days move with the calendar, so make one if need be
        base = next(t for t in feed["trades"] if t["date"] == DAY.isoformat() and t.get("kind") == "option")
        nifty_trade = {**base, "id": f"{DAY.isoformat()}-NIFTY-1", "unit": "NIFTY", "name": "Nifty 50", "book": "Index options",
                       "contract": "NIFTY 17 Nov 26 24200 CE"}
        feed["trades"].append(nifty_trade)
    j = 11                                                          # the 10:10 candle, which closes at 10:15
    nifty_trade.update({                                            # the Nifty trade is still open in the sample, so it has a chart and a plan
        "status": "OPEN", "dir": 1, "action": "BUY", "option_type": "CE", "entry_min": clock.ts(at(DAY, "10:17")), "entry_time": at(DAY, "10:17").isoformat(),
        "range_high": round(hi, 2), "range_low": round(lo, 2), "range_mid": round((hi + lo) / 2, 2), "underlying_entry": round(S.c[j], 2),
        "stop_underlying": round((hi + lo) / 2, 2), "target_underlying": round(hi + (hi - lo), 2), "phase": "initial", "remaining": nifty_trade["qty"],
        "last_price": round(nifty_trade["entry_price"] * 1.12, 2), "open_pnl": round(nifty_trade["entry_price"] * 0.12 * nifty_trade["qty"], 2),
        "last_update": stamp(40), "quote_status": "FRESH", "exit_time": None, "exit_price": None, "exit_reason": None, "exit_type": None,
        "pnl": None, "pnl_pct": None, "r": None, "result": None})
    read = {"id": "BANKNIFTY", "name": "Bank Nifty", "state": "Call side broke out", "score": 5, "side": "CE", "market_agrees": True, "grade": "take", "why_not": [],
            "factors": [{"text": "Broke above the opening range", "ok": True}, {"text": "Above VWAP", "ok": True}], "spot": round(S.c[-1], 2),
            "range_high": round(hi, 2), "range_low": round(lo, 2), "warnings": [],
            "idea": {"contract": "BANKNIFTY 54000 CE", "symbol": "NSE_FO|777", "premium": 412.5, "stop": 309.4, "target": 567.2, "lot": 30, "risk_per_lot": 3093,
                     "cost_ratio": 0.11, "spread_pct": 0.9, "oi": 48210, "quote_age_s": 14, "expiry": "2026-11-18"}}
    desk["options_scan"] = {"asof": stamp(45), "items": [read], "heavy": []}
    doc = live.build(NOW, cfg, feed, desk, extra={"journal": {"enabled": True}})
    for idea, minutes in zip(doc["ideas"], (2, 4, 7, 11)):          # the sample's signals are from this morning; make a few recent
        idea["signaled_at"] = stamp(minutes * 60)
    contract.assert_valid("live", doc)
    write("live.json", doc)
    strategy_files, options = sample_charts(doc, feed, desk, S)
    scan_key = next(i["chart"]["key"] for i in doc["ideas"] if i["id"].startswith("scan:"))
    options[scan_key] = charts.scan_chart(S, read, {"name": "Bank Nifty", "range": ["09:15", "09:45"]}, DAY)
    for ch in options.values():
        contract.assert_valid("chart", ch)
    for name, content in {**strategy_files, "chartlive_options.json": options}.items():
        for ch in content.values():
            contract.assert_valid("chart", ch)
        write(name, content)

    work = tempfile.mkdtemp()
    try:
        ledger.sync(ledger.Ledger(work), feed["trades"], desk.get("trades") or [])
        swing_doc = swing_sample(work)
        contract.assert_valid("swing", swing_doc)
        write("swing.json", swing_doc)
        for name in os.listdir(work):
            if name.startswith(("ledger-", "ledger_swing-")):
                shutil.copy(os.path.join(work, name), os.path.join(OUT, name))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    write("intelligence.json", {
        "schema": 1, "sample": True,
        "status": {"last_scan": stamp(120), "running": True, "worker_mode": "continuous", "analysis_mode": "rules"},
        "events": [
            {"id": "s1", "title": "SAMPLE: Shipping disruption reported in the Strait of Hormuz", "source": "Sample wire", "published_at": stamp(1800),
             "category": "Geopolitics and energy", "impact_score": 84, "severity": "high", "confidence": "medium", "horizon": "Days",
             "summary": "A sample headline used to show how the Impact Radar reads. It is not real news.",
             "impacts": [{"asset": "CRUDE", "label": "Crude oil", "bias": "up", "why": "Supply risk can lift crude if the disruption persists."},
                         {"asset": "GOLD", "label": "Gold", "bias": "up", "why": "Safe-haven demand can follow geopolitical stress."},
                         {"asset": "ONGC", "label": "ONGC", "bias": "up", "why": "An upstream producer benefits if crude rises."}],
             "confirm": ["Crude futures move in the same direction on volume"], "market_confirmation": "not_checked", "evidence_count": 3},
            {"id": "s2", "title": "SAMPLE: Central bank signals a slower pace of rate cuts", "source": "Sample wire", "published_at": stamp(7200),
             "category": "Rates and currency", "impact_score": 61, "severity": "medium", "confidence": "low", "horizon": "Days",
             "summary": "A second sample item.", "impacts": [{"asset": "BANKNIFTY", "label": "Bank Nifty", "bias": "mixed", "why": "Banks react both ways to rate signals."}],
             "confirm": [], "market_confirmation": "not_checked", "evidence_count": 1}],
        "disclaimer": "Sample data. Impact score ranks headline relevance, not the probability of a price move."})
    shutil.copy(os.path.join(ROOT, "web", "test", "fixtures", "journal.bin"), os.path.join(OUT, "journal.bin"))
    manifest.write(OUT)
    contract.assert_valid("manifest", json.load(open(os.path.join(OUT, "manifest.json"), encoding="utf-8")))
    print("Sample data written to", OUT, "-", ", ".join(sorted(os.listdir(OUT))))
    print("Journal passphrase for the sample:", PASSPHRASE, "(journal fixture:", journal.FILE + ")")


if __name__ == "__main__":
    main()
