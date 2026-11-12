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
                if not isinstance(j, dict):
                    j = {}
                api_code = j.get("code")
                try:
                    api_code = int(api_code) if api_code is not None else None
                except (TypeError, ValueError):
                    pass
                detail = self._error_detail(j)
                if r.status_code == 401 or (r.status_code < 400 and api_code in AUTH_CODES):
                    self._record(kind, "error", time.monotonic() - started)
                    raise AuthError(detail or "Fyers login expired")
                if r.status_code == 429 or r.status_code >= 500:
                    self._record(kind, "refused", time.monotonic() - started)
                    last = FyersError(f"Fyers {kind} endpoint HTTP {r.status_code}: {detail or 'provider temporarily unavailable'}")
                    time.sleep(2 + 3 * attempt)
                    continue
                if r.status_code >= 400:
                    self._record(kind, "error", time.monotonic() - started)
                    raise FyersError(f"Fyers {kind} endpoint HTTP {r.status_code}: {detail or 'request rejected'}")
                if j.get("s") not in ("ok", "no_data"):
                    self._record(kind, "error", time.monotonic() - started)
                    raise FyersError(f"Fyers {kind} endpoint rejected request: {detail or 'no provider detail returned'}")
                self._record(kind, "ok", time.monotonic() - started)
                return j
            except requests.RequestException as e:
                self._record(kind, "error", time.monotonic() - started)
                last = FyersError(f"network problem: {type(e).__name__}")
                time.sleep(2 + 3 * attempt)
        raise last

    @staticmethod
    def _error_detail(payload):
        """Return useful provider diagnostics without exposing request headers or tokens."""
        if not isinstance(payload, dict):
            return ""
        parts = []
        for key in ("message", "error", "code"):
            value = payload.get(key)
            if value not in (None, "", "ok"):
                parts.append(f"{key}={str(value)[:160]}")
        return "; ".join(parts)

    @staticmethod
    def _row_error(row):
        if not isinstance(row, dict):
            return "invalid quote row"
        value = row.get("v") or {}
        if not isinstance(value, dict):
            value = {}
        status = row.get("s") or "unknown status"
        details = []
        for source in (row, value):
            for key in ("message", "errmsg", "error", "code"):
                item = source.get(key)
                if item not in (None, "", "ok"):
                    details.append(f"{key}={str(item)[:120]}")
        suffix = "; ".join(details)
        return f"symbol status={status}" + (f" ({suffix})" if suffix else "")

    def probe_detail(self, symbol="NSE:NIFTY50-INDEX"):
        """Return (usable, explanation); preserve auth errors and per-symbol API messages."""
        if not self.token:
            return False, "No Fyers access token is loaded; complete the Fyers login first."
        try:
            j = self._get(f"{DATA}/quotes", {"symbols": symbol})
        except AuthError:
            raise
        except BrokerError as exc:
            return False, f"Fyers quotes API: {exc}"
        rows = j.get("d") or []
        if not rows:
            return False, f"Fyers quotes API returned no symbol rows for {symbol}. {self._error_detail(j)}".strip()
        matching = [row for row in rows if isinstance(row, dict) and row.get("n") == symbol]
        candidates = matching or [row for row in rows if isinstance(row, dict)]
        for row in candidates:
            # Fyers returns a per-symbol status inside an otherwise successful
            # HTTP response. A stale/partial `v.lp` must not override `s=error`.
            if str(row.get("s", "")).lower() != "ok":
                continue
            value = row.get("v") or {}
            if not isinstance(value, dict):
                value = {}
            try:
                lp = float(value.get("lp"))
                if lp > 0:
                    return True, {"symbol": symbol, "ltp": lp, "row_status": "ok"}
            except (TypeError, ValueError, OverflowError):
                pass
        first = candidates[0] if candidates else {}
        reported = self._row_error(first) if first else "no matching symbol row"
        top = self._error_detail(j)
        if top:
            reported += f"; response {top}"
        return False, f"Fyers has no usable LTP for {symbol}: {reported}. Check that the Fyers app has Market Data permission and that MCX data is enabled for the account."

    def probe(self, symbol="NSE:NIFTY50-INDEX"):
        """Boolean compatibility wrapper; diagnostics are available from probe_detail()."""
        try:
            ok, _ = self.probe_detail(symbol)
            return bool(ok)
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
                # Respect Fyers' per-symbol status. An error row may still carry
                # diagnostic/partial fields; never turn those into a market quote.
                if not isinstance(d, dict) or str(d.get("s", "")).lower() != "ok":
                    continue
                v = d.get("v") or {}
                if not isinstance(v, dict):
                    continue
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
