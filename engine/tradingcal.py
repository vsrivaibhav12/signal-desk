"""Which days the exchange is open: weekends and NSE's published trading holidays.

Read from NSE's holiday list about once a month and saved. If NSE cannot be reached the saved list is used; names can also be
added under  holidays:  in config.yaml. With no list at all it falls back to weekends only, and says so (`known` is False).
"""
import json
import os
import threading
from datetime import date, datetime, timedelta

from . import nse

URL = "https://www.nseindia.com/api/holiday-master"
REFRESH_EVERY = timedelta(days=20)


def _day(text):
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(text).strip(), fmt).date()
        except ValueError:
            pass
    return None


def nse_holidays():
    """The trading holidays of the capital market, from NSE."""
    data = nse.get_json(URL, {"type": "trading"}, referer="https://www.nseindia.com/resources/exchange-communication-holidays")
    rows = data.get("CM") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("unexpected holiday list from NSE")
    return [d for d in (_day(r.get("tradingDate")) for r in rows) if d]


class TradingCalendar:
    def __init__(self, folder=None, fetch=None, extra=None, log=print):
        self.path = os.path.join(folder, "holidays.json") if folder else None
        self._fetch = fetch or nse_holidays
        self.log = log
        self._thread = None
        st = {}
        if self.path:
            try:
                with open(self.path, encoding="utf-8") as f:
                    st = json.load(f)
            except (OSError, ValueError):
                st = {}
        self.state = {"days": st.get("days", []), "fetched_at": st.get("fetched_at"), "ok": bool(st.get("days")),
                      "fails": st.get("fails", 0), "next_try": st.get("next_try"), "error": st.get("error", "")}
        self.extra = {d for d in (_day(x) for x in (extra or [])) if d}

    @property
    def known(self):
        return bool(self.state["days"])

    def holidays(self):
        return {d for d in (_day(x) for x in self.state["days"]) if d} | self.extra

    def is_trading_day(self, d):
        return d.weekday() < 5 and d not in self.holidays()

    def prev(self, d):
        d -= timedelta(days=1)
        while not self.is_trading_day(d):
            d -= timedelta(days=1)
        return d

    def next(self, d):
        d += timedelta(days=1)
        while not self.is_trading_day(d):
            d += timedelta(days=1)
        return d

    # ── refreshing ───────────────────────────────────────────
    def due(self, now):
        nt = self.state.get("next_try")
        if nt and now < datetime.fromisoformat(nt):
            return False                                        # a failed read is not retried every minute
        at = self.state.get("fetched_at")
        return not at or now - datetime.fromisoformat(at) > REFRESH_EVERY

    def refresh(self, now, force=False):
        if not force and not self.due(now):
            return False
        try:
            days = self._fetch()
        except Exception as e:
            self.state["fails"] += 1
            self.state.update({"ok": self.known, "error": f"{type(e).__name__}: {str(e)[:100]}",
                               "next_try": (now + timedelta(minutes=min(240, 15 * 2 ** (self.state["fails"] - 1)))).isoformat(timespec="seconds")})
            self._save()
            self.log(f"  Trading holidays not refreshed: {self.state['error']}")
            return False
        self.state.update({"days": sorted(d.isoformat() for d in days), "fetched_at": now.isoformat(timespec="seconds"),
                           "ok": True, "fails": 0, "next_try": None, "error": ""})
        self._save()
        return True

    def refresh_async(self, now):
        """Starts a refresh in the background if one is due, so a slow NSE never holds up the engine."""
        if (self._thread and self._thread.is_alive()) or not self.due(now):
            return False
        self._thread = threading.Thread(target=self.refresh, args=(now,), daemon=True)
        self._thread.start()
        return True

    def _save(self):
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, separators=(",", ":"))
        os.replace(tmp, self.path)


def weekends_only():
    """A calendar with no holiday list: weekends are skipped, holidays are not known."""
    return TradingCalendar(None, fetch=lambda: [])
