"""The "Leader Playbook" ideas, turned into tests: a tight pivot breakout, a pullback to the 21-day average, a partial-profit exit, a bear
gauge, and how much of the drawdown is just position size.

Source: study notes of a trader's podcast (Himanshu Sharma on MastersInOne), which cite O'Neil, Livermore, Minervini and Zanger. The notes
give rules, not evidence. These tests ask whether the rules, written down exactly and run on survivor-free prices with costs, do anything.

THE RULES BELOW WERE FIXED BEFORE ANY RESULT WAS SEEN AND MUST NOT BE TUNED TO IT. They sit on top of the universe, relative-strength and
liquidity filters already used (engine/context.py): a share must be in the 200 most traded that day, in an uptrend (price above its 50, 150
and 200 day averages in order, the 200 rising) and have a relative-strength percentile of 80 or more. The tests, all of them:

  P1  PIVOT BREAKOUT. The previous 10 sessions form a base no more than 5% from high to low, on the right side of the chart (the close is
      within 10% of the 252-session high). Buy the next open after a close above the base's high, on at least 1.2 times normal volume,
      if the close is no more than 5% above that high and yesterday's close was not already above it. Stop at the base's low.
  P2  The same, with the partial-profit exit instead.
  P3  PULLBACK TO THE 21-DAY AVERAGE. The close is within 25% of the 252-session high, at least 10 of the previous 15 closes were above the
      21-day exponential average, today's low touched within 1% of it, and today closed back above it and above yesterday's high (it turned
      up). Buy the next open. Stop at the lowest low of the last 5 sessions (the pullback low).
  P4  The same, with the partial-profit exit.
  P5  The partial-profit exit on the tight-base + relative-strength breakout already tested (8% stop).
  P6  BEAR GAUGE. When fewer than 20% of the universe are above their 200-day average, no new trades: how many days, and what the
      breakouts that began on those days did.
  P7  POSITION SIZE. The same trades at 0.25%, 0.5%, 1% and 1.5% of equity risked, with at most 5 positions.

Exits. Standard ("trail"): the stop moves to the entry after a close one R above it, and the trade ends at the next open after a close below the
21-day exponential average. Partial: sell 40% when price reaches +20% (at the open if it gaps over), move the stop on the rest to the entry,
trail it with the 21-day average. A stop is checked before anything else; a gap through it fills at the open. A trade is skipped if the
next open is more than 5% above the signal close, or if the stop is closer than 1% or farther than 8% from the entry. Costs are 0.40% of
the position round trip, so a tight stop pays a bigger share of its risk in costs. R is the distance from entry to stop.

Every test has a random-entry placebo with the same stop construction and exit: random days in which the same shares are in an uptrend and
are relative-strength leaders. The family adds seven tests to the eighteen already run: 25 looks, so a lone near-miss means little.
"""
import random
from collections import defaultdict

from . import context as cx
from . import daily as dl

R2 = {
    "pivot_days": 10, "pivot_depth": 0.05, "near_high_pivot": 0.10, "pivot_volume": 1.2, "max_over_pivot": 0.05,
    "pullback_near_high": 0.25, "above_days": 10, "above_window": 15, "touch": 0.01, "low_window": 5,
    "high_window": 252, "max_gap": 0.05, "stop_floor": 0.01, "stop_cap": 0.08,
    "partial_gain": 0.20, "partial_fraction": 0.40, "trail_ema": 21,
    "bear_below": 0.20, "risk_levels": (0.0025, 0.005, 0.01, 0.015),
}


def ema(values, n):
    out, e, a = [], None, 2.0 / (n + 1)
    for v in values:
        e = v if e is None else e + a * (v - e)
        out.append(e)
    return out


def ensure(D):
    """Adds the 21-day exponential average and the rolling 252-session high to a Daily (once)."""
    if not hasattr(D, "ema21"):
        D.ema21 = ema(D.c, R2["trail_ema"])
        w = R2["high_window"]
        D.hh252 = [max(D.h[max(0, i - w):i]) if i else D.h[0] for i in range(D.n)]


