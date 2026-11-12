"""live.json: the one small file the cockpit needs to show the present moment.

It is built from what the two engines already hold (the rulebook feed and the strategy desk's state) and published every cycle. The
dashboard depends on this file and the ledger, not on the engines' internal files, so those can change without breaking the page.

Contents (contract/schemas/live.schema.json):
  session, brokers, health, freshness   whether the data can be trusted right now, and how old it is
  risk                                  the shared portfolio budget and how much of it is used
  ideas                                 what is worth considering, ranked, from every desk, each with the checks behind its grade
  positions, ended                      open paper positions, and today's settled ones
  market                                index tiles, regime, movers, results, relative-strength breadth

Nothing here is a recommendation to trade. A TAKE grade means the signal passed this tool's own checks; each idea also carries what
the research found for that kind of signal (engine/evidence.py).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from . import costs, evidence
from .domain import DESK_NAMES, Check, Idea, Position, _f, _round, rulebook_desk, strategy_desk

SCHEMA = 1

# The page uses these to age numbers: amber, then stale; an idea older than the last one is shown as expired.
THRESHOLDS = {"price_amber_s": 300, "price_stale_s": 600, "idea_expire_s": 900}

CHECK_LABELS = {"cost": "Stop wide enough for the costs", "vol": "Heavy volume", "mkt": "Nifty agrees", "conf": "No conflicting signal",
                "range": "Broke out of the opening range", "score": "Enough supporting factors"}

RISK_REASONS = {
    "signal_not_take_grade": "not graded take",
    "daily_loss_limit": "the strategy desk's daily loss limit is reached",
    "max_open_positions": "the strategy desk is at its position limit",
    "symbol_already_exposed": "already in a position in this symbol",
    "max_total_open_risk": "the strategy desk's open-risk budget would be exceeded",
    "shared_daily_loss_limit": "the shared daily loss limit is reached",
    "shared_max_open_positions": "the shared position limit is reached",
    "shared_max_open_risk": "the shared open-risk budget would be exceeded",
    "invalid_risk": "the trade has no valid risk amount",
    "correlated_index_exposure": "too many same-direction index positions are already open",
}

WATCH_RECENT_S = 2700          # an ungraded signal older than this is no longer shown as an idea
MAX_IDEAS = 30
MAX_ENDED = 20


# ── small helpers ────────────────────────────────────────────────────────────
def _age(now: datetime, stamp: Optional[str]) -> Optional[float]:
    if not stamp:
        return None
    try:
        then = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    if then.tzinfo is None:
        return None
    return (now - then).total_seconds()


def quality(grade: str, cost_ratio: Optional[float], unknown: int, failed: int, in_play: bool, rs: Optional[float], direction: int) -> int:
    """A 0 to 100 score used only to order ideas on screen. It is not a probability and not a prediction of profit.

    Starts from the grade (take 70, setup 50, watch 25, wait 10), adds up to 10 for a low cost ratio, 8 for a share that is in play,
    6 for a long in a relative-strength leader; takes off 10 for each check that could not be verified and 5 for each that failed.
    """
    q = {"take": 70, "setup": 50, "watch": 25, "wait": 10}.get(grade, 10)
    if cost_ratio is not None:
        q += round(10 * max(0.0, 1 - min(max(cost_ratio, 0.0), 0.25) / 0.25))
    if in_play:
        q += 8
    if rs is not None and rs >= 80 and direction == 1:
        q += 6
    q -= 10 * unknown + 5 * failed
    return max(0, min(100, int(q)))


def strategy_checks(t: dict) -> list:
    """The four checks behind a strategy signal's grade, from the decision record. Unknown data is its own state, never a pass."""
    dec = t.get("dec")
    if not dec:
        return []
    failed, unknown = set(dec.get("f") or []), set(dec.get("unknown") or [])
    is_index = t.get("kind") == "index"
    out = [Check("cost", CHECK_LABELS["cost"], "fail" if "cost" in failed else "pass")]
    if is_index:
        out.append(Check("vol", CHECK_LABELS["vol"], "na"))
    else:
        out.append(Check("vol", CHECK_LABELS["vol"], "unknown" if "vol_data" in unknown else "fail" if "vol" in failed else "pass"))
    if "mkt_data" in unknown:
        out.append(Check("mkt", CHECK_LABELS["mkt"], "unknown"))
    elif "mkt" in failed:
        out.append(Check("mkt", CHECK_LABELS["mkt"], "fail"))
    else:
        out.append(Check("mkt", CHECK_LABELS["mkt"], "pass" if dec.get("nm") is True else "na"))
    out.append(Check("conf", CHECK_LABELS["conf"], "fail" if "conf" in failed else "pass"))
    return out


