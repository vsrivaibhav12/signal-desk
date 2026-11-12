"""Turns strategy signals into complete trades: size, stop, target, exit and cost.

The same function is used for the backtest (all past days) and for the live desk (today only),
so a live trade follows exactly the rules that were tested. It never places an order.
"""
import copy

from .. import clock
from .defs import EXITS, STRATEGIES, window_minutes
from .indicators import hhmm_to_min, minute_of_day

DEFAULTS = {
    "exit_by": "15:15",                 # everything is closed by this time (the article says by 3:20)
    "risk_per_trade": 500,              # rupees risked on one stock trade; sets the quantity
    "max_position_value": 40000,        # never size a stock position above this many rupees
    "min_risk_pct": {"stock": 0.2, "index": 0.1},   # a stop closer than this % is moved out to it
    "min_bars": 100,                    # candles needed before indicators are trusted
    "max_trades_per_symbol_day": 0,     # 0 = no limit
    "index_lots": {"NIFTY": 65, "SENSEX": 20, "BANKNIFTY": 30, "FINNIFTY": 60, "MIDCPNIFTY": 120},
    # Costs are estimates of brokerage, taxes and the gap between the price you see and the price you get.
    "costs": {"stock_per_trade": 40.0, "stock_pct": 0.03, "stock_slippage_pct": 0.03,
              "index_per_trade": 80.0, "index_pct": 0.02, "index_slippage_pct": 0.01},
    "params": {},                       # per-strategy overrides, e.g. {"momentum": {"stock": {"rsi": 65}}}
}


