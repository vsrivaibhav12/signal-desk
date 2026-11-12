"""Stand-ins for things the past cannot give us: old option prices and old contract lists.

Fyers does not keep price history for options that have expired, so an option's price is
estimated from the index with the standard Black-Scholes formula. That makes option results
estimates. The "underlying" figures in the report do not depend on this model at all.
"""
import calendar
import math
from datetime import date, timedelta

from .. import clock
from ..clock import at, ts

RATE = 0.065


def _cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def option_price(spot, strike, secs_left, iv, cp, rate=RATE):
    t = max(secs_left, 60) / (365 * 24 * 3600)
    if iv <= 0:
        return max(0.05, (spot - strike) if cp == "CE" else (strike - spot))
    d1 = (math.log(spot / strike) + (rate + iv * iv / 2) * t) / (iv * math.sqrt(t))
    d2 = d1 - iv * math.sqrt(t)
    if cp == "CE":
        p = spot * _cdf(d1) - strike * math.exp(-rate * t) * _cdf(d2)
    else:
        p = strike * math.exp(-rate * t) * _cdf(-d2) - spot * _cdf(-d1)
    return max(0.05, round(round(p / 0.05) * 0.05, 2))


def _last_weekday(year, month, weekday):
    d = date(year, month, calendar.monthrange(year, month)[1])
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


class SyntheticMaster:
    """Answers the same questions as the real contract list, using today's exchange rules
    (expiry weekday, weekly or monthly, strike gap, lot size) applied to past days."""

    def __init__(self, rules, spot_of_root):
        self.rules, self.spot = rules, spot_of_root

    def _expiry_dates(self, root, day):
        r = self.rules[root]
        wd = r["weekday"]
        out = []
        if r["weekly"]:
            d = day + timedelta(days=(wd - day.weekday()) % 7)
            out = [d + timedelta(days=7 * k) for k in range(8)]
        else:
            y, m = day.year, day.month
            while len(out) < 4:
                e = _last_weekday(y, m, wd)
                if e >= day:
                    out.append(e)
                y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return out

    def choose_expiry(self, name, root, day, min_days, near):
        exps = self._expiry_dates(root, day)
        left = (exps[0] - day).days
        if left >= min_days:
            return ts(at(exps[0], "15:30")), None
        if near == "skip":
            return None, f"too close to expiry ({left} day{'s' if left != 1 else ''} left)"
        cutoff = day + timedelta(days=min_days)
        for e in exps[1:]:
            if e >= cutoff:
                return ts(at(e, "15:30")), None
        return None, "no later contract available"

    def nearest_future(self, name, root, day):
        s = self.spot.get(root) or self.spot.get("NIFTY")
        return {"symbol": s} if s else None

    def pick_option(self, name, root, expiry, price, cp, choice="ATM"):
        r = self.rules[root]
        step = r["step"]
        k = round(price / step) * step
        if str(choice).upper() == "ITM1":
            k = k - step if cp == "CE" else k + step
        exp_day = clock.from_ts(expiry).date()
        return {"symbol": f"BT|{root}|{expiry}|{k:g}|{cp}", "lot": r["lot"], "strike": float(k),
                "desc": f"{root} {exp_day.strftime('%d %b %y')} {k:g} {cp}"}
