"""The claims in the "Market research" document, turned into tests.

The document is an AI-written playbook (Gemini) that summarises what well-known short-term traders say (Minervini, O'Neil, Bonde, Connors,
Kullamaegi) and then fact-checks itself. It cites blog posts and a document-sharing site, and several of its numbers (win rates, "verified"
returns) are not checkable from its own sources. It also makes claims about Indian costs and survivorship that this project has measured, and
it proposes rules this project had not tested. Here the testable ones are written as exact rules and run on the survivor-free universe with
Indian costs, each with a random-entry placebo, and the holdout sealed.

THE RULES BELOW WERE FIXED BEFORE ANY RESULT WAS SEEN AND MUST NOT BE TUNED TO IT. Common to all: the universe is the 200 most traded shares on
each date; costs are 0.40% of the position round trip (the document says 0.385% to 0.45%, so the estimate already used agrees with it); entries
are at the next open unless a test says otherwise; a stop is checked before any other exit and a gap through it fills at the open; one position
per share at a time with a 10-session cool-off; a share that stops trading is closed at its last price.

CONNORS MEAN REVERSION (M). RSI(2) is Wilder's 2-period RSI of the close. Buy the next open when the close is above its 200-day average and
RSI(2) is below the threshold. Exit at the next open after the first close above the 5-day average, or after 10 sessions, or on a catastrophic
stop 12% below the entry. Results are in percent per trade (there is no tight stop to define R).
  M1  RSI(2) below 10.        M2  RSI(2) below 5.
  M3  RSI(2) below 10 and a relative-strength percentile of 80 or more (the document's "momentum pullback").
  M4  RSI(2) below 15, strength 80+, the 20-day average rising over 5 sessions and the close within 3% above it ("pullback toward the rising
      20-day average").
  M5  as M1 with a hard 3% stop (the document's claim that a tight stop destroys mean reversion).
STOCKBEE MOMENTUM BURST (B). Shares whose 20-day average daily range is at least 3.5%. Yesterday was a narrow, quiet day (it moved less than 1.5%
and traded less than the day before). Today a stop-buy order at 4% above yesterday's close fills at that level (or at the open if higher; skipped
if the open is more than 8% above). Stop at yesterday's low (no more than 8% away, at least 1%). The document's same-day volume condition cannot
be used with daily data without look-ahead, so it is left out.
  B1  hold 5 sessions, exit at the close.   B2  trail: exit at the next open after a close below the 10-day average (at most 20 sessions).
  B3  as B2, and leave at the next open if the close on the third session is not above the entry.
EPISODIC PIVOT (E), a daily-data version. A gap: the open is 8% to 30% above yesterday's close, volume that day is at least 3 times normal, the
previous 60 sessions were a base no more than 35% deep, and the day closed at or above its open. Buy the next open (skipped if more than 5% above
that close). Stop at the gap day's low (1% to 12% away). Exit at the next open after a close below the average, at most 40 sessions.
  E1  10-day average.   E2  20-day average.
  E3  E1 only when a results announcement (NSE's calendar) fell on the gap day or the day before.   E4  E1 with no results announcement.
RISK-ADJUSTED MOMENTUM (V). Monthly, equal weight, the 20 shares above their 200-day average with the highest 126-session return divided by the
standard deviation of their last 20 daily returns, costs paid.   V1  as described.   V2  and cash when Nifty is below its 200-day average.
ROLLING PROFIT FACTOR (R). The document's robustness bar: profit factor above 1.25 net of costs in at least 75% of rolling 12-month windows
(stepping one month, at least 30 trades in a window), for the breakout tests that looked best (the 3R-target and trail versions of tight-base,
strength, and the full system).
EXITS AND SIZING on the relative-strength tight-base breakouts (the best earlier result), all with the same entries and the 21-day average trail:
  X0  baseline.   T1  leave at the next open if the fifth session's close is not at least 3% above the entry (the document's "time stop").
  X1  leave at the close of the entry day if it closes in the bottom quarter of its range (the "squat").
  S1  pilot sizing: start by risking 0.4% a trade, move to 1.5% after two winning trades in a row, and after two losing trades in a row go to
      cash for 14 days at 0.4% (Minervini's pilot buying), against a fixed 1.5%.
That is 20 more looks, 45 in all: with that many, a few near-misses are what chance alone produces, and the report says so.
"""
import random
from collections import defaultdict
from datetime import timedelta

