"""The shared vocabulary: what a desk, an idea, a position and a settled trade look like, whichever engine produced them.

Before this module the rulebook engine and the strategy desk each described the same things with different field names (`pnl` and
`net`, `risk_amount` and `risk`, `entry_price` and `entry`). Everything published to the dashboard or kept in the ledger goes through
the types here, and each has a JSON Schema under contract/schemas/ that the tests check.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

DESKS = ("index_options", "intraday_stocks", "index_futures", "mcx", "swing")
DESK_NAMES = {
    "index_options": "Index options",
    "intraday_stocks": "Intraday stocks",
    "index_futures": "Index futures (paper)",
    "mcx": "MCX commodities",
    "swing": "Swing stocks",
}
CHECK_STATES = ("pass", "fail", "unknown", "na")
GRADES = ("take", "watch", "setup", "wait")

_RULEBOOK_DESK = {"Index options": "index_options", "Commodity options": "mcx", "Stocks": "intraday_stocks"}


def _f(value: Any) -> Optional[float]:
    """A finite float, or None. Published numbers are never NaN or infinity."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _round(value: Any, digits: int = 2) -> Optional[float]:
    number = _f(value)
    return None if number is None else round(number, digits)


def rulebook_desk(trade: dict) -> str:
    """Which desk a rulebook-engine trade belongs to."""
    return _RULEBOOK_DESK.get(trade.get("book"), "index_options")


def strategy_desk(trade: dict) -> str:
    """Which desk a strategy-desk trade belongs to: shares are intraday stocks, index trades are paper futures."""
    return "intraday_stocks" if trade.get("kind") == "stock" else "index_futures"


