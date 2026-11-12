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

from engine import clock, contract, journal, ledger, live, manifest
from engine.run import load_config
from tests import test_swing as ts
from tests.simulate import main as simulate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "web", "public", "demo")
PASSPHRASE = "correct horse battery staple"
SWING_END = date(2026, 11, 10)             # the previous evening's close
NOW = datetime.fromisoformat("2026-11-11T13:05:30+05:30")


def write(name, obj):
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


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
    doc = live.build(NOW, cfg, feed, desk, extra={"journal": {"enabled": True}})
    for idea, minutes in zip(doc["ideas"], (2, 4, 7, 11)):          # the sample's signals are from this morning; make a few recent
        idea["signaled_at"] = stamp(minutes * 60)
    contract.assert_valid("live", doc)
    write("live.json", doc)

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