# ── signals ──────────────────────────────────────────────────────────────────────────────────────
def _eligible(D, i, key, members, rs_map):
    d = D.d[i]
    if members is not None and key not in members.get(d, ()):
        return False
    rs = rs_map.get(key, {}).get(d)
    return rs is not None and rs >= cx.CONTEXT["rs_min"] and dl.liquid(D, i) and dl.trend_ok(D, i)


def pivot_signals(D, key, members, rs_map):
    ensure(D)
    w, out = R2["pivot_days"], []
    for i in range(dl.RULES["warmup"], D.n - 1):
        hi, lo = max(D.h[i - w:i]), min(D.l[i - w:i])
        if (hi - lo) / hi > R2["pivot_depth"] or not (D.c[i] > hi >= D.c[i - 1]) or D.c[i] > hi * (1 + R2["max_over_pivot"]):
            continue
        vp = D.vol_prev[i]
        if not vp or D.v[i] < R2["pivot_volume"] * vp or D.c[i] < D.hh252[i] * (1 - R2["near_high_pivot"]):
            continue
        if not _eligible(D, i, key, members, rs_map):
            continue
        out.append({"i": i, "date": D.d[i], "stop": lo, "ref": D.c[i], "volx": D.v[i] / vp, "kind": "pivot"})
    return out


def pullback_signals(D, key, members, rs_map):
    ensure(D)
    out = []
    for i in range(dl.RULES["warmup"], D.n - 1):
        e = D.ema21[i]
        if D.c[i] < D.hh252[i] * (1 - R2["pullback_near_high"]):
            continue
        above = sum(1 for j in range(i - R2["above_window"], i) if D.c[j] > D.ema21[j])
        if above < R2["above_days"] or D.l[i] > e * (1 + R2["touch"]) or not (D.c[i] > e and D.c[i] > D.h[i - 1]):
            continue
        if not _eligible(D, i, key, members, rs_map):
            continue
        vp = D.vol_prev[i]
        out.append({"i": i, "date": D.d[i], "stop": min(D.l[i - R2["low_window"] + 1:i + 1]), "ref": D.c[i],
                    "volx": (D.v[i] / vp) if vp else 1.0, "kind": "pullback"})
    return out


# ── trades ───────────────────────────────────────────────────────────────────────────────────────
def simulate2(D, i_sig, stop_price, exit_rule, ref, stop_fn=None, time_exit=None, squat=None):
    """One trade from a signal on day i_sig: bought at the next open; `stop_price` (or stop_fn(entry)) is the initial stop. exit_rule is
    "trail" or "partial". Optional: time_exit=(sessions, gain) leaves at the next open if the close on that session is not at least that gain
    above the entry; squat=fraction leaves at the close of the entry day if it closes in the bottom `fraction` of its range.
    Returns the trade dict, or None if skipped."""
    ensure(D)
    e = i_sig + 1
    if e >= D.n:
        return None
    entry = D.o[e]
    if entry > ref * (1 + R2["max_gap"]):
        return None
    stop = stop_fn(entry) if stop_fn else stop_price
    risk = (entry - stop) / entry
    if not (R2["stop_floor"] <= risk <= R2["stop_cap"]):
        return None
    cost_r = dl.RULES["cost_round_trip"] / risk
    st = {"stop": stop, "be": False, "sold": 0.0, "got": 0.0}          # sold: fraction already sold, got: its gain per unit of entry

    def done(k, px, reason, still_open=False):
        left = 1.0 - st["sold"]
        gain = st["got"] + left * (px / entry - 1)
        return {"entry_i": e, "exit_i": k, "entry_date": D.d[e], "exit_date": D.d[k], "entry": round(entry, 2), "exit": round(px, 2),
                "reason": reason, "hold": k - e, "gross_r": round(gain / risk, 3), "net_r": round(gain / risk - cost_r, 3),
                "open": still_open, "risk_pct": risk}

    gone = "delisted or merged" if D.ended else "end of data"
    for k in range(e, D.n):
        if D.l[k] <= st["stop"]:
            return done(k, D.o[k] if D.o[k] < st["stop"] else st["stop"], "stop")
        if exit_rule == "partial" and not st["sold"] and D.h[k] >= entry * (1 + R2["partial_gain"]):
            px = max(D.o[k], entry * (1 + R2["partial_gain"]))
            st["sold"], st["got"] = R2["partial_fraction"], R2["partial_fraction"] * (px / entry - 1)
            st["stop"] = max(st["stop"], entry)                          # the rest can no longer lose
        if squat and k == e and D.h[k] > D.l[k] and (D.c[k] - D.l[k]) / (D.h[k] - D.l[k]) < squat:
            return done(k, D.c[k], "squat")
        if time_exit and k - e + 1 == time_exit[0] and D.c[k] < entry * (1 + time_exit[1]):
            return done(k + 1, D.o[k + 1], "time stop") if k + 1 < D.n else done(k, D.c[k], gone, not D.ended)
        if not st["be"] and D.c[k] >= entry * (1 + risk):
            st["be"], st["stop"] = True, max(st["stop"], entry)
        if k > e and D.c[k] < D.ema21[k]:
            if k + 1 < D.n:
                return done(k + 1, D.o[k + 1], "trend break")
            return done(k, D.c[k], gone, not D.ended)
    return done(D.n - 1, D.c[-1], gone, not D.ended)


