"""The swing desk: multi-day setups, found each evening from NSE's daily price files.

    python -m engine.swing --bhav <folder> --out <folder>

Why it exists: intraday trades pay about 0.14R in costs and the research shows no edge after them. A trade held for days pays the same
money in costs but risks far more per trade, so costs matter much less. This desk applies the ONE swing hypothesis the research took to
its sealed holdout, with its rules unchanged (engine/daily.py RULES, engine/context.py CONTEXT), and adds nothing:

    a relative-strength leader (rating 80 or more) in an uptrend (price above its 50, 150 and 200 day averages in order, the 200-day
    rising) with a tight base (the previous 40 sessions fell no more than 25 percent from their high), that closes above that 40-day
    high on at least 1.5 times normal volume (and not more than 7 percent above it), while Nifty is above its own 200-day average.
    Buy the next open (skip it if that is 8 percent above the pivot), initial stop 8 percent below the entry (1R), the stop moves to the
    entry after a close one R up, and the trade ends at the next open after a close below the 20-day average.

Honest status. The holdout gave +0.08R over 127 trades (interval -0.26 to +0.44), indistinguishable from zero and below the development
period. The evidence is for the same rule WITHOUT the Nifty filter; adding it only removes trades. So every swing idea is a SETUP, never a
TAKE, and carries that sentence. Nothing here is a recommendation.

What it writes:
    swing.json                 tonight's triggered setups, setups near their pivot, open paper positions, gap-ups being observed
    swing_state.json           the paper book (so positions are followed from night to night)
    ledger_swing-YYYY-MM.jsonl every settled paper position, kept for good (engine/ledger.py)

The paper book follows each triggered setup exactly as the research simulated it, per share and in R (the distance to the stop), with
the research's cost of 0.40 percent of the position. A position is never sized: your size is yours to choose.

Gap-ups on results days (the lead from the market-research test: 28 trades, picked after seeing the first result) are only listed for
observation as a hypothesis. They are not paper-traded and not counted as ideas.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, timedelta
from typing import Any, Optional

from . import bhav, clock, context as cx, daily as dl, evidence
from . import leaders as ld
from .archetypes import A as EP
from .domain import Check, Idea, LedgerTrade
from .ledger import SWING_PREFIX, Ledger
from .live import quality

SCHEMA = 1
FLAGS = ("trend", "tight", "rs_ok", "regime")       # every one must be true for a trigger
NEAR_PIVOT = 0.05                                    # a setup is "near" when the close is within 5 percent below the pivot
MAX_SETUPS = 25
KEEP_CLOSED = 15
PRUNE_DAYS = 400
NOTE = ("Swing setups are hypotheses from one tested rule, not recommendations. The sealed holdout gave +0.08R over 127 trades "
        "(range -0.26 to +0.44), indistinguishable from zero.")
BUY_NOTE = "Buy tomorrow's open (skip it if it opens more than 8 percent above the pivot). Initial stop 8 percent below the entry."
EXIT_NOTE = "After a close one R up the stop moves to the entry. Leave at the next open after a close below the 20-day average."


# ── rules (mirror engine/daily.py signals, evaluated for any single day) ─────────────────────────
def evaluate(D, i: int, rs: Optional[float], regime_ok: Optional[bool]) -> Optional[dict]:
    """The research's breakout test for day i, with the flags it may require. None if day i is not a breakout day.

    This is `engine.daily.signals` for one day, except that it does not need a following day to exist (tonight's signal has none yet).
    """
    if i < dl.RULES["warmup"] or i >= D.n:
        return None
    piv, vp = D.pivot[i], D.vol_prev[i]
    if piv is None or not vp or not dl.liquid(D, i):
        return None
    if not (D.c[i] > piv and D.v[i] >= dl.RULES["volume_multiple"] * vp and D.c[i] <= piv * (1 + dl.RULES["max_extended"])):
        return None
    return {"i": i, "pivot": piv, "volx": D.v[i] / vp, "trend": bool(dl.trend_ok(D, i)),
            "tight": (piv - D.base_low[i]) / piv <= dl.RULES["base_depth"], "regime": regime_ok is True,
            "rs": rs, "rs_ok": rs is not None and rs >= cx.CONTEXT["rs_min"]}


def triggered(sig: Optional[dict]) -> bool:
    return bool(sig) and all(sig[f] for f in FLAGS)


def near_pivot(D, i: int, rs: Optional[float]) -> Optional[dict]:
    """A leader in an uptrend with a tight base that is still below its pivot, within NEAR_PIVOT of it."""
    if i < dl.RULES["warmup"] or i >= D.n:
        return None
    piv = D.pivot[i]
    if piv is None or not dl.liquid(D, i) or not dl.trend_ok(D, i) or D.c[i] > piv:
        return None
    if rs is None or rs < cx.CONTEXT["rs_min"] or (piv - D.base_low[i]) / piv > dl.RULES["base_depth"]:
        return None
    gap = (piv - D.c[i]) / piv
    if gap > NEAR_PIVOT:
        return None
    return {"pivot": piv, "pct_to_pivot": round(gap * 100, 2), "base_depth_pct": round((piv - D.base_low[i]) / piv * 100, 1), "rs": rs}


def gap_watch(D, i: int, symbol: str, results: set) -> Optional[dict]:
    """A gap-up in the market-research test's episodic-pivot sense, for observation only (see the module note)."""
    if i < dl.RULES["warmup"] or i >= D.n or i < EP["base_days"]:
        return None
    pc, vp = D.c[i - 1], D.vol_prev[i]
    if not vp or not (pc * (1 + EP["gap_min"]) <= D.o[i] <= pc * (1 + EP["gap_max"])) or D.v[i] < EP["gap_vol"] * vp or D.c[i] < D.o[i]:
        return None
    b = EP["base_days"]
    hi, lo = max(D.h[i - b:i]), min(D.l[i - b:i])
    if (hi - lo) / hi > EP["base_depth"] or not dl.liquid(D, i):
        return None
    day, prev = D.d[i], D.d[i - 1]
    return {"gap_pct": round((D.o[i] / pc - 1) * 100, 1), "volx": round(D.v[i] / vp, 1), "close": round(D.c[i], 2),
            "results": (symbol, day) in results or (symbol, prev) in results}


# ── the paper book ───────────────────────────────────────────────────────────────────────────────
ACTIVE = ("PENDING", "OPEN", "EXIT_PENDING")


def new_book() -> dict:
    return {"schema": SCHEMA, "asof": None, "positions": []}


def _position_id(symbol: str, signal_date: str) -> str:
    return f"{symbol}:{signal_date}"


def in_cooldown(book: dict, D, symbol: str, i: int) -> bool:
    """Ten sessions must pass after an exit before the same share can be bought again (the research's rule)."""
    for p in book["positions"]:
        if p["symbol"] == symbol and p["status"] == "CLOSED" and p.get("exit_date"):
            j = D.index.get(date.fromisoformat(p["exit_date"]))
            if j is not None and i + 1 <= j + dl.RULES["cooldown"]:
                return True
    return False


def _close(p: dict, day: date, price: float, reason: str) -> None:
    p.update({"status": "CLOSED", "exit_date": day.isoformat(), "exit": round(price, 2), "reason": reason})
    risk = p["entry"] - p["initial_stop"]
    p["r"] = round(((price - p["entry"]) - dl.RULES["cost_round_trip"] * p["entry"]) / risk, 3) if risk > 0 else None


def advance(book: dict, data: dict, day: date) -> list:
    """Follows every position through one trading day, exactly as engine.daily.simulate does. Returns the positions that closed."""
    closed = []
    st = dl.RULES["stop"]
    for p in book["positions"]:
        if p["status"] not in ACTIVE:
            continue
        D = data.get(p["symbol"])
        if D is None:
            continue
        k = D.index.get(day)
        if k is None:
            if D.ended and day > D.d[-1] and p["status"] != "PENDING":      # stopped trading: closed at its last price, never dropped
                _close(p, D.d[-1], D.c[-1], "delisted or merged")
                closed.append(p)
            continue
        if p["status"] == "EXIT_PENDING":
            _close(p, day, D.o[k], p.get("reason") or "trend break")
            closed.append(p)
            continue
        if p["status"] == "PENDING":
            if p["signal_date"] >= day.isoformat():
                continue
            entry = D.o[k]
            if entry > p["pivot"] * (1 + dl.RULES["max_gap"]):
                p.update({"status": "CANCELLED", "reason": "opened more than 8 percent above the pivot"})
                continue
            p.update({"status": "OPEN", "entry_date": day.isoformat(), "entry": round(entry, 2), "stop": round(entry * (1 - st), 2),
                      "initial_stop": round(entry * (1 - st), 2), "breakeven": False})
        e_idx = D.index[date.fromisoformat(p["entry_date"])]
        if D.l[k] <= p["stop"]:                                              # the stop is checked first, as the worst case
            _close(p, day, D.o[k] if D.o[k] < p["stop"] else p["stop"], "stop")
            closed.append(p)
            continue
        if not p["breakeven"] and D.c[k] >= p["entry"] * (1 + st):           # up one R: the stop moves to the entry from tomorrow
            p["breakeven"], p["stop"] = True, max(p["stop"], p["entry"])
        s20 = D.sma20[k]
        if k > e_idx and s20 is not None and D.c[k] < s20:
            p.update({"status": "EXIT_PENDING", "reason": "trend break"})
        p["last_close"] = round(D.c[k], 2)
    return closed


def open_r(p: dict) -> Optional[float]:
    if p["status"] not in ("OPEN", "EXIT_PENDING") or p.get("last_close") is None:
        return None
    risk = p["entry"] - p["initial_stop"]
    return round((p["last_close"] - p["entry"]) / risk, 2) if risk > 0 else None


# ── charts and the plan in words ─────────────────────────────────────────────────────────────────
TRAIL_LABEL = "Trail: leave at the next open after a close below this 20-day average"
CHART_BARS = 60


def daily_chart(D, e: int, *, ep: float, st: Optional[float], lines, el: str, tn: str) -> dict:
    """A daily-candle chart in the same shape as the intraday ones (contract/schemas/chart.schema.json), with the 20-day average as the trail.

    `e` is the index in D of the candle the chart is about (the signal, or the entry). The window ends at the latest candle.
    """
    last = D.n - 1
    j0 = max(0, min(e, last) - (CHART_BARS - 1 if e >= last else 40))
    rng = range(j0, last + 1)
    return {
        "t": [int(clock.ts(clock.at(D.d[j], "09:15"))) for j in rng], "unit": "day",
        "k": [[round(D.o[j], 2), round(D.h[j], 2), round(D.l[j], 2), round(D.c[j], 2)] for j in rng],
        "e": e - j0, "x": None, "ep": round(ep, 2), "xp": None, "ln": [[lab, round(px, 2)] for lab, px in lines], "ov": {},
        "tr": {"label": TRAIL_LABEL, "v": [None if D.sma20[j] is None else round(D.sma20[j], 2) for j in rng]},
        "st": None if st is None else round(st, 2), "tg": None, "dir": 1, "el": el, "tn": tn,
    }


def swing_plan(close: float, pivot: float) -> dict:
    return {
        "enter": f"Buy at tomorrow's open (about {close:,.2f}). Skip it if it opens more than 8 percent above {pivot:,.2f}.",
        "stop": f"Stop 8 percent below your actual entry price (about {close * (1 - dl.RULES['stop']):,.2f} if you pay {close:,.2f}).",
        "target": "No fixed target: let it run while the trend holds.",
        "trail": ("After a close 8 percent above your entry, move the stop up to your entry. "
                  "Leave at the next open after a close below the 20-day average (the line on the chart)."),
        "exit_by": None,
    }


# ── tonight ──────────────────────────────────────────────────────────────────────────────────────
def build_data(days: dict, log=lambda m: None) -> tuple[dict, date]:
    """{symbol: Daily} for every liquid share, from the raw daily files. A renamed share keeps the series that reaches the last day."""
    candles, meta, last_day = bhav.build(days, min_value_cr=dl.RULES["min_value_cr"], min_bars=dl.RULES["warmup"] + 2, log=log)
    data: dict = {}
    for isin, c in candles.items():
        sym = meta[isin]["symbol"]
        D = dl.Daily(c, ended=meta[isin]["ended"])
        if sym not in data or (D.d[-1], D.n) > (data[sym].d[-1], data[sym].n):
            data[sym] = D
    return data, last_day


def nifty_regime(nifty, last_day: date) -> dict:
    """Is Nifty above its own 200-day average? None (unknown, which counts as no) if there is no recent Nifty history."""
    if nifty is None or not nifty.n or nifty.sma200[-1] is None or nifty.d[-1] < last_day - timedelta(days=5):
        return {"nifty_above_200": None, "nifty_asof": None if nifty is None or not nifty.n else nifty.d[-1].isoformat()}
    return {"nifty_above_200": bool(nifty.c[-1] > nifty.sma200[-1]), "nifty_asof": nifty.d[-1].isoformat()}


def _idea(sym: str, D, sig: dict, last_day: date) -> Idea:
    close = D.c[-1]
    checks = [Check("trend", "Uptrend (50 > 150 > 200 day averages, 200 rising)", "pass"),
              Check("tight", "Tight base (fell no more than 25 percent)", "pass"),
              Check("rs", "Relative strength 80 or more", "pass"),
              Check("regime", "Nifty above its 200-day average", "pass"),
              Check("volume", "Closed above the 40-day high on 1.5x normal volume", "pass")]
    return Idea(
        id=f"swing:{_position_id(sym, last_day.isoformat())}", desk="swing", source="swing", name=sym, symbol=f"NSE:{sym}-EQ", direction=1,
        action="BUY", grade="setup", status="CANDIDATE", signaled_at=f"{last_day.isoformat()}T15:30:00+05:30", entry=round(close, 2),
        stop=round(close * (1 - dl.RULES["stop"]), 2), target=None, qty=None, risk=None, cost_ratio=None,
        checks=[c.to_dict() for c in checks], blockers=[],
        why=[f"Closed at {close:,.2f}, above its 40-day high of {sig['pivot']:,.2f}, on {sig['volx']:.1f}x normal volume.",
             f"Relative strength {sig['rs']:.0f}. " + BUY_NOTE, EXIT_NOTE],
        context={"pivot": round(sig["pivot"], 2), "volx": round(sig["volx"], 2), "rs": sig["rs"], "reference_close": round(close, 2),
                 "plan": "next_open", "stop_pct": dl.RULES["stop"] * 100, "unit": "reference (the entry is tomorrow's open)"},
        evidence=evidence.for_source("swing"), paper_position=True,
        quality=quality("setup", None, 0, 0, False, sig["rs"], 1), plan=swing_plan(close, sig["pivot"]),
        chart={"file": "swing.json", "in": "charts", "key": _position_id(sym, last_day.isoformat())})


def run(days: dict, nifty=None, results: Optional[set] = None, book: Optional[dict] = None, ledger: Optional[Ledger] = None,
        now: Optional[datetime] = None, log=print) -> Optional[tuple[dict, dict]]:
    """Tonight's swing document and the updated paper book, or None when there is too little history to rate anything."""
    lead = ld.compute(days)
    if lead is None:
        return None
    data, last_day = build_data(days)
    book = book or new_book()
    now = now or clock.now()
    regime = nifty_regime(nifty, last_day)
    results = results or set()

    # 1. follow existing positions through every session since the book was last updated, then tonight's
    first = date.fromisoformat(book["asof"]) if book.get("asof") else None
    sessions = sorted({d for D in data.values() for d in D.d if (first is None or d > first) and d <= last_day})
    if first is None:
        sessions = []                                   # a new book starts tonight: there is nothing to follow yet
    newly_closed = []
    for day in sessions:
        newly_closed.extend(advance(book, data, day))

    # 2. tonight's signals (relative strength is known for tonight only)
    rs_map = lead["rs"]
    trig, setups, gaps = [], [], []
    for sym, D in data.items():
        if not D.n or D.d[-1] != last_day:
            continue
        i = D.n - 1
        rs = rs_map.get(sym)
        sig = evaluate(D, i, rs, regime["nifty_above_200"])
        if sig and triggered(sig):
            active = any(p["symbol"] == sym and p["status"] in ACTIVE for p in book["positions"])
            if not active and not in_cooldown(book, D, sym, i):
                trig.append((sym, D, sig))
        elif not sig:
            near = near_pivot(D, i, rs)
            if near:
                setups.append({"symbol": sym, "close": round(D.c[i], 2), **near})
        g = gap_watch(D, i, sym, results)
        if g:
            gaps.append({"symbol": sym, "stop_hint": round(D.l[i], 2), "hypothesis": True, **g})
    trig.sort(key=lambda t: -(t[2]["rs"] or 0))
    for sym, D, sig in trig:
        book["positions"].append({"id": _position_id(sym, last_day.isoformat()), "symbol": sym, "signal_date": last_day.isoformat(),
                                  "pivot": round(sig["pivot"], 2), "status": "PENDING", "signal_close": round(D.c[-1], 2),
                                  "volx": round(sig["volx"], 2), "rs": sig["rs"]})
    book["asof"] = last_day.isoformat()

    # 3. settled positions go to the ledger for good; old ones leave the book
    if ledger is not None:
        for p in book["positions"]:
            if p["status"] == "CLOSED":
                try:
                    ledger.record(LedgerTrade.from_swing(p, dl.RULES["cost_round_trip"]))
                except (KeyError, TypeError, ValueError) as exc:
                    log(f"Swing: skipped a malformed closed position ({type(exc).__name__}: {exc})")
    cut = (last_day - timedelta(days=PRUNE_DAYS)).isoformat()
    book["positions"] = [p for p in book["positions"] if p["status"] in ACTIVE or (p.get("exit_date") or p.get("signal_date", "")) >= cut]

    setups.sort(key=lambda s: (s["pct_to_pivot"], -s["rs"]))
    gaps.sort(key=lambda g_: -g_["gap_pct"])
    live_positions = [p for p in book["positions"] if p["status"] in ACTIVE]
    charts: dict = {}
    for sym, D, sig in trig:
        charts[_position_id(sym, last_day.isoformat())] = daily_chart(
            D, D.n - 1, ep=D.c[-1], st=D.c[-1] * (1 - dl.RULES["stop"]), lines=[("Break level (40-day high)", sig["pivot"])], el="Signal",
            tn=BUY_NOTE + " The stop shown is for an entry at the signal close.")
    for p in live_positions:
        D = data.get(p["symbol"])
        if D is None or p["id"] in charts:
            continue
        if p["status"] == "PENDING":
            e = D.index.get(date.fromisoformat(p["signal_date"]))
            if e is not None:
                charts[p["id"]] = daily_chart(D, e, ep=D.c[e], st=D.c[e] * (1 - dl.RULES["stop"]), lines=[("Break level (40-day high)", p["pivot"])],
                                              el="Signal", tn=BUY_NOTE + " The stop shown is for an entry at the signal close.")
            continue
        e = D.index.get(date.fromisoformat(p["entry_date"]))
        if e is not None:
            charts[p["id"]] = daily_chart(
                D, e, ep=p["entry"], st=p["stop"], lines=[("Break level (40-day high)", p["pivot"])], el="Entry",
                tn=("The stop has moved up to the entry price." if p.get("breakeven") else "The stop is 8 percent below the entry.") + " " + EXIT_NOTE)
    for s in sorted(setups, key=lambda s: (s["pct_to_pivot"], -s["rs"]))[:10]:
        D = data.get(s["symbol"])
        if D is not None:
            charts[f"{s['symbol']}:setup"] = daily_chart(
                D, D.n - 1, ep=D.c[-1], st=None, lines=[("Break level (40-day high)", s["pivot"])], el="Now",
                tn="Not a setup yet: price has to close above the break level on at least 1.5 times normal volume.")
    closed_recent = sorted((p for p in book["positions"] if p["status"] == "CLOSED"), key=lambda p: p["exit_date"], reverse=True)[:KEEP_CLOSED]
    doc: dict[str, Any] = {
        "schema": SCHEMA, "generated_at": now.isoformat(timespec="seconds"), "asof": last_day.isoformat(),
        "rules": {"rs_min": cx.CONTEXT["rs_min"], "base_days": dl.RULES["base_days"], "volume_multiple": dl.RULES["volume_multiple"],
                  "base_depth": dl.RULES["base_depth"], "stop_pct": dl.RULES["stop"] * 100, "trail_sma": dl.RULES["trail_sma"],
                  "cost_round_trip_pct": dl.RULES["cost_round_trip"] * 100, "near_pivot_pct": NEAR_PIVOT * 100},
        "regime": regime, "breadth": lead["breadth"],
        "triggered": [{"symbol": s, "close": round(D.c[-1], 2), "pivot": round(sig["pivot"], 2), "volx": round(sig["volx"], 2), "rs": sig["rs"],
                       "stop_hint": round(D.c[-1] * (1 - dl.RULES["stop"]), 2), "risk_pct": dl.RULES["stop"] * 100,
                       "sma20": None if D.sma20[-1] is None else round(D.sma20[-1], 2)} for s, D, sig in trig],
        "setups": setups[:MAX_SETUPS],
        "positions": [{"id": p["id"], "symbol": p["symbol"], "status": p["status"], "signal_date": p["signal_date"],
                       "entry_date": p.get("entry_date"), "entry": p.get("entry"), "stop": p.get("stop"), "breakeven": p.get("breakeven"),
                       "last_close": p.get("last_close"), "open_r": open_r(p)} for p in live_positions],
        "recently_closed": [{"id": p["id"], "symbol": p["symbol"], "entry_date": p["entry_date"], "entry": p["entry"],
                             "exit_date": p["exit_date"], "exit": p["exit"], "reason": p["reason"], "r": p.get("r")} for p in closed_recent],
        "gap_watch": gaps[:10],
        "charts": charts,
        "ideas": [_idea(s, D, sig, last_day).to_dict() for s, D, sig in trig],
        "evidence": evidence.for_source("swing"),
        "note": NOTE,
    }
    if regime["nifty_above_200"] is None:
        doc["note"] += " Nifty's history could not be read, so the market filter is unknown and nothing was marked as triggered."
    return doc, book


# ── files ────────────────────────────────────────────────────────────────────────────────────────
def _load_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    os.replace(tmp, path)


def results_from(folder: str) -> set:
    """{(symbol, date)} for every results announcement in results.json (engine/results.py). Used only to mark gap-ups for observation."""
    data = _load_json(os.path.join(folder, "results.json")) or {}
    out = set()
    for e in data.get("events") or []:
        try:
            out.add((str(e["symbol"]), date.fromisoformat(e["date"])))
        except (KeyError, ValueError, TypeError):
            continue
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Find tonight's swing setups and follow the paper book.")
    ap.add_argument("--bhav", required=True, help="folder of NSE daily files (python -m engine.bhav)")
    ap.add_argument("--out", required=True, help="the data folder (swing.json, swing_state.json and the swing ledger go here)")
    ap.add_argument("--days", type=int, default=430, help="calendar days of files to read")
    ap.add_argument("--nifty-cache", default=None, help="folder for the cached Nifty history")
    ap.add_argument("--no-publish", action="store_true")
    a = ap.parse_args(argv)
    end = clock.now().date()
    days = bhav.load_days(a.bhav, end - timedelta(days=a.days), end)
    nifty = None
    try:
        got, _missing = dl.load_daily([dl.NIFTY], cache=a.nifty_cache, years=3, log=lambda m: None)
        nifty = got.get(dl.NIFTY)
    except Exception as exc:                               # Nifty unavailable: the market filter is then unknown, which counts as no
        print("Could not read Nifty's history:", type(exc).__name__)
    os.makedirs(a.out, exist_ok=True)
    state_path = os.path.join(a.out, "swing_state.json")
    result = run(days, nifty, results_from(a.out), _load_json(state_path), Ledger(a.out, SWING_PREFIX))
    if result is None:
        raise SystemExit("Not enough daily files to rate relative strength (need about 14 months).")
    doc, book = result
    _write_json(os.path.join(a.out, "swing.json"), doc)
    _write_json(state_path, book)
    print(f"{doc['asof']}: {len(doc['triggered'])} triggered, {len(doc['setups'])} near their pivot, {len(doc['positions'])} open paper positions, "
          f"{len(doc['gap_watch'])} gap-ups under observation. Nifty above 200-day: {doc['regime']['nifty_above_200']}.")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(root, "_data")
    if not a.no_publish and os.path.isdir(os.path.join(data_dir, ".git")) and os.path.abspath(a.out) == os.path.abspath(data_dir):
        from .store import Store
        Store(data_dir, "unused").publish(own={"swing.json", "swing_state.json"}, own_prefix=(SWING_PREFIX,))
        print("Published to your dashboard.")


if __name__ == "__main__":
    main()
