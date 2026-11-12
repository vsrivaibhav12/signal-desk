"""Results segment: which Nifty 200 companies report, and when, so their stocks are on the watchlist before the move.

Source: NSE's public event calendar (board meetings that will consider financial results). It is read a few times a day.
If NSE cannot be reached the last good copy is kept and the dashboard says how old it is; names can also be added by hand
under  results: extra:  in config.yaml. NSE does not say whether a company reports before, during or after the market, so a
company is watched from the day before its date until the day after, and the price and volume at the open confirm the reaction.
"""
import json
import os
import threading
from datetime import date, datetime, timedelta

from . import clock, nse

API = "https://www.nseindia.com/api/event-calendar"
REFRESH_EVERY = timedelta(hours=3)
RETRY_FIRST, RETRY_MAX = timedelta(minutes=10), timedelta(hours=2)      # after a failed read: 10 min, then 20, 40 ... up to 2 h
LOOKBACK, LOOKAHEAD = 4, 8            # days around today that are fetched
# Biggest Nifty weights. Their results move the index, so index option ideas mention them.
HEAVY = {"HDFCBANK", "RELIANCE", "ICICIBANK", "INFY", "TCS", "BHARTIARTL", "LT", "ITC", "SBIN", "AXISBANK",
         "KOTAKBANK", "HINDUNILVR", "M&M", "BAJFINANCE", "MARUTI", "SUNPHARMA", "NTPC", "HCLTECH", "TATAMOTORS", "TMPV"}


def prev_trading_day(day):
    d = day - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def next_trading_day(day):
    d = day + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _parse_day(text):
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(text).strip(), fmt).date()
        except ValueError:
            pass
    return None


def nse_fetch(frm, to):
    """The raw event list from NSE."""
    data = nse.get_json(API, {"index": "equities", "from_date": frm.strftime("%d-%m-%Y"), "to_date": to.strftime("%d-%m-%Y")})
    if not isinstance(data, list):
        raise ValueError("unexpected reply from NSE")
    return data


