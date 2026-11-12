"""The rulebook engine.

Once a minute it looks at each market and walks through the rulebook:
mark the opening range, check the trend filter, wait for a 5-minute close
outside the range, then follow the trade to its stop, target or time exit.
Every trade it suggests is written to the log and scored. It never places orders.
"""
import os
import html
import traceback
from datetime import timedelta

from . import clock, rules
from .clock import at, ts, clock_text
from .fyers import AuthError
from .news import NewsDesk

WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
NIFTY_SPOT = "NSE:NIFTY50-INDEX"


def num(x, d=2):
    s = f"{x:,.{d}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def rs(x):
    return ("-" if x < 0 else "") + "₹" + num(abs(x))


def tick_round(x):
    return round(round(x / 0.05) * 0.05, 2)


def new_state():
    return {"trades": 0, "losses": 0, "armed": True, "last5": 0, "done": False, "note": "", "errors": 0}


def score(trades):
    closed = [t for t in trades if t["status"] == "CLOSED"]
    wins = [t for t in closed if t["result"] == "WIN"]
    losses = [t for t in closed if t["result"] == "LOSS"]
    decided = len(wins) + len(losses)
    return {"closed": len(closed), "wins": len(wins), "losses": len(losses),
            "win_rate": round(len(wins) / decided * 100, 1) if decided else None,
            "pnl": round(sum(t["pnl"] for t in closed), 2)}


