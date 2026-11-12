"""Past price data: where it comes from and how the replay reads it."""
import json
import os
import time
import urllib.parse
from bisect import bisect_right
from datetime import timedelta

import requests

from .. import clock
from ..clock import at, ts

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{}"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
YAHOO_INDEX = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "SENSEX": "^BSESN",
               "FINNIFTY": "^CNXFIN", "MIDCPNIFTY": "NIFTY_MID_SELECT.NS"}


class Bars:
    """One symbol's candles, grouped by day.  A candle is [start, open, high, low, close, volume]."""

    def __init__(self, candles, res):
        self.res = res
        self.by_day, self.starts = {}, {}
        for c in sorted(candles, key=lambda x: x[0]):
            self.by_day.setdefault(clock.from_ts(c[0]).date(), []).append(c)
        for d, lst in self.by_day.items():
            self.starts[d] = [c[0] for c in lst]
        self.days = sorted(self.by_day)
        self._last = {d: self.by_day[d][-1][4] for d in self.days}

    def visible(self, day, now_ts):
        """Only candles that had already finished by now_ts. Nothing from the future."""
        lst = self.by_day.get(day)
        if not lst:
            return []
        return lst[:bisect_right(self.starts[day], now_ts - self.res)]

    def prev_close(self, day):
        before = [d for d in self.days if d < day]
        return self._last[before[-1]] if before else None

    def first15_volume(self, day):
        t0 = ts(at(day, "09:15"))
        return sum(c[5] for c in self.by_day.get(day, []) if t0 <= c[0] < t0 + 900)


def yahoo_candles(ysym, interval="5m", rng="60d", tries=3):
    last = None
    for k in range(tries):
        try:
            r = requests.get(YAHOO.format(urllib.parse.quote(ysym, safe="%^&")),
                             params={"interval": interval, "range": rng}, headers=UA, timeout=25)
            if r.status_code == 429:
                time.sleep(3 * (k + 1))
                continue
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            out = []
            for i, t in enumerate(res.get("timestamp") or []):
                o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
                if None in (o, h, l, c):
                    continue
                out.append([t, round(o, 4), round(h, 4), round(l, 4), round(c, 4), q["volume"][i] or 0])
            return out
        except Exception as e:
            last = e
            time.sleep(1.5)
    raise RuntimeError(f"Yahoo gave no data for {ysym}: {type(last).__name__}")


def yahoo_stock(symbol):
    return symbol.replace("&", "%26") + ".NS"


def load_yahoo(markets, universe, cache_dir=None, log=print):
    """Returns ({fyers-style symbol: Bars}, how many stocks loaded, notes)."""
    wanted = [(m["spot"], YAHOO_INDEX[m["root"]]) for m in markets if m["root"] in YAHOO_INDEX]
    nifty = next(m for m in markets if m["root"] == "NIFTY")
    if not any(w[0] == nifty["spot"] for w in wanted):
        wanted.append((nifty["spot"], YAHOO_INDEX["NIFTY"]))
    wanted += [(f"NSE:{s}-EQ", yahoo_stock(s)) for s in universe]
    bars, missing = {}, []
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
    for i, (fy, ys) in enumerate(wanted):
        path = os.path.join(cache_dir, urllib.parse.quote(ys, safe="") + ".json") if cache_dir else None
        try:
            if path and os.path.exists(path) and time.time() - os.path.getmtime(path) < 6 * 3600:
                candles = json.load(open(path))
            else:
                candles = yahoo_candles(ys)
                if path:
                    json.dump(candles, open(path, "w"))
                time.sleep(0.15)
            if len(candles) < 500:
                raise RuntimeError("too little data")
            bars[fy] = Bars(candles, 300)
        except Exception as e:
            missing.append(fy)
            log(f"  skipped {fy}: {e}")
        if (i + 1) % 40 == 0:
            log(f"  loaded {i + 1} of {len(wanted)} price histories")
    stocks = sum(1 for k in bars if k.endswith("-EQ"))
    return bars, stocks, missing


def load_fyers(broker, markets, universe, days, log=print):
    """One-minute history from Fyers, fetched in chunks (Fyers allows ~100 days per request)."""
    end = clock.now().date()
    start = end - timedelta(days=days)
    wanted = [m["spot"] for m in markets if m.get("spot")]
    wanted += [f"NSE:{s}-EQ" for s in universe]
    bars, missing = {}, []
    for i, sym in enumerate(wanted):
        candles, hi = [], end
        try:
            while hi > start:
                lo = max(start, hi - timedelta(days=95))
                chunk = broker.history(sym, "1", lo, hi)
                candles += chunk
                hi = lo - timedelta(days=1)
            if len(candles) < 2000:
                raise RuntimeError("too little data")
            bars[sym] = Bars(candles, 60)
        except Exception as e:
            missing.append(sym)
            log(f"  skipped {sym}: {e}")
        if (i + 1) % 25 == 0:
            log(f"  loaded {i + 1} of {len(wanted)} price histories")
    return bars, sum(1 for k in bars if k.endswith("-EQ")), missing
