"""Portfolio-level admission control for simulated strategy trades.

This module does not place orders. It evaluates a complete, chronological set of
candidate trades and marks which candidates could coexist in one constrained
paper portfolio. Exits are processed before entries at the same timestamp.
"""
from collections import Counter
from datetime import datetime

DEFAULTS = {
    "enabled": True,
    "require_take_grade": True,
    "max_open_positions": 5,
    "max_open_positions_per_symbol": 1,
    "max_total_open_risk": 2500.0,
    "max_daily_loss": 1500.0,
}

SHARED_DEFAULTS = {
    "enabled": True,
    "max_open_positions": 8,
    "max_open_risk": 17500.0,
    "max_daily_loss": 3500.0,
}


def settings(cfg):
    out = dict(DEFAULTS)
    if cfg and "portfolio" in cfg:
        out.update(cfg.get("portfolio") or {})
    elif cfg and "enabled" in cfg:
        out.update(cfg)
    return out


def _timestamp(tr, key, fallback_key):
    for value in (tr.get(key), tr.get(fallback_key), tr.get("entry_time") if key == "entry_ts" else None,
                  tr.get("exit_time") if key == "exit_ts" else None):
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            try:
                return int(datetime.fromisoformat(str(value)).timestamp())
            except (TypeError, ValueError):
                continue
    return 0


