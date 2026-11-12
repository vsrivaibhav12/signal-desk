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
from datetime import timedelta

from .. import clock
from ..clock import at, ts
from ..fyers import AuthError
from . import sim
from .defs import STRATEGIES
from .indicators import Series

KEEP_DAYS = 10            # trades older than this are folded into running totals
LATE_AFTER = 600          # a signal found more than this many seconds after its candle closed is recorded, not announced
WARM_DAYS = 10            # calendar days of earlier candles kept for the indicators
PASS_BUDGET = 200         # seconds one pass may take; the rest waits for the next candle
MAX_LINES = 10            # example signals listed in one Telegram message


def rs(x):
    return ("-" if x < 0 else "") + "₹" + f"{abs(x):,.0f}"


def px(x):
    return f"{x:,.2f}".rstrip("0").rstrip(".")


class StrategyDesk:
    def __init__(self, cfg, broker, notify, folder, universe, master_markets, heartbeat=None):
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
        self.path = os.path.join(folder, "strategies.json")
        lots = self.cfg["index_lots"]
        self.insts = sim.build_instruments(master_markets, universe, lots)
        self.warm = {}                     # symbol -> earlier candles
        self.warm_day = None
        self.dirty = False
        self.state = self._load()

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

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, self.path)

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
                for strat in STRATEGIES:
                    fresh += sim.simulate(S, inst, strat, self.cfg, days=[today], live_day=today)
            except AuthError:
                raise
            except Exception as e:
                failed += 1
                if failed <= 3:
                    print(f"Strategy desk: no data for {sym}: {e}")
        self.rotate = (self.rotate + stopped_at) % len(names) if stopped_at else 0
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
        stt.update({"cycle_at": now.isoformat(timespec="seconds"), "bar_end": bar_end.isoformat(timespec="minutes"),
                    "symbols": len(self.insts), "scanned": scanned, "failed": failed, "error": None})
        self.state["date"] = iso
        self.state["updated"] = now.isoformat(timespec="seconds")
        announce_new = [t for t in announce_new if t["strategy"] in self.alert_for]
        announce_done = [t for t in announce_done if t["strategy"] in self.alert_for]
        if self.tg_signals and (announce_new or announce_done):
            self._tell(announce_new, announce_done)

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
