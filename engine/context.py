"""Market context research: relative strength, breadth, a relative-strength portfolio, and a trend-following index benchmark.

These were the pieces an outside review said the tool lacked (what MarketStrength shows: relative strength, breadth, sector rotation).
They are built here from the same survivor-free NSE daily prices as the daily research, and TESTED, not just added: does a context
filter improve the base-and-breakout, and does buying relative strength on its own beat simply holding the index?

THE SETTINGS BELOW WERE FIXED BEFORE ANY RESULT WAS SEEN. The tests that use them are listed here and nowhere else:
  1. breakout + uptrend + tight base, plus relative strength 80 or more                      (with each of the two exits)
  2. ... plus breadth (50% or more of the universe above its 50-day average)                 (each exit)
  3. ... plus both                                                                           (each exit)
  4. a relative-strength portfolio: the 20 strongest of the universe, equal weight, rebalanced monthly. Two scores: the classic
     12-month return skipping the last month, and the weighted 3/6/9/12-month score (40/20/20/20) that MarketStrength describes.
     Each with and without a rule to hold cash when Nifty is below its 200-day average.
  5. benchmarks: Nifty buy and hold; Nifty held only while above its 200-day average; all members equal weight, rebalanced monthly.
These add 6 breakout tests and 4 portfolio tests to the 8 breakout tests already run. With that many looks, a lone near-miss is what
chance produces, and the report says so.

Not done: SECTOR ROTATION. It needs each share's sector at each date, and no free, point-in-time classification exists (NSE publishes
today's sectors only, and shares that were later delisted are missing from it). Using today's sectors would bring the survivorship back.
"""
from collections import defaultdict

CONTEXT = {
    "rs_weights": ((63, 0.4), (126, 0.2), (189, 0.2), (252, 0.2)),   # 3, 6, 9, 12 months of sessions
    "rs_min": 80.0,                    # percentile among the universe on that date
    "breadth_min": 0.5,                # share of the universe above its 50-day average
    "top": 20,                         # shares held in the relative-strength portfolio
    "classic_lookback": 252,           # 12-month return ...
    "classic_skip": 21,                # ... skipping the last month
    "cost_side": 0.002,                # 0.20% each way (0.40% round trip) on shares bought or sold
    "index_switch_cost": 0.001,        # 0.10% to move in or out of the index
}


def universe_by_date(data, members=None):
    """{date: set of keys} the universe on each date (everything that traded, if no point-in-time list is given)."""
    if members is not None:
        return members
    out = defaultdict(set)
    for sym, D in data.items():
        for d in D.d:
            out[d].add(sym)
    return out


def rs_score(D, i):
    """Weighted 3/6/9/12-month return at index i (uses only data up to i), or None."""
    if i < CONTEXT["rs_weights"][-1][0]:
        return None
    return sum(w * (D.c[i] / D.c[i - k] - 1) for k, w in CONTEXT["rs_weights"])


def classic_score(D, i):
    a, b = CONTEXT["classic_lookback"], CONTEXT["classic_skip"]
    if i < a:
        return None
    return D.c[i - b] / D.c[i - a] - 1


def rs_percentiles(data, members, dates, score=rs_score):
    """{key: {date: percentile 0-100}} of each member's score among the members that day."""
    out = defaultdict(dict)
    for d in dates:
        rows = []
        for sym in members.get(d, ()):
            D = data.get(sym)
            i = D.index.get(d) if D else None
            sc = score(D, i) if i is not None else None
            if sc is not None:
                rows.append((sc, sym))
        if len(rows) < 20:
            continue
        rows.sort()
        n = len(rows) - 1
        for k, (_, sym) in enumerate(rows):
            out[sym][d] = 100.0 * k / n
    return out


