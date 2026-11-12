"""Talks to Fyers for prices. Read-only: this tool never places an order."""
import hashlib
import time
import urllib.parse
from datetime import datetime

import requests

API = "https://api-t1.fyers.in/api/v3"
DATA = "https://api-t1.fyers.in/data"
AUTH_CODES = {-8, -15, -16, -17}


from .errors import AuthError, BrokerError  # noqa: E402  (shared with the Kotak adapter)
from .clock import IST


class FyersError(BrokerError):
    pass


class Fyers:
    def __init__(self, app_id, secret):
        self.app_id = app_id
        self.secret = secret
        self.token = None
        self._calls = []
        self.since = time.time()
        self.stats = {"history": {"calls": 0, "ok": 0, "error": 0, "refused": 0, "ms": 0},
                      "quotes": {"calls": 0, "ok": 0, "error": 0, "refused": 0, "ms": 0},
                      "other": {"calls": 0, "ok": 0, "error": 0, "refused": 0, "ms": 0}}

    # ── login ────────────────────────────────────────────────
    def _hash(self):
        return hashlib.sha256(f"{self.app_id}:{self.secret}".encode()).hexdigest()

    def login_url(self, redirect_uri):
        q = urllib.parse.urlencode({"client_id": self.app_id, "redirect_uri": redirect_uri,
                                    "response_type": "code", "state": "signaldesk"})
        return f"{API}/generate-authcode?{q}"

    def exchange_code(self, code):
        j = requests.post(f"{API}/validate-authcode", timeout=20, json={
            "grant_type": "authorization_code", "appIdHash": self._hash(), "code": code}).json()
        if j.get("s") != "ok" or not j.get("access_token"):
            raise AuthError(j.get("message", "login code was not accepted"))
        return j["access_token"], j.get("refresh_token")

    def refresh(self, refresh_token, pin):
        j = requests.post(f"{API}/validate-refresh-token", timeout=20, json={
            "grant_type": "refresh_token", "appIdHash": self._hash(),
            "refresh_token": refresh_token, "pin": str(pin)}).json()
        if j.get("s") != "ok" or not j.get("access_token"):
            raise AuthError(j.get("message", "refresh was not accepted"))
        return j["access_token"]

    # ── data ─────────────────────────────────────────────────
    def _throttle(self):
        # Fyers allows 10 calls a second and 200 a minute. Stay well inside both.
        now = time.monotonic()
        self._calls = [t for t in self._calls if now - t < 60]
        if len(self._calls) >= 170:
            time.sleep(max(0, 60 - (now - self._calls[0])) + 0.1)
        elif self._calls and now - self._calls[-1] < 0.15:
            time.sleep(0.15 - (now - self._calls[-1]))
        self._calls.append(time.monotonic())

    def _record(self, kind, outcome, elapsed):
        row = self.stats.setdefault(kind, {"calls": 0, "ok": 0, "error": 0, "refused": 0, "ms": 0})
        row["calls"] += 1
        row[outcome] += 1
        row["ms"] += int(elapsed * 1000)

    def stats_snapshot(self):
        by = {name: {"calls": row["calls"], "ok": row["ok"], "errors": row["error"],
                     "refused": row["refused"],
                     "avg_ms": round(row["ms"] / row["calls"]) if row["calls"] else 0}
              for name, row in sorted(self.stats.items()) if row["calls"]}
        return {"since": datetime.fromtimestamp(self.since, IST).isoformat(timespec="seconds"),
                "total": sum(row["calls"] for row in self.stats.values()), "by_method": by,
                "history_rate": 0, "cooling": 0}

    def _get(self, url, params=None):
        kind = url.rstrip("/").split("/")[-1]
        kind = kind if kind in ("history", "quotes") else "other"
        last = None
        for attempt in range(3):
            self._throttle()
            started = time.monotonic()
            try:
                r = requests.get(url, params=params, timeout=15, headers={
                    "Authorization": f"{self.app_id}:{self.token}", "version": "3"})
                try:
                    j = r.json()
                except ValueError:
                    j = {}
                if r.status_code == 401 or j.get("code") in AUTH_CODES:
                    self._record(kind, "error", time.monotonic() - started)
                    raise AuthError(j.get("message", "login expired"))
                if r.status_code == 429 or r.status_code >= 500:
                    self._record(kind, "refused", time.monotonic() - started)
                    last = FyersError(f"Fyers busy ({r.status_code})")
                    time.sleep(2 + 3 * attempt)
                    continue
                if j.get("s") not in ("ok", "no_data"):
                    self._record(kind, "error", time.monotonic() - started)
                    raise FyersError(j.get("message") or f"Fyers error {r.status_code}")
                self._record(kind, "ok", time.monotonic() - started)
                return j
            except requests.RequestException as e:
                self._record(kind, "error", time.monotonic() - started)
                last = FyersError(f"network problem: {type(e).__name__}")
                time.sleep(2 + 3 * attempt)
        raise last

    def probe(self, symbol="NSE:NIFTY50-INDEX"):
        """Check a known symbol on the account's permitted market-data segment."""
        if not self.token:
            return False
        try:
            j = self._get(f"{DATA}/quotes", {"symbols": symbol})
            rows = j.get("d") or []
            for row in rows:
                value = row.get("v") or {}
                try:
                    if value.get("lp") is not None and float(value["lp"]) > 0:
                        return True
                except (TypeError, ValueError):
                    continue
            return False
        except BrokerError:
            return False

    def history(self, symbol, resolution, day_from, day_to):
        j = self._get(f"{DATA}/history", {
            "symbol": symbol, "resolution": resolution, "date_format": "1",
            "range_from": day_from.isoformat(), "range_to": day_to.isoformat(), "cont_flag": "1"})
        return j.get("candles") or []

    def quotes(self, symbols):
        out = {}
        symbols = list(symbols)
        for i in range(0, len(symbols), 50):
            j = self._get(f"{DATA}/quotes", {"symbols": ",".join(symbols[i:i + 50])})
            received_at = int(time.time())
            for d in j.get("d") or []:
                v = d.get("v") or {}
                if d.get("s") == "ok" or v.get("lp"):
                    # Fyers quote responses expose exchange feed/trade timestamps on
                    # supported feeds. Keep both the exchange clock and receipt time;
                    # the latter is only a fallback, never mislabelled as exchange time.
                    raw_stamp = v.get("exch_feed_time") or v.get("exch_trade_time") or v.get("tt") or v.get("last_traded_time")
                    stamp_invalid = False
                    try:
                        stamp = int(float(raw_stamp)) if raw_stamp not in (None, "") else None
                        if raw_stamp not in (None, "") and stamp is None:
                            stamp_invalid = True
                        # Some Fyers fields are formatted as milliseconds.
                        if stamp and stamp > 10_000_000_000:
                            stamp //= 1000
                    except (TypeError, ValueError, OverflowError):
                        stamp, stamp_invalid = None, raw_stamp not in (None, "")
                    depth = v.get("depth") or {}
                    buys = depth.get("buy") or []
                    sells = depth.get("sell") or []
                    bid = buys[0] if buys else {}
                    ask = sells[0] if sells else {}
                    out[d.get("n")] = {"lp": v.get("lp"), "prev_close": v.get("prev_close_price"),
                                       "volume": v.get("volume"), "ts": stamp, "ts_invalid": stamp_invalid,
                                       "received_ts": received_at, "ts_source": "invalid" if stamp_invalid else "exchange" if stamp else "response",
                                       "bid": bid.get("price"), "ask": ask.get("price"),
                                       "bid_qty": bid.get("volume") or bid.get("qty"),
                                       "ask_qty": ask.get("volume") or ask.get("qty"),
                                       "oi": v.get("oi") or v.get("open_interest")}
        return out
