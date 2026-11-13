"""The live strategy desk.

Every time a 5-minute candle closes it takes today's candles for the watchlist (indices, open trades, results,
news, and shares whose volume or price is out of the ordinary), runs the active strategies over them with the same
code the backtest uses, and compares the result with what it already knows. A trade it has not seen before is a new signal. A trade that was open and has now ended is a result.
Because the answer is always worked out afresh from the day's candles, a restart loses nothing.
It never places an order.
"""
import json
import os
import time
import traceback
from datetime import datetime, timedelta

from .. import charts as charts_mod
from .. import clock, journal, migrations, optscan
from ..errors import BrokerError
from ..schema import SCHEMA
from ..clock import at, ts
from ..fyers import AuthError
from . import decide, sim, risk
from .defs import STRATEGIES
from .indicators import Series, normalize_candles

KEEP_DAYS = 10            # trades older than this are folded into running totals
CHART_DAYS = 3            # calendar days of trade charts kept (today, and the last session or two)
LATE_AFTER = 600          # a signal found more than this many seconds after its candle closed is recorded, not announced
WARM_DAYS = 10            # calendar days of earlier candles kept for the indicators
PASS_BUDGET = 200         # seconds one pass may take; the rest waits for the next candle
MAX_LINES = 10            # example signals listed in one Telegram message
HOT_MAX = 45              # symbols looked at first in each pass: indices, open trades, results, news, recent movers


def rs(x):
    return ("-" if x < 0 else "") + "₹" + f"{abs(x):,.0f}"


def px(x):
    return f"{x:,.2f}".rstrip("0").rstrip(".")


def candle_is_fresh(candle_end_ts, now_ts, max_age_s=600, future_skew_s=10):
    """True only for a completed candle whose end time is recent and not implausibly in the future."""
    try:
        age = float(now_ts) - float(candle_end_ts)
    except (TypeError, ValueError):
        return False
    return -future_skew_s <= age <= max_age_s