class Engine:
    def __init__(self, cfg, broker, master, notify, feed, news=None, portfolio_provider=None):
        self.cfg = cfg
        self.broker = broker
        self.master = master
        self.notify = notify
        self.feed = feed
        self.portfolio_provider = portfolio_provider or (lambda: [])
        self.dirty = False
        self._cache = {}
        here = os.path.dirname(__file__)
        with open(os.path.join(here, "nifty200.txt"), encoding="utf-8") as f:
            pairs = [x.strip().split("|") for x in f if x.strip() and not x.startswith("#")]
        self.universe = [p[0] for p in pairs]
        self.news = news if news is not None else NewsDesk([(p[0], p[-1]) for p in pairs])

    # ── small helpers ────────────────────────────────────────
    @property
    def day(self):
        return self.feed["status"]["day"]

    @property
    def trades(self):
        return self.feed["trades"]

    def _log(self, now, text, kind="info"):
        self.day["log"].append({"t": now.isoformat(timespec="seconds"), "text": text, "kind": kind})
        del self.day["log"][:-150]
        self.dirty = True

    def _finish(self, st, now, text):
        st["done"] = True
        st["note"] = text
        self._log(now, text)

    def _candles(self, symbol, start):
        key = (symbol, ts(start))
        if key not in self._cache:
            day = start.date()
            raw = self.broker.history(symbol, "1", day, day)
            self._cache[key] = [c for c in raw if c[0] >= ts(start)]
        return self._cache[key]

    def _quote(self, symbol):
        key = ("q", symbol)
        if key not in self._cache:
            self._cache[key] = self.broker.quotes([symbol]).get(symbol)
        return self._cache[key]

    def _prev_close(self, st, symbol):
        if st.get("prev_close") is None:
            q = self._quote(symbol)
            st["prev_close"] = q.get("prev_close") if q else None
        return st["prev_close"]

    def _open_trade(self, unit_id, day):
        d = day.isoformat()
        for t in self.trades:
            if t["status"] == "OPEN" and t["unit"] == unit_id and t["date"] == d:
                return t
        return None

    def _safe(self, fn, st, name, now):
        try:
            fn()
            st["errors"] = 0
        except AuthError:
            raise
        except Exception as e:
            st["errors"] = st.get("errors", 0) + 1
            print(f"Problem while checking {name}: {e}")
            traceback.print_exc()
            if st["errors"] == 5:
                self.notify(f"⚠️ I have not been able to read prices for {name} for 5 minutes. "
                            f"I will keep trying.\nReason: {e}")
                self._log(now, f"Trouble reading prices for {name}: {e}", "warn")

    # ── one pass over everything ─────────────────────────────
    def tick(self, now):
        self._cache = {}
        self._roll_day(now)
        self.day.setdefault("news", [])
        self.day.setdefault("newsdesk", {"errors": 0, "last": 0})
        for m in self.cfg["markets"]:
            if not m.get("enabled", True):
                continue
            st = self.day["markets"].setdefault(m["id"], new_state())
            self._safe(lambda: self._tick_market(m, st, now), st, m["name"], now)
        if self.cfg["stocks"].get("enabled"):
            S = self.day["stocks"]
            if (self.cfg.get("news") or {}).get("enabled", True):
                self._safe(lambda: self._tick_news(now), self.day["newsdesk"], "the news feeds", now)
            self._safe(lambda: self._tick_stocks(now), S, "Nifty 200 stocks", now)
        self._refresh_status(now)

    # ── news ─────────────────────────────────────────────────
    def _tick_news(self, now):
        nd = self.day["newsdesk"]
        today = now.date()
        if not (at(today, "08:40") <= now <= at(today, "15:10")) or ts(now) - nd.get("last", 0) < 1200:
            return
        nd["last"] = ts(now)
        have = {(i["symbol"], i["title"][:50].lower()) for i in self.day["news"]}
        fresh = [i for i in self.news.refresh() if (i["symbol"], i["title"][:50].lower()) not in have]
        if fresh:
            self.day["news"] = sorted(self.day["news"] + fresh, key=lambda x: x["t"], reverse=True)[:80]
            self.dirty = True
        if not nd.get("briefed") and self.day["news"] and now >= at(today, "08:50"):
            nd["briefed"] = True
            seen, lines = set(), []
            for i in sorted(self.day["news"], key=lambda x: x["tag"] != "Results"):
                if i["symbol"] not in seen and len(lines) < 10:
                    seen.add(i["symbol"])
                    lines.append(f"• <b>{html.escape(i['name'])}</b> ({html.escape(i['tag'])}): "
                                 f"{html.escape(i['title'])} [{html.escape(i['source'])}]")
            self.notify("📰 <b>Stocks in the news today</b>\n" + "\n".join(lines) +
                        "\n\nThese are only headlines. A stock still has to pass the scan and the rules to become a trade.")
            self._log(now, f"News scan: {len(seen)} Nifty 200 stocks are in today's headlines.")

    def _news_for(self, symbol):
        return [i for i in self.day["news"] if i["symbol"] == symbol][:2]

    def _roll_day(self, now):
        today = now.date().isoformat()
        status = self.feed["status"]
        if status.get("day", {}).get("date") == today:
            return
        stale = [t for t in self.trades if t["status"] == "OPEN"]
        stocks = new_state()
        stocks.update({"watch": [], "scanned": False, "units": {}})
        status["day"] = {"date": today, "skip": today in [str(d) for d in self.cfg.get("skip_dates") or []],
                         "markets": {}, "stocks": stocks, "log": [], "news": [],
                         "newsdesk": {"errors": 0, "last": 0}}
        # Anything still open from an earlier day was missed while the tool was offline.
        # An option cannot be defensibly marked closed at its last cached premium: its
        # quote may be stale and the option market may have moved materially. Keep the
        # record, but exclude it from realised P&L and expose it as unresolved.
        for t in stale:
            if t.get("kind") == "option":
                t.update({
                    "status": "UNRESOLVED",
                    "exit_time": now.isoformat(timespec="seconds"),
                    "exit_reason": ("No verified option exit price was available before the tool rolled to a new day. "
                                    "The last premium is retained for reference only; P&L is unresolved."),
                    "open_pnl": None,
                    "pnl": None,
                    "result": None,
                    "unresolved_reason": "Last option premium is not a verified exit fill.",
                })
                self._event(t, now, "UNRESOLVED", "Paper position retained as unresolved; no exit price or P&L was invented.")
                self.notify(f"⚠️ <b>{t['name']}: paper trade unresolved</b>\n"
                            "The tool rolled to a new day without a verified option exit price. "
                            "The position is retained in the trade log without realised P&L.")
            else:
                self._close(t, None, None, now, t.get("last_price", t["entry_price"]),
                            "Closed at the last known underlying price because the tool was offline at exit time")
        if status["day"]["skip"]:
            self._log(now, "Today is on your skip list, so no new trades will be taken.")
        self.dirty = True

    def set_skip(self, now, flag):
        self.day["skip"] = flag
        self._log(now, "You paused new trades for today." if flag else "You resumed new trades.")

    # ── index and commodity options ──────────────────────────
    def _tick_market(self, m, st, now):
        today = now.date()
        open_tr = self._open_trade(m["id"], today)
        if st["done"] and not open_tr:
            return
        r0 = at(today, m["range"][0])
        if now < r0:
            st["note"] = f"Starts at {clock_text(r0)}"
            return
        if WEEKDAYS[today.weekday()] in (m.get("skip_weekdays") or []) and not open_tr:
            return self._finish(st, now, f"{m['name']}: skipped today as set in your settings.")
        if "c" not in st:
            expiry, why = self.master.choose_expiry(m["master"], m["root"], today,
                                                    m.get("min_days_to_expiry", 1), m.get("near_expiry", "roll"))
            if not expiry:
                return self._finish(st, now, f"{m['name']}: no trade today, {why}.")
            if m["kind"] == "index":
                fut = self.master.nearest_future(m["master"], m["root"], today)
                signal, vw = m["spot"], (fut["symbol"] if fut else m["spot"])
            else:
                fut = self.master.future_for_expiry(m["master"], m["root"], expiry, today)
                if not fut:
                    return self._finish(st, now, f"{m['name']}: no futures contract found.")
                signal = vw = fut["symbol"]
            st["c"] = {"expiry": expiry, "signal": signal, "vwap": vw}
        g = self.cfg["general"]
        unit = {
            "id": m["id"], "name": m["name"],
            "book": "Index options" if m["kind"] == "index" else "Commodity options",
            "signal": st["c"]["signal"], "vwap": st["c"]["vwap"],
            "session_start": m.get("session_start", m["range"][0]),
            "range": m["range"], "entry": m["entry"], "exit_by": m["exit_by"],
            "max_range_pct": m.get("max_range_pct", g.get("max_range_pct")),
            "use_prev_close": True, "nifty_filter": False, "trade_as": "option",
            "opt": {"master": m["master"], "root": m["root"], "expiry": st["c"]["expiry"],
                    "stop_pct": m["premium_stop_pct"], "multiplier": m.get("multiplier")},
        }
        self._tick_unit(unit, st, st, now)

    # ── Nifty 200 stocks ─────────────────────────────────────
    def _tick_stocks(self, now):
        s = self.cfg["stocks"]
        S = self.day["stocks"]
        today = now.date()
        r1, e1 = at(today, s["range"][1]), at(today, s["entry"][1])
        has_open = any(t["status"] == "OPEN" and t["book"] == "Stocks" for t in self.trades)
        if S["done"] and not has_open:
            return
        if now < r1 + timedelta(seconds=20):
            S["note"] = f"Scans the Nifty 200 at {clock_text(r1)}"
            return
        if not S["scanned"]:
            if now > e1:
                return self._finish(S, now, "Stocks: the scan was missed today, so no stock trades.")
            if self.day["skip"]:
                S["note"] = "Paused for today"
                return
            if not self._candles(NIFTY_SPOT, at(today, s["range"][0])):
                if now > r1 + timedelta(minutes=10):
                    self._finish(S, now, "Stocks: no price data today. The market looks closed.")
                return
            self._scan(S, s, today, now)
        for w in S["watch"]:
            st = S["units"].setdefault(w["symbol"], new_state())
            unit = {
                "id": w["name"], "name": w["name"], "book": "Stocks", "group": "Stocks",
                "signal": w["symbol"], "vwap": w["symbol"], "session_start": s["range"][0],
                "range": s["range"], "entry": s["entry"], "exit_by": s["exit_by"],
                "max_range_pct": None, "use_prev_close": False, "nifty_filter": True,
                "trade_as": s.get("trade_as", "cash"), "scan": w,
                "opt": {"master": "NSE_FO", "root": w["name"], "expiry": None,
                        "stop_pct": s.get("premium_stop_pct", 30), "multiplier": None},
            }
            self._safe(lambda: self._tick_unit(unit, st, S, now), st, w["name"], now)
        if S["watch"] and all(S["units"].get(w["symbol"], {}).get("done") for w in S["watch"]):
            S["done"] = True
        if S["watch"]:
            S["note"] = "Watching " + ", ".join(w["name"] for w in S["watch"])

    def _scan(self, S, s, today, now):
        quotes = self.broker.quotes([f"NSE:{x}-EQ" for x in self.universe])
        movers = []
        for sym, q in quotes.items():
            if q.get("lp") and q.get("prev_close"):
                chg = (q["lp"] - q["prev_close"]) / q["prev_close"] * 100
                if abs(chg) >= s["min_move_pct"]:
                    movers.append((sym, chg))
        movers.sort(key=lambda x: -abs(x[1]))
        moves = {sym.split(":")[1][:-3]: round(chg, 2) for sym, chg in
                 ((s_, (q["lp"] - q["prev_close"]) / q["prev_close"] * 100) for s_, q in quotes.items()
                  if q.get("lp") and q.get("prev_close"))}
        for i in self.day["news"]:
            i["chg"] = moves.get(i["symbol"])
        picks = []
        for sym, chg in movers[:40]:  # strongest first, so the first to pass are the strongest
            first = {}
            for c in self.broker.history(sym, "15", today - timedelta(days=25), today):
                dt = clock.from_ts(c[0])
                if (dt.hour, dt.minute) == (9, 15):
                    first[dt.date()] = c[5]
            today_vol = first.pop(today, None)
            past = [first[d] for d in sorted(first)][-10:]
            if not today_vol or len(past) < 3 or sum(past) == 0:
                continue
            mult = today_vol / (sum(past) / len(past))
            if mult >= s["volume_multiple"]:
                name = sym.split(":")[1][:-3]
                news = self._news_for(name)
                if not news and (self.cfg.get("news") or {}).get("enabled", True):
                    try:
                        news = self.news.for_stock(name)
                        for i in news:
                            i["chg"] = round(chg, 2)
                        self.day["news"] = (news + self.day["news"])[:80]
                    except Exception as e:
                        print("News lookup skipped for", name, type(e).__name__)
                picks.append({"symbol": sym, "name": name, "chg": round(chg, 2), "volx": round(mult, 1),
                              "news": [{k: i[k] for k in ("title", "source", "tag", "link")} for i in news[:2]]})
            if len(picks) >= s["watchlist_size"]:
                break
        S["watch"], S["scanned"] = picks, True
        if not picks:
            return self._finish(S, now, f"Stocks: no Nifty 200 stock passed today's scan (needs a "
                                        f"{num(s['min_move_pct'])}% move on {num(s['volume_multiple'])}x usual volume).")
        lines = [f"• {p['name']}: {'up' if p['chg'] > 0 else 'down'} {abs(p['chg'])}%, volume {p['volx']}x usual"
                 + (f"\n   📰 {html.escape(p['news'][0]['title'])} [{html.escape(p['news'][0]['source'])}]" if p["news"] else "")
                 for p in picks]
        self._log(now, "Stock watchlist: " + "; ".join(
            f"{p['name']} {'up' if p['chg'] > 0 else 'down'} {abs(p['chg'])}% on {p['volx']}x volume" for p in picks))
        self.notify("👀 <b>Stock watchlist for today</b>\n" + "\n".join(lines) +
                    "\nI will alert you if any of them breaks out with the market's support.")

    # ── the rulebook for one thing being watched ─────────────
    def _tick_unit(self, u, st, disc, now):
        today = now.date()
        now_ts = ts(now)
        g = self.cfg["general"]
        r0, r1 = at(today, u["range"][0]), at(today, u["range"][1])
        e1 = at(today, u["entry"][1])
        open_tr = self._open_trade(u["id"], today)
        if st["done"] and not open_tr:
            return

        sig = self._candles(u["signal"], at(today, u["session_start"]))
        if not [c for c in sig if c[0] >= ts(r0)]:
            if now > r0 + timedelta(minutes=12) and not open_tr:
                self._finish(st, now, f"{u['name']}: no price data today. The market looks closed.")
            return
        vw = sig if u["vwap"] == u["signal"] else self._candles(u["vwap"], at(today, u["session_start"]))
        st["price"] = sig[-1][4]
        v_now = rules.vwap(vw)
        st["vwap"] = round(v_now, 2) if v_now is not None else None
        if u["use_prev_close"]:
            self._prev_close(st, u["signal"])

        if now < r1:
            st["note"] = f"Marking the opening range until {clock_text(r1)}"
            return
        if "range" not in st:
            R = rules.opening_range(sig, ts(r0), ts(r1))
            st["range"] = R
            self._log(now, f"{u['name']}: opening range is {num(R['low'])} to {num(R['high'])} "
                           f"({R['pct']:.2f}% wide).")
            if u["max_range_pct"] and R["pct"] > u["max_range_pct"]:
                return self._finish(st, now, f"{u['name']}: skipped today. The opening range is "
                                             f"{R['pct']:.2f}% wide, more than the {num(u['max_range_pct'])}% limit.")
        R = st["range"]

        if open_tr:
            return self._manage(open_tr, u, st, disc, now, sig, vw)
        if st["done"]:
            return
        if self.day["skip"]:
            st["note"] = "Paused for today"
            return
        if g.get("stop_after_losses") and disc["losses"] >= g["stop_after_losses"]:
            return self._finish(st, now, f"{u.get('group', u['name'])}: stopped for the day after a losing trade.")
        if g.get("max_trades_per_day") and disc["trades"] >= g["max_trades_per_day"]:
            return self._finish(st, now, f"{u.get('group', u['name'])}: daily limit of {g['max_trades_per_day']} trades reached.")

        five = [c for c in rules.five_minute(sig, now_ts)
                if c[0] >= ts(r1) and c[0] > st["last5"] and c[0] + 300 <= ts(e1)]
        for c in five:
            st["last5"] = c[0]
            close = c[4]
            if R["low"] <= close <= R["high"]:
                st["armed"] = True
                continue
            if not st["armed"] or now_ts - (c[0] + 300) > 180:
                continue  # already traded this breakout, or the candle is too old to act on
            up = close > R["high"]
            height = R["high"] - R["low"]
            if (up and close >= R["high"] + height) or (not up and close <= R["low"] - height):
                self._log(now, f"{u['name']}: broke out at {num(close)} but the move has already "
                               f"passed the target, so no trade.")
                continue
            ok, reasons, fails, v = self._filter(u, st, up, c, vw, now)
            if ok:
                if self._open(u, st, disc, now, up, close, reasons, v):
                    return
            else:
                self._log(now, f"{u['name']}: broke {'above' if up else 'below'} the range at {num(close)} "
                               f"but no trade, because {' and '.join(fails)}.")
        if now >= e1 + timedelta(seconds=90):
            return self._finish(st, now, f"{u['name']}: no valid breakout by {clock_text(e1)}, so no trade today.")
        st["note"] = "Waiting for a 5-minute close outside the range"

    def _filter(self, u, st, up, c, vw, now):
        cut = c[0] + 300
        word = "above" if up else "below"
        R = st["range"]
        reasons = [f"{u['name']} closed a 5-minute candle at {num(c[4])}, {word} the opening range "
                   f"{'high' if up else 'low'} of {num(R['high'] if up else R['low'])}."]
        fails = []
        v, p = rules.vwap(vw, cut), rules.last_close(vw, cut)
        if v is None or p is None:
            fails.append("the day's average price line is not available")
        elif p != v and (p > v) == up:
            reasons.append(f"Price is {word} the day's average price line (VWAP {num(v)}).")
        else:
            fails.append(f"price is not {word} the day's average price line (VWAP {num(v)})")
        if u["use_prev_close"]:
            pc = st.get("prev_close")
            if pc is None:
                fails.append("yesterday's close is not available")
            elif c[4] != pc and (c[4] > pc) == up:
                reasons.append(f"Price is {word} yesterday's close of {num(pc)}.")
            else:
                fails.append(f"price is not {word} yesterday's close of {num(pc)}")
        if u["nifty_filter"]:
            if "nifty_fut" not in self.day:
                f = self.master.nearest_future("NSE_FO", "NIFTY", now.date())
                self.day["nifty_fut"] = f["symbol"] if f else NIFTY_SPOT
            nc = self._candles(self.day["nifty_fut"], at(now.date(), "09:15"))
            nv, np_ = rules.vwap(nc, cut), rules.last_close(nc, cut)
            if nv is None or np_ is None:
                fails.append("Nifty's average price line is not available")
            elif np_ != nv and (np_ > nv) == up:
                reasons.append(f"Nifty is also {word} its average price line, so the wider market agrees.")
            else:
                fails.append(f"Nifty is not {word} its own average price line")
            w = u.get("scan")
            if w:
                reasons.append(f"The stock made today's watchlist: {'up' if w['chg'] > 0 else 'down'} "
                               f"{abs(w['chg'])}% with {w['volx']}x its usual early volume.")
                for n in (w.get("news") or [])[:1]:
                    reasons.append(f"It is in the news today ({html.escape(n['tag'])}): “{html.escape(n['title'])}” "
                               f"[{html.escape(n['source'])}].")
        if u["max_range_pct"]:
            reasons.append(f"The opening range was {R['pct']:.2f}% wide, inside the {num(u['max_range_pct'])}% limit.")
        return not fails, reasons, fails, v

    # ── opening a trade ──────────────────────────────────────
    def _portfolio_rejection(self, candidate, today):
        """Shared limits for the rulebook engine's options and cash paper positions."""
        limits = (self.cfg.get("general") or {}).get("portfolio") or {}
        day = today.isoformat()
        open_now = [t for t in self.trades if t.get("date") == day and t.get("status") == "OPEN"]
        closed = [t for t in self.trades if t.get("date") == day and t.get("status") == "CLOSED"]
        realized = sum(float(t.get("pnl", 0) or 0) for t in closed)
        open_risk = sum(float(t.get("risk_amount", 0) or 0) for t in open_now)
        risk_amount = float(candidate.get("risk_amount", 0) or 0)
        reasons = []
        if limits.get("enabled", True):
            if realized <= -float(limits.get("max_daily_loss", 3000)):
                reasons.append("rulebook daily loss limit reached")
            if len(open_now) >= int(limits.get("max_open_positions", 5)):
                reasons.append("rulebook maximum concurrent positions reached")
            if open_risk + risk_amount > float(limits.get("max_open_risk", 15000)):
                reasons.append("rulebook maximum open risk would be exceeded")

        # The second guard is shared with the strategy desk. It prevents two
        # independent engines from spending the same capital/risk budget twice.
        shared = self.cfg.get("portfolio") or {}
        if shared.get("enabled", True):
            external = [t for t in (self.portfolio_provider() or [])
                        if t.get("date") == day and t.get("status") in ("OPEN", "CLOSED")
                        and t.get("risk_approved", True)]
            external_open = [t for t in external if t.get("status") == "OPEN"]
            external_closed = [t for t in external if t.get("status") == "CLOSED"]
            combined_positions = len(open_now) + len(external_open)
            combined_risk = (open_risk + sum(float(t.get("risk", t.get("risk_amount", 0)) or 0)
                                             for t in external_open))
            combined_realized = realized + sum(float(t.get("net", t.get("pnl", 0)) or 0)
                                                for t in external_closed)
            if combined_realized <= -float(shared.get("max_daily_loss", 3500)):
                reasons.append("shared portfolio daily loss limit reached")
            if combined_positions >= int(shared.get("max_open_positions", 8)):
                reasons.append("shared portfolio maximum positions reached")
            if combined_risk + risk_amount > float(shared.get("max_open_risk", 17500)):
                reasons.append("shared portfolio open risk would be exceeded")
        return reasons

    def _open(self, u, st, disc, now, up, close, reasons, v):
        g = self.cfg["general"]
        R = st["range"]
        today = now.date()
        height = R["high"] - R["low"]
        target = R["high"] + height if up else R["low"] - height
        count = sum(1 for t in self.trades if t["date"] == today.isoformat() and t["unit"] == u["id"]) + 1
        tr = {
            "id": f"{today.isoformat()}-{u['id']}-{count}", "date": today.isoformat(),
            "unit": u["id"], "name": u["name"], "book": u["book"], "dir": 1 if up else -1,
            "underlying_entry": close, "range_high": R["high"], "range_low": R["low"],
            "range_mid": R["mid"], "range_pct": round(R["pct"], 3), "vwap_at_entry": v,
            "stop_underlying": R["mid"], "target_underlying": target, "exit_by": u["exit_by"],
            "reasons": reasons, "status": "OPEN", "phase": "initial", "events": [], "fills": [],
            "entry_time": now.isoformat(timespec="seconds"), "entry_min": ts(now) // 60 * 60,
            "news": (u.get("scan") or {}).get("news") or [],
        }
        edge = "below" if up else "above"
        if u["trade_as"] == "option":
            o = u["opt"]
            cp = "CE" if up else "PE"
            expiry = o["expiry"]
            if not expiry:
                expiry, why = self.master.choose_expiry(o["master"], o["root"], today,
                                                        self.cfg["stocks"].get("min_days_to_expiry", 5), "skip")
                if not expiry:
                    st["armed"] = False
                    self._log(now, f"{u['name']}: breakout seen but no option trade, {why}.")
                    return False
            opt = self.master.pick_option(o["master"], o["root"], expiry, close, cp, g.get("strike", "ATM"))
            q = self._quote(opt["symbol"]) if opt else None
            if not opt or not q or not q.get("lp"):
                self._log(now, f"{u['name']}: breakout seen but the option price could not be read.", "warn")
                return False
            price = q["lp"]
            lots = int(g.get("lots", 1))
            per_lot = o["multiplier"] or opt["lot"]
            stop = tick_round(price * (1 - o["stop_pct"] / 100))
            tr.update({"kind": "option", "side": 1, "action": "BUY", "symbol": opt["symbol"],
                       "contract": opt["desc"], "option_type": cp, "strike": opt["strike"],
                       "expiry": clock.from_ts(expiry).date().isoformat(), "lots": lots, "lot_size": per_lot,
                       "qty": lots * per_lot, "units": lots, "entry_price": price, "stop_price": stop,
                       "stop_pct": o["stop_pct"], "risk_per_unit": round(price - stop, 2)})
            what = f"BUY {lots} lot{'s' if lots > 1 else ''} {opt['desc']}"
            stop_line = (f"Stop-loss: option at ₹{num(stop)}, or {u['name']} closing a 5-minute candle "
                         f"{edge} {num(R['mid'])}")
            target_line = f"Target: {u['name']} at {num(target)}"
        else:
            s = self.cfg["stocks"]
            risk = abs(close - R["mid"]) or close * 0.002
            from .strategies.risk import quantity_for_risk
            qty = quantity_for_risk(close, risk, s["risk_per_trade"], s["max_position_value"])
            if qty < 1:
                st["armed"] = False
                self._log(now, f"{u['name']}: breakout passed, but no share can be sized within both the "
                               f"₹{num(s['risk_per_trade'], 0)} risk and ₹{num(s['max_position_value'], 0)} position limits.", "warn")
                return False
            tr.update({"kind": "stock", "side": 1 if up else -1, "action": "BUY" if up else "SELL",
                       "symbol": u["signal"], "contract": u["name"], "lots": None, "lot_size": 1,
                       "qty": qty, "units": qty, "entry_price": close, "stop_price": None,
                       "risk_per_unit": round(risk, 2)})
            what = f"{tr['action']} {qty} share{'s' if qty > 1 else ''} of {u['name']}"
            stop_line = f"Stop-loss: {u['name']} closing a 5-minute candle {edge} ₹{num(R['mid'])}"
            target_line = f"Target: ₹{num(target)}"
        tr.update({"remaining": tr["qty"], "last_price": tr["entry_price"], "underlying_last": close,
                   "last_update": tr["entry_time"], "open_pnl": 0.0,
                   "risk_amount": round(tr["risk_per_unit"] * tr["qty"], 2), "headline": what})
        if tr["kind"] == "option":
            tr["quote_status"] = "FRESH"
            tr["quote_received_at"] = now.isoformat(timespec="seconds")
        rejected = self._portfolio_rejection(tr, today)
        if rejected:
            st["armed"] = False
            self._log(now, f"{u['name']}: setup passed, but no paper entry was accepted: {'; '.join(rejected)}.", "warn")
            return False
        self.trades.append(tr)
        disc["trades"] += 1
        st["armed"] = False
        st["note"] = "In a trade"
        self._event(tr, now, "ENTRY", f"{what} at ₹{num(tr['entry_price'])}", tr["entry_price"])
        x = at(today, u["exit_by"])
        self.notify(
            f"{'📈' if up else '📉'} <b>{u['name']}: {what}</b>\n"
            f"Entry near ₹{num(tr['entry_price'])}\n{stop_line}\n{target_line}\n"
            f"Exit by {clock_text(x)} if neither is hit\n"
            f"Planned risk: about {rs(tr['risk_amount'])}\n\n<b>Why this trade</b>\n" +
            "\n".join("• " + r for r in reasons))
        return True

    def _event(self, tr, now, kind, text, price=None):
        tr["events"].append({"t": now.isoformat(timespec="seconds"), "kind": kind, "text": text, "price": price})
        self._log(now, f"{tr['name']}: {text}", "trade")
        self.dirty = True

    # ── following an open trade ──────────────────────────────
    def _manage(self, tr, u, st, disc, now, sig, vw):
        now_ts = ts(now)
        d = tr["dir"]
        if tr["kind"] == "option":
            q = self._quote(tr["symbol"])
            if not q or not isinstance(q.get("lp"), (int, float)) or q.get("lp") <= 0:
                # Never present a cached premium as a fresh mark or claim that its stop
                # is being monitored. Leave the paper position explicitly unmonitored.
                tr["quote_status"] = "STALE"
                tr["underlying_last"] = sig[-1][4]
                tr.setdefault("quote_stale_since", now.isoformat(timespec="seconds"))
                if not tr.get("quote_warning_sent"):
                    tr["quote_warning_sent"] = True
                    self.notify(f"⚠️ <b>{tr['name']}: option quote unavailable</b>\n"
                                f"The last premium ₹{num(tr['last_price'])} is stale. The premium stop cannot be verified; "
                                f"this paper trade is marked UNMONITORED until a fresh quote returns.")
                    self._log(now, f"{tr['name']}: option quote unavailable; premium-based trade management paused.", "warn")
                self.dirty = True
                return
            price = q["lp"]
            tr["quote_status"] = "FRESH"
            tr["quote_received_at"] = now.isoformat(timespec="seconds")
            tr.pop("quote_stale_since", None)
            tr["quote_warning_sent"] = False
        else:
            price = sig[-1][4]
        booked = sum(tr["side"] * (f["price"] - tr["entry_price"]) * f["qty"] for f in tr["fills"])
        tr["last_price"] = price
        tr["underlying_last"] = sig[-1][4]
        tr["last_update"] = now.isoformat(timespec="seconds")
        tr["open_pnl"] = round(booked + tr["side"] * (price - tr["entry_price"]) * tr["remaining"], 2)
        self.dirty = True

        since = [c for c in sig if c[0] >= tr["entry_min"]]
        five = rules.five_minute(since, now_ts)
        name = tr["name"]

        if tr["phase"] == "initial":
            if tr["kind"] == "option" and price <= tr["stop_price"]:
                return self._close(tr, st, disc, now, price,
                                   f"Stop-loss hit: the option fell {num(tr['stop_pct'])}% to ₹{num(price)}", "STOP")
            if five and d * (five[-1][4] - tr["stop_underlying"]) < 0:
                return self._close(tr, st, disc, now, price,
                                   f"Stop-loss hit: {name} closed a 5-minute candle back past the middle "
                                   f"of the range ({num(tr['stop_underlying'])})", "STOP")
            hit = since and (max(c[2] for c in since) >= tr["target_underlying"] if d == 1
                             else min(c[3] for c in since) <= tr["target_underlying"])
            if hit:
                half = tr["units"] // 2
                if half < 1:
                    return self._close(tr, st, disc, now, price,
                                       f"Target reached: {name} touched {num(tr['target_underlying'])}", "TARGET")
                self._fill(tr, now, half, price, "Target reached, half booked")
                tr["phase"] = "trail"
                tr["t1_min"] = now_ts // 60 * 60
                if tr["kind"] == "option":
                    tr["stop_price"] = tr["entry_price"]
                self._event(tr, now, "TARGET", f"Target reached. Booked half at ₹{num(price)}; "
                                               f"stop moved to the entry price", price)
                self.notify(f"🎯 <b>{name}: target reached</b>\nBook half of {tr['contract']} near ₹{num(price)}.\n"
                            f"Move your stop to the entry price (₹{num(tr['entry_price'])}). "
                            f"I will trail the rest along the average price line.")
                return
            ee = self.cfg.get("early_exit") or {}
            if ee.get("enabled"):
                n = int(ee.get("closes_back_inside_range") or 0)
                if n and len(five) >= n and all((c[4] < tr["range_high"]) if d == 1 else (c[4] > tr["range_low"])
                                                for c in five[-n:]):
                    return self._close(tr, st, disc, now, price,
                                       f"Early exit: the breakout failed, with {n} five-minute closes "
                                       f"back inside the opening range", "EARLY")
                if ee.get("vwap_break"):
                    vw5 = [c for c in rules.five_minute(vw, now_ts) if c[0] >= tr["entry_min"]]
                    v = rules.vwap(vw, vw5[-1][0] + 300) if vw5 else None
                    if v is not None and d * (vw5[-1][4] - v) < 0:
                        return self._close(tr, st, disc, now, price,
                                           "Early exit: price closed on the wrong side of the day's "
                                           "average price line, so the trend support is gone", "EARLY")
        else:
            if tr["side"] * (price - tr["entry_price"]) <= 0:
                return self._close(tr, st, disc, now, price, "Rest closed at the entry price after the target",
                                   "TRAIL")
            vw5 = [c for c in rules.five_minute(vw, now_ts) if c[0] >= tr["t1_min"]]
            if vw5:
                v = rules.vwap(vw, vw5[-1][0] + 300)
                if v is not None and d * (vw5[-1][4] - v) < 0:
                    return self._close(tr, st, disc, now, price,
                                       "Trailing exit: price closed back across the day's average price line",
                                       "TRAIL")
        x = at(now.date(), tr["exit_by"])
        if now >= x:
            return self._close(tr, st, disc, now, price, f"Time exit at {clock_text(x)}", "TIME")
        st["note"] = "In a trade"

    def _fill(self, tr, now, units, price, reason):
        qty = units * tr["lot_size"]
        tr["fills"].append({"t": now.isoformat(timespec="seconds"), "qty": qty, "price": price, "reason": reason})
        tr["remaining"] -= qty

    def _close(self, tr, st, disc, now, price, reason, kind="OTHER"):
        if tr["remaining"] > 0:
            self._fill(tr, now, tr["remaining"] // tr["lot_size"], price, reason)
        pnl = sum(tr["side"] * (f["price"] - tr["entry_price"]) * f["qty"] for f in tr["fills"])
        qty = sum(f["qty"] for f in tr["fills"]) or 1
        tr.update({
            "status": "CLOSED", "exit_time": now.isoformat(timespec="seconds"),
            "exit_price": round(sum(f["price"] * f["qty"] for f in tr["fills"]) / qty, 2),
            "exit_reason": reason, "exit_type": kind, "pnl": round(pnl, 2), "open_pnl": None, "last_price": price,
            "pnl_pct": round(pnl / (tr["entry_price"] * qty) * 100, 2) if tr["entry_price"] else 0,
            "r": round(pnl / tr["risk_amount"], 2) if tr.get("risk_amount") else 0,
            "result": "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "FLAT",
        })
        if disc is not None and tr["result"] == "LOSS":
            disc["losses"] += 1
        if st is not None:
            st["note"] = "Last trade closed"
        self._event(tr, now, "EXIT", f"{reason}. Result {rs(tr['pnl'])} ({tr['r']:+.2f}R)", price)
        sc = score(self.trades)
        rate = f"{sc['win_rate']}%" if sc["win_rate"] is not None else "not enough trades yet"
        self.notify(
            f"{'✅' if pnl > 0 else '❌' if pnl < 0 else '➖'} <b>{tr['name']}: exit {tr['contract']}</b>\n"
            f"{reason}.\nExit near ₹{num(price)} (entry was ₹{num(tr['entry_price'])})\n"
            f"Result: <b>{rs(tr['pnl'])}</b> ({tr['pnl_pct']:+.1f}%, {tr['r']:+.2f}R)\n\n"
            f"Tool's record so far: {sc['wins']} won, {sc['losses']} lost, win rate {rate}, total {rs(sc['pnl'])}")

    # ── what the dashboard shows ─────────────────────────────
    def _refresh_status(self, now):
        status = self.feed["status"]
        tiles = []
        for m in self.cfg["markets"]:
            if not m.get("enabled", True):
                continue
            st = self.day["markets"].get(m["id"], {})
            tiles.append(self._tile(m["id"], m["name"], "Index options" if m["kind"] == "index"
                                    else "Commodity options", st, now))
        if self.cfg["stocks"].get("enabled"):
            S = self.day["stocks"]
            if S.get("watch"):
                for w in S["watch"]:
                    tiles.append(self._tile(w["name"], w["name"], "Stocks", S["units"].get(w["symbol"], {}), now))
            else:
                tiles.append({"id": "STOCKS", "name": "Nifty 200 stocks", "book": "Stocks",
                              "note": S.get("note") or "", "done": S.get("done", False)})
        status["news"] = self.day.get("news", [])[:40]
        status["tiles"] = tiles
        status["updated"] = now.isoformat(timespec="seconds")
        status["score"] = score(self.trades)

    def _tile(self, uid, name, book, st, now):
        R = st.get("range") or {}
        note = st.get("note", "")
        for lead in (name + ": ", "Stocks: "):
            if note.startswith(lead):
                note = note[len(lead):]
                note = note[:1].upper() + note[1:]
        return {"id": uid, "name": name, "book": book, "note": note, "done": st.get("done", False),
                "price": st.get("price"), "vwap": st.get("vwap"), "prev_close": st.get("prev_close"),
                "range_high": R.get("high"), "range_low": R.get("low"),
                "in_trade": bool(self._open_trade(uid, now.date()))}

    def summary(self, now):
        today = now.date().isoformat()
        mine = [t for t in self.trades if t["date"] == today]
        sc = score(self.trades)
        if not mine:
            body = "No trade passed the rules today."
        else:
            body = "\n".join(
                f"• {t['name']}: {t['headline']} → " +
                (f"{rs(t['pnl'])} ({t['result'].lower()})" if t["status"] == "CLOSED" else "still open")
                for t in mine)
            body += f"\nToday: {rs(sum(t.get('pnl') or 0 for t in mine))}"
        rate = f"{sc['win_rate']}%" if sc["win_rate"] is not None else "not enough trades yet"
        return (f"🧾 <b>Session summary</b>\n{body}\n\n"
                f"Record so far: {sc['closed']} trades, {sc['wins']} won, {sc['losses']} lost, "
                f"win rate {rate}, total {rs(sc['pnl'])}")
