"""Builds the preview data for the strategy tabs from real prices.  Run:

    python -m tests.make_strategy_demo --cache <folder>       (the folder is filled on the first run, about 3 minutes)

It writes tests/strategies_demo.json (a real backtest) and tests/strategies_live_demo.json (the last session
replayed through the real live desk, stopped part-way through the day so some trades are still open).
Then run  python -m tests.build_dashboard  to put them into index.html.
"""
import argparse
import json
import os
import sys
import tempfile
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import clock  # noqa: E402
from engine.clock import at  # noqa: E402
from engine.strategies import backtest, data, sim  # noqa: E402
from engine.strategies.desk import StrategyDesk  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


class ReplayBroker:
    def __init__(self, candles):
        self.candles = candles

    def history(self, sym, res, d0, d1):
        now = clock.ts(clock.now())
        return [c for c in self.candles.get(sym, []) if d0 <= clock.from_ts(c[0]).date() <= d1 and c[0] <= now]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--stop-at", default="13:05", help="time of day the live replay stops, so some trades stay open")
    a = ap.parse_args()
    cfg = backtest.load_config()
    backtest.main(["--source", "yahoo", "--cache", a.cache, "--out", os.path.join(HERE, "strategies_demo.json")])
    sc = sim.settings(cfg)
    uni = backtest.universe()
    markets = [m for m in cfg["markets"] if m.get("kind") == "index" and m.get("enabled", True)]
    insts = sim.build_instruments(markets, uni, sc["index_lots"])
    bars, _, _ = data.load_yahoo(markets, uni, cache_dir=a.cache, log=lambda m: None)
    candles = {s: [c for d in b.days for c in b.by_day[d]] for s, b in bars.items() if s in insts}
    day = max(bars[next(iter(candles))].days)
    folder = tempfile.mkdtemp()
    desk = StrategyDesk(cfg, ReplayBroker(candles), lambda m: None, folder, uni, markets)
    now = at(day, a.stop_at) + timedelta(seconds=30)
    clock.set_fake(now)
    desk.tick(now)
    clock.set_fake(None)
    st = desk.state
    for t in st["trades"]:
        t.pop("late", None)
    st["demo"] = f"Preview only: this replays the real {day.strftime('%d %b %Y')} session up to {a.stop_at}, through the live desk."
    json.dump(st, open(os.path.join(HERE, "strategies_live_demo.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("live demo:", len(st["trades"]), "trades,", sum(1 for t in st["trades"] if t["status"] == "OPEN"), "still open at", a.stop_at)


if __name__ == "__main__":
    main()