from . import context as cx
from . import daily as dl
from . import playbook as pb

A = {
    "rsi_period": 2, "rsi_below": (10, 5, 10, 15, 10), "mr_time": 10, "mr_cat_stop": 0.12, "mr_hard_stop": 0.03, "sma_exit": 5,
    "adr_days": 20, "adr_min": 0.035, "narrow": 0.015, "burst_up": 0.04, "burst_max_open": 0.08, "burst_hold": 5, "burst_trail": 10, "burst_max_days": 20,
    "gap_min": 0.08, "gap_max": 0.30, "gap_vol": 3.0, "base_days": 60, "base_depth": 0.35, "ep_max_chase": 0.05, "ep_cap": 0.12, "ep_max_days": 40,
    "vol_lookback": 126, "vol_days": 20, "top": 20,
    "time_days": 5, "time_gain": 0.03, "squat": 0.25,
    "pilot_risk": 0.004, "full_risk": 0.015, "pilot_pause_days": 14,
}


def rsi(values, n=2):
    """Wilder's RSI."""
    out, up, dn = [None] * len(values), 0.0, 0.0
    for i in range(1, len(values)):
        ch = values[i] - values[i - 1]
        g, l = max(ch, 0.0), max(-ch, 0.0)
        if i <= n:
            up += g / n
            dn += l / n
            if i == n:
                out[i] = 100.0 if dn == 0 else 100.0 - 100.0 / (1 + up / dn)
        else:
            up, dn = (up * (n - 1) + g) / n, (dn * (n - 1) + l) / n
            out[i] = 100.0 if dn == 0 else 100.0 - 100.0 / (1 + up / dn)
    return out


def ensure3(D):
    """Adds the indicators these tests need (once)."""
    pb.ensure(D)
    if not hasattr(D, "rsi2"):
        D.rsi2 = rsi(D.c, A["rsi_period"])
        D.sma5 = dl.rolling_mean(D.c, A["sma_exit"])
        D.sma10 = dl.rolling_mean(D.c, 10)
        rng = [(h - l) / c if c else 0.0 for h, l, c in zip(D.h, D.l, D.c)]
        m = dl.rolling_mean(rng, A["adr_days"])
        D.adr = [None] * D.n                       # average daily range of the previous 20 sessions (not including today)
        for i in range(1, D.n):
            D.adr[i] = m[i - 1]


def _member(D, i, key, members):
    return (members is None or key in members.get(D.d[i], ())) and dl.liquid(D, i)


def _plain(key):
    return key.split("|")[0]


# ── signals ──────────────────────────────────────────────────────────────────────────────────────
def mr_signals(D, key, members, rs_map, thr, need_rs=False, pullback=False):
    ensure3(D)
    out = []
    for i in range(dl.RULES["warmup"], D.n - 1):
        s200, r2 = D.sma200[i], D.rsi2[i]
        if s200 is None or r2 is None or not D.c[i] > s200 or r2 >= thr or not _member(D, i, key, members):
            continue
        if need_rs and rs_map.get(key, {}).get(D.d[i], -1) < cx.CONTEXT["rs_min"]:
            continue
        if pullback:
            s20, s20b = D.sma20[i], D.sma20[i - 5]
            if s20 is None or s20b is None or not s20 > s20b or D.c[i] > s20 * 1.03:
                continue
        out.append({"i": i, "date": D.d[i], "volx": 1.0})
    return out


def burst_signals(D, key, members):
    ensure3(D)
    out = []
    for i in range(dl.RULES["warmup"], D.n):
        adr = D.adr[i]
        if not adr or adr < A["adr_min"] or not _member(D, i, key, members):
            continue
        pc = D.c[i - 1]
        if abs(D.c[i - 1] / D.c[i - 2] - 1) >= A["narrow"] or D.v[i - 1] >= D.v[i - 2]:
            continue
        level = pc * (1 + A["burst_up"])
        if D.h[i] < level or D.o[i] > pc * (1 + A["burst_max_open"]):
            continue
        out.append({"i": i, "date": D.d[i], "level": level, "stop": D.l[i - 1], "volx": 1.0})
    return out


