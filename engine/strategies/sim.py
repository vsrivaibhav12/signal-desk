"""Turns strategy signals into complete trades: size, stop, target, exit and cost.

The same function is used for the backtest (all past days) and for the live desk (today only),
so a live trade follows exactly the rules that were tested. It never places an order.
"""
import copy

from .. import clock
from . import decide, risk
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
    "portfolio": {
        "enabled": True, "require_take_grade": True, "max_open_positions": 5,
        "max_open_positions_per_symbol": 1, "max_total_open_risk": 2500.0,
        "max_daily_loss": 1500.0,
    },
    "execution": {"entry_mode": "next_open"},
}


def settings(cfg):
    out = copy.deepcopy(DEFAULTS)
    for k, v in ((cfg or {}).get("strategies") or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    out["decide"] = decide.settings(cfg)
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


def _open_trade(strat, inst, S, i, sig, cfg, entry_i=None):
    """Create a trade from a completed signal candle and an execution candle.

    i is always the signal candle. entry_i may point to the next candle for a
    more conservative fill model; chart identity remains tied to the signal.
    """
    entry_i = i if entry_i is None else entry_i
    d, c = sig["dir"], (S.c[i] if entry_i == i else S.o[entry_i])
    p_trail = params(cfg, strat, inst["kind"]).get("trail", 3)
    stop = sig["stop"]
    # If the market gaps through the signal's protective stop before the modeled
    # execution, the setup is no longer valid. Do not widen the stop to force a fill.
    if d * (c - stop) <= 0:
        return None
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
        qty = int(inst["lot"])
    else:
        qty = risk.quantity_for_risk(c, unit, cfg["risk_per_trade"], cfg["max_position_value"])
        if qty < 1:
            return None
    entry_ts = S.t[i] + 300 if entry_i == i else S.t[entry_i]
    return {
        "id": f"{strat['id']}|{inst['name']}|{S.t[i]}", "strategy": strat["id"], "symbol": inst["symbol"],
        "name": inst["name"], "kind": inst["kind"], "dir": d, "date": S.day[i].isoformat(),
        "signal_t": _iso(S.t[i] + 300), "signal_ts": S.t[i] + 300,
        "entry_t": _iso(entry_ts), "entry_ts": entry_ts, "entry": round(c, 2),
        "stop": round(stop, 2), "target": round(target, 2) if target is not None else None,
        "qty": qty, "risk": round(qty * unit, 2), "why": sig["why"], "regime": sig["regime"],
        "rule_exit": sig.get("exit"), "trail": p_trail, "status": "OPEN", "last": round(c, 2), "open_pnl": 0.0,
        "exit_by": cfg["exit_by"],
        "_lines": sig.get("lines") or [], "_box": sig.get("box"), "_i0": i,      # used for the chart, then removed
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


SERIES_ATTR = {"vwap": "vwap", "ema9": "ema9", "ema21": "ema21", "ema20": "ema20", "ema50": "ema50",
               "bb_up": "bb_up", "bb_mid": "bb_mid", "bb_lo": "bb_lo"}


def build_chart(S, i_sig, i_end, overlays, lines, box, ep, xp=None, before=30, after=4):
    """Everything needed to draw a trade: the candles around it, indicator lines, trigger levels, entry and exit.

    Candles are [open, high, low, close]. e is the trigger candle, x the exit candle (None while open).
    """
    day = S.day[i_sig]
    j0, j1 = max(S.starts[day], i_sig - before), min(S.ends[day], i_end + after)
    rng = range(j0, j1 + 1)
    ch = {"t0": S.t[j0], "d": [(S.t[j] - S.t[j0]) // 300 for j in rng],
          "k": [[round(S.o[j], 2), round(S.h[j], 2), round(S.l[j], 2), round(S.c[j], 2)] for j in rng],
          "e": i_sig - j0, "x": (i_end - j0) if xp is not None else None, "ep": round(ep, 2),
          "xp": round(xp, 2) if xp is not None else None,
          "ln": [[lab, round(px, 2)] for lab, px in lines],
          "ov": {name: [None if getattr(S, SERIES_ATTR[name])[j] is None else round(getattr(S, SERIES_ATTR[name])[j], 2) for j in rng]
                 for name in overlays}}
    if box:
        ch["bx"] = [max(0, box["i0"] - j0), max(0, box["i1"] - j0), round(box["lo"], 2), round(box["hi"], 2), box["label"]]
    return ch


def _finish(tr, S, strat, i_end, closed, want):
    lines, box, i0 = tr.pop("_lines"), tr.pop("_box"), tr.pop("_i0")
    if want:
        tr["_chart"] = build_chart(S, i0, i_end, strat.get("overlay", []), lines, box, tr["entry"], tr["exit"] if closed else None)
    return tr


def simulate(S, inst, strat, cfg, days=None, live_day=None, chart_from=None, market=None):
    """All trades one strategy would have taken in one symbol.

    days     the dates to look at (None = every date in the data)
    live_day a date allowed to end the data with a trade still open (live use). Any other
             day's open trade is closed at that day's last candle.
    chart_from  from this date on, each trade also gets a "_chart" (candles and levels to draw it)
    market  the Nifty series, used for the "does Nifty agree" check that grades each trade
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
        want = chart_from is not None and day >= chart_from
        for i in range(a, b + 1):
            end = minute_of_day(S.t[i]) + 5
            if tr is not None:
                done = _manage(tr, S, i, cfg, exit_min)
                if done:
                    out.append(_finish(done, S, strat, i, True, want))
                    tr = None
                    continue
            if tr is None and w0 <= end <= w1 and end < exit_min and i >= cfg["min_bars"] and (not cap or taken < cap):
                sig = strat["scan"](S, i, p)
                if sig:
                    mode = (cfg.get("execution") or {}).get("entry_mode", "next_open")
                    provisional = False
                    if mode == "next_open":
                        # Signal is known only after candle i closes; the fill is the next candle's open.
                        if i >= b:
                            if day != live_day:
                                continue
                            # Live, and that candle has not been drawn yet. Report the signal now instead of a
                            # candle late, filled at this candle's close (the best guess of the next open). The next
                            # pass replays the day and replaces it with the real open; the trade keeps its id.
                            entry_i, provisional = i, True
                        else:
                            entry_i = i + 1
                    elif mode == "signal_close":
                        entry_i = i
                    else:
                        raise ValueError(f"Unsupported execution.entry_mode: {mode}")
                    tr = _open_trade(strat, inst, S, i, sig, cfg, entry_i=entry_i)
                    if tr:
                        taken += 1
                        tr["fill_model"] = "provisional" if provisional else mode
                        tr["dec"] = decide.assess(S, i, tr, trade_cost(tr["kind"], tr["qty"], tr["entry"], tr["entry"], cfg), market,
                                                  cfg["decide"])
        if tr is not None:
            if day == live_day:
                out.append(_finish(tr, S, strat, b, False, want))
            else:
                out.append(_finish(_close(tr, S, b, S.c[b], "TIME", "Closed at the last candle of the day", cfg), S, strat, b, True, want))
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


def market_series(series, insts):
    """The Nifty 50 series, if it is among the loaded data."""
    for sym, inst in insts.items():
        if inst["name"] == "Nifty 50" and sym in series:
            series[sym].symbol = sym
            return series[sym]
    return None


def run_all(series, insts, cfg, days=None, live_day=None, only=None, log=print, chart_from=None, strategies=None):
    """series: {symbol: Series}. Returns {strategy id: [trades]}."""
    pool = strategies or STRATEGIES
    res = {s["id"]: [] for s in pool if not only or s["id"] in only}
    mkt = market_series(series, insts)
    for n, (sym, S) in enumerate(series.items()):
        inst = insts.get(sym)
        if not inst:
            continue
        for strat in pool:
            if strat["id"] in res:
                res[strat["id"]] += simulate(S, inst, strat, cfg, days, live_day, chart_from, mkt)
        if (n + 1) % 50 == 0:
            log(f"    scanned {n + 1} of {len(series)} symbols")
    decide.finalize([t for v in res.values() for t in v], cfg["decide"])
    for v in res.values():
        v.sort(key=lambda t: t["entry_ts"])
    return res
