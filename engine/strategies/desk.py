"""The live strategy desk.

Every time a 5-minute candle closes it takes today's candles for each share and index, runs all ten
strategies over them with the same code the backtest uses, and compares the result with what it already
knows. A trade it has not seen before is a new signal. A trade that was open and has now ended is a result.
Because the answer is always worked out afresh from the day's candles, a restart loses nothing.
It never places an order.
"""
import json
import os
import time
import traceback
from datetime import datetime, timedelta

from .. import clock
from ..clock import at, ts
from ..fyers import AuthError
from . import sim
from .defs import STRATEGIES
from .indicators import Series

KEEP_DAYS = 10            # trades older than this are folded into running totals
CHART_DAYS = 3            # calendar days of trade charts kept (today, and the last session or two)
LATE_AFTER = 600          # a signal found more than this many seconds after its candle closed is recorded, not announced
WARM_DAYS = 10            # calendar days of earlier candles kept for the indicators
PASS_BUDGET = 200         # seconds one pass may take; the rest waits for the next candle
MAX_LINES = 10            # example signals listed in one Telegram message


def rs(x):
    return ("-" if x < 0 else "") + "₹" + f"{abs(x):,.0f}"


def px(x):
    return f"{x:,.2f}".rstrip("0").rstrip(".")