def ep_signals(D, key, members, results_days):
    ensure3(D)
    out = []
    b = A["base_days"]
    for i in range(dl.RULES["warmup"], D.n - 1):
        pc, vp = D.c[i - 1], D.vol_prev[i]
        if not vp or not (pc * (1 + A["gap_min"]) <= D.o[i] <= pc * (1 + A["gap_max"])) or D.v[i] < A["gap_vol"] * vp or D.c[i] < D.o[i]:
            continue
        hi, lo = max(D.h[i - b:i]), min(D.l[i - b:i])
        if (hi - lo) / hi > A["base_depth"] or not _member(D, i, key, members):
            continue
        d, prev = D.d[i], D.d[i - 1]
        news = (_plain(key), d) in results_days or (_plain(key), prev) in results_days
        out.append({"i": i, "date": d, "stop": D.l[i], "ref": D.c[i], "volx": D.v[i] / vp, "news": news})
    return out


# ── trades ───────────────────────────────────────────────────────────────────────────────────────
def _end(D):
    return "delisted or merged" if D.ended else "end of data"


def sim_mr(D, i, stop_pct):
    """Connors-style trade: buy the next open, leave at the next open after a close above the 5-day average (or after 10 sessions)."""
    e = i + 1
    if e >= D.n:
        return None
    entry = D.o[e]
    stop = entry * (1 - stop_pct)

    def done(k, px, reason, still_open=False):
        gross = px / entry - 1
        return {"entry_i": e, "exit_i": k, "entry_date": D.d[e], "exit_date": D.d[k], "entry": round(entry, 2), "exit": round(px, 2), "reason": reason,
                "hold": k - e, "gross_pct": round(gross * 100, 3), "net_pct": round((gross - dl.RULES["cost_round_trip"]) * 100, 3), "open": still_open}

    for k in range(e, D.n):
        if D.l[k] <= stop:
            return done(k, D.o[k] if D.o[k] < stop else stop, "stop")
        if D.c[k] > D.sma5[k] or k - e + 1 >= A["mr_time"]:
            reason = "5-day average" if D.c[k] > D.sma5[k] else "time"
            return done(k + 1, D.o[k + 1], reason) if k + 1 < D.n else done(k, D.c[k], _end(D), not D.ended)
    return done(D.n - 1, D.c[-1], _end(D), not D.ended)


def sim_r(D, i, entry, stop, rule, start, cap_days=None):
    """A trade measured in R (the distance to the stop). `start` is the entry day's index; rule is "hold5", "trail10", "trail10_t3", "trail20"."""
    risk = (entry - stop) / entry
    if not (0.01 <= risk <= (0.12 if rule.startswith("ep") else 0.08)):
        return None
    cost_r = dl.RULES["cost_round_trip"] / risk

    def done(k, px, reason, still_open=False):
        gain = px / entry - 1
        return {"entry_i": start, "exit_i": k, "entry_date": D.d[start], "exit_date": D.d[k], "entry": round(entry, 2), "exit": round(px, 2), "reason": reason,
                "hold": k - start, "gross_r": round(gain / risk, 3), "net_r": round(gain / risk - cost_r, 3), "open": still_open, "risk_pct": risk}

    ma = {"trail10": D.sma10, "trail10_t3": D.sma10, "ep10": D.sma10, "ep20": D.sma20}.get(rule)
    max_days = A["ep_max_days"] if rule.startswith("ep") else A["burst_max_days"]
    for k in range(start, D.n):
        if D.l[k] <= stop:
            return done(k, D.o[k] if D.o[k] < stop and k > start else stop, "stop")
        n = k - start + 1
        if rule == "hold5":
            if n >= A["burst_hold"]:
                return done(k, D.c[k], "5-session exit")
            continue
        if rule == "trail10_t3" and n == 3 and D.c[k] <= entry:
            return done(k + 1, D.o[k + 1], "not up by day 3") if k + 1 < D.n else done(k, D.c[k], _end(D), not D.ended)
        cut = ma[k] is not None and D.c[k] < ma[k] and k > start
        if cut or n >= max_days:
            reason = "trend break" if cut else "time"
            return done(k + 1, D.o[k + 1], reason) if (cut and k + 1 < D.n) else done(k, D.c[k], reason if n >= max_days else _end(D), (not D.ended) and not n >= max_days)
    return done(D.n - 1, D.c[-1], _end(D), not D.ended)