def breadth_by_date(data, members, dates, ma="sma50"):
    """{date: share of the universe closing above its own moving average} (the 50-day by default; "sma200" for the bear gauge)."""
    out = {}
    for d in dates:
        above = tot = 0
        for sym in members.get(d, ()):
            D = data.get(sym)
            i = D.index.get(d) if D else None
            avg = getattr(D, ma)[i] if i is not None else None
            if i is None or avg is None:
                continue
            tot += 1
            above += D.c[i] > avg
        if tot >= 20:
            out[d] = above / tot
    return out


# ── a book of equal-weight shares, rebalanced monthly ────────────────────────────────────────────
def month_starts(dates):
    seen, out = set(), []
    for d in dates:
        if (d.year, d.month) not in seen:
            seen.add((d.year, d.month))
            out.append(d)
    return set(out)


def run_book(data, dates, pick, cost_side=None):
    """Equity (starting at 1.0) of a book that, on the first session of each month, sells what is no longer wanted and splits its cash
    over the newly wanted shares. `pick(prev_date, d)` returns the keys to hold, decided only from data up to `prev_date`; trades are
    at the close of `d`. A share that stops trading is sold at its last price. Returns (curve, trades_made)."""
    cost = CONTEXT["cost_side"] if cost_side is None else cost_side
    reb = month_starts(dates)
    cash, held, last, curve, made = 1.0, {}, {}, [], 0
    prev = None
    for d in dates:
        for sym in list(held):                                   # a share that has stopped trading is closed at its last price
            D = data[sym]
            if D.d[-1] < d:
                cash += held.pop(sym) * D.c[-1] * (1 - cost)
                made += 1
        for sym, sh in held.items():
            j = data[sym].index.get(d)
            if j is not None:
                last[sym] = data[sym].c[j]
        if d in reb and prev is not None:
            want = [s for s in pick(prev, d) if data[s].index.get(d) is not None]
            for sym in list(held):
                if sym not in want:
                    cash += held.pop(sym) * last.get(sym, data[sym].c[-1]) * (1 - cost)
                    made += 1
            new = [s for s in want if s not in held]
            if new and cash > 0:
                each = cash / len(new)
                for s in new:
                    px = data[s].c[data[s].index[d]]
                    held[s] = each * (1 - cost) / px
                    last[s] = px
                    made += 1
                cash = 0.0
        curve.append(cash + sum(sh * last.get(s, data[s].c[-1]) for s, sh in held.items()))
        prev = d
    return curve, made


def momentum_picker(data, members, nifty, score, hold_cash_below_200=False):
    """A pick function for run_book: the strongest shares by `score` among those in the universe, or none when Nifty is weak."""
    top = CONTEXT["top"]

    def pick(prev, d):
        if hold_cash_below_200:
            j = nifty.index.get(prev)
            if j is None or nifty.sma200[j] is None or nifty.c[j] <= nifty.sma200[j]:
                return []
        rows = []
        for sym in members.get(prev, ()):
            D = data.get(sym)
            i = D.index.get(prev) if D else None
            sc = score(D, i) if i is not None else None
            if sc is not None:
                rows.append((sc, sym))
        rows.sort(reverse=True)
        return [s for _, s in rows[:top]]
    return pick


def equal_weight_picker(data, members):
    return lambda prev, d: sorted(members.get(prev, ()))


def index_trend_hold(nifty, dates):
    """Nifty held while it closes above its 200-day average (decided on the previous close), cash otherwise. Starts at 1.0."""
    eq, invested, prev = 1.0, False, None
    curve, switches = [], 0
    for d in dates:
        j = nifty.index.get(d)
        if prev is not None and j is not None:
            pj = nifty.index[prev]
            if invested:
                eq *= nifty.c[j] / nifty.c[pj]
            want = nifty.sma200[pj] is not None and nifty.c[pj] > nifty.sma200[pj]
            if want != invested:
                eq *= 1 - CONTEXT["index_switch_cost"]
                invested = want
                switches += 1
        curve.append(eq)
        if j is not None:
            prev = d
    return curve, switches


def buy_and_hold(nifty, dates):
    base = nifty.c[nifty.index[dates[0]]]
    return [nifty.c[nifty.index[d]] / base for d in dates if d in nifty.index]
