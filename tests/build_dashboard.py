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
page = page.replace("/*__SL__*/null", js(load("strategies_live_demo.json")))
blob = js(feed)
open(os.path.join(ROOT, "index.html"), "w", encoding="utf-8").write(page.replace("/*__DEMO__*/null", blob))
print("index.html written,", len(blob) // 1024, "KB of sample data,", len(feed["trades"]), "sample trades")