class StrategyDesk:
    def __init__(self, cfg, broker, notify, folder, universe, master_markets, heartbeat=None, options_trades=None, flush=None,
                 results=None, watch=None, master=None, alerts=None, catalysts=None, leaders=None):
        self.cfg = sim.settings(cfg)
        self.shared_portfolio = cfg.get("portfolio") or {}
        sc = cfg.get("strategies") or {}
        self.on = sc.get("enabled", True)
        mode = sc.get("telegram", "signals")
        self.tg_on = mode not in (False, "off")                    # the end-of-day summary
        self.tg_signals = mode in (True, "signals")                # a grouped message whenever new signals appear
        want = sc.get("alert_for", "all")
        self.alert_for = {s["id"] for s in STRATEGIES} if want in ("all", None) else set(want)
        self.heartbeat = heartbeat or (lambda: None)               # called often so a long pass never looks like a dead engine
        self.flush = flush or (lambda: None)                       # publishes what is known so far, so the interesting stocks show up first
        self.alert_grade = sc.get("alert_grade", "take")           # "take": only trades that pass the checks. "all": every signal
        self.rotate = 0                                            # where the next pass starts, so no symbol is always last
        self.broker, self.notify = broker, notify
        self.alerts = alerts
        self.say = alerts.send if alerts else (lambda text, kind="info", buttons=None: notify(text))
        self.journal = None                                         # set by the session: gives alerts their Took it / Skip buttons
        self.results, self.watch, self.master = results, watch, master
        self.leaders = leaders                                      # relative-strength ratings from NSE's daily files (engine/leaders.py)
        self.catalysts = catalysts or (lambda: [])                  # names of shares with a news catalyst today
        self.markets = [m for m in master_markets if m.get("kind") == "index" and m.get("enabled", True)]
        self.oscfg = optscan.settings(cfg)
        self.folder = folder
        self.path = os.path.join(folder, "strategies.json")
        lots = self.cfg["index_lots"]
        self.insts = sim.build_instruments(master_markets, universe, lots)
        self.by_name = {i["name"]: sym for sym, i in self.insts.items()}
        self.options_trades = options_trades or (lambda: [])        # the options desk's trades, so they get charts too
        self.warm = {}                     # symbol -> earlier candles
        self.warm_day = None
        self.series = {}                   # symbol -> today's Series from the latest pass
        self._bad_current_candles = {}
        self._invalid_candle_symbols = []
        self.dirty = False
        self.state = self._load()
        self.charts = self._load_charts()  # source id -> {trade id: chart}
        self._charts_changed = set()
        self._stale_symbols = []
        self.option_tape_path = os.path.join(folder, "options_tape.jsonl")

    # ── saved state ──────────────────────────────────────────
    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = {}
        if st and st.get("schema") != SCHEMA:                 # a file from an older layout: migrate it, or keep its trades and start clean
            st, was_reset = migrations.upgrade_strategy_state(st, self.folder)
            if was_reset:                                     # the old charts belong to the old layout
                for f in os.listdir(self.folder):
                    if f.startswith("chartlive_"):
                        try:
                            os.remove(os.path.join(self.folder, f))
                        except OSError:
                            pass
        st["schema"] = SCHEMA
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
                self.say(self.summary_text(today.isoformat()), "summary")
        self.dirty = True

    def _history(self, sym, today):
        if self.warm_day != today:
            self.warm, self.warm_day = {}, today
            self._bad_current_candles = {}
        if sym not in self.warm:
            raw = self.broker.history(sym, "5", today - timedelta(days=WARM_DAYS), today)
        else:
            raw = self.broker.history(sym, "5", today, today)
        clean, _, invalid_days, invalid_unknown, _ = normalize_candles(raw)
        self._bad_current_candles[sym] = today in invalid_days or bool(invalid_unknown)
        if sym not in self.warm:
            self.warm[sym] = [c for c in clean if clock.from_ts(c[0]).date() < today]
        return clean

    def _hot(self, today):
        """Symbols to look at first: indices (the market read), open trades, recent movers, the options desk's trades."""
        iso = today.isoformat()
        hot = [sym for sym, i in self.insts.items() if i["kind"] == "index"]
        for t in self.state["trades"]:
            if t["date"] == iso and t["status"] == "OPEN" and t["symbol"] not in hot:
                hot.append(t["symbol"])
        for m in (self.state.get("movers") or {}).get("list", []):
            sym = self.by_name.get(m["name"])
            if sym and sym not in hot:
                hot.append(sym)
        for t in self.options_trades():
            sym = self.by_name.get(t.get("name"))
            if t.get("date") == iso and sym and sym not in hot:
                hot.append(sym)
        return hot[:HOT_MAX]

    def _scan(self, group, today, now_ts, started, mkt_hint=None):
        """Runs every strategy over each symbol in the group. Returns (new trade list, symbols done, failures, index it stopped at)."""
        fresh, done, failed, stopped = [], set(), 0, None
        for k, sym in enumerate(group):
            inst = self.insts[sym]
            if k % 10 == 0:
                self.heartbeat()
            if time.monotonic() - started > PASS_BUDGET:       # never hold up the rest of the engine; carry on next candle
                stopped = k
                break
            if getattr(self.broker, "cooling", lambda: 0)() > 5:   # the broker told us to back off: stop here, resume next candle
                stopped = k
                break
            try:
                raw = self._history(sym, today)
                if self._bad_current_candles.get(sym):
                    self._invalid_candle_symbols.append({"symbol": sym, "reason": "invalid timestamp or OHLC row in the current history response"})
                    self.series.pop(sym, None)
                    failed += 1
                    continue
                todays = [c for c in raw if clock.from_ts(c[0]).date() == today and c[0] + 300 <= now_ts]
                if not todays:
                    self.series.pop(sym, None)
                    continue
                # A successful history response can still end on an old candle.
                # Skip that symbol rather than promoting stale prices into a live signal.
                max_age = int(self.cfg.get("max_candle_age_s", 600))
                candle_end = todays[-1][0] + 300
                candle_age = now_ts - candle_end
                if not candle_is_fresh(candle_end, now_ts, max_age, 10):
                    self._stale_symbols.append({"symbol": sym, "age_s": int(candle_age)})
                    self.series.pop(sym, None)
                    failed += 1
                    continue
                S = Series(self.warm[sym] + todays)
                if S.n < self.cfg["min_bars"]:
                    self.series.pop(sym, None)
                    continue
                done.add(sym)
                self.series[sym] = S
                mkt = sim.market_series(self.series, self.insts)
                for strat in STRATEGIES:
                    fresh += sim.simulate(S, inst, strat, self.cfg, days=[today], live_day=today, chart_from=today, market=mkt)
            except AuthError:
                raise
            except Exception as e:
                self.series.pop(sym, None)
                failed += 1
                if failed <= 3:
                    print(f"Strategy desk: no data for {sym}: {e}")
        decide.finalize(fresh, self.cfg["decide"])              # the "conflicting signal" check needs every strategy's trades for the stock
        return fresh, done, failed, stopped

    def _plan(self, now, today):
        """Who to scan this candle. Returns (hot, rest, play): the shares that matter most, the shares promoted by the quotes
        scan, and a reason for each share that is in play (results, news, heavy volume, a big move)."""
        hot, play = self._hot(today), {}
        names = {}
        if self.results:
            self.state["results"] = self.results.public(today)
            for w in self.results.watch(today):
                sym = self.by_name.get(w["symbol"])
                if sym:
                    play[sym] = w["why"]
                    if sym not in hot:
                        hot.append(sym)
        for item in list(self.catalysts())[:20]:
            # Older callers may provide a symbol string. The 4.1 intelligence
            # service supplies a structured reason tied to a particular event.
            if isinstance(item, dict):
                name = str(item.get("symbol") or "").upper()
                why = str(item.get("why") or "In the news")
            else:
                name, why = str(item).upper(), "In the news"
            sym = self.by_name.get(name)
            if sym:
                play.setdefault(sym, why)
                if sym not in hot:
                    hot.append(sym)
        if self.watch is not None:
            try:
                board = self.watch.scan(self.broker, now)
            except AuthError:
                raise
            except BrokerError as e:
                print(f"Strategy desk: quotes scan failed: {e}")
                board = self.watch.board
            for r in board:
                play.setdefault(r["symbol"], f"Volume {r['volx']}x normal" if r.get("volx") else f"Moved {r['move']:+.1f}%")
            rest = [r["symbol"] for r in board if r["symbol"] in self.insts and r["symbol"] not in hot]
        else:
            rest = [n for n in self.insts if n not in hot]
            k = self.rotate % max(1, len(rest)) if rest else 0
            rest = rest[k:] + rest[:k]
        hot = hot[:HOT_MAX]
        for sym in hot + rest:
            names[sym] = play.get(sym) or ("Index" if self.insts[sym]["kind"] == "index" else "Open trade or mover")
        rs = {}
        if self.leaders is not None and self.leaders.data and self.leaders.ok(today):
            rs = {sym: self.leaders.rs(self.insts[sym]["name"]) for sym in hot + rest if sym in self.insts}
            rs = {k: v for k, v in rs.items() if v is not None}
        if self.leaders is not None and self.leaders.data:
            self.state["leaders"] = self.leaders.public(today)
        self.cfg["decide"]["_rs"] = rs                              # tags each trade with the share's rating (a flag, not a gate)
        self.state["watchlist"] = [{"symbol": sym, "name": self.insts[sym]["name"], "why": names[sym], "rs": rs.get(sym)} for sym in hot + rest
                                   if sym in self.insts]
        if self.watch is not None:
            self.state["scan"] = self.watch.public()
        return hot, rest, play

    def _calls(self):
        snap = getattr(self.broker, "stats_snapshot", None)
        return snap()["total"] if snap else None

    def _cycle(self, now, today, bar_end):
        now_ts, iso = ts(bar_end), today.isoformat()
        self._stale_symbols = []
        self._invalid_candle_symbols = []
        # Every pass must stand on data fetched in this pass. Keeping an old
        # index Series after an empty/failed response could otherwise let a
        # previous Nifty read leak into the options confirmation.
        self.series = {}
        calls0, t0 = self._calls(), time.monotonic()
        hot, rest, play = self._plan(now, today)
        self.cfg["decide"]["_play"] = play                           # lets the decision tag trades on shares that are in play
        started = time.monotonic()
        totals = {"scanned": 0, "failed": 0}
        for phase, group in enumerate((hot, rest)):
            fresh, done, failed, stopped = self._scan(group, today, now_ts, started)
            if phase == 1 and self.watch is None:
                self.rotate = (self.rotate + stopped) % max(1, len(rest)) if stopped else 0
            totals["scanned"] += len(done)
            totals["failed"] += failed
            self._apply(now, today, bar_end, fresh, done, totals)
            if phase == 0 and hot:
                self.flush()                                    # the stocks that matter are visible before the full pass finishes
        try:
            self._options_scan(now, today)
        except AuthError:
            raise
        except Exception as e:                                  # the scanner must never stop the strategy desk
            traceback.print_exc()
            self.state["options_scan"] = {"asof": now.isoformat(timespec="minutes"), "items": [], "error": str(e)[:120]}
        calls1 = self._calls()
        if calls0 is not None and calls1 is not None:           # how many broker requests this candle really cost
            self.state["status"]["cycle_calls"] = calls1 - calls0
            self.state["status"]["cycle_seconds"] = round(time.monotonic() - t0, 1)
            self.state["api"] = self.broker.stats_snapshot()
        try:
            self.state["health"] = self._health(now, today)
        except Exception:
            traceback.print_exc()

    # ── data health ──────────────────────────────────────────
    def _health(self, now, today):
        """Is every input the decisions rely on present and recent? Anything not fresh means the desk says WATCH."""
        items, limit = [], self.oscfg["max_data_age_s"]
        for m in self.markets:
            S = self.series.get(m["spot"])
            if S is None or not S.n or S.day[-1] != today:
                items.append({"name": f"{m['name']} candles", "status": "missing", "detail": "no candles from today"})
                continue
            age = int(optscan._age(S, now))
            fresh = -10 <= age <= limit
            detail = f"newest candle finished {age // 60} min ago" if age >= 0 else f"newest candle timestamp is {abs(age)}s in the future"
            items.append({"name": f"{m['name']} candles", "status": "fresh" if fresh else "stale", "detail": detail})
        if self.watch is not None:
            sc = self.watch.public()
            age = None if not sc["at"] else int((now - datetime.fromisoformat(sc["at"])).total_seconds())
            items.append({"name": "Quotes scan", "status": "missing" if age is None else "fresh" if age <= 600 else "stale",
                          "detail": "not run yet" if age is None else f"{age // 60} min ago"})
            quality = sc.get("quality") or {}
            total, good = int(quality.get("expected", 0)), int(quality.get("fresh", 0))
            coverage = good / max(1, total)
            items.append({"name": "Quote coverage", "status": "complete" if coverage >= 0.95 else "partial" if good else "missing",
                          "detail": f"{good} of {total} fresh quotes; {quality.get('exchange_timestamps', 0)} exchange-stamped, "
                                    f"{quality.get('response_timestamps', 0)} response-time fallback, "
                                    f"{quality.get('stale', 0)} stale, {quality.get('future', 0)} future, "
                                    f"{quality.get('invalid', 0)} invalid"})
            frac = sc["have_baseline"] / max(1, sc["of"])
            items.append({"name": "Normal-volume baseline", "status": "complete" if frac >= 0.95 else "partial" if frac > 0 else "missing",
                          "detail": f"{sc['have_baseline']} of {sc['of']} shares" + (f", {sc['baseline_backing_off']} backing off" if sc["baseline_backing_off"] else "")})
        if self.results:
            st = self.results.state
            items.append({"name": "Results calendar", "status": "fresh" if st.get("ok") else "unavailable",
                          "detail": (st.get("fetched_at") or "never read")[:16].replace("T", " ") + ("" if st.get("ok") else f"; {st.get('error') or 'not read yet'}")})
        tape_status = self.state.get("options_tape_status")
        if tape_status:
            items.append({"name": "Option quote capture", "status": "ok" if tape_status.get("ok") else "unavailable",
                          "detail": tape_status.get("detail", "no status")})
        api = self.state.get("api")
        if api:
            items.append({"name": "Broker", "status": "slowed" if api["cooling"] else "ok",
                          "detail": f"{api['total']} requests this run, history {api['history_rate']}/s"
                                    + (f", paused {api['cooling']}s" if api["cooling"] else "")})
        if self._stale_symbols:
            items.append({"name": "Selected stock candles", "status": "stale",
                          "detail": f"{len(self._stale_symbols)} selected symbol(s) skipped because their last completed candle was too old"})
        invalid_candles = getattr(self, "_invalid_candle_symbols", [])
        if invalid_candles:
            items.append({"name": "Selected stock candle integrity", "status": "invalid",
                          "detail": f"{len(invalid_candles)} selected symbol(s) skipped because the broker returned malformed timestamps or OHLC values"})
        bad = [i for i in items if i["status"] in ("missing", "stale", "invalid") and ("candles" in i["name"] or "candle" in i["name"] or i["name"] == "Quotes scan")]
        quote_coverage = next((i for i in items if i["name"] == "Quote coverage"), None)
        if quote_coverage:
            q = self.watch.public().get("quality", {}) if self.watch else {}
            total, good = int(q.get("expected", 0)), int(q.get("fresh", 0))
            if total and good / total < 0.90:
                bad.append(quote_coverage)
        return {"asof": now.isoformat(timespec="minutes"), "decision": "WATCH" if bad else "OK", "items": items,
                "blocking": [i["name"] for i in bad]}

    # ── options scanner ──────────────────────────────────────
    def _options_scan(self, now, today):
        if not (self.master and self.markets):
            return
        iso = today.isoformat()
        nm = next((m for m in self.markets if m["id"] == "NIFTY"), None)
        nifty = optscan.nifty_vwap_side(self.series.get(nm["spot"]), today, now, self.oscfg["max_data_age_s"]) if nm else None
        heavy = self.results.heavyweights(today) if self.results else []
        seen = self.state.setdefault("opt_seen", {})
        if seen.get("date") != iso:
            seen.clear()
            seen["date"] = iso
            seen["keys"] = []
        items = []
        for m in self.markets:
            read = optscan.read_index(self.series.get(m["spot"]), today, m, now, nifty, self.oscfg["max_data_age_s"],
                                      self.oscfg["max_skew_s"])
            if read["state"].endswith("broke out") and read["score"] >= self.oscfg["min_score"] and optscan.in_window(m, now):
                try:
                    optscan.suggest(read, m, self.master, self.broker, today, self.oscfg, now)
                except AuthError:
                    raise
                except Exception as e:
                    read["idea"] = {"error": f"contract lookup failed ({type(e).__name__})"}
            read["grade"], read["why_not"] = optscan.grade(read, m, now, self.oscfg, heavy)
            items.append(read)
            if (read.get("side") and (read.get("idea") or {}).get("contract") and not (read.get("idea") or {}).get("error")
                    and (read["grade"] == "take" or (read["grade"] == "watch" and read["score"] >= self.oscfg["min_score"]))):
                ch = charts_mod.scan_chart(self.series.get(m["spot"]), read, m, today)      # the same ideas live.json lists
                if ch:
                    self.charts.setdefault("options", {})[f"scan:{iso}:{m['id']}:{read['side']}"] = ch
                    self._charts_changed.add("options")
            key = f"{m['id']}:{read.get('side')}"
            if read["grade"] == "take" and key not in seen["keys"]:
                seen["keys"].append(key)
                self._tell_option(read, m)
        self.state["options_scan"] = {"asof": now.isoformat(timespec="minutes"), "items": items, "heavy": heavy}
        self._record_option_quotes(now, items)

    def _record_option_quotes(self, now, items):
        """Append live option quote observations for prospective premium research.

        Older option backtests are underlying-price proxies. This tape captures
        real bid/ask and depth from this release onward; it does not backfill or
        pretend to contain historical option-chain data.
        """
        import json
        rows = []
        for r in items:
            idea = r.get("idea") or {}
            if not idea.get("symbol") or idea.get("bid") is None or idea.get("ask") is None:
                continue
            rows.append({"schema": 1, "observed_at": now.isoformat(timespec="seconds"),
                         "underlying": r.get("id"), "underlying_spot": r.get("spot"), "direction": r.get("side"),
                         "score": r.get("score"), "grade": r.get("grade"), "reasons": r.get("why_not", []),
                         "contract": idea.get("contract"), "symbol": idea.get("symbol"), "strike": idea.get("strike"),
                         "expiry": idea.get("expiry"), "lot": idea.get("lot"), "bid": idea.get("bid"), "ask": idea.get("ask"),
                         "bid_qty": idea.get("bid_qty"), "ask_qty": idea.get("ask_qty"), "open_interest": idea.get("oi"),
                         "quote_ts": idea.get("quote_ts"), "quote_age_s": idea.get("quote_age_s"), "premium_entry": idea.get("premium"),
                         "stop": idea.get("stop"), "target": idea.get("target"), "risk_per_lot": idea.get("risk_per_lot"),
                         "cost_ratio": idea.get("cost_ratio")})
        if not rows:
            return
        os.makedirs(os.path.dirname(self.option_tape_path) or ".", exist_ok=True)
        try:
            with open(self.option_tape_path, "a", encoding="utf-8", newline="\n") as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            self.state["options_tape_status"] = {"ok": True, "asof": now.isoformat(timespec="seconds"),
                                                  "detail": f"{len(rows)} option quote observation(s) captured"}
        except OSError as e:
            # Research capture must never interrupt a market-data/decision cycle.
            self.state["options_tape_status"] = {"ok": False, "asof": now.isoformat(timespec="seconds"),
                                                  "detail": f"could not write option quote tape: {str(e)[:120]}"}
            return
        # Bound repository growth. Keep the most recent ~12 MB if this tape grows
        # beyond 24 MB; at typical scan volume this preserves many months of data.
        try:
            if os.path.getsize(self.option_tape_path) > 24 * 1024 * 1024:
                with open(self.option_tape_path, "rb") as f:
                    f.seek(-12 * 1024 * 1024, os.SEEK_END)
                    tail = f.read()
                tail = tail[tail.find(b"\n") + 1:]
                tmp = self.option_tape_path + ".tmp"
                with open(tmp, "wb") as f:
                    f.write(tail)
                os.replace(tmp, self.option_tape_path)
        except OSError:
            pass

    def _tell_option(self, r, m):
        i = r["idea"]
        side = "CALL" if r["side"] == "CE" else "PUT"
        warn = ("\n⚠️ " + ", ".join(r["warnings"])) if r.get("warnings") else ""
        buttons = self.journal.buttons([journal.summary_from_scan(r, m, clock.now().date().isoformat())]) if self.journal else None
        self.say(f"🎯 <b>{m['name']}: {side} idea</b> ({r['score']} of 5)\n{i['contract']} at ₹{px(i['premium'])}, lot {i['lot']}\n"
                 f"Stop ₹{px(i['stop'])}, target ₹{px(i['target'])}. Risk about {rs(i['risk_per_lot'])} a lot."
                 f"{warn}\nPaper idea; check the dashboard before acting.", "options", buttons=buttons)

    def _apply(self, now, today, bar_end, fresh, done_syms, totals):
        stt = self.state["status"]
        now_ts, iso = ts(bar_end), today.isoformat()
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
        if done_syms:                                   # never wipe the day's record on a bad data minute
            older = [t for t in self.state["trades"] if t["date"] != iso]
            for t in older:                             # earlier days keep their numbers but not their long text
                t.pop("why", None)
            # Symbols not looked at in this pass keep their previous trades. Re-run the
            # portfolio admission controller across the whole current-day candidate set.
            kept = [t for t in self.state["trades"] if t["date"] == iso and t["symbol"] not in done_syms]
            day_trades = kept + fresh
            external_today = [t for t in (self.options_trades() or [])
                              if t.get("date") == iso and t.get("status") in ("OPEN", "CLOSED")
                              and t.get("risk_approved", True)]
            risk.annotate(day_trades, self.cfg.get("portfolio"),
                          external_trades=external_today, shared_cfg=self.shared_portfolio)
            portfolio_today = risk.summary(day_trades)
            strategy_open_risk = sum(float(t.get("risk", 0) or 0) for t in day_trades
                                     if t.get("risk_approved") and t.get("status") == "OPEN")
            external_open = [t for t in external_today if t.get("status") == "OPEN"]
            external_closed = [t for t in external_today if t.get("status") == "CLOSED"]
            portfolio_today.update({
                "shared_open_positions": portfolio_today["open"] + len(external_open),
                "shared_open_risk": round(strategy_open_risk + sum(
                    float(t.get("risk", t.get("risk_amount", 0)) or 0) for t in external_open), 2),
                "external_open_positions": len(external_open),
                "shared_realized_pnl": round(sum(float(t.get("net", 0) or 0) for t in day_trades
                                                   if t.get("risk_approved") and t.get("status") == "CLOSED")
                                                  + sum(float(t.get("net", t.get("pnl", 0)) or 0)
                                                        for t in external_closed), 2),
            })
            self.state["portfolio_today"] = portfolio_today
            self.state["portfolio_limits"] = dict(self.cfg.get("portfolio") or {})
            self.state["trades"] = older + sorted(day_trades, key=lambda x: x["entry_ts"])
        self._roll_archive(today)
        self._today_stats(iso)
        self._option_charts(today)
        self._prune_charts(today)
        self.state["regime"] = self._regime(today)
        new_movers = self._movers(today, now)
        stt.update({"cycle_at": now.isoformat(timespec="seconds"), "bar_end": bar_end.isoformat(timespec="minutes"),
                    "symbols": len(self.insts), "scanned": totals["scanned"], "failed": totals["failed"], "error": None})
        self.state["date"] = iso
        self.state["updated"] = now.isoformat(timespec="seconds")
        self.dirty = True

        def wanted(t):
            return (t.get("risk_approved", True) and t["strategy"] in self.alert_for
                    and (self.alert_grade == "all" or t["dec"]["g"] == "take"))
        announce_new = [t for t in announce_new if wanted(t)]
        announce_done = [t for t in announce_done if wanted(t)]
        if self.tg_signals and (announce_new or announce_done):
            self._tell(announce_new, announce_done)
        if self.tg_signals and new_movers:
            self._tell_movers(new_movers)

    # ── trend setters ────────────────────────────────────────
    def _movers(self, today, now):
        """Stocks making a big move. 'Genuine' means the move is backed by volume, price is on the right side of
        VWAP, and it has held (it is still near the day's extreme), not a spike that has already faded."""
        iso = today.isoformat()
        seen = self.state.setdefault("movers_seen", {})
        if seen.get("date") != iso:
            seen.clear()
            seen["date"] = iso
            seen["first"] = {}
            seen["alerted"] = []
        rows = []
        for sym, S in self.series.items():
            inst = self.insts.get(sym)
            if not inst or inst["kind"] != "stock" or not S.n or S.day[-1] != today:
                continue
            i, a = S.n - 1, S.starts[today]
            pc = S.prev_close.get(today)
            if not pc or S.atr[i] is None:
                continue
            c, o = S.c[i], S.day_open[today]
            mv_pc, mv_open = (c / pc - 1) * 100, (c / o - 1) * 100
            big = mv_pc if abs(mv_pc) >= abs(mv_open) else mv_open
            if abs(big) < 1.5:
                continue
            d = 1 if big > 0 else -1
            hi, lo = max(S.h[a:]), min(S.l[a:])
            pos = (c - lo) / (hi - lo) if hi > lo else 0.5
            cum = S.cumratio[i] if S.has_volume else None
            ok_vol, ok_vwap = cum is not None and cum >= 2, d * (c - S.vwap[i]) > 0
            ok_held = pos >= 0.7 if d == 1 else pos <= 0.3
            genuine = abs(big) >= 2.0 and ok_vol and ok_vwap and ok_held
            rows.append({"name": inst["name"], "dir": d, "price": round(c, 2), "move": round(big, 2), "vs_close": round(mv_pc, 2), "from_open": round(mv_open, 2),
                         "vol": None if cum is None else round(cum, 1), "held": round(pos, 2), "genuine": genuine,
                         "checks": {"volume": ok_vol, "vwap": ok_vwap, "held": ok_held}, "size": round(abs(big), 2)})
            if genuine and inst["name"] not in seen["first"]:
                seen["first"][inst["name"]] = now.isoformat(timespec="minutes")
        for r in rows:
            r["since"] = seen["first"].get(r["name"])
        rows.sort(key=lambda r: (not r["genuine"], -r["size"] * (1 + min(r["vol"] or 0, 6) / 6)))
        up = [r for r in rows if r["dir"] == 1][:8]
        down = [r for r in rows if r["dir"] == -1][:8]
        lst = sorted(up + down, key=lambda r: (not r["genuine"], -r["size"]))
        self.state["movers"] = {"asof": now.isoformat(timespec="minutes"), "list": lst}
        fresh = [r for r in lst if r["genuine"] and r["name"] not in seen["alerted"]]
        for r in fresh:
            seen["alerted"].append(r["name"])
        return fresh

    def _tell_movers(self, movers):
        lines = [f"🔥 <b>{len(movers)} genuine trend setter{'s' if len(movers) > 1 else ''}</b>"]
        for r in movers[:6]:
            lines.append(f"• <b>{r['name']}</b> {'up' if r['dir'] == 1 else 'down'} {abs(r['move']):.1f}% on {r['vol']}x its usual volume, "
                         f"price ₹{px(r['price'])}, above/below VWAP and holding near the {'high' if r['dir'] == 1 else 'low'}")
        lines.append("\nBig move, real volume, and still going. Open the dashboard to see what the tool is doing with each.")
        self.say("\n".join(lines), "movers")

    # ── charts ───────────────────────────────────────────────
    def _option_charts(self, today):
        """Charts for the options desk's trades, drawn on the same candles (indices and shares only)."""
        iso = today.isoformat()
        for t in self.options_trades():
            if t.get("date") != iso:
                continue
            ch = charts_mod.option_trade_chart(self.series.get(self.by_name.get(t["name"])), t)
            if ch:
                self.charts.setdefault("options", {})[t["id"]] = ch
                self._charts_changed.add("options")

    def _prune_charts(self, today):
        cut = (today - timedelta(days=CHART_DAYS)).isoformat()
        dates = {t["id"]: t["date"] for t in self.state["trades"]}
        dates.update({t["id"]: t["date"] for t in self.options_trades()})
        for sid, d in self.charts.items():
            # a scanner idea's chart is named scan:<date>:<index>:<side>, so its date is in its name
            gone = [k for k in d if (dates.get(k) or (k.split(":")[1] if k.startswith("scan:") else "")) < cut]
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
            if t["status"] != "CLOSED" or not t.get("risk_approved", True):
                continue
            a = self.state["archive"].setdefault(t["strategy"], {"n": 0, "wins": 0, "losses": 0, "gross": 0.0, "net": 0.0, "r": 0.0})
            a["n"] += 1
            a["wins"] += t["net"] > 0
            a["losses"] += t["net"] < 0
            a["gross"] = round(a["gross"] + t["gross"], 2)
            a["net"] = round(a["net"] + t["net"], 2)
            a["r"] = round(a["r"] + t["net_r"], 3)
        # Archive only completed, eligible trades. Never discard a position that
        # remained open because the process/data feed was interrupted: keep it in
        # the live ledger so the dashboard can surface it for reconciliation.
        self.state["trades"] = [
            t for t in self.state["trades"]
            if t["date"] >= cut or t.get("status") != "CLOSED"
        ]

    def _today_stats(self, iso):
        out = {}
        for s in STRATEGIES:
            mine = [t for t in self.state["trades"] if t["date"] == iso and t["strategy"] == s["id"]]
            approved = [t for t in mine if t.get("risk_approved", True)]
            closed = [t for t in approved if t["status"] == "CLOSED"]
            out[s["id"]] = {"signals": len(mine), "approved": len(approved),
                            "rejected": len(mine) - len(approved),
                            "open": sum(1 for t in approved if t["status"] == "OPEN"), "closed": len(closed),
                            "wins": sum(1 for t in closed if t["net"] > 0),
                            "net": round(sum(t["net"] for t in closed) + sum(t.get("open_pnl") or 0 for t in approved if t["status"] == "OPEN"), 2)}
        self.state["today"] = out

    def _tell(self, new, done):
        names = {s["id"]: s["name"] for s in STRATEGIES}
        if new:
            lines = []
            for t in sorted(new, key=lambda x: x["entry_ts"])[:MAX_LINES]:
                tgt = f"target {px(t['target'])}" if t["target"] else "no fixed target"
                tag = (t.get("dec") or {}).get("ip")
                strength = (t.get("dec") or {}).get("rs")
                lines.append(f"🎯 <b>{'BUY' if t['dir'] == 1 else 'SELL'} {t['qty']} {t['name']}</b> near ₹{px(t['entry'])} · "
                             f"stop {px(t['stop'])} · {tgt} · {names[t['strategy']]}" + (f" · {tag}" if tag else "")
                             + (f" · RS leader {strength:.0f}" if strength is not None and strength >= 80 and t["dir"] == 1 else ""))
            if len(new) > MAX_LINES:
                lines.append(f"… and {len(new) - MAX_LINES} more on the dashboard.")
            lines.append("Paper trades. Reasons and chart on the dashboard.")
            shown = sorted(new, key=lambda x: x["entry_ts"])[:MAX_LINES]
            takes = [journal.summary_from_strategy(t) for t in shown if (t.get("dec") or {}).get("g") == "take"]
            buttons = self.journal.buttons(takes) if self.journal and takes else None
            self.say("\n".join(lines), "trades", buttons=buttons)
        if done:
            self.say("<b>Ended:</b> " + ", ".join(f"{t['name']} {rs(t['net'])}" for t in sorted(done, key=lambda x: x["exit_ts"])[:6])
                     + (f", and {len(done) - 6} more" if len(done) > 6 else ""), "exits")

    def summary_text(self, iso):
        names = {s["id"]: s["name"] for s in STRATEGIES}
        lines = ["🧾 <b>Strategy desk: today</b>"]
        total = 0.0
        for s in STRATEGIES:
            x = self.state["today"].get(s["id"]) or {}
            if x.get("signals"):
                total += x["net"]
                lines.append(f"• {names[s['id']]}: {x.get('approved', x['signals'])} risk-approved, "
                             f"{x['wins']} won, net {rs(x['net'])} ({x.get('rejected', 0)} filtered)")
        if len(lines) == 1:
            lines.append("No strategy produced a trade today.")
        else:
            lines.append(f"All strategies together: {rs(total)} after estimated costs")
        return "\n".join(lines)
