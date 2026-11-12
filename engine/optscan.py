"""Options scanner: for each index, is there a call or a put worth looking at right now, and which contract?

It reads the index candles the strategy desk already fetched, so it adds no load, and asks for a price only for the one
contract it would suggest. Everything is a paper idea: nothing is ordered.

An index scores up to five points for a CALL (the mirror image for a PUT):
    1. price has broken above the opening range          4. today's change from the previous close is positive
    2. price is above VWAP                                5. price is in the top 30% of today's range
    3. the 9 EMA is above the 21 EMA
These five overlap (they all describe the same price move), so the score is a description, not five independent proofs.

An idea is "worth taking" only when every one of these holds, and anything missing or out of date means WATCH, never TAKE:
    - the score is 4 or more, inside the entry window;
    - the index's own candles are recent;
    - for indices other than Nifty, Nifty is on the same side of its VWAP, from a recent and matching candle (PASS / FAIL / UNKNOWN);
    - the contract's price is recent, has a bid and an ask, the spread is small, and the lot can be bought at the ask;
    - costs, counting the bid-ask spread, are a small share of the risk.
"""
import math

from . import clock
from .strategies.indicators import hhmm_to_min, minute_of_day

DEFAULTS = {"min_score": 4, "target_r": 1.5, "max_cost_ratio": 0.25, "cost_per_trade": 100, "slippage_pct": 0.5,
            "strike": "ATM", "max_data_age_s": 600, "max_skew_s": 300, "max_quote_age_s": 120, "max_spread_pct": 5.0,
            "min_open_interest": 1000}


def settings(cfg):
    out = dict(DEFAULTS)
    out.update((cfg or {}).get("options_scan") or {})
    return out


def _age(S, now):
    """Seconds since the newest candle in S finished."""
    return now.timestamp() - (S.t[-1] + 300)


def nifty_vwap_side(S, today, now=None, max_age=600):
    """Nifty against its VWAP, as {"side": +1 or -1, "as_of": last candle end}. None when it is missing, from another day,
    or older than `max_age` seconds, because a stale reading is not a confirmation."""
    if S is None or not S.n or S.day[-1] != today or S.vwap[S.n - 1] is None:
        return None
    if now is not None:
        age = _age(S, now)
        if age < -10 or age > max_age:
            return None
    d = S.c[S.n - 1] - S.vwap[S.n - 1]
    return {"side": 1 if d > 0 else -1 if d < 0 else 0, "as_of": S.t[-1] + 300}


