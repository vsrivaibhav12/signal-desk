"""The portfolio rules that both engines share, written once.

The rulebook engine (engine/core.py) decides one entry at a time as the day unfolds. The strategy desk's controller
(engine/strategies/risk.py) replays a whole day of candidates in time order. They ask the same questions, so the arithmetic lives here
and each caller only chooses its own words for the answer:

  limit_breaches            position count, open risk and realised daily loss against a set of limits
  correlated_exposure_breached
                            too many open positions that are really the same bet. Nifty, Bank Nifty, Fin Nifty, Midcap Nifty and
                            Sensex move together, so same-direction positions in them count as one family.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

DAILY_LOSS = "daily_loss"
POSITIONS = "positions"
OPEN_RISK = "open_risk"

# Names under which the index underlyings appear (rulebook unit names, instrument names, trading symbols), compared with spaces and
# punctuation removed and capitals folded.
INDEX_FAMILY = {"NIFTY", "NIFTY50", "BANKNIFTY", "NIFTYBANK", "FINNIFTY", "NIFTYFINSERVICE", "MIDCPNIFTY", "MIDCAPNIFTY",
                "NIFTYMIDSELECT", "SENSEX"}


def limit_breaches(open_positions: int, open_risk: float, realized_pnl: float, new_risk: float, *, max_positions: int,
                   max_risk: float, max_daily_loss: float, tolerance: float = 0.0) -> list[str]:
    """Which limits stop a new position. Always in the order: daily loss, position count, open risk.

    The loss limit is reached when realised P&L is at or below minus the limit; the position limit when the count has reached it; the
    risk limit when the new position would take open risk above it (by more than `tolerance`, for float noise).
    """
    out = []
    if realized_pnl <= -float(max_daily_loss):
        out.append(DAILY_LOSS)
    if open_positions >= int(max_positions):
        out.append(POSITIONS)
    if open_risk + new_risk > float(max_risk) + tolerance:
        out.append(OPEN_RISK)
    return out


def index_key(name: Optional[str]) -> Optional[str]:
    """The family key of an index underlying ('Bank Nifty', 'BANKNIFTY' and 'NSE:NIFTYBANK-INDEX' all match), else None."""
    if not name:
        return None
    text = str(name).upper()
    text = re.sub(r"^[A-Z]+:", "", text)              # an exchange prefix such as NSE:
    text = re.sub(r"-(INDEX|EQ)$", "", text)
    text = re.sub(r"[^A-Z0-9]", "", text)
    return text if text in INDEX_FAMILY else None


def is_index(name: Optional[str]) -> bool:
    return index_key(name) is not None


def correlated_exposure_breached(name: Optional[str], direction: Optional[int], open_positions: Iterable[dict],
                                 cap: Optional[int]) -> bool:
    """True when `cap` or more open positions in the index family already lean the same way as a new index position.

    Only index underlyings are ever limited, a cap of None or 0 turns the rule off, and positions with an unknown direction never count.
    """
    if not cap or direction not in (1, -1) or not is_index(name):
        return False
    same = sum(1 for p in open_positions if is_index(p.get("name") or p.get("symbol")) and p.get("dir") == direction)
    return same >= int(cap)