def _check_blockers(checks: list) -> list:
    out = []
    for c in checks:
        if c.state == "fail":
            out.append(f"{c.label}: failed")
        elif c.state == "unknown":
            out.append(f"{c.label}: could not be verified")
    return out


# ── what to do, in words, and where the chart is ────────────────────────────
def _px(n) -> str:
    number = _f(n)
    return "–" if number is None else f"{number:,.2f}".rstrip("0").rstrip(".")


def strategy_chart_ref(t: dict) -> dict:
    return {"file": f"chartlive_{t['strategy']}.json", "key": str(t["id"])}


def rulebook_chart_ref(t: dict) -> dict:
    return {"file": "chartlive_options.json", "key": str(t["id"])}


def strategy_plan(t: dict) -> dict:
    """Where to enter, the stop, the target, the trail and when to leave, for a strategy-desk signal."""
    d = int(t["dir"])
    long = d == 1
    rule, n = t.get("rule_exit"), t.get("trail", 3)
    if rule == "drive_trail":
        trail = (f"Trail: leave when a 5-minute candle closes {'below the low' if long else 'above the high'} of the previous {n} candles. "
                 "The line on the chart moves with price.")
    elif rule == "trend_fail":
        trail = "Exit line: leave early if a 5-minute candle closes through the 50-period average (the dotted line on the chart)."
    else:
        trail = "No trailing: the stop and the target stay where they are."
    return {
        "enter": f"{'Buy' if long else 'Sell'} near {_px(t.get('entry'))}, at the open of the candle after the signal.",
        "stop": f"Stop {_px(t.get('stop'))}. If price touches it, leave.",
        "target": f"Target {_px(t.get('target'))}." if t.get("target") is not None else "No fixed target: the trail decides when to leave.",
        "trail": trail,
        "exit_by": f"Leave by {t['exit_by']} whatever happens." if t.get("exit_by") else None,
    }


def rulebook_plan(t: dict, cfg: Optional[dict] = None) -> dict:
    """The same, for an opening-range breakout of the rulebook engine. Its levels are on the index (or share), not on the option."""
    long = int(t["dir"]) == 1
    name = t["name"]
    ee = (cfg or {}).get("early_exit") or {}
    early = []
    if ee.get("enabled") and ee.get("closes_back_inside_range"):
        early.append(f"{int(ee['closes_back_inside_range'])} closes back inside the opening range")
    if ee.get("enabled") and ee.get("vwap_break"):
        early.append("a close on the wrong side of VWAP")
    option = t.get("kind") == "option"
    back = "below" if long else "above"
    if option:
        enter = f"Buy {t.get('contract') or name} near ₹{_px(t.get('entry_price'))}."
        stop = (f"Leave if the option falls to ₹{_px(t.get('stop_price'))}, or if {name} closes a 5-minute candle {back} "
                f"{_px(t.get('stop_underlying'))}.")
    else:
        enter = f"{t.get('action') or ('Buy' if long else 'Sell')} {name} near ₹{_px(t.get('entry_price'))}."
        stop = f"Leave if {name} closes a 5-minute candle {back} {_px(t.get('stop_underlying'))}."
    return {
        "enter": enter, "stop": stop,
        "target": f"Target: {name} reaching {_px(t.get('target_underlying'))}. At the target book half and move your stop to your entry price.",
        "trail": ("After the target, trail the rest along VWAP (the line on the chart) and leave when a candle closes back across it."
                  + (f" Leave early after {' or '.join(early)}." if early else "")),
        "exit_by": f"Leave by {t['exit_by']} if nothing else has ended it." if t.get("exit_by") else None,
    }


