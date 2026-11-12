"""Is this signal a real trade, or just something to watch?

A signal is graded "take" only if it passes four checks. They were chosen because, on 60 days of real prices,
each one separated better trades from worse ones in BOTH halves of the period, and none of them is a tuned number:

  cost    the round-trip cost is no more than 25% of the amount risked. A stop too tight for the costs means the
          trade starts the day behind; this was by far the biggest single factor.
  volume  the stock is trading at 2x its usual volume or more for this time of day (it is "in play").
          Indices have no volume, so they pass.
  market  Nifty is on the same side of its own average price line (VWAP) as the trade.
  conflict  no other strategy has already taken the opposite side of the same stock today.

Everything else is "watch". The grade says how good the setup is, not that it is profitable: in the backtest even
trades that pass all four checks lost a little after costs. Nothing here places an order.
"""
DEFAULTS = {"max_cost_ratio": 0.25, "min_volume": 2.0, "need_market": True, "no_conflict": True}

CHECK_NAMES = {"cost": "Stop wide enough for the costs", "vol": "Heavy volume", "mkt": "Nifty agrees", "conf": "No conflicting signal"}


def settings(cfg):
    out = dict(DEFAULTS)
    out.update(((cfg or {}).get("strategies") or {}).get("decide") or {})
    return out


def assess(S, i, tr, cost_est, mkt, dc):
    """Grade one signal. Missing required inputs fail closed as data-unknown."""
    d = tr["dir"]
    cr = cost_est / tr["risk"] if tr["risk"] else 9.0
    is_index = tr.get("kind") == "index" or str(tr.get("symbol", "")).endswith("-INDEX")
    cv = S.cumratio[i] if S.has_volume else None
    nm = None
    same_as_market = mkt is not None and tr.get("symbol") == getattr(mkt, "symbol", None)
    if dc["need_market"] and not same_as_market:
        if mkt is not None:
            j = mkt.index_of(S.t[i])
            if j is not None and mkt.vwap[j] is not None:
                nm = d * (mkt.c[j] - mkt.vwap[j]) > 0

    fails = []
    unknown = []
    if cr > dc["max_cost_ratio"]:
        fails.append("cost")
    # Index volume is not a meaningful input. For shares, missing volume is not a pass.
    if not is_index:
        if cv is None:
            unknown.append("vol_data")
        elif cv < dc["min_volume"]:
            fails.append("vol")
    if dc["need_market"] and not same_as_market:
        if nm is None:
            unknown.append("mkt_data")
        elif nm is False:
            fails.append("mkt")
    fails.extend(unknown)
    return {"g": "watch" if fails else "take", "f": fails, "unknown": unknown,
            "cr": round(cr, 2), "cv": None if cv is None else round(cv, 1), "nm": nm, "cf": 0}


def finalize(trades, dc):
    """Adds the conflict check: another strategy already on the opposite side of the same stock today."""
    by = {}
    for t in trades:
        by.setdefault((t["date"], t["symbol"]), []).append(t)
    for lst in by.values():
        if len(lst) < 2:
            continue
        for t in lst:
            n = sum(1 for o in lst if o["dir"] == -t["dir"] and o["entry_ts"] <= t["entry_ts"])
            dec = t.get("dec")
            if not dec:
                continue
            dec["cf"] = n
            if n and dc["no_conflict"] and "conf" not in dec["f"]:
                dec["f"].append("conf")
                dec["g"] = "watch"
    return trades
