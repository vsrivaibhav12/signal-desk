"""Rebuilds index.html with sample data.  Run:  python -m tests.build_dashboard

The options desk tab uses the replay test's sample trades. The strategy tabs use the real-price files made by
tests/make_strategy_demo.py (if they exist). Without them the strategy tabs simply show their "no data yet" state.
"""
import json
import os

from engine.strategies.defs import STRATEGIES
from engine.strategies.sim import DEFAULTS
from tests.simulate import ROOT, main


def js(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def load(name):
    path = os.path.join(ROOT, "tests", name)
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else None


feed, _ = main(verbose=False)
feed["status"]["day"].pop("markets", None)   # the page only needs the tiles, log and trades
stocks = feed["status"]["day"].get("stocks") or {}
feed["status"]["day"]["stocks"] = {"watch": stocks.get("watch", []), "note": stocks.get("note", ""), "done": stocks.get("done", False)}
feed["status"]["day"].pop("news", None)
feed["status"]["heartbeat"] = feed["status"]["updated"]
page = open(os.path.join(ROOT, "index.template.html"), encoding="utf-8").read()
meta = [{k: s[k] for k in ("id", "name", "source", "best_for", "risk", "rules", "window")} | {"exit_by": DEFAULTS["exit_by"]}
        for s in STRATEGIES]
page = page.replace("/*__BT__*/null", js(load("backtest_demo.json")))
page = page.replace("/*__STRATS__*/[]", js(meta))
page = page.replace("/*__SB__*/null", js(load("strategies_demo.json")))
page = page.replace("/*__RP__*/null", js(load("replay_demo.json")))
page = page.replace("/*__DR__*/null", js(load("daily_demo.json")))
live = load("strategies_live_demo.json")
if live:                                   # sample content for the new sections (clearly sample data in the preview)
    day = live.get("date") or "2026-11-12"
    live["results"] = {"day": day, "ok": True, "fetched_at": day + "T08:31:00", "error": "",
                       "last": [{"symbol": "ITC", "date": day}], "today": [{"symbol": "HAL", "date": day}, {"symbol": "IRCTC", "date": day}],
                       "next": [{"symbol": "ABB", "date": day}, {"symbol": "GRASIM", "date": day}], "later": [{"symbol": "ASHOKLEY", "date": "2026-11-14"}]}
    live["watchlist"] = [{"symbol": "NSE:NIFTY50-INDEX", "name": "Nifty 50", "why": "Index"},
                         {"symbol": "NSE:ITC-EQ", "name": "ITC", "why": "Results just out: watch the reaction"},
                         {"symbol": "NSE:HAL-EQ", "name": "HAL", "why": "Results today"},
                         {"symbol": "NSE:SBILIFE-EQ", "name": "SBILIFE", "why": "Volume 2.6x normal"}]
    live["health"] = {"asof": day + "T10:05:00", "decision": "OK", "blocking": [], "items": [
        {"name": "Nifty 50 candles", "status": "fresh", "detail": "newest candle finished 1 min ago"},
        {"name": "Quotes scan", "status": "fresh", "detail": "1 min ago"},
        {"name": "Normal-volume baseline", "status": "complete", "detail": "205 of 205 shares"},
        {"name": "Results calendar", "status": "fresh", "detail": day + " 08:31"},
        {"name": "Broker", "status": "ok", "detail": "412 requests this run, history 2.5/s"}]}
    live["status"] = {**(live.get("status") or {}), "cycle_calls": 38, "cycle_seconds": 21.4}
    live["api"] = {"total": 412, "history_rate": 2.5, "cooling": 0, "by_method": {"historical_data": {"calls": 330, "ok": 330, "errors": 0, "refused": 0, "avg_ms": 310}, "quotes": {"calls": 80, "ok": 80, "errors": 0, "refused": 0, "avg_ms": 270}}}
    live["leaders"] = {"ok": True, "asof": day, "universe": 956, "rs_min": 80.0, "n_leaders": 190,
                       "breadth": {"above50": 0.457, "above200": 0.535, "advancing": 0.726, "bear_phase": False},
                       "leaders": [{"symbol": "SBILIFE", "rs": 97.4, "ret3m_pct": 21.3, "off_high_pct": 3.1, "turnover_cr": 142.0},
                                   {"symbol": "HAL", "rs": 94.8, "ret3m_pct": 18.2, "off_high_pct": 6.4, "turnover_cr": 310.5},
                                   {"symbol": "TBZ", "rs": 99.9, "ret3m_pct": 130.3, "off_high_pct": 19.4, "turnover_cr": 75.8}]}
    for w in live["watchlist"]:
        w["rs"] = {"Nifty 50": None, "ITC": 41.0, "HAL": 94.8, "SBILIFE": 97.4}.get(w["name"])
    live["scan"] = {"at": day + "T09:25:00", "have_baseline": 205, "of": 205, "board": []}
    live["options_scan"] = {"asof": day + "T10:05:00", "heavy": ["HAL"] and [], "items": [
        {"id": "NIFTY", "name": "Nifty 50", "spot": 24512.4, "change": 0.62, "state": "Call side broke out", "side": "CE", "score": 5,
         "range_high": 24440.0, "range_low": 24365.0, "market_agrees": True, "grade": "take", "why_not": [],
         "factors": [{"text": "Broke above the opening range", "ok": True}, {"text": "Above VWAP", "ok": True}, {"text": "9 EMA above 21 EMA", "ok": True},
                     {"text": "Up on the day", "ok": True}, {"text": "Near the day's high", "ok": True}],
         "idea": {"contract": "NIFTY26NOV24500CE", "side": "CE", "strike": 24500, "lot": 75, "expiry": "2026-11-26", "premium": 142.5,
                  "bid": 141.5, "ask": 142.5, "spread_pct": 0.71, "bid_qty": 450, "ask_qty": 600, "oi": 154000, "quote_age_s": 3,
                  "stop": 106.9, "target": 195.9, "risk_per_lot": 2670, "capital_per_lot": 10688, "cost_ratio": 0.11}, "warnings": []},
        {"id": "BANKNIFTY", "name": "Bank Nifty", "spot": 52210.0, "change": 0.18, "state": "Inside the opening range", "side": "CE", "score": 3,
         "range_high": 52300.0, "range_low": 52080.0, "grade": "watch", "why_not": ["score 3 of 5, needs 4"], "factors": [], "warnings": []},
        {"id": "SENSEX", "name": "Sensex", "spot": 80120.0, "change": -0.4, "state": "Put side broke out", "side": "PE", "score": 4,
         "range_high": 80500.0, "range_low": 80200.0, "market_agrees": False, "grade": "watch", "why_not": ["Nifty is on the other side of VWAP"],
         "factors": [{"text": "Broke below the opening range", "ok": True}, {"text": "Below VWAP", "ok": True}, {"text": "9 EMA below 21 EMA", "ok": True},
                     {"text": "Down on the day", "ok": True}, {"text": "Near the day's low", "ok": False}, {"text": "Nifty on the same side of VWAP", "ok": False}],
         "idea": {"contract": "SENSEX26NOV80100PE", "side": "PE", "strike": 80100, "lot": 20, "expiry": "2026-11-19", "premium": 310.0, "stop": 232.5,
                  "target": 426.2, "risk_per_lot": 1550, "capital_per_lot": 6200, "cost_ratio": 0.15}, "warnings": []}]}
page = page.replace("/*__SL__*/null", js(live))
intel_demo = {
    "schema": 1,
    "status": {"running": False, "worker_mode": "preview", "last_scan": "2026-11-12T18:55:00+05:30",
               "analysis_mode": "Rules-based demo", "new_events": 1, "next_scan_seconds": 300,
               "source_health": {"queries": 7, "queries_with_results": 5, "query_errors_or_empty": 2,
                                  "headlines_received": 18, "unique_headlines": 12}},
    "events": [{
        "id": "demo-iran-energy", "title": "[DEMO] Iran conflict escalation raises Strait of Hormuz shipping risk",
        "source": "Illustrative sample", "source_domains": ["demo.example"], "evidence_count": 1,
        "published_at": "2026-11-12T18:40:00+05:30", "category": "Geopolitics / energy supply",
        "impact_score": 85, "severity": "high", "overall_bias": "conditional",
        "summary": "Conflict escalation can add a geopolitical risk premium, but a sustained crude move generally needs evidence of supply, export or transit disruption. The asset-level direction remains conditional and needs price/volume confirmation.",
        "confidence": "low", "confidence_note": "Confidence in the relevance mapping only; not a probability of a price move.",
        "horizon": "intraday to 2 weeks", "analysis_method": "rules", "market_confirmation": "not_checked",
        "decision_status": "CONTEXT_ONLY", "probability": None,
        "impacts": [
            {"asset": "CRUDE", "label": "MCX Crude Oil", "bias": "bullish", "why": "A conflict near major oil export or shipping routes can add a geopolitical risk premium; actual supply disruption matters more than rhetoric."},
            {"asset": "GOLD", "label": "MCX Gold", "bias": "bullish", "why": "Escalation can increase safe-haven demand, while the US dollar and real yields can offset it."},
            {"asset": "IOC", "label": "Indian Oil Corporation", "bias": "mixed", "why": "Higher crude raises input costs; inventory effects and retail-price pass-through can offset or reverse the impact."},
        ],
        "confirm": ["Verified disruption to oil exports or transit", "MCX crude holds a breakout with abnormal volume", "Related energy instruments confirm the move"],
        "invalidate": ["Diplomatic de-escalation or reopening of disrupted routes", "No sustained change in physical supply or freight", "Crude fails to confirm after liquidity returns"],
        "link": ""
    }],
    "disclaimer": "Impact scores rank headline relevance; they are not a calibrated probability estimate. Directional effects are conditional hypotheses until corroborated by market data."
}
blob = js(feed)
page = page.replace("/*__DEMO__*/null", blob).replace("/*__INTEL__*/null", js(intel_demo))
open(os.path.join(ROOT, "index.html"), "w", encoding="utf-8").write(page)
print("index.html written,", len(blob) // 1024, "KB of sample data,", len(feed["trades"]), "sample trades")