def quantity_for_risk(price, stop_distance, risk_budget, position_cap):
    """Whole-share quantity that respects both the rupee risk and capital caps.

    Returns zero when even one share breaches either constraint. Callers should
    skip the trade instead of forcing a minimum quantity of one.
    """
    try:
        price, stop_distance = float(price), float(stop_distance)
        risk_budget, position_cap = float(risk_budget), float(position_cap)
    except (TypeError, ValueError):
        return 0
    if price <= 0 or stop_distance <= 0 or risk_budget <= 0 or position_cap <= 0:
        return 0
    risk_qty = int(risk_budget // stop_distance)
    value_qty = int(position_cap // price)
    return max(0, min(risk_qty, value_qty))

def annotate(trades, cfg=None, external_trades=None, shared_cfg=None):
    """Annotate strategy candidates under both strategy-book and shared limits.

    External trades are positions from the rulebook engine. They are never
    re-admitted here; their already-approved entries/exits simply consume shared
    position count, risk and daily-loss budget at their actual timestamps.
    """
    lim = settings(cfg)
    own_enabled = bool(lim.get("enabled", True))
    shared = dict(SHARED_DEFAULTS)
    if shared_cfg is not None:
        shared.update(shared_cfg or {})
    shared_enabled = shared_cfg is not None and bool(shared.get("enabled", True))
    external_trades = external_trades or []

    for tr in trades:
        tr["risk_approved"] = not own_enabled
        tr["risk_reasons"] = []

    by_day = {}
    for tr in trades:
        by_day.setdefault(tr.get("date", ""), {"candidates": [], "external": []})["candidates"].append(tr)
    for tr in external_trades:
        if tr.get("status") not in ("OPEN", "CLOSED") or tr.get("risk_approved", True) is False:
            continue
        day = tr.get("date", "")
        if day:
            by_day.setdefault(day, {"candidates": [], "external": []})["external"].append(tr)

    for _, group in by_day.items():
        events = []
        for tr in group["external"]:
            entry_ts = _timestamp(tr, "entry_ts", "entry_t")
            exit_ts = _timestamp(tr, "exit_ts", "exit_t")
            ext_id = "external:" + str(tr.get("id", id(tr)))
            normalized = {
                "risk": float(tr.get("risk", tr.get("risk_amount", 0.0)) or 0.0),
                "symbol": tr.get("symbol") or tr.get("unit") or tr.get("name"),
                "source": "external", "id": ext_id,
            }
            events.append((entry_ts, 1, 0, ext_id, "external_entry", (tr, normalized)))
            if tr.get("status") == "CLOSED" and exit_ts:
                events.append((exit_ts, 0, 0, ext_id, "external_exit", (tr, normalized)))

        for tr in group["candidates"]:
            entry_ts = _timestamp(tr, "entry_ts", "entry_t")
            exit_ts = _timestamp(tr, "exit_ts", "exit_t")
            tid = str(tr.get("id", ""))
            grade = (tr.get("dec") or {}).get("g", "take")
            priority = 0 if grade == "take" else 1
            events.append((entry_ts, 1, 1, f"{priority}:{tr.get('strategy','')}:{tid}", "candidate_entry", tr))
            if tr.get("status") == "CLOSED" and exit_ts:
                events.append((exit_ts, 0, 0, tid, "candidate_exit", tr))

        active_own = {}
        active_all = {}
        realized_own = 0.0
        realized_all = 0.0
        for _, _, _, event_id, event_type, payload in sorted(events, key=lambda x: (x[0], x[1], x[2], x[3])):
            if event_type == "external_entry":
                ext, normalized = payload
                active_all[event_id] = normalized
                continue
            if event_type == "external_exit":
                ext, _ = payload
                active_all.pop(event_id, None)
                realized_all += float(ext.get("net", ext.get("pnl", 0.0)) or 0.0)
                continue
            if event_type == "candidate_exit":
                tr = payload
                tid = str(tr.get("id", ""))
                if tr.get("risk_approved") and tid in active_own:
                    active_own.pop(tid, None)
                    active_all.pop("candidate:" + tid, None)
                    pnl = float(tr.get("net", 0.0) or 0.0)
                    realized_own += pnl
                    realized_all += pnl
                continue

            tr = payload
            tid = str(tr.get("id", ""))
            reasons = []
            grade = (tr.get("dec") or {}).get("g", "take")
            candidate_risk = float(tr.get("risk", 0.0) or 0.0)
            if own_enabled:
                if lim.get("require_take_grade", True) and grade != "take":
                    reasons.append("signal_not_take_grade")
                if realized_own <= -float(lim["max_daily_loss"]):
                    reasons.append("daily_loss_limit")
                if len(active_own) >= int(lim["max_open_positions"]):
                    reasons.append("max_open_positions")
                same_symbol = sum(1 for x in active_own.values() if x.get("symbol") == tr.get("symbol"))
                if same_symbol >= int(lim["max_open_positions_per_symbol"]):
                    reasons.append("symbol_already_exposed")
                own_risk = sum(float(x.get("risk", 0.0) or 0.0) for x in active_own.values())
                if own_risk + candidate_risk > float(lim["max_total_open_risk"]) + 1e-9:
                    reasons.append("max_total_open_risk")

            if shared_enabled:
                if realized_all <= -float(shared["max_daily_loss"]):
                    reasons.append("shared_daily_loss_limit")
                if len(active_all) >= int(shared["max_open_positions"]):
                    reasons.append("shared_max_open_positions")
                shared_risk = sum(float(x.get("risk", 0.0) or 0.0) for x in active_all.values())
                if shared_risk + candidate_risk > float(shared["max_open_risk"]) + 1e-9:
                    reasons.append("shared_max_open_risk")

            if candidate_risk <= 0:
                reasons.append("invalid_risk")
            tr["risk_approved"] = not reasons
            tr["risk_reasons"] = reasons
            if not reasons:
                active_own[tid] = tr
                active_all["candidate:" + tid] = {"risk": candidate_risk, "symbol": tr.get("symbol")}

    return trades


def summary(trades):
    """Compact audit summary of portfolio decisions."""
    approved = [t for t in trades if t.get("risk_approved")]
    rejected = [t for t in trades if not t.get("risk_approved")]
    reasons = Counter(reason for t in rejected for reason in t.get("risk_reasons", []))
    closed = [t for t in approved if t.get("status") == "CLOSED"]
    gross = sum(float(t.get("gross", 0.0) or 0.0) for t in closed)
    costs = sum(float(t.get("cost", 0.0) or 0.0) for t in closed)
    net = sum(float(t.get("net", 0.0) or 0.0) for t in closed)
    wins = sum(1 for t in closed if float(t.get("net", 0.0) or 0.0) > 0)
    losses = sum(1 for t in closed if float(t.get("net", 0.0) or 0.0) < 0)
    gw = sum(float(t.get("net", 0.0) or 0.0) for t in closed if float(t.get("net", 0.0) or 0.0) > 0)
    gl = -sum(float(t.get("net", 0.0) or 0.0) for t in closed if float(t.get("net", 0.0) or 0.0) < 0)
    equity = peak = max_dd = 0.0
    for tr in sorted(closed, key=lambda t: (_timestamp(t, "exit_ts", "exit_t"), t.get("id", ""))):
        equity += float(tr.get("net", 0.0) or 0.0)
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "candidates": len(trades), "approved": len(approved), "rejected": len(rejected),
        "closed": len(closed), "open": sum(1 for t in approved if t.get("status") == "OPEN"),
        "wins": wins, "losses": losses,
        "win_rate": round(wins / (wins + losses) * 100, 1) if wins + losses else None,
        "gross_pnl": round(gross, 2), "costs": round(costs, 2), "net_pnl": round(net, 2),
        "profit_factor": round(gw / gl, 2) if gl else (None if not gw else "inf"),
        "max_drawdown": round(max_dd, 2), "rejection_reasons": dict(reasons),
    }