def run_trades(data, sigs, make, key_name="sdate"):
    """One trade per signal with a cool-off after each exit. `make(D, sig)` returns a trade dict or None."""
    out = []
    for sym, D in data.items():
        free_from = -1
        for s in sigs.get(sym, []):
            if s["i"] + 1 <= free_from:
                continue
            t = make(D, s)
            if t is None:
                continue
            free_from = t["exit_i"] + dl.RULES["cooldown"]
            out.append({**t, "symbol": sym, "sdate": s["date"].isoformat(), "date": s["date"].isoformat()[:7], "volx": round(s.get("volx", 1.0), 2),
                        "news": s.get("news"), "net": t.get("net_r", t.get("net_pct"))})
    out.sort(key=lambda t: (t["entry_date"], t["symbol"]))
    return out


# ── numbers ──────────────────────────────────────────────────────────────────────────────────────
def brief_pct(trades):
    closed = [t for t in trades if not t["open"]]
    if not closed:
        return {"n": 0}
    wins = [t["net_pct"] for t in closed if t["net_pct"] > 0]
    losses = [-t["net_pct"] for t in closed if t["net_pct"] < 0]
    return {"n": len(closed), "avg_pct": round(sum(t["net_pct"] for t in closed) / len(closed), 3),
            "gross_avg_pct": round(sum(t["gross_pct"] for t in closed) / len(closed), 3), "win_rate": round(len(wins) / len(closed) * 100, 1),
            "profit_factor": round(sum(wins) / sum(losses), 2) if losses else None,
            "avg_hold": round(sum(t["hold"] for t in closed) / len(closed), 1), "ci_avg_pct": dl.day_bootstrap(closed, "net_pct"), "open": len(trades) - len(closed)}


def _rows(defs, trades, splits, shown, final, brief):
    rows = []
    for eid, name, desc, extra in defs:
        tr = [t for t in trades[eid] if t["sdate"] in shown]
        row = {"id": eid, "name": name, "desc": desc, "unit": extra, "all": brief(tr)}
        for part in ("development", "validation") + (("holdout",) if final else ()):
            ds = set(splits[part])
            row[part] = brief([t for t in tr if t["sdate"] in ds])
        rows.append(row)
    return rows


def _placebo(data, pool_fn, make, n_real, window, key, draws, seed, members, unit):
    pool = []
    for sym, D in data.items():
        ensure3(D)
        for i in range(dl.RULES["warmup"], D.n - 1):
            if window[0] <= D.d[i].isoformat() <= window[1] and pool_fn(D, i, sym):
                pool.append((sym, i))
    if not pool or not n_real:
        return None
    rng, means = random.Random(seed), []
    for _ in range(draws):
        got = []
        for sym, i in rng.sample(pool, min(n_real, len(pool))):
            t = make(data[sym], {"i": i, "date": data[sym].d[i]})
            if t and not t["open"]:
                got.append(t[key])
        if got:
            means.append(sum(got) / len(got))
    if not means:
        return None
    means.sort()
    return {"mean": round(sum(means) / len(means), 3), "ci": [round(means[int(0.025 * len(means))], 3), round(means[int(0.975 * len(means)) - 1], 3)],
            "draws": len(means), "pool": len(pool)}


# ── adaptive (pilot) sizing ──────────────────────────────────────────────────────────────────────
def portfolio_pilot(trades, data, dates, adaptive=True):
    """Like daily.portfolio, but the risk per trade follows recent results: pilot, then full after two wins in a row, and cash for 14 days
    after two losses in a row. With adaptive=False it risks the full amount every time."""
    st, cost = dl.RULES["stop"], dl.RULES["cost_round_trip"]
    by_entry, by_exit = defaultdict(list), defaultdict(list)
    for t in trades:
        by_entry[t["entry_date"]].append(t)
        by_exit[t["exit_date"]].append(t)
    cash, held, last, curve, taken = 1.0, {}, {}, [], 0
    level, results, pause_until = (A["pilot_risk"] if adaptive else A["full_risk"]), [], None
    for d in dates:
        for t in sorted(by_entry.get(d, []), key=lambda x: -x["volx"]):
            if len(held) >= dl.RULES["max_positions"] or (pause_until is not None and d < pause_until):
                continue
            equity = cash + sum(p["shares"] * last.get(p["sym"], p["entry"]) for p in held.values())
            size = equity * level / t.get("risk_pct", st)
            if size > cash:
                continue
            held[id(t)] = {"sym": t["symbol"], "shares": size / t["entry"], "entry": t["entry"]}
            cash -= size
            taken += 1
        for t in by_exit.get(d, []):
            pos = held.pop(id(t), None)
            if pos:
                cash += pos["shares"] * t["exit"] - pos["shares"] * pos["entry"] * cost
                if adaptive:
                    results.append(t["net_r"] > 0)
                    if len(results) >= 2 and results[-1] and results[-2]:
                        level = A["full_risk"]
                    elif len(results) >= 2 and not results[-1] and not results[-2]:
                        level, pause_until = A["pilot_risk"], d + timedelta(days=A["pilot_pause_days"])
                        results.clear()
        for p in held.values():
            D = data[p["sym"]]
            j = D.index.get(d)
            if j is not None:
                last[p["sym"]] = D.c[j]
        curve.append(cash + sum(p["shares"] * last.get(p["sym"], p["entry"]) for p in held.values()))
    return curve, taken