def settings(cfg):
    out = copy.deepcopy(DEFAULTS)
    for k, v in ((cfg or {}).get("strategies") or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def params(cfg, strat, kind):
    p = dict(strat[kind])
    p.update(((cfg.get("params") or {}).get(strat["id"]) or {}).get(kind) or {})
    return p


def trade_cost(kind, qty, entry, exit_, cfg):
    c = cfg["costs"]
    k = "stock" if kind == "stock" else "index"
    return c[f"{k}_per_trade"] + qty * (entry + exit_) * (c[f"{k}_pct"] / 2 + c[f"{k}_slippage_pct"]) / 100


def _iso(t):
    return clock.from_ts(t).isoformat(timespec="seconds")


def _open_trade(strat, inst, S, i, sig, cfg):
    d, c = sig["dir"], S.c[i]
    stop = sig["stop"]
    floor = c * cfg["min_risk_pct"][inst["kind"]] / 100
    if d * (c - stop) < floor:
        stop = c - d * floor
    unit = d * (c - stop)
    if unit <= 0:
        return None
    target = sig["target"]
    if sig["target_r"]:
        target = c + d * sig["target_r"] * unit
    if target is not None and d * (target - c) <= 0:
        return None
    if inst["kind"] == "index":
        qty = inst["lot"]
    else:
        qty = max(1, min(int(cfg["risk_per_trade"] // unit), int(cfg["max_position_value"] // c) or 1))
    return {
        "id": f"{strat['id']}|{inst['name']}|{S.t[i]}", "strategy": strat["id"], "symbol": inst["symbol"],
        "name": inst["name"], "kind": inst["kind"], "dir": d, "date": S.day[i].isoformat(),
        "entry_t": _iso(S.t[i] + 300), "entry_ts": S.t[i] + 300, "entry": round(c, 2),
        "stop": round(stop, 2), "target": round(target, 2) if target is not None else None,
        "qty": qty, "risk": round(qty * unit, 2), "why": sig["why"], "regime": sig["regime"],
        "rule_exit": sig.get("exit"), "status": "OPEN", "last": round(c, 2), "open_pnl": 0.0,
        "exit_by": cfg["exit_by"],
    }


def _close(tr, S, i, price, kind, reason, cfg):
    d = tr["dir"]
    gross = d * (price - tr["entry"]) * tr["qty"]
    cost = trade_cost(tr["kind"], tr["qty"], tr["entry"], price, cfg)
    net = gross - cost
    tr.update({"status": "CLOSED", "exit_t": _iso(S.t[i] + 300), "exit_ts": S.t[i] + 300, "exit": round(price, 2), "exit_type": kind,
               "exit_reason": reason, "gross": round(gross, 2), "cost": round(cost, 2), "net": round(net, 2),
               "pnl": round(gross, 2), "r": round(net / tr["risk"], 3) if tr["risk"] else 0.0,
               "net_r": round(net / tr["risk"], 3) if tr["risk"] else 0.0, "last": round(price, 2),
               "open_pnl": None, "result": "WIN" if net > 0 else "LOSS" if net < 0 else "FLAT"})
    return tr


def _manage(tr, S, i, cfg, exit_min):
    """Looks at candle i for an open trade. Returns the closed trade, or None if it stays open."""
    d, o, h, l, c = tr["dir"], S.o[i], S.h[i], S.l[i], S.c[i]
    # Stop first: if one candle touches both stop and target we assume the worse thing happened.
    if (l <= tr["stop"]) if d == 1 else (h >= tr["stop"]):
        price = min(tr["stop"], o) if d == 1 else max(tr["stop"], o)
        return _close(tr, S, i, price, "STOP", f"Stop-loss hit at {price:,.2f}", cfg)
    if tr["target"] is not None and (h >= tr["target"] if d == 1 else l <= tr["target"]):
        price = max(tr["target"], o) if d == 1 else min(tr["target"], o)
        return _close(tr, S, i, price, "TARGET", f"Target reached at {price:,.2f}", cfg)
    if tr.get("rule_exit"):
        why = EXITS[tr["rule_exit"]](S, i, tr)
        if why:
            return _close(tr, S, i, c, "RULE", why, cfg)
    if minute_of_day(S.t[i]) + 5 >= exit_min:
        return _close(tr, S, i, c, "TIME", f"Time exit at {cfg['exit_by']}", cfg)
    tr["last"] = round(c, 2)
    tr["open_pnl"] = round(d * (c - tr["entry"]) * tr["qty"], 2)
    return None


def simulate(S, inst, strat, cfg, days=None, live_day=None):
    """All trades one strategy would have taken in one symbol.

    days     the dates to look at (None = every date in the data)
    live_day a date allowed to end the data with a trade still open (live use). Any other
             day's open trade is closed at that day's last candle.
    """
    kind = inst["kind"]
    p = params(cfg, strat, kind)
    w0, w1 = window_minutes(strat)
    exit_min = hhmm_to_min(cfg["exit_by"])
    cap = cfg["max_trades_per_symbol_day"]
    out = []
    for day in (days if days is not None else S.days):
        if day not in S.starts:
            continue
        a, b = S.starts[day], S.ends[day]
        tr, taken = None, 0
        for i in range(a, b + 1):
            end = minute_of_day(S.t[i]) + 5
            if tr is not None:
                done = _manage(tr, S, i, cfg, exit_min)
                if done:
                    out.append(done)
                    tr = None
                    continue
            if tr is None and w0 <= end <= w1 and end < exit_min and i >= cfg["min_bars"] and (not cap or taken < cap):
                sig = strat["scan"](S, i, p)
                if sig:
                    tr = _open_trade(strat, inst, S, i, sig, cfg)
                    if tr:
                        taken += 1
        if tr is not None:
            if day == live_day:
                out.append(tr)
            else:
                out.append(_close(tr, S, b, S.c[b], "TIME", "Closed at the last candle of the day", cfg))
    return out


def build_instruments(markets, universe, lots):
    """The things we trade: Nifty 200 shares (bought or sold intraday) and index futures (one lot)."""
    out = {}
    for m in markets:
        if m.get("kind") == "index" and m.get("spot") and m["root"] in lots and m.get("enabled", True):
            out[m["spot"]] = {"symbol": m["spot"], "name": m["name"], "kind": "index", "lot": lots[m["root"]]}
    for s in universe:
        sym = f"NSE:{s}-EQ"
        out[sym] = {"symbol": sym, "name": s, "kind": "stock", "lot": 1}
    return out


def run_all(series, insts, cfg, days=None, live_day=None, only=None, log=print):
    """series: {symbol: Series}. Returns {strategy id: [trades]}."""
    res = {s["id"]: [] for s in STRATEGIES if not only or s["id"] in only}
    for n, (sym, S) in enumerate(series.items()):
        inst = insts.get(sym)
        if not inst:
            continue
        for strat in STRATEGIES:
            if strat["id"] in res:
                res[strat["id"]] += simulate(S, inst, strat, cfg, days, live_day)
        if (n + 1) % 50 == 0:
            log(f"    scanned {n + 1} of {len(series)} symbols")
    for v in res.values():
        v.sort(key=lambda t: t["entry_ts"])
    return res