def run_signals(data, sigs, exit_rule, key_filter=None):
    """One trade per signal, one position per share at a time with a cool-off after each exit."""
    trades = []
    for sym, D in data.items():
        free_from = -1
        for s in sigs.get(sym, []):
            if s["i"] + 1 <= free_from:
                continue
            t = simulate2(D, s["i"], s["stop"], exit_rule, s["ref"], s.get("stop_fn"))
            if t is None:
                continue
            free_from = t["exit_i"] + dl.RULES["cooldown"]
            trades.append({**t, "symbol": sym, "sdate": s["date"].isoformat(), "date": s["date"].isoformat()[:7], "volx": round(s["volx"], 2),
                           "net": t["net_r"]})
    trades.sort(key=lambda t: (t["entry_date"], t["symbol"]))
    return trades


def placebo2(data, kind, exit_rule, n_real, window, members, rs_map, draws=40, seed=13):
    """Random entry on days when the same shares are eligible, with the same kind of stop and exit."""
    pool = []
    for sym, D in data.items():
        ensure(D)
        for i in range(dl.RULES["warmup"], D.n - 1):
            if window[0] <= D.d[i].isoformat() <= window[1] and _eligible(D, i, sym, members, rs_map):
                pool.append((sym, i))
    if not pool or not n_real:
        return None
    rng, means = random.Random(seed), []
    for _ in range(draws):
        got = []
        for sym, i in rng.sample(pool, min(n_real, len(pool))):
            D = data[sym]
            stop = min(D.l[i - R2["pivot_days"]:i]) if kind == "pivot" else min(D.l[i - R2["low_window"] + 1:i + 1])
            t = simulate2(D, i, stop, exit_rule, D.c[i])
            if t and not t["open"]:
                got.append(t["net_r"])
        if got:
            means.append(sum(got) / len(got))
    if not means:
        return None
    means.sort()
    return {"mean": round(sum(means) / len(means), 3), "ci": [round(means[int(0.025 * len(means))], 3), round(means[int(0.975 * len(means)) - 1], 3)],
            "draws": len(means), "pool": len(pool)}


# ── the whole family ─────────────────────────────────────────────────────────────────────────────
PLAYBOOK = [
    ("pivot", "Tight pivot breakout", "A base no more than 5% deep over 10 sessions, near the high; stop at the base low.", "trail"),
    ("pivot_p", "Tight pivot breakout, partial-profit exit", "Sell 40% at +20%, trail the rest.", "partial"),
    ("pullback", "Pullback to the 21-day average", "A leader dips to its 21-day average and turns up; stop at the pullback low.", "trail"),
    ("pullback_p", "Pullback to the 21-day average, partial-profit exit", "Sell 40% at +20%, trail the rest.", "partial"),
    ("rsbase_p", "Tight-base + relative strength breakout, partial-profit exit", "The earlier test (8% stop) with the partial exit.", "partial"),
]


