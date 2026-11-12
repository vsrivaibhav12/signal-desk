"""Wipes the saved logs so the tool starts clean. Keeps the Telegram link (secure.bin), the results calendar and the volume baseline.

    python -m engine.reset <data folder>

Removed: every trade and signal log, strategy and options results, the prospective option quote tape, all trade charts, and all backtest results.
Used by the "Reset data" workflow, which then commits the change to the data branch.
"""
import json
import os
import sys

from .schema import SCHEMA

DROP_FILES = {"strategies.json", "strategies_backtest.json", "backtest.json", "options_tape.jsonl"}
DROP_PREFIXES = ("chartlive_", "chartbt_")


def reset(folder):
    removed = []
    for name in sorted(os.listdir(folder)):
        if name in DROP_FILES or name.startswith(DROP_PREFIXES):
            os.remove(os.path.join(folder, name))
            removed.append(name)
    with open(os.path.join(folder, "feed.json"), "w", encoding="utf-8") as f:
        json.dump({"status": {}, "trades": [], "schema": SCHEMA}, f, separators=(",", ":"))
    removed.append("feed.json (emptied)")
    return removed


if __name__ == "__main__":
    if len(sys.argv) != 2 or not os.path.isdir(sys.argv[1]):
        sys.exit("usage: python -m engine.reset <data folder>")
    for n in reset(sys.argv[1]):
        print("removed", n)