@dataclass
class Check:
    id: str
    label: str
    state: str          # pass, fail, unknown (the data was missing, which counts as not passing), na (does not apply)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LedgerTrade:
    """A settled paper trade, kept for good. Once written it is never changed."""
    id: str
    desk: str
    source: str
    date: str
    symbol: str
    name: str
    instrument: str                     # stock, index_future or option
    direction: int                      # +1 long the underlying, -1 short
    action: str                         # BUY or SELL
    qty: float
    entry_price: float
    entry_time: str
    status: str                         # CLOSED, or UNRESOLVED when no verified exit price exists
    risk: Optional[float] = None
    exit_price: Optional[float] = None
    exit_time: Optional[str] = None
    exit_type: Optional[str] = None
    exit_reason: Optional[str] = None
    gross: Optional[float] = None
    cost: Optional[float] = None
    cost_estimated: bool = True
    net: Optional[float] = None
    r: Optional[float] = None
    result: Optional[str] = None
    grade: Optional[str] = None
    failed_checks: list = field(default_factory=list)
    approved: bool = True
    schema: int = 1

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_rulebook(t: dict, cost_table: Optional[dict] = None) -> "LedgerTrade":
        """From a trade of the rulebook engine (engine/core.py). Raises KeyError or TypeError on a malformed record."""
        from . import costs
        status = t["status"]
        if status not in ("CLOSED", "UNRESOLVED"):
            raise ValueError("only settled trades go in the ledger")
        option = t.get("kind") == "option"
        qty = float(t["qty"])
        entry = float(t["entry_price"])
        exit_price = _f(t.get("exit_price")) if status == "CLOSED" else None
        gross = _round(t.get("pnl")) if status == "CLOSED" else None
        cost = net = r = result = None
        risk = _round(t.get("risk_amount"))
        if gross is not None and exit_price is not None:
            cost = round(costs.round_trip("option" if option else "stock", qty, entry, exit_price, cost_table), 2)
            net = round(gross - cost, 2)
            r = round(net / risk, 3) if risk else None
            result = "WIN" if net > 0 else "LOSS" if net < 0 else "FLAT"
        return LedgerTrade(
            id="rulebook:" + str(t["id"]), desk=rulebook_desk(t), source="rulebook", date=str(t["date"]),
            symbol=str(t.get("symbol") or t.get("unit") or t["name"]), name=str(t["name"]),
            instrument="option" if option else "stock", direction=int(t["dir"]),
            action=str(t.get("action") or ("BUY" if int(t["dir"]) == 1 else "SELL")),
            qty=qty, entry_price=entry, entry_time=str(t["entry_time"]), status=status, risk=risk,
            exit_price=exit_price, exit_time=t.get("exit_time"), exit_type=t.get("exit_type"),
            exit_reason=t.get("exit_reason") or t.get("unresolved_reason"), gross=gross, cost=cost, cost_estimated=True,
            net=net, r=r, result=result, grade="take", failed_checks=[], approved=True)

    @staticmethod
    def from_swing(p: dict, cost_round_trip: float) -> "LedgerTrade":
        """From a closed paper position of the swing desk (engine/swing.py). Measured per share: one unit, risk = entry minus stop."""
        if p.get("status") != "CLOSED":
            raise ValueError("only settled trades go in the ledger")
        entry, exit_price, stop0 = float(p["entry"]), float(p["exit"]), float(p["initial_stop"])
        risk = entry - stop0
        gross = exit_price - entry
        cost = cost_round_trip * entry
        net = gross - cost
        return LedgerTrade(
            id="swing:" + str(p["id"]), desk="swing", source="swing", date=str(p["entry_date"]), symbol="NSE:" + str(p["symbol"]) + "-EQ",
            name=str(p["symbol"]), instrument="stock", direction=1, action="BUY", qty=1.0, entry_price=round(entry, 2),
            entry_time=str(p["entry_date"]) + "T09:15:00+05:30", status="CLOSED", risk=round(risk, 2), exit_price=round(exit_price, 2),
            exit_time=str(p["exit_date"]) + "T09:15:00+05:30", exit_type=str(p.get("reason") or "").upper().replace(" ", "_") or None,
            exit_reason=p.get("reason"), gross=round(gross, 2), cost=round(cost, 2), cost_estimated=True, net=round(net, 2),
            r=round(net / risk, 3) if risk > 0 else None, result="WIN" if net > 0 else "LOSS" if net < 0 else "FLAT", grade="setup",
            failed_checks=[], approved=True)

    @staticmethod
    def from_strategy(t: dict) -> "LedgerTrade":
        """From a trade of the strategy desk (engine/strategies/sim.py). Raises KeyError or TypeError on a malformed record."""
        if t["status"] != "CLOSED":
            raise ValueError("only settled trades go in the ledger")
        dec = t.get("dec") or {}
        return LedgerTrade(
            id="strategy:" + str(t["id"]), desk=strategy_desk(t), source="strategy:" + str(t["strategy"]), date=str(t["date"]),
            symbol=str(t["symbol"]), name=str(t["name"]), instrument="stock" if t.get("kind") == "stock" else "index_future",
            direction=int(t["dir"]), action="BUY" if int(t["dir"]) == 1 else "SELL", qty=float(t["qty"]),
            entry_price=float(t["entry"]), entry_time=str(t["entry_t"]), status="CLOSED", risk=_round(t.get("risk")),
            exit_price=_f(t.get("exit")), exit_time=t.get("exit_t"), exit_type=t.get("exit_type"),
            exit_reason=t.get("exit_reason"), gross=_round(t.get("gross")), cost=_round(t.get("cost")), cost_estimated=True,
            net=_round(t.get("net")), r=_round(t.get("net_r"), 3), result=t.get("result"), grade=dec.get("g"),
            failed_checks=list(dec.get("f") or []), approved=bool(t.get("risk_approved", True)))


@dataclass
class Position:
    """An open paper position, with how fresh its price is."""
    id: str
    desk: str
    source: str
    name: str
    symbol: str
    direction: int
    action: str
    qty: float
    entry_price: float
    entry_time: str
    stop: Optional[float]
    target: Optional[float]
    last_price: Optional[float]
    last_update: Optional[str]
    open_pnl: Optional[float]
    risk: Optional[float]
    open_r: Optional[float]
    quote_status: str                   # FRESH, STALE (the stop cannot be verified) or UNKNOWN
    contract: Optional[str] = None
    chart: Optional[dict] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Idea:
    """Something worth considering right now, from any desk."""
    id: str
    desk: str
    source: str
    name: str
    symbol: str
    direction: int
    action: str
    grade: str
    status: str                         # OPEN, or CANDIDATE for an idea with no paper position yet
    signaled_at: Optional[str]
    entry: Optional[float]
    stop: Optional[float]
    target: Optional[float]
    qty: Optional[float]
    risk: Optional[float]
    cost_ratio: Optional[float]
    checks: list = field(default_factory=list)
    blockers: list = field(default_factory=list)
    why: list = field(default_factory=list)
    context: dict = field(default_factory=dict)
    evidence: Optional[dict] = None
    contract: Optional[str] = None
    paper_position: bool = False
    quality: int = 0
    plan: dict = field(default_factory=dict)         # in words: where to enter, the stop, the target, the trail, when to leave
    chart: Optional[dict] = None                      # where its chart is: {"file", "key"} and optionally "in"

    def to_dict(self) -> dict:
        return asdict(self)