def run(data, members, rs_map, breadth200, sigs, runs, splits, shown, window, final, brief, portfolio_fn, shown_dates, draws=40):
    """Everything in this family. `runs` holds the earlier experiments' trades (for the bear gauge and the sizing test)."""
    ps = {sym: pivot_signals(D, sym, members, rs_map) for sym, D in data.items()}
    qs = {sym: pullback_signals(D, sym, members, rs_map) for sym, D in data.items()}
    rows, trades = [], {}
    for pid, name, desc, rule in PLAYBOOK:
        if pid.startswith("pivot"):
            tr = run_signals(data, ps, rule)
        elif pid.startswith("pullback"):
            tr = run_signals(data, qs, rule)
        else:                                                         # the earlier RS breakout signals, an 8% stop, the partial exit
            base = {}
            for sym, lst in sigs.items():
                base[sym] = [{"i": s["i"], "date": s["date"], "stop": None, "ref": s["pivot"], "volx": s["volx"], "stop_fn": lambda en: en * 0.92}
                             for s in lst if s["trend"] and s["tight"] and s["rs_ok"]]
            tr = run_signals(data, base, rule)
        tr = [t for t in tr if t["sdate"] in shown]
        trades[pid] = tr
        row = {"id": pid, "name": name, "desc": desc, "exit": rule, "all": brief(tr)}
        for part in ("development", "validation") + (("holdout",) if final else ()):
            ds = set(splits[part])
            row[part] = brief([t for t in tr if t["sdate"] in ds])
        rows.append(row)
    plc = []
    for pid, kind, rule in (("pivot", "pivot", "trail"), ("pivot_p", "pivot", "partial"), ("pullback", "pullback", "trail"), ("pullback_p", "pullback", "partial")):
        n_real = len([t for t in trades[pid] if not t["open"]])
        real = next(r["all"] for r in rows if r["id"] == pid)
        pb = placebo2(data, kind, rule, n_real, window, members, rs_map, draws)
        plc.append({"id": pid, "real": real, "placebo": pb,
                    "beats_random": None if not pb or not real.get("n") else real["avg_r"] > pb["ci"][1]})
    # P6: the bear gauge
    bear_days = sorted(d for d, v in breadth200.items() if v < R2["bear_below"] and d.isoformat() in shown)
    bear = {"threshold": R2["bear_below"], "days_flagged": len(bear_days), "days_measured": len([d for d in breadth200 if d.isoformat() in shown]),
            "first": bear_days[0].isoformat() if bear_days else None, "last": bear_days[-1].isoformat() if bear_days else None, "rows": {}}
    flagged = {d.isoformat() for d in bear_days}
    for eid in ("full", "full_t", "ctx_rs", "ctx_rs_t"):
        tr = runs.get(eid, [])
        on = [t for t in tr if t["sdate"] in flagged]
        bear["rows"][eid] = {"all": brief(tr), "started_on_bear_days": brief(on), "without_bear_days": brief([t for t in tr if t["sdate"] not in flagged])}
    # P7: position size
    sizing = {}
    for eid in ("full_t", "ctx_rs_t", "ctx_rs"):
        closed = [t for t in runs.get(eid, []) if not t["open"]]
        sizing[eid] = []
        for risk in R2["risk_levels"]:
            curve, taken = portfolio_fn(closed, data, shown_dates, risk)
            sizing[eid].append({"risk_pct": round(risk * 100, 2), **dl.book_stats(curve, shown_dates, splits), "trades_taken": taken})
    return {"experiments": rows, "placebo": plc, "bear_gauge": bear, "sizing": sizing, "rules": {k: (list(v) if isinstance(v, tuple) else v) for k, v in R2.items()}}