class StrategyDesk:
    def __init__(self, cfg, broker, notify, folder, universe, master_markets, heartbeat=None, options_trades=None):
        self.cfg = sim.settings(cfg)
        sc = cfg.get("strategies") or {}
        self.on = sc.get("enabled", True)
        mode = sc.get("telegram", "signals")
        self.tg_on = mode not in (False, "off")                    # the end-of-day summary
        self.tg_signals = mode in (True, "signals")                # a grouped message whenever new signals appear
        want = sc.get("alert_for", "all")
        self.alert_for = {s["id"] for s in STRATEGIES} if want in ("all", None) else set(want)
        self.heartbeat = heartbeat or (lambda: None)               # called often so a long pass never looks like a dead engine
        self.rotate = 0                                            # where the next pass starts, so no symbol is always last
        self.broker, self.notify = broker, notify
        self.folder = folder
        self.path = os.path.join(folder, "strategies.json")
        lots = self.cfg["index_lots"]
        self.insts = sim.build_instruments(master_markets, universe, lots)
        self.by_name = {i["name"]: sym for sym, i in self.insts.items()}
        self.options_trades = options_trades or (lambda: [])        # the options desk's trades, so they get charts too
        self.warm = {}                     # symbol -> earlier candles
        self.warm_day = None
        self.series = {}                   # symbol -> today's Series from the latest pass
        self.dirty = False
        self.state = self._load()
        self.charts = self._load_charts()  # source id -> {trade id: chart}
        self._charts_changed = set()

    # ── saved state ──────────────────────────────────────────
    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = {}
        st.setdefault("trades", [])
        st.setdefault("archive", {})
        st.setdefault("status", {})
        st.setdefault("today", {})
        st.setdefault("first_day", None)
        return st

    def _chart_path(self, sid):
        return os.path.join(self.folder, f"chartlive_{sid}.json")

    def _load_charts(self):
        out = {}
        for sid in [s["id"] for s in STRATEGIES] + ["options"]:
            try:
                with open(self._chart_path(sid), encoding="utf-8") as f:
                    out[sid] = json.load(f)
            except (OSError, ValueError):
                out[sid] = {}
        return out

    @staticmethod
    def _write(path, obj):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)

    def save(self):
        self._write(self.path, self.state)
        for sid in self._charts_changed:
            self._write(self._chart_path(sid), self.charts[sid])
        self._charts_changed = set()

    # ── one pass ─────────────────────────────────────────────
    def tick(self, now):
        if not self.on or now.weekday() >= 5:
            return
        today = now.date()
        bar_end = now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)
        if bar_end < at(today, "09:20") or bar_end > at(today, self.cfg["exit_by"]) + timedelta(minutes=10):
            return
        stt = self.state["status"]
        if stt.get("done_for") == ts(bar_end) or ts(now) - ts(bar_end) < 20:
            return                                     # already handled this candle, or it is not published yet
        if not self.state["first_day"]:
            self.state["first_day"] = today.isoformat()
        try:
            self._cycle(now, today, bar_end)
        except AuthError:
            raise
        except Exception as e:
            traceback.print_exc()
            stt["error"] = str(e)[:200]
            self.dirty = True
            return
        stt["done_for"] = ts(bar_end)
        if now >= at(today, "15:20") and stt.get("summary_for") != today.isoformat():
            stt["summary_for"] = today.isoformat()
            if self.tg_on:
                self.notify(self.summary_text(today.isoformat()))
        self.dirty = True

    def _history(self, sym, today):
        if self.warm_day != today:
            self.warm, self.warm_day = {}, today
        if sym not in self.warm:
            raw = self.broker.history(sym, "5", today - timedelta(days=WARM_DAYS), today)
            self.warm[sym] = [c for c in raw if clock.from_ts(c[0]).date() < today]
            return raw
        return self.broker.history(sym, "5", today, today)

    def _cycle(self, now, today, bar_end):
        stt = self.state["status"]
        now_ts, iso = ts(bar_end), today.isoformat()
        fresh, failed, scanned, done_syms = [], 0, 0, set()
        names = list(self.insts)
        order = names[self.rotate:] + names[:self.rotate]
        started, stopped_at = time.monotonic(), None
        for k, sym in enumerate(order):
            inst = self.insts[sym]
            if k % 10 == 0:
                self.heartbeat()
            if time.monotonic() - started > PASS_BUDGET:       # never hold up the rest of the engine; carry on next candle
                stopped_at = k
                break
            try:
                raw = self._history(sym, today)
                todays = [c for c in raw if clock.from_ts(c[0]).date() == today and c[0] + 300 <= now_ts]
                if not todays:
                    continue
                S = Series(self.warm[sym] + todays)
                if S.n < self.cfg["min_bars"]:
                    continue
                scanned += 1
                done_syms.add(sym)
                self.series[sym] = S
                for strat in STRATEGIES:
                    fresh += sim.simulate(S, inst, strat, self.cfg, days=[today], live_day=today, chart_from=today)
            except AuthError:
                raise
            except Exception as e:
                failed += 1
                if failed <= 3:
                    print(f"Strategy desk: no data for {sym}: {e}")
        self.rotate = (self.rotate + stopped_at) % len(names) if stopped_at else 0
        for t in fresh:                                  # charts live in their own files, loaded only when a trade is opened
            ch = t.pop("_chart", None)
            if ch:
                self.charts.setdefault(t["strategy"], {})[t["id"]] = ch
                self._charts_changed.add(t["strategy"])
        known = {t["id"]: t for t in self.state["trades"] if t["date"] == iso}
        announce_new, announce_done = [], []
        for t in fresh:
            old = known.get(t["id"])
            if old is None:
                if now_ts - t["entry_ts"] <= LATE_AFTER:
                    announce_new.append(t)
                else:
                    t["late"] = True
            else:
                if old.get("late"):
                    t["late"] = True
                if old["status"] == "OPEN" and t["status"] == "CLOSED":
                    announce_done.append(t)
        if scanned:                                     # never wipe the day's record on a bad data minute
            older = [t for t in self.state["trades"] if t["date"] != iso]
            for t in older:                             # earlier days keep their numbers but not their long text
                t.pop("why", None)
            # symbols not looked at in this pass (a data hiccup, or the time budget) keep what they had
            kept = [t for t in self.state["trades"] if t["date"] == iso and t["symbol"] not in done_syms]
            self.state["trades"] = older + sorted(kept + fresh, key=lambda x: x["entry_ts"])
        self._roll_archive(today)
        self._today_stats(iso)
        self._option_charts(today)
        self._prune_charts(today)
        self.state["regime"] = self._regime(today)
        stt.update({"cycle_at": now.isoformat(timespec="seconds"), "bar_end": bar_end.isoformat(timespec="minutes"),
                    "symbols": len(self.insts), "scanned": scanned, "failed": failed, "error": None})
        self.state["date"] = iso
        self.state["updated"] = now.isoformat(timespec="seconds")
        announce_new = [t for t in announce_new if t["strategy"] in self.alert_for]
        announce_done = [t for t in announce_done if t["strategy"] in self.alert_for]
        if self.tg_signals and (announce_new or announce_done):
            self._tell(announce_new, announce_done)

    # ── charts ───────────────────────────────────────────────
    def _option_charts(self, today):
        """Charts for the options desk's trades, drawn from the same candles (indices and shares only)."""
        iso = today.isoformat()
        for t in self.options_trades():
            if t.get("date") != iso or not t.get("entry_min"):
                continue
            S = self.series.get(self.by_name.get(t["name"]))
            if S is None or not S.n:
                continue
            sig = [j for j in range(S.n) if S.t[j] + 300 <= t["entry_min"] + 59]
            if not sig:
                continue
            i_sig = sig[-1]
            closed = t["status"] == "CLOSED"
            i_end = S.n - 1
            if closed and t.get("exit_time"):
                x_ts = int(datetime.fromisoformat(t["exit_time"]).timestamp())
                ends = [j for j in range(S.n) if S.t[j] + 300 <= x_ts + 59]
                i_end = ends[-1] if ends else i_end
            a = S.starts[S.day[i_sig]]
            n_range = 3 if t.get("book") == "Stocks" else 6
            lines = [("Range high", t["range_high"]), ("Range low", t["range_low"]), ("Range middle (stop)", t["range_mid"])]
            box = {"i0": a, "i1": a + n_range - 1, "lo": t["range_low"], "hi": t["range_high"], "label": "Opening range"}
            xp = t.get("underlying_last") if closed else None
            self.charts.setdefault("options", {})[t["id"]] = sim.build_chart(
                S, i_sig, max(i_end, i_sig), ["vwap"], lines, box, t["underlying_entry"], xp)
            self._charts_changed.add("options")

    def _prune_charts(self, today):
        cut = (today - timedelta(days=CHART_DAYS)).isoformat()
        dates = {t["id"]: t["date"] for t in self.state["trades"]}
        dates.update({t["id"]: t["date"] for t in self.options_trades()})
        for sid, d in self.charts.items():
            gone = [k for k in d if dates.get(k, "") < cut]
            for k in gone:
                del d[k]
            if gone:
                self._charts_changed.add(sid)

    # ── market conditions ────────────────────────────────────
    def _regime(self, today):
        """The state of the market right now, and how many shares pass each condition-gated strategy's filter."""
        def label(adx):
            return "Trending" if adx >= 22 else "Early trend or mixed" if adx >= 18 else "Ranging"
        markets = []
        for name in ("Nifty 50", "Bank Nifty"):
            S = self.series.get(self.by_name.get(name))
            if S is None or not S.n or S.day[-1] != today or S.adx[-1] is None:
                continue
            i, a = S.n - 1, S.starts[today]
            pc = S.prev_close.get(today)
            hi, lo = max(S.h[a:]), min(S.l[a:])
            markets.append({"name": name, "price": round(S.c[i], 2), "chg": round((S.c[i] / pc - 1) * 100, 2) if pc else None,
                            "adx": round(S.adx[i], 1), "label": label(S.adx[i]),
                            "trend": "up" if S.ema20[i] > S.ema50[i] else "down", "vwap": "above" if S.c[i] > S.vwap[i] else "below",
                            "range": round((hi - lo) / S.day_open[today] * 100, 2), "squeeze": bool(S.squeeze[i])})
        stocks = [S for sym, S in self.series.items() if sym.endswith("-EQ") and S.n and S.day[-1] == today and S.adx[-1] is not None]
        if not markets and not stocks:
            return None
        n = len(stocks)

        def share(test):
            return round(sum(1 for S in stocks if test(S)) / n * 100) if n else None
        breadth = {"stocks": n, "trending": share(lambda S: S.adx[-1] >= 22), "ranging": share(lambda S: S.adx[-1] < 22),
                   "above_vwap": share(lambda S: S.c[-1] > S.vwap[-1]),
                   "up_on_day": share(lambda S: bool(S.prev_close.get(today)) and S.c[-1] > S.prev_close[today])}
        filters = {"bb_bounce": {"gate": "ADX below 22 (the market is not trending)", "share": breadth["ranging"]},
                   "ema_pullback": {"gate": "ADX of 22 or more (a strong trend)", "share": breadth["trending"]},
                   "ema_cross": {"gate": "ADX of 18 or more and rising (a trend building)", "share": share(lambda S: S.adx[-1] >= 18)}}
        return {"asof": clock.now().isoformat(timespec="minutes"), "markets": markets, "breadth": breadth, "filters": filters}

    # ── bookkeeping ──────────────────────────────────────────
    def _roll_archive(self, today):
        cut = (today - timedelta(days=KEEP_DAYS)).isoformat()
        old = [t for t in self.state["trades"] if t["date"] < cut]
        if not old:
            return
        for t in old:
            if t["status"] != "CLOSED":
                continue
            a = self.state["archive"].setdefault(t["strategy"], {"n": 0, "wins": 0, "losses": 0, "gross": 0.0, "net": 0.0, "r": 0.0})
            a["n"] += 1
            a["wins"] += t["net"] > 0
            a["losses"] += t["net"] < 0
            a["gross"] = round(a["gross"] + t["gross"], 2)
            a["net"] = round(a["net"] + t["net"], 2)
            a["r"] = round(a["r"] + t["net_r"], 3)
        self.state["trades"] = [t for t in self.state["trades"] if t["date"] >= cut]

    def _today_stats(self, iso):
        out = {}
        for s in STRATEGIES:
            mine = [t for t in self.state["trades"] if t["date"] == iso and t["strategy"] == s["id"]]
            closed = [t for t in mine if t["status"] == "CLOSED"]
            out[s["id"]] = {"signals": len(mine), "open": len(mine) - len(closed), "closed": len(closed),
                            "wins": sum(1 for t in closed if t["net"] > 0),
                            "net": round(sum(t["net"] for t in closed) + sum(t.get("open_pnl") or 0 for t in mine if t["status"] == "OPEN"), 2)}
        self.state["today"] = out

    def _tell(self, new, done):
        names = {s["id"]: s["name"] for s in STRATEGIES}
        lines = []
        if new:
            by = {}
            for t in sorted(new, key=lambda x: x["entry_ts"]):
                by.setdefault(t["strategy"], []).append(t)
            counts = ", ".join(f"{names[k]} {len(v)}" for k, v in by.items())
            lines.append(f"🧭 <b>{len(new)} new strategy signal{'s' if len(new) > 1 else ''}</b>: {counts}")
            shown, queues = [], [list(v) for v in by.values()]
            while queues and len(shown) < MAX_LINES:        # one from each strategy in turn, so none is drowned out
                for q in list(queues):
                    shown.append(q.pop(0))
                    if not q:
                        queues.remove(q)
                    if len(shown) >= MAX_LINES:
                        break
            for t in shown:
                tgt = f"target {px(t['target'])}" if t["target"] else "no fixed target"
                lines.append(f"• {names[t['strategy']]}: {'BUY' if t['dir'] == 1 else 'SELL'} {t['qty']} {t['name']} near "
                             f"₹{px(t['entry'])}, stop {px(t['stop'])}, {tgt}")
            if len(new) > len(shown):
                lines.append(f"… and {len(new) - len(shown)} more, all on the dashboard.")
        if done:
            lines.append(f"\n<b>{len(done)} ended</b>: " + ", ".join(
                f"{names[t['strategy']]} {t['name']} {rs(t['net'])}" for t in sorted(done, key=lambda x: x["exit_ts"])[:5])
                + (f", and {len(done) - 5} more" if len(done) > 5 else ""))
        lines.append("\nThese are paper trades for judging each strategy. Open the dashboard for the reasons.")
        self.notify("\n".join(lines))

    def summary_text(self, iso):
        names = {s["id"]: s["name"] for s in STRATEGIES}
        lines = ["🧾 <b>Strategy desk: today</b>"]
        total = 0.0
        for s in STRATEGIES:
            x = self.state["today"].get(s["id"]) or {}
            if x.get("signals"):
                total += x["net"]
                lines.append(f"• {names[s['id']]}: {x['signals']} trades, {x['wins']} won, net {rs(x['net'])}")
        if len(lines) == 1:
            lines.append("No strategy produced a trade today.")
        else:
            lines.append(f"All strategies together: {rs(total)} after estimated costs")
        return "\n".join(lines)