class Results:
    def __init__(self, folder, universe, fetch=None, extra=None, log=print, cal=None):
        self.path = os.path.join(folder, "results.json")
        self.universe = set(universe)
        self._fetch = fetch or nse_fetch
        self.extra = extra or []
        self.log = log
        self.cal = cal                                         # a TradingCalendar; None = weekends only
        self._thread = None
        self.state = self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = {}
        st.setdefault("events", [])
        st.setdefault("fetched_at", None)
        st.setdefault("ok", False)
        st.setdefault("error", "")
        st.setdefault("fails", 0)
        st.setdefault("next_try", None)
        return st

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, self.path)

    # ── fetching ─────────────────────────────────────────────
    def _prev(self, d):
        return self.cal.prev(d) if self.cal else prev_trading_day(d)

    def _next(self, d):
        return self.cal.next(d) if self.cal else next_trading_day(d)

    def stale(self, now):
        """True when a new read is due: the last good copy is old, and a failed attempt has had time to cool off."""
        nt = self.state.get("next_try")
        if nt and now < datetime.fromisoformat(nt):
            return False
        at = self.state.get("fetched_at")
        if not at:
            return True
        try:
            return now - datetime.fromisoformat(at) > REFRESH_EVERY
        except ValueError:
            return True

    def refresh_async(self, now):
        """Starts a refresh in the background if one is due, so a slow or blocked NSE never holds up the engine."""
        if (self._thread and self._thread.is_alive()) or not self.stale(now):
            return False
        self._thread = threading.Thread(target=self.refresh, args=(now,), daemon=True)
        self._thread.start()
        return True

    def refresh(self, now, force=False):
        """Reads NSE's calendar when the saved copy is old. Returns True when something new was saved."""
        if not force and not self.stale(now):
            return False
        today = now.date()
        try:
            raw = self._fetch(today - timedelta(days=LOOKBACK), today + timedelta(days=LOOKAHEAD))
        except Exception as e:                                # NSE blocks some networks; keep the last good copy
            fails = self.state.get("fails", 0) + 1
            wait = min(RETRY_MAX, RETRY_FIRST * 2 ** (fails - 1))
            self.state.update({"ok": False, "error": f"{type(e).__name__}: {str(e)[:120]}", "fails": fails,
                               "checked_at": now.isoformat(timespec="seconds"),
                               "next_try": (now + wait).isoformat(timespec="seconds")})
            self.save()
            self.log(f"  Results calendar not refreshed: {self.state['error']} (next try in {int(wait.total_seconds() // 60)} min)")
            return False
        seen, events = set(), []
        for e in raw:
            sym, d = str(e.get("symbol", "")).strip(), _parse_day(e.get("date"))
            if not sym or d is None or sym not in self.universe or "result" not in str(e.get("purpose", "")).lower():
                continue
            if (sym, d) in seen:
                continue
            seen.add((sym, d))
            events.append({"symbol": sym, "company": e.get("company", sym), "date": d.isoformat(),
                           "purpose": e.get("purpose", ""), "source": "NSE"})
        for x in self.extra:                                  # added by hand in config.yaml
            sym, d = str(x.get("symbol", "")).strip(), _parse_day(x.get("date"))
            if sym in self.universe and d and (sym, d) not in seen:
                seen.add((sym, d))
                events.append({"symbol": sym, "company": sym, "date": d.isoformat(), "purpose": "Results (added by hand)",
                               "source": "config"})
        events.sort(key=lambda e: (e["date"], e["symbol"]))
        self.state.update({"events": events, "fetched_at": now.isoformat(timespec="seconds"), "ok": True,
                           "error": "", "fails": 0, "next_try": None, "checked_at": now.isoformat(timespec="seconds"),
                           "all_reporting": len(raw)})
        self.save()
        return True

    # ── questions the rest of the tool asks ──────────────────
    def groups(self, today):
        """Results already out since the last session, due today, and due before the next session."""
        last_from, next_to = self._prev(today), self._next(today)
        out = {"last": [], "today": [], "next": []}
        for e in self.state["events"]:
            d = date.fromisoformat(e["date"])
            if d == today:
                out["today"].append(e)
            elif last_from <= d < today:
                out["last"].append(e)
            elif today < d <= next_to:
                out["next"].append(e)
        return out

    def label(self, symbol, today):
        """'Results today', 'Results yesterday' or 'Results tomorrow' for a symbol, else None."""
        g = self.groups(today)
        for key, text in (("today", "Results today"), ("last", "Results just out"), ("next", "Results next session")):
            if any(e["symbol"] == symbol for e in g[key]):
                return text
        return None

    def watch(self, today):
        """Symbols to put first on the watchlist, most important first, each with the reason."""
        g, out, seen = self.groups(today), [], set()
        for key, text in (("last", "Results just out: watch the reaction"), ("today", "Results today"),
                          ("next", "Results next session: run-up risk")):
            for e in g[key]:
                if e["symbol"] not in seen:
                    seen.add(e["symbol"])
                    out.append({"symbol": e["symbol"], "company": e.get("company", e["symbol"]), "why": text, "kind": key,
                                "date": e["date"]})
        return out

    def heavyweights(self, today):
        """Index heavyweights reporting today or just out (they move Nifty and the bank indices)."""
        g = self.groups(today)
        return sorted({e["symbol"] for k in ("last", "today") for e in g[k] if e["symbol"] in HEAVY})

    def public(self, today):
        """What the dashboard shows: the three groups plus how fresh the data is."""
        g = self.groups(today)
        later = [e for e in self.state["events"] if date.fromisoformat(e["date"]) > self._next(today)][:12]
        return {"today": g["today"], "last": g["last"], "next": g["next"], "later": later,
                "fetched_at": self.state.get("fetched_at"), "ok": self.state.get("ok", False),
                "error": self.state.get("error", ""), "day": today.isoformat(),
                "holidays_known": bool(self.cal and self.cal.known)}


def check(log=print):
    """python -m engine.results: can this network read NSE's calendar, and who reports around today?"""
    import tempfile
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    names = [x.split("|")[0] for x in open(os.path.join(root, "engine", "nifty200.txt"), encoding="utf-8")
             if x.strip() and not x.startswith("#")]
    r = Results(tempfile.mkdtemp(), names, log=log)
    now = clock.now()
    r.refresh(now, force=True)
    if not r.state["ok"]:
        log(f"FAIL  NSE's results calendar could not be read from here: {r.state['error']}")
        log("      The tool keeps working without it, but results will not be on the watchlist. "
            "Add names by hand under results: extra: in config.yaml.")
        return False
    g = r.groups(now.date())
    names_of = lambda ev: ", ".join(sorted({e["symbol"] for e in ev})) or "none"
    log(f"PASS  NSE calendar read: {r.state.get('all_reporting', 0)} companies reporting in the window, {len(r.state['events'])} in the Nifty 200")
    log(f"      just out: {names_of(g['last'])}")
    log(f"      today: {names_of(g['today'])}")
    log(f"      next session: {names_of(g['next'])}")
    try:
        from .tradingcal import nse_holidays
        hol = sorted(d for d in nse_holidays() if d >= now.date())
        log(f"PASS  NSE trading holidays read: next ones are {', '.join(d.isoformat() for d in hol[:4]) or 'none listed'}")
    except Exception as e:
        log(f"WARN  NSE trading holidays could not be read ({type(e).__name__}); only weekends will be skipped. "
            "Add holidays under holidays: in config.yaml.")
    return True


if __name__ == "__main__":
    import sys
    sys.exit(0 if check() else 1)