def scan_plan(read: dict, idea: dict) -> dict:
    watch = read.get("grade") != "take"
    first = (read.get("why_not") or [""])[0]
    return {
        "enter": (f"Buy {idea.get('contract')} near ₹{_px(idea.get('premium'))}, the ask price." + (f" Not yet: {first}." if watch and first else "")),
        "stop": f"Stop ₹{_px(idea.get('stop'))} on the option's price.",
        "target": f"Target ₹{_px(idea.get('target'))} on the option's price.",
        "trail": "No trailing rule for this idea. The rulebook desk trails the rest along VWAP after its target; decide your own before you enter.",
        "exit_by": None,
    }


# ── ideas ────────────────────────────────────────────────────────────────────
def strategy_idea(t: dict, now: datetime) -> Optional[Idea]:
    """An open signal from the strategy desk, or None if it should not be shown (too old to act on, or ungraded)."""
    if t.get("status") != "OPEN":
        return None
    dec = t.get("dec") or {}
    grade = dec.get("g", "watch")
    approved = bool(t.get("risk_approved", True))
    age = _age(now, t.get("signal_t"))
    if grade != "take" and (age is None or age > WATCH_RECENT_S):
        return None
    checks = strategy_checks(t)
    blockers = _check_blockers(checks)
    if not approved:
        blockers += [RISK_REASONS.get(code, code) for code in (t.get("risk_reasons") or [])]
    direction = int(t["dir"])
    unknown = sum(1 for c in checks if c.state == "unknown")
    failed = sum(1 for c in checks if c.state == "fail")
    in_play, rs = dec.get("ip"), dec.get("rs")
    return Idea(
        id="strategy:" + str(t["id"]), desk=strategy_desk(t), source="strategy:" + str(t["strategy"]),
        name=str(t["name"]), symbol=str(t["symbol"]), direction=direction, action="BUY" if direction == 1 else "SELL",
        grade=grade, status="OPEN", signaled_at=t.get("signal_t"), entry=_round(t.get("entry")), stop=_round(t.get("stop")),
        target=_round(t.get("target")), qty=_f(t.get("qty")), risk=_round(t.get("risk")), cost_ratio=_f(dec.get("cr")),
        checks=[c.to_dict() for c in checks], blockers=blockers, why=[str(x) for x in (t.get("why") or [])][:6],
        context={"in_play": in_play, "rs": rs, "volume_x": dec.get("cv"), "regime": t.get("regime"), "exit_by": t.get("exit_by"),
                 "risk_approved": approved},
        evidence=evidence.for_source("strategy:" + str(t["strategy"])), paper_position=approved,
        quality=quality(grade, _f(dec.get("cr")), unknown, failed, bool(in_play), _f(rs), direction),
        plan=strategy_plan(t), chart=strategy_chart_ref(t))


def rulebook_idea(t: dict, cost_table: Optional[dict] = None, cfg: Optional[dict] = None) -> Optional[Idea]:
    """An open entry of the rulebook engine. Its filters are all-or-nothing, so an open trade has passed every one of them."""
    if t.get("status") != "OPEN":
        return None
    option = t.get("kind") == "option"
    direction = int(t["dir"])
    entry = _f(t.get("entry_price"))
    qty = _f(t.get("qty"))
    risk = _f(t.get("risk_amount"))
    ratio = None
    if option and entry and qty and risk:
        ratio = round(costs.round_trip("option", qty, entry, entry, cost_table) / risk, 2)
    name = str(t["name"])
    return Idea(
        id="rulebook:" + str(t["id"]), desk=rulebook_desk(t), source="rulebook", name=name, symbol=str(t.get("symbol") or name),
        direction=direction, action=str(t.get("action") or ("BUY" if direction == 1 else "SELL")), grade="take", status="OPEN",
        signaled_at=t.get("entry_time"), entry=_round(entry), stop=_round(t.get("stop_price")),
        target=None if option else _round(t.get("target_underlying")), qty=qty, risk=risk, cost_ratio=ratio,
        checks=[], blockers=[], why=[str(x) for x in (t.get("reasons") or [])][:6],
        context={"underlying_entry": t.get("underlying_entry"), "underlying_stop": t.get("stop_underlying"),
                 "underlying_target": t.get("target_underlying"), "exit_by": t.get("exit_by"), "phase": t.get("phase")},
        evidence=evidence.for_source("rulebook"), contract=t.get("contract"), paper_position=True,
        quality=quality("take", ratio, 0, 0, False, None, direction), plan=rulebook_plan(t, cfg), chart=rulebook_chart_ref(t))


