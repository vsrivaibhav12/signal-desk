"""Reads Fyers' free public contract lists to find the right futures and options."""
import csv
import io
from datetime import timedelta

import requests

from . import clock

URL = "https://public.fyers.in/sym_details/{}.csv"


class Master:
    def __init__(self, loader=None):
        self._loader = loader or self._download
        self._cache = {}

    @staticmethod
    def _download(name):
        r = requests.get(URL.format(name), timeout=60)
        r.raise_for_status()
        return r.text

    def rows(self, name, root):
        key = (name, root)
        if key not in self._cache:
            if name not in self._cache:
                self._cache[name] = self._loader(name)
            out = []
            for r in csv.reader(io.StringIO(self._cache[name])):
                if len(r) < 17 or r[13] != root or not r[8]:
                    continue
                out.append({"desc": r[1].strip(), "lot": int(float(r[3] or 1)), "expiry": int(r[8]),
                            "symbol": r[9], "scrip": r[12], "und_scrip": r[14],
                            "strike": float(r[15]), "type": r[16]})
            self._cache[key] = out
        return self._cache[key]

    def futures(self, name, root):
        return sorted((r for r in self.rows(name, root) if r["symbol"].endswith("FUT")),
                      key=lambda r: r["expiry"])

    def nearest_future(self, name, root, day):
        for f in self.futures(name, root):
            if clock.from_ts(f["expiry"]).date() >= day:
                return f
        return None

    def choose_expiry(self, name, root, day, min_days, near):
        """Returns (expiry, None) or (None, reason)."""
        exps = sorted({r["expiry"] for r in self.rows(name, root) if r["type"] in ("CE", "PE")})
        exps = [e for e in exps if clock.from_ts(e).date() >= day]
        if not exps:
            return None, "no option contracts found"
        left = (clock.from_ts(exps[0]).date() - day).days
        if left >= min_days:
            return exps[0], None
        if near == "skip":
            return None, f"too close to expiry ({left} day{'s' if left != 1 else ''} left)"
        cutoff = day + timedelta(days=min_days)
        for e in exps[1:]:
            if clock.from_ts(e).date() >= cutoff:
                return e, None
        return None, "no later contract available"

    def future_for_expiry(self, name, root, expiry, day):
        """For MCX, the futures contract a given option expiry is based on."""
        opts = [r for r in self.rows(name, root) if r["expiry"] == expiry and r["type"] in ("CE", "PE")]
        if opts:
            for f in self.futures(name, root):
                if f["scrip"] == opts[0]["und_scrip"]:
                    return f
        return self.nearest_future(name, root, day)

    def pick_option(self, name, root, expiry, price, cp, choice="ATM"):
        opts = sorted((r for r in self.rows(name, root) if r["expiry"] == expiry and r["type"] == cp),
                      key=lambda r: r["strike"])
        if not opts:
            return None
        i = min(range(len(opts)), key=lambda k: abs(opts[k]["strike"] - price))
        if str(choice).upper() == "ITM1":
            i = max(0, i - 1) if cp == "CE" else min(len(opts) - 1, i + 1)
        return opts[i]