# ── risk-adjusted momentum picker ────────────────────────────────────────────────────────────────
def riskadj_score(D, i):
    n = A["vol_lookback"]
    if i < n:
        return None
    rets = [D.c[j] / D.c[j - 1] - 1 for j in range(i - A["vol_days"] + 1, i + 1)]
    mean = sum(rets) / len(rets)
    sd = (sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5
    if sd <= 0:
        return None
    return (D.c[i] / D.c[i - n] - 1) / sd


def riskadj_picker(data, members, nifty, regime=False):
    top = A["top"]

    def pick(prev, d):
        if regime:
            j = nifty.index.get(prev)
            if j is None or nifty.sma200[j] is None or nifty.c[j] <= nifty.sma200[j]:
                return []
        rows = []
        for sym in members.get(prev, ()):
            D = data.get(sym)
            i = D.index.get(prev) if D else None
            if i is None or D.sma200[i] is None or D.c[i] <= D.sma200[i]:
                continue
            s = riskadj_score(D, i)
            if s is not None:
                rows.append((s, sym))
        rows.sort(reverse=True)
        return [s for _, s in rows[:top]]
    return pick


# ── the whole family ─────────────────────────────────────────────────────────────────────────────
MR = [("m1", "RSI(2) below 10 in an uptrend", "Connors: buy the dip above the 200-day, leave on a close above the 5-day average.", 10, False, False, 0.12),
      ("m2", "RSI(2) below 5", "A stricter dip.", 5, False, False, 0.12),
      ("m3", "RSI(2) below 10 and relative strength 80+", "The document's momentum pullback.", 10, True, False, 0.12),
      ("m4", "RSI(2) below 15, strength 80+, near the rising 20-day average", "The document's second hybrid.", 15, True, True, 0.12),
      ("m5", "RSI(2) below 10 with a hard 3% stop", "The document says a tight stop destroys mean reversion.", 10, False, False, 0.03)]
BURST = [("b1", "Momentum burst, hold 5 sessions", "A 4% burst after a quiet day, in a share that moves 3.5% a day.", "hold5"),
         ("b2", "Momentum burst, trail the 10-day average", "", "trail10"),
         ("b3", "Momentum burst, trail and leave if not up by day 3", "", "trail10_t3")]
EP = [("e1", "Episodic pivot (gap 8 to 30% on volume), trail the 10-day average", "Daily-data version of the news gap.", "ep10", None),
      ("e2", "Episodic pivot, trail the 20-day average", "", "ep20", None),
      ("e3", "Episodic pivot with a results announcement", "NSE's calendar shows results on the gap day or the day before.", "ep10", True),
      ("e4", "Episodic pivot with no results announcement", "", "ep10", False)]


def run(data, members, rs_map, sigs, runs, nifty, splits, shown, window, final, results_days, brief_r, shown_dates, dates, draws=40):
    for D in data.values():
        ensure3(D)
    out = {}
    # M: mean reversion, in percent per trade
    mtr, mpl = {}, []
    for eid, name, desc, thr, need_rs, pull, stop in MR:
        sg = {s: mr_signals(D, s, members, rs_map, thr, need_rs, pull) for s, D in data.items()}
        make = lambda D, s, stop=stop: sim_mr(D, s["i"], stop)
        mtr[eid] = run_trades(data, sg, make)
        shown_tr = [t for t in mtr[eid] if t["sdate"] in shown]
        pool = lambda D, i, sym, thr=thr, need_rs=need_rs, pull=pull: (
            D.sma200[i] is not None and D.c[i] > D.sma200[i] and _member(D, i, sym, members)
            and (not need_rs or rs_map.get(sym, {}).get(D.d[i], -1) >= cx.CONTEXT["rs_min"])
            and (not pull or (D.sma20[i] is not None and D.sma20[i - 5] is not None and D.sma20[i] > D.sma20[i - 5] and D.c[i] <= D.sma20[i] * 1.03)))
        ph = _placebo(data, pool, make, len([t for t in shown_tr if not t["open"]]), window, "net_pct", draws, 17, members, "pct")
        real = brief_pct(shown_tr)
        mpl.append({"id": eid, "real": real, "placebo": ph, "beats_random": None if not ph or not real.get("n") else real["avg_pct"] > ph["ci"][1]})
    out["mean_reversion"] = {"experiments": _rows([(m[0], m[1], m[2], "pct") for m in MR], mtr, splits, shown, final, brief_pct), "placebo": mpl}
    # B: momentum burst
    btr, bpl = {}, []
    bsig = {s: burst_signals(D, s, members) for s, D in data.items()}
    for eid, name, desc, rule in BURST:
        def make(D, s, rule=rule):
            pc = D.c[s["i"] - 1]
            entry = max(D.o[s["i"]], s["level"] if "level" in s else pc * (1 + A["burst_up"]))
            return sim_r(D, s["i"], entry, D.l[s["i"] - 1], rule, s["i"])
        btr[eid] = run_trades(data, bsig, make)
        shown_tr = [t for t in btr[eid] if t["sdate"] in shown]
        pool = lambda D, i, sym: bool(D.adr[i] and D.adr[i] >= A["adr_min"]) and _member(D, i, sym, members)
        make_pl = lambda D, s, rule=rule: sim_r(D, s["i"], D.o[s["i"] + 1], D.l[s["i"]], rule, s["i"] + 1)
        ph = _placebo(data, pool, make_pl, len([t for t in shown_tr if not t["open"]]), window, "net_r", draws, 19, members, "r")
        real = brief_r(shown_tr)
        bpl.append({"id": eid, "real": real, "placebo": ph, "beats_random": None if not ph or not real.get("n") else real["avg_r"] > ph["ci"][1]})
    out["burst"] = {"experiments": _rows([(b[0], b[1], b[2], "r") for b in BURST], btr, splits, shown, final, brief_r), "placebo": bpl}
    # E: episodic pivot
    esig = {s: ep_signals(D, s, members, results_days) for s, D in data.items()}
    etr, epl = {}, []
    base_trades = {}
    for eid, name, desc, rule, news in EP:
        sg = {s: [x for x in lst if news is None or x["news"] == news] for s, lst in esig.items()}

        def make(D, s, rule=rule):
            e = s["i"] + 1
            if e >= D.n or D.o[e] > s["ref"] * (1 + A["ep_max_chase"]):
                return None
            return sim_r(D, s["i"], D.o[e], s["stop"], rule, e)
        etr[eid] = run_trades(data, sg, make)
        shown_tr = [t for t in etr[eid] if t["sdate"] in shown]
        pool = lambda D, i, sym: _member(D, i, sym, members)
        make_pl = lambda D, s, rule=rule: sim_r(D, s["i"], D.o[s["i"] + 1], D.l[s["i"]], rule, s["i"] + 1)
        ph = _placebo(data, pool, make_pl, len([t for t in shown_tr if not t["open"]]), window, "net_r", draws, 23, members, "r")
        real = brief_r(shown_tr)
        epl.append({"id": eid, "real": real, "placebo": ph, "beats_random": None if not ph or not real.get("n") else real["avg_r"] > ph["ci"][1]})
    out["episodic"] = {"experiments": _rows([(e[0], e[1], e[2], "r") for e in EP], etr, splits, shown, final, brief_r), "placebo": epl,
                       "gap_days": sum(len(v) for v in esig.values()), "with_results": sum(1 for v in esig.values() for x in v if x["news"])}
    # X: exit modifiers on the relative-strength tight-base breakouts
    base = {s: [{"i": x["i"], "date": x["date"], "stop": None, "ref": x["pivot"], "volx": x["volx"]} for x in lst if x["trend"] and x["tight"] and x["rs_ok"]]
            for s, lst in sigs.items()}
    xtr = {}
    for eid, opts in (("x0", {}), ("t1", {"time_exit": (A["time_days"], A["time_gain"])}), ("x1", {"squat": A["squat"]})):
        def make(D, s, opts=opts):
            return pb.simulate2(D, s["i"], None, "trail", s["ref"], lambda en: en * 0.92, **opts)
        xtr[eid] = run_trades(data, base, make)
    out["exits"] = {"experiments": _rows([("x0", "Tight-base + strength breakout, 21-day trail (baseline)", "", "r"),
                                          ("t1", "... leave if not up 3% by the fifth session", "The document's time stop.", "r"),
                                          ("x1", "... leave if the entry day closes in the bottom quarter of its range", "The squat.", "r")],
                                         xtr, splits, shown, final, brief_r)}
    # S: pilot sizing, on the earlier relative-strength 3R-target trades
    closed = [t for t in runs.get("ctx_rs_t", []) if not t["open"]]
    sizing = {}
    for name, adaptive in (("fixed_full", False), ("pilot", True)):
        curve, taken = portfolio_pilot(closed, data, shown_dates, adaptive)
        sizing[name] = {**dl.book_stats(curve, shown_dates, splits), "trades_taken": taken}
    out["pilot_sizing"] = sizing
    # V: risk-adjusted momentum books
    books = {}
    for name, regime in (("v1", False), ("v2", True)):
        curve, made = cx.run_book(data, shown_dates, riskadj_picker(data, members, nifty, regime))
        books[name] = {**dl.book_stats(curve, shown_dates, splits), "trades": made}
    out["momentum_books"] = books
    out["rolling_profit_factor"] = {eid: rolling_pf(runs.get(eid, [])) for eid in ("tight", "tight_t", "ctx_rs", "ctx_rs_t", "full", "full_t")}
    out["rules"] = {k: (list(v) if isinstance(v, tuple) else v) for k, v in A.items()}
    return out


def rolling_pf(trades, key="net_r", months=12, min_trades=30, bar=1.25):
    """Profit factor in every rolling `months`-month window of signal dates, stepping one month."""
    closed = [t for t in trades if not t["open"]]
    if not closed:
        return {}
    ms = sorted({t["date"] for t in closed})                       # "YYYY-MM"
    allm = []
    y, m = int(ms[0][:4]), int(ms[0][5:])
    while f"{y:04d}-{m:02d}" <= ms[-1]:
        allm.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    pfs = []
    for k in range(0, len(allm) - months + 1):
        win = set(allm[k:k + months])
        tr = [t for t in closed if t["date"] in win]
        if len(tr) < min_trades:
            continue
        wins = sum(t[key] for t in tr if t[key] > 0)
        losses = -sum(t[key] for t in tr if t[key] < 0)
        pfs.append(wins / losses if losses else 9.99)
    if not pfs:
        return {"windows": 0}
    pfs.sort()
    return {"windows": len(pfs), "share_above_bar": round(sum(1 for x in pfs if x > bar) / len(pfs), 3), "share_above_1": round(sum(1 for x in pfs if x > 1) / len(pfs), 3),
            "median_pf": round(pfs[len(pfs) // 2], 2), "worst_pf": round(pfs[0], 2), "best_pf": round(pfs[-1], 2), "bar": bar}


def load_results_days(start, end, cache=None, log=print):
    """{(symbol, date)} of every results announcement NSE lists between two dates, read in 60-day pieces (cached in `cache`)."""
    import json
    import os
    from .results import nse_fetch
    days, missed = set(), 0
    lo = start
    while lo <= end:
        hi = min(end, lo + timedelta(days=59))
        path = os.path.join(cache, f"results_{lo}_{hi}.json") if cache else None
        raw = None
        if path and os.path.exists(path):
            raw = json.load(open(path, encoding="utf-8"))
        else:
            try:
                raw = nse_fetch(lo, hi)
                if path:
                    os.makedirs(cache, exist_ok=True)
                    json.dump(raw, open(path, "w", encoding="utf-8"))
            except Exception as e:
                missed += 1
                log(f"  results calendar {lo} to {hi} not read ({type(e).__name__})")
        for e in raw or []:
            if "result" in str(e.get("purpose", "")).lower():
                d = results_day(e.get("date"))
                if d:
                    days.add((str(e.get("symbol", "")).strip(), d))
        lo = hi + timedelta(days=1)
    return days, missed


def results_day(text):
    from .results import _parse_day
    return _parse_day(text)
