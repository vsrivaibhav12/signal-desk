"""Price history for the strategy tools: Fyers first, Yahoo Finance as the fallback."""
import os
import sys
from datetime import timedelta

from .. import clock
from ..backtest.data import load_yahoo
from .indicators import Series


def series_from_yahoo(insts, markets, universe, cache_dir=None, log=print):
    """About 60 days of 5-minute candles, free, no login."""
    bars, _, missing = load_yahoo(markets, universe, cache_dir=cache_dir, log=log)
    out = {}
    for sym, b in bars.items():
        if sym in insts:
            out[sym] = Series([c for d in b.days for c in b.by_day[d]])
    return out, missing


def series_from_fyers(fy, symbols, days, log=print):
    """5-minute candles from Fyers, fetched in chunks (Fyers allows about 100 days per request)."""
    end = clock.now().date()
    start = end - timedelta(days=days)
    out, missing = {}, []
    for n, sym in enumerate(symbols):
        candles, hi = [], end
        try:
            while hi > start:
                lo = max(start, hi - timedelta(days=95))
                candles += fy.history(sym, "5", lo, hi)
                hi = lo - timedelta(days=1)
            if len(candles) < 500:
                raise RuntimeError("too little data")
            seen, uniq = set(), []
            for c in sorted(candles, key=lambda x: x[0]):
                if c[0] not in seen:
                    seen.add(c[0])
                    uniq.append(c)
            out[sym] = Series(uniq)
        except Exception as e:
            missing.append(sym)
            log(f"  Fyers gave nothing for {sym}: {e}")
        if (n + 1) % 25 == 0:
            log(f"  loaded {n + 1} of {len(symbols)} price histories from Fyers")
    return out, missing


def quiet_fyers_login(root, log=print):
    """Uses the login the live engine saved. Never messages you. Returns a ready Fyers object or None."""
    env = os.environ
    if not (env.get("FYERS_APP_ID") and env.get("FYERS_SECRET_KEY") and env.get("TELEGRAM_BOT_TOKEN")):
        return None
    try:
        from ..fyers import Fyers
        from ..store import Store
        fy = Fyers(env["FYERS_APP_ID"], env["FYERS_SECRET_KEY"])
        store = Store(os.path.join(root, "_data"), env["FYERS_SECRET_KEY"] + env["TELEGRAM_BOT_TOKEN"])
        secure = store.load_secure()
        fy.token = secure.get("access_token")
        if fy.token and fy.probe():
            return fy
        pin = env.get("FYERS_PIN", "").strip()
        if secure.get("refresh_token") and pin:
            fy.token = fy.refresh(secure["refresh_token"], pin)
            if fy.probe():
                return fy
    except Exception as e:
        log(f"Fyers login was not available ({e}).")
    return None
