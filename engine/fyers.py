"""Talks to Fyers for prices. Read-only: this tool never places an order."""
import hashlib
import time
import urllib.parse

import requests

API = "https://api-t1.fyers.in/api/v3"
DATA = "https://api-t1.fyers.in/data"
AUTH_CODES = {-8, -15, -16, -17}


class FyersError(Exception):
    pass


class AuthError(FyersError):
    pass


class Fyers:
    def __init__(self, app_id, secret):
        self.app_id = app_id
        self.secret = secret
        self.token = None
        self._calls = []

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

    def _get(self, url, params=None):
        last = None
        for attempt in range(3):
            self._throttle()
            try:
                r = requests.get(url, params=params, timeout=15, headers={
                    "Authorization": f"{self.app_id}:{self.token}", "version": "3"})
                try:
                    j = r.json()
                except ValueError:
                    j = {}
                if r.status_code == 401 or j.get("code") in AUTH_CODES:
                    raise AuthError(j.get("message", "login expired"))
                if r.status_code == 429 or r.status_code >= 500:
                    last = FyersError(f"Fyers busy ({r.status_code})")
                    time.sleep(2 + 3 * attempt)
                    continue
                if j.get("s") not in ("ok", "no_data"):
                    raise FyersError(j.get("message") or f"Fyers error {r.status_code}")
                return j
            except requests.RequestException as e:
                last = FyersError(f"network problem: {type(e).__name__}")
                time.sleep(2 + 3 * attempt)
        raise last

    def probe(self):
        if not self.token:
            return False
        try:
            self._get(f"{DATA}/quotes", {"symbols": "NSE:NIFTY50-INDEX"})
            return True
        except FyersError:
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
            for d in j.get("d") or []:
                v = d.get("v") or {}
                if d.get("s") == "ok" or v.get("lp"):
                    out[d.get("n")] = {"lp": v.get("lp"), "prev_close": v.get("prev_close_price"),
                                       "volume": v.get("volume")}
        return out