def scan_ideas(scan: dict, min_score: int, date_iso: str) -> list:
    """Calls and puts the options scanner is looking at. Takes always; watches only when they are close to a take."""
    out = []
    for r in (scan or {}).get("items") or []:
        idea = r.get("idea") or {}
        if not r.get("side") or idea.get("error") or not idea.get("contract") or r.get("grade") not in ("take", "watch"):
            continue
        if r["grade"] == "watch" and (r.get("score") or 0) < min_score:
            continue
        agrees = r.get("market_agrees")
        checks = [
            Check("range", CHECK_LABELS["range"], "pass" if str(r.get("state", "")).endswith("broke out") else "fail"),
            Check("score", CHECK_LABELS["score"], "pass" if (r.get("score") or 0) >= min_score else "fail"),
            Check("mkt", CHECK_LABELS["mkt"], "pass" if agrees is True else "fail" if agrees is False else "unknown"),
        ]
        direction = 1 if r["side"] == "CE" else -1
        unknown = sum(1 for c in checks if c.state == "unknown")
        failed = sum(1 for c in checks if c.state == "fail")
        ratio = _f(idea.get("cost_ratio"))
        out.append(Idea(
            id=f"scan:{date_iso}:{r.get('id')}:{r['side']}", desk="index_options", source="options_scan",
            name=f"{r.get('name') or r.get('id')} {r['side']}", symbol=str(idea.get("symbol") or r.get("id")), direction=direction,
            action="BUY", grade=r["grade"], status="CANDIDATE", signaled_at=(scan or {}).get("asof"), entry=_round(idea.get("premium")),
            stop=_round(idea.get("stop")), target=_round(idea.get("target")), qty=_f(idea.get("lot")), risk=_round(idea.get("risk_per_lot")),
            cost_ratio=ratio, checks=[c.to_dict() for c in checks], blockers=[str(x) for x in (r.get("why_not") or [])][:8],
            why=[f["text"] for f in (r.get("factors") or []) if f.get("ok")][:6],
            context={"spot": r.get("spot"), "score": r.get("score"), "spread_pct": idea.get("spread_pct"), "oi": idea.get("oi"),
                     "quote_age_s": idea.get("quote_age_s"), "expiry": idea.get("expiry"), "warnings": r.get("warnings") or []},
            evidence=evidence.for_source("options_scan"), contract=idea.get("contract"), paper_position=False,
            quality=quality(r["grade"], ratio, unknown, failed, False, None, direction), plan=scan_plan(r, idea),
            chart={"file": "chartlive_options.json", "key": f"scan:{date_iso}:{r.get('id')}:{r['side']}"}))
    return out


# ── positions ────────────────────────────────────────────────────────────────
def rulebook_position(t: dict, now: datetime) -> Position:
    status = t.get("quote_status")
    age = _age(now, t.get("last_update"))
    if status not in ("FRESH", "STALE"):
        status = "FRESH" if age is not None and age <= THRESHOLDS["price_stale_s"] else "UNKNOWN"
    risk, pnl = _f(t.get("risk_amount")), _f(t.get("open_pnl"))
    return Position(
        id="rulebook:" + str(t["id"]), desk=rulebook_desk(t), source="rulebook", name=str(t["name"]), symbol=str(t.get("symbol") or t["name"]),
        direction=int(t["dir"]), action=str(t.get("action") or "BUY"), qty=float(t.get("remaining") or t["qty"]),
        entry_price=float(t["entry_price"]), entry_time=str(t["entry_time"]), stop=_round(t.get("stop_price")),
        target=None if t.get("kind") == "option" else _round(t.get("target_underlying")), last_price=_round(t.get("last_price")),
        last_update=t.get("last_update"), open_pnl=_round(pnl), risk=risk,
        open_r=round(pnl / risk, 2) if pnl is not None and risk else None, quote_status=status, contract=t.get("contract"),
        chart=rulebook_chart_ref(t))


