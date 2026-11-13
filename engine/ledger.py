"""The ledger: every settled paper trade, kept for good, one file per month.

    _data/ledger-2026-11.jsonl      one JSON object per line, one line per settled trade

The files sit at the top level of the data folder, not in a subfolder, because the publisher (engine/store.py) refreshes other
workflows' top-level files from the data branch before it pushes, which is what stops a stale copy from overwriting newer lines.

Rules:
  - Append only. A trade is written once, when it settles, and never edited or removed. Writing the same trade again is a no-op,
    so the engine can call `record` on every cycle without creating duplicates.
  - A schema upgrade never clears it. When an old state file cannot be migrated, its settled trades are written here first
    (see engine/migrations.py).
  - A torn last line (the process died mid-write) is ignored on read and healed before the next append.
"""
from __future__ import annotations

import json
import os
import re
from typing import Iterable, Iterator

from .domain import LedgerTrade

MONTH = re.compile(r"^\d{4}-\d{2}$")
PREFIX = "ledger-"                 # settled trades of the live engine
SWING_PREFIX = "ledger_swing-"     # settled paper trades of the nightly swing desk (a different publisher, so a different file)
SUFFIX = ".jsonl"


class Ledger:
    def __init__(self, folder: str, prefix: str = PREFIX):
        self.dir = folder
        self.prefix = prefix
        self._file = re.compile("^" + re.escape(prefix) + r"(\d{4}-\d{2})" + re.escape(SUFFIX) + "$")
        self._ids: dict[str, set] = {}

    def _path(self, month: str) -> str:
        if not MONTH.match(month):
            raise ValueError(f"bad ledger month: {month!r}")
        return os.path.join(self.dir, self.prefix + month + SUFFIX)

    @staticmethod
    def _lines(path: str) -> Iterator[dict]:
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue                     # a torn line is skipped, never fatal
                    if isinstance(row, dict) and "id" in row:
                        yield row
        except OSError:
            return

    def months(self) -> list[str]:
        try:
            names = os.listdir(self.dir)
        except OSError:
            return []
        return sorted(m.group(1) for m in (self._file.match(n) for n in names) if m)

    def _known(self, month: str) -> set:
        if month not in self._ids:
            self._ids[month] = {row["id"] for row in self._lines(self._path(month))}
        return self._ids[month]

    def has(self, trade_id: str, month: str) -> bool:
        return trade_id in self._known(month)

    def record(self, trade: LedgerTrade | dict) -> bool:
        """Writes one settled trade. Returns True if it was new, False if it was already there."""
        row = trade.to_dict() if isinstance(trade, LedgerTrade) else dict(trade)
        month = str(row["date"])[:7]
        known = self._known(month)
        if row["id"] in known:
            return False
        path = self._path(month)
        os.makedirs(self.dir, exist_ok=True)
        line = json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
        needs_break = False
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                if f.tell():
                    f.seek(-1, os.SEEK_END)
                    needs_break = f.read(1) != b"\n"     # heal a torn last line so the new row starts on its own line
        except OSError:
            pass
        with open(path, "a", encoding="utf-8", newline="\n") as f:        # always LF, so the file's bytes (and its hash) match on every system
            if needs_break:
                f.write("\n")
            f.write(line)
            f.flush()
        known.add(row["id"])
        return True

    def record_many(self, trades: Iterable[LedgerTrade | dict]) -> int:
        return sum(1 for t in trades if self.record(t))

    def read_month(self, month: str) -> list[dict]:
        return list(self._lines(self._path(month)))

    def read_all(self) -> list[dict]:
        out: list[dict] = []
        for month in self.months():
            out.extend(self.read_month(month))
        return out


def sync(ledger: Ledger, rulebook_trades: Iterable[dict] = (), strategy_trades: Iterable[dict] = (),
         cost_table: dict | None = None, log=print) -> int:
    """Records every settled trade from both engines that the ledger does not hold yet. Malformed trades are skipped, not fatal.

    Strategy trades count only when the portfolio controller approved them: the others were signals, not paper positions.
    """
    added = 0
    for t in rulebook_trades:
        if t.get("status") not in ("CLOSED", "UNRESOLVED"):
            continue
        try:
            added += ledger.record(LedgerTrade.from_rulebook(t, cost_table))
        except (KeyError, TypeError, ValueError) as exc:
            log(f"Ledger: skipped a malformed rulebook trade ({type(exc).__name__}: {exc})")
    for t in strategy_trades:
        if t.get("status") != "CLOSED" or not t.get("risk_approved", True):
            continue
        try:
            added += ledger.record(LedgerTrade.from_strategy(t))
        except (KeyError, TypeError, ValueError) as exc:
            log(f"Ledger: skipped a malformed strategy trade ({type(exc).__name__}: {exc})")
    return added
