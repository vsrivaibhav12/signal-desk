"""Price history for the strategy tools: Kotak Neo first (or Fyers), Yahoo Finance as the fallback."""
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


def series_from_broker(broker, symbols, days, log=print, label="the broker"):
    """5-minute candles from Kotak Neo or Fyers. The broker class splits long ranges into the sizes its API allows."""
    end = clock.now().date()
    start = end - timedelta(days=days)
    out, missing = {}, []
    for n, sym in enumerate(symbols):
        try:
            candles, hi = [], end
            while hi > start:                       # Fyers takes about 100 days a request; Kotak splits these further itself
                lo = max(start, hi - timedelta(days=95))
                candles += broker.history(sym, "5", lo, hi)
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
            log(f"  {label} gave nothing for {sym}: {e}")
        if (n + 1) % 25 == 0:
            log(f"  loaded {n + 1} of {len(symbols)} price histories from {label}")
    return out, missing


def series_from_fyers(fy, symbols, days, log=print):
    return series_from_broker(fy, symbols, days, log, "Fyers")


def quiet_kotak(log=print):
    """Kotak Neo needs only the consumer key, so there is nothing to log in to. Returns a ready Kotak or None."""
    key = os.environ.get("KOTAK_CONSUMER_KEY", "").strip()
    if not key:
        return None
    try:
        from ..kotak import Kotak
        import yaml
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(root, "config.yaml"), encoding="utf-8") as f:
            bc = ((yaml.safe_load(f) or {}).get("broker") or {}).get("kotak") or {}
        k = Kotak(key, environment=bc.get("environment", "prod"), index_tokens=bc.get("index_tokens"), log=log)
        if k.probe():
            return k
        log("Kotak Neo did not accept the consumer key.")
    except Exception as e:
        log(f"Kotak Neo was not available ({e}).")
    return None


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