def strategy_position(t: dict, now: datetime, desk_updated: Optional[str]) -> Position:
    age = _age(now, desk_updated)
    status = "FRESH" if age is not None and age <= THRESHOLDS["price_stale_s"] else "STALE" if age is not None else "UNKNOWN"
    risk, pnl = _f(t.get("risk")), _f(t.get("open_pnl"))
    direction = int(t["dir"])
    return Position(
        id="strategy:" + str(t["id"]), desk=strategy_desk(t), source="strategy:" + str(t["strategy"]), name=str(t["name"]),
        symbol=str(t["symbol"]), direction=direction, action="BUY" if direction == 1 else "SELL", qty=float(t["qty"]),
        entry_price=float(t["entry"]), entry_time=str(t["entry_t"]), stop=_round(t.get("stop")), target=_round(t.get("target")),
        last_price=_round(t.get("last")), last_update=desk_updated, open_pnl=_round(pnl), risk=risk,
        open_r=round(pnl / risk, 2) if pnl is not None and risk else None, quote_status=status, chart=strategy_chart_ref(t))


def _ended(t: dict, kind: str) -> Optional[dict]:
    if t.get("status") != "CLOSED":
        return None
    if kind == "rulebook":
        return {"id": "rulebook:" + str(t["id"]), "desk": rulebook_desk(t), "name": t["name"], "action": t.get("action"),
                "pnl": _round(t.get("pnl")), "r": _round(t.get("r"), 2), "result": t.get("result"), "exit_type": t.get("exit_type"),
                "exit_time": t.get("exit_time"), "pnl_is_net": False}
    if not t.get("risk_approved", True):
        return None
    return {"id": "strategy:" + str(t["id"]), "desk": strategy_desk(t), "name": t["name"], "action": "BUY" if t["dir"] == 1 else "SELL",
            "pnl": _round(t.get("net")), "r": _round(t.get("net_r"), 2), "result": t.get("result"), "exit_type": t.get("exit_type"),
            "exit_time": t.get("exit_t"), "pnl_is_net": True}