def read_index(S, today, market, now, nifty=None, max_age=600, max_skew=300):
    """What one index is doing. `nifty` is the result of nifty_vwap_side (or a bare +1 / -1 for a quick check)."""
    out = {"id": market["id"], "name": market["name"], "state": "No data", "score": 0, "side": None, "factors": [], "spot": None,
           "market_agrees": None}
    if S is None or not S.n or S.day[-1] != today:
        return out
    out["as_of"] = S.t[-1] + 300
    age = _age(S, now)
    if age < -10:
        out["state"] = "Data timestamp is in the future"
        out["spot"] = round(S.c[-1], 2)
        return out
    if age > max_age:
        out["state"] = "Data is out of date"
        out["spot"] = round(S.c[-1], 2)
        return out
    i, a = S.n - 1, S.starts[today]
    c = S.c[i]
    out["spot"] = round(c, 2)
    range_min = hhmm_to_min(market["range"][1]) - hhmm_to_min(market["range"][0])
    need = max(1, range_min // 5)
    done_min = minute_of_day(S.t[i]) + 5
    if i - a + 1 < need or done_min < hhmm_to_min(market["range"][1]):
        out["state"] = f"Opening range forms until {market['range'][1]}"
        return out
    rh, rl = max(S.h[a:a + need]), min(S.l[a:a + need])
    out.update({"range_high": round(rh, 2), "range_low": round(rl, 2)})
    pc = S.prev_close.get(today)
    chg = (c / pc - 1) * 100 if pc else 0.0
    out["change"] = round(chg, 2)
    hi, lo = max(S.h[a:]), min(S.l[a:])
    pos = (c - lo) / (hi - lo) if hi > lo else 0.5
    vwap, e9, e21 = S.vwap[i], S.ema9[i], S.ema21[i]
    up = [c > rh, vwap is not None and c > vwap, e9 is not None and e21 is not None and e9 > e21, chg > 0, pos >= 0.7]
    dn = [c < rl, vwap is not None and c < vwap, e9 is not None and e21 is not None and e9 < e21, chg < 0, pos <= 0.3]
    names_up = ["Broke above the opening range", "Above VWAP", "9 EMA above 21 EMA", "Up on the day", "Near the day's high"]
    names_dn = ["Broke below the opening range", "Below VWAP", "9 EMA below 21 EMA", "Down on the day", "Near the day's low"]
    broke_up, broke_dn = up[0], dn[0]
    if broke_up or (not broke_dn and sum(up) >= sum(dn) and c >= (rh + rl) / 2):
        side, score, flags, names = "CE", sum(up), up, names_up
    else:
        side, score, flags, names = "PE", sum(dn), dn, names_dn
    out["side"], out["score"] = side, score
    out["factors"] = [{"text": n, "ok": bool(f)} for n, f in zip(names, flags)]
    # Nifty must agree for the other indices. PASS, FAIL or UNKNOWN: missing, stale or out-of-step data is UNKNOWN.
    if market["id"] == "NIFTY":
        out["market_agrees"] = True
    else:
        if isinstance(nifty, dict):
            side_now, in_step = nifty.get("side"), abs(nifty.get("as_of", out["as_of"]) - out["as_of"]) <= max_skew
        else:
            side_now, in_step = nifty, True
        if side_now is None or not in_step:
            out["market_agrees"] = None
            out["factors"].append({"text": "Nifty on the same side of VWAP (no recent matching reading)", "ok": False})
        else:
            ok = side_now > 0 if side == "CE" else side_now < 0
            out["market_agrees"] = bool(ok)
            out["factors"].append({"text": "Nifty on the same side of VWAP", "ok": bool(ok)})
    inside = rl <= c <= rh
    out["state"] = ("Inside the opening range" if inside else "Call side broke out" if broke_up else "Put side broke out")
    out["to_break"] = {"up": round(rh - c, 2), "down": round(c - rl, 2)}
    return out


def in_window(market, now):
    m = now.hour * 60 + now.minute
    return hhmm_to_min(market["entry"][0]) <= m < hhmm_to_min(market["entry"][1])


def suggest(read, market, master, broker, today, cfg, now=None):
    """Adds a contract to a read that scores well enough, priced the way it would really trade: bought at the ask."""
    if read.get("side") is None or read["spot"] is None:
        return read
    now = now or clock.now()
    exp, why = master.choose_expiry(market["master"], market["root"], today, market.get("min_days_to_expiry", 1),
                                    market.get("near_expiry", "roll"))
    if not exp:
        read["idea"] = {"error": why}
        return read
    opt = master.pick_option(market["master"], market["root"], exp, read["spot"], read["side"], cfg["strike"])
    if not opt:
        read["idea"] = {"error": "no matching contract"}
        return read
    q = broker.quotes([opt["symbol"]]).get(opt["symbol"]) or {}

    def finite(value):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    lp, bid, ask = finite(q.get("lp")), finite(q.get("bid")), finite(q.get("ask"))
    ts = finite(q.get("ts"))
    bid_qty, ask_qty, oi = finite(q.get("bid_qty")), finite(q.get("ask_qty")), finite(q.get("oi"))
    quote_age = None if ts is None else now.timestamp() - ts
    idea = {"contract": opt["desc"], "symbol": opt["symbol"], "side": read["side"], "strike": opt["strike"], "lot": opt["lot"],
            "expiry": clock.from_ts(exp).date().isoformat(), "premium": None, "last": lp, "bid": bid, "ask": ask,
            "bid_qty": bid_qty, "ask_qty": ask_qty, "oi": oi,
            "quote_ts": ts, "quote_age_s": None if quote_age is None else round(quote_age)}
    if ask is not None and bid is not None and ask >= bid > 0:
        entry = ask                                            # a long option is bought at the ask
        stop_pct = market.get("premium_stop_pct", 25) / 100.0
        stop = entry * (1 - stop_pct)
        risk_lot = (entry - stop) * opt["lot"]
        spread_cost = (ask - bid) * opt["lot"]                 # the price of getting out at the bid
        cost = cfg["cost_per_trade"] + spread_cost + 2 * entry * opt["lot"] * cfg["slippage_pct"] / 100.0
        idea.update({"premium": round(entry, 2), "stop": round(stop, 2), "target": round(entry + cfg["target_r"] * (entry - stop), 2),
                     "risk_per_lot": round(risk_lot, 0), "capital_per_lot": round(entry * opt["lot"], 0),
                     "spread_pct": round((ask - bid) / ask * 100, 1),
                     "cost_ratio": round(cost / risk_lot, 2) if risk_lot else None})
    read["idea"] = idea
    return read


def grade(read, market, now, cfg, heavy):
    """take / watch / wait, with every reason it is not a take. Anything missing or stale is a reason."""
    why = []
    state = read.get("state", "")
    if state.startswith("Data is out of date"):
        return "watch", ["the index candles are out of date"]
    if state.startswith("Data timestamp is in the future"):
        return "watch", ["the index candle timestamp is in the future"]
    if read.get("side") is None or state.startswith(("No data", "Opening range forms")):
        return "wait", ["Waiting for the opening range" if state.startswith("Opening") else "No index data"]
    if not state.endswith("broke out"):
        why.append("price is still inside the opening range")
    if read["score"] < cfg["min_score"]:
        why.append(f"score {read['score']} of 5, needs {cfg['min_score']}")
    if read.get("market_agrees") is False:
        why.append("Nifty is on the other side of VWAP")
    elif read.get("market_agrees") is None:
        why.append("Nifty's reading is missing, out of date or out of step with this index")
    if not in_window(market, now):
        why.append("outside the entry window " + "-".join(market["entry"]))
    idea = read.get("idea")
    if idea is None:
        if state.endswith("broke out"):
            why.append("no contract priced yet")
    elif idea.get("error"):
        why.append(idea["error"])
    else:
        age = idea.get("quote_age_s")
        try:
            age = float(age) if age is not None else None
        except (TypeError, ValueError):
            age = None
        if age is None or not math.isfinite(age):
            why.append("the contract's price has no usable timestamp")
        elif age < -10:
            why.append("the contract's quote timestamp is in the future")
        elif age > cfg["max_quote_age_s"]:
            why.append(f"the contract's price is {int(age)}s old")
        bid, ask = idea.get("bid"), idea.get("ask")
        if bid is None or ask is None or not (math.isfinite(float(bid)) and math.isfinite(float(ask))) or not (0 < bid <= ask):
            why.append("the contract has no valid bid and ask")
        elif idea.get("spread_pct") is not None and (not math.isfinite(float(idea["spread_pct"])) or idea["spread_pct"] > cfg["max_spread_pct"]):
            why.append(f"bid-ask spread is {idea['spread_pct']}% of the price")

        try:
            lot = int(idea.get("lot"))
        except (TypeError, ValueError):
            lot = 0
        if lot <= 0:
            why.append("contract lot size is invalid")
        for field, label in (("ask_qty", "ask"), ("bid_qty", "bid")):
            value = idea.get(field)
            if value is None or not math.isfinite(float(value)):
                why.append(f"{label} depth quantity is unavailable or invalid")
            elif lot <= 0 or value < lot:
                why.append(f"only {int(value)} available at the {label}, a lot is {idea.get('lot')}")
        oi = idea.get("oi")
        if oi is None or not math.isfinite(float(oi)):
            why.append("open interest is unavailable or invalid")
        elif oi < cfg["min_open_interest"]:
            why.append(f"open interest {int(oi)} is below the minimum {int(cfg['min_open_interest'])}")
        cost_ratio = idea.get("cost_ratio")
        if cost_ratio is None or not math.isfinite(float(cost_ratio)):
            why.append("no usable price for the contract")
        elif cost_ratio > cfg["max_cost_ratio"]:
            why.append(f"costs are {cost_ratio:.0%} of the risk")
    read["warnings"] = [f"{s} reports results" for s in heavy] if heavy and market["id"] in ("NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY") else []
    return ("watch" if why else "take"), why