# ── the whole document ───────────────────────────────────────────────────────
def build(now: datetime, cfg: dict, feed: Optional[dict], desk_state: Optional[dict], extra: Optional[dict] = None) -> dict:
    """The live document. `feed` is the rulebook feed, `desk_state` the strategy desk's state; either may be missing early in a session."""
    feed = feed or {}
    desk_state = desk_state or {}
    status = feed.get("status") or {}
    iso = now.date().isoformat()
    cost_table = costs.from_config(cfg)
    shared = {"max_open_positions": 8, "max_open_risk": 17500.0, "max_daily_loss": 3500.0}
    shared.update({k: v for k, v in ((cfg or {}).get("portfolio") or {}).items() if k in shared})

    rb_today = [t for t in (feed.get("trades") or []) if t.get("date") == iso]
    st_today = [t for t in (desk_state.get("trades") or []) if t.get("date") == iso]
    rb_open = [t for t in rb_today if t.get("status") == "OPEN"]
    st_open = [t for t in st_today if t.get("status") == "OPEN" and t.get("risk_approved", True)]

    positions, ideas, ended = [], [], []
    for t in rb_open:
        try:
            positions.append(rulebook_position(t, now))
            idea = rulebook_idea(t, cost_table, cfg)
            if idea:
                ideas.append(idea)
        except (KeyError, TypeError, ValueError):
            continue
    for t in st_open:
        try:
            positions.append(strategy_position(t, now, desk_state.get("updated")))
        except (KeyError, TypeError, ValueError):
            continue
    for t in st_today:
        try:
            idea = strategy_idea(t, now)
            if idea:
                ideas.append(idea)
        except (KeyError, TypeError, ValueError):
            continue
    min_score = int(((cfg or {}).get("options_scan") or {}).get("min_score", 4))
    ideas.extend(scan_ideas(desk_state.get("options_scan"), min_score, iso))
    ideas.sort(key=lambda i: (-i.quality, _neg_time(i.signaled_at)))
    ideas = ideas[:MAX_IDEAS]

    for t in rb_today:
        row = _ended(t, "rulebook")
        if row:
            ended.append(row)
    for t in st_today:
        row = _ended(t, "strategy")
        if row:
            ended.append(row)
    ended.sort(key=lambda r: r.get("exit_time") or "", reverse=True)

    open_risk = sum(p.risk or 0.0 for p in positions)
    realized = sum(_f(t.get("pnl")) or 0.0 for t in rb_today if t.get("status") == "CLOSED") + \
        sum(_f(t.get("net")) or 0.0 for t in st_today if t.get("status") == "CLOSED" and t.get("risk_approved", True))
    open_pnl = sum(p.open_pnl or 0.0 for p in positions)

    health = desk_state.get("health") or {}
    scan = desk_state.get("scan") or {}
    dstatus = desk_state.get("status") or {}
    leaders = desk_state.get("leaders") or {}
    movers = (desk_state.get("movers") or {}).get("list") or []
    doc: dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": now.isoformat(timespec="seconds"),
        "date": iso,
        "thresholds": dict(THRESHOLDS),
        "session": {k: status.get(k) for k in ("phase", "running", "heartbeat", "session", "updated")},
        "brokers": status.get("brokers") or {},
        "health": {"decision": health.get("decision"), "asof": health.get("asof"), "blocking": health.get("blocking") or [],
                   "items": health.get("items") or []},
        "freshness": {"prices_asof": dstatus.get("cycle_at") or status.get("updated"), "last_candle_end": dstatus.get("bar_end"),
                      "quotes_scan_asof": scan.get("at"), "desk_updated": desk_state.get("updated")},
        "risk": {"limits": shared, "open_positions": len(positions), "open_risk": round(open_risk, 2), "realized_pnl": round(realized, 2),
                 "open_pnl": round(open_pnl, 2), "day_pnl": round(realized + open_pnl, 2)},
        "desks": [{"id": k, "name": v} for k, v in DESK_NAMES.items()],
        "ideas": [i.to_dict() for i in ideas],
        "positions": [p.to_dict() for p in positions],
        "ended": ended[:MAX_ENDED],
        "market": {
            "tiles": [{k: tile.get(k) for k in ("id", "name", "book", "note", "price", "vwap", "prev_close", "range_high", "range_low", "in_trade", "done")}
                      for tile in (status.get("tiles") or [])][:24],
            "regime": desk_state.get("regime"),
            "movers": movers[:8],
            "results": desk_state.get("results"),
            "leaders": {"ok": leaders.get("ok"), "asof": leaders.get("asof"), "universe": leaders.get("universe"),
                        "n_leaders": leaders.get("n_leaders"), "breadth": leaders.get("breadth")} if leaders else None,
            "watchlist_count": len(desk_state.get("watchlist") or []),
        },
        "evidence": {"system": {"headline": evidence.EVIDENCE["system"]["headline"], "avg_r": evidence.EVIDENCE["system"]["avg_r"],
                                "asof": evidence.ASOF, "source": evidence.SOURCE}},
    }
    if extra:
        doc.update(extra)
    return _clean(doc)


def _clean(value: Any) -> Any:
    """Replaces any NaN or infinity anywhere in the document with null, so the published file is always valid JSON."""
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else None
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def _neg_time(stamp: Optional[str]) -> float:
    """Sort key that puts newer signals first among equal-quality ideas."""
    try:
        return -datetime.fromisoformat(stamp).timestamp()
    except (TypeError, ValueError):
        return 0.0
