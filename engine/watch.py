"""Watchlist and load control.

Asking the broker for every share's candles every five minutes is what overloaded the first live runs. Instead:
  1. one cheap quotes call (50 shares at a time) reads price and volume for the whole Nifty 200;
  2. shares whose volume is running well above normal for the time of day, or that have moved a lot, are promoted;
  3. only the watchlist (indices, open trades, results, news, promoted shares) gets candles and strategy scans.
A share that is not moving on volume is not in play, and none of the strategies would take it anyway.
"""
import json
import math
import os
from datetime import date, timedelta

# Share of a normal day's volume usually traded by this many minutes after 09:15 (rough; only used to promote, the
# exact volume check later uses real candles). The close is heavy, hence the jump at the end.
PROFILE = [(0, 0.0), (15, 0.11), (30, 0.17), (60, 0.26), (120, 0.38), (180, 0.47), (240, 0.56), (300, 0.68),
           (345, 0.82), (375, 1.0)]
DEFAULTS = {"min_volume_ratio": 1.5, "min_move_pct": 1.5, "max_promoted": 30, "baseline_per_pass": 40, "baseline_days": 10,
            "baseline_max_age_days": 5, "max_quote_age_s": 600, "max_future_skew_s": 10}


def settings(cfg):
    out = dict(DEFAULTS)
    out.update((cfg or {}).get("watch") or {})
    return out


def expected_fraction(now):
    return fraction_at((now - now.replace(hour=9, minute=15, second=0, microsecond=0)).total_seconds() / 60.0)


def fraction_at(mins):
    """Share of a normal day's volume traded `mins` minutes after 09:15."""
    if mins <= 0:
        return 0.0
    if mins >= PROFILE[-1][0]:
        return 1.0
    for (a, fa), (b, fb) in zip(PROFILE, PROFILE[1:]):
        if a <= mins <= b:
            return fa + (fb - fa) * (mins - a) / (b - a)
    return 1.0


class Baseline:
    """Each share's normal daily volume, from its last ten daily candles. Kept in a small file so it survives restarts.

    A share whose history cannot be read is not retried at once: it goes to the back of the queue with a back-off
    (30 minutes, then 1 hour, 2 hours ... up to a day), so a few bad shares can never starve the rest.
    """

    def __init__(self, folder):
        self.path = os.path.join(folder, "volbase.json")
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        self.v = data.get("v", {})
        self.tries = data.get("tries", {})                  # symbol -> [failures, do not try again before (epoch)]
        self.last = {"tried": 0, "added": 0, "failed": 0}

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"v": self.v, "tries": self.tries}, f, separators=(",", ":"))
        os.replace(tmp, self.path)

    def missing(self, symbols, now, max_age):
        """Shares that need a baseline and are not backing off, never-tried first, then the longest-waiting."""
        today, t = now.date(), now.timestamp()
        out = []
        for s in symbols:
            hit = self.v.get(s)
            if hit and (today - date.fromisoformat(hit[1])).days <= max_age:
                continue
            fails, nxt = self.tries.get(s, [0, 0])
            if nxt > t:
                continue
            out.append((nxt, fails, s))
        out.sort()
        return [s for _, _, s in out]

    def backing_off(self, now):
        t = now.timestamp()
        return sum(1 for f, nxt in self.tries.values() if nxt > t)

    def _failed(self, symbol, now):
        fails = self.tries.get(symbol, [0, 0])[0] + 1
        self.tries[symbol] = [fails, now.timestamp() + min(86400, 1800 * 2 ** (fails - 1))]

    def fill(self, broker, symbols, now, cfg, log=print):
        """Reads daily candles for some of the shares that have no baseline yet. Returns how many were added."""
        todo = self.missing(symbols, now, cfg["baseline_max_age_days"])[:cfg["baseline_per_pass"]]
        today, added, failed, tried = now.date(), 0, 0, 0
        for s in todo:
            if getattr(broker, "cooling", lambda: 0)() > 0:
                break                                           # the broker asked us to wait; carry on next pass
            tried += 1
            try:
                candles = broker.history(s, "D", today - timedelta(days=25), today - timedelta(days=1))
            except Exception as e:
                log(f"  no daily volume for {s}: {e}")
                self._failed(s, now)
                failed += 1
                continue
            vols = [c[5] for c in candles if len(c) > 5 and c[5]][-cfg["baseline_days"]:]
            if len(vols) >= 3:
                self.v[s] = [sum(vols) / len(vols), today.isoformat()]
                self.tries.pop(s, None)
                added += 1
            else:
                self._failed(s, now)
                failed += 1
        self.last = {"tried": tried, "added": added, "failed": failed}
        if tried:
            self.save()
        return added

    def ratio(self, symbol, volume, now):
        hit = self.v.get(symbol)
        frac = expected_fraction(now)
        if not hit or not volume or frac < 0.03:
            return None
        return volume / (hit[0] * frac)


class Watch:
    def __init__(self, folder, symbols, cfg=None, log=print):
        self.cfg = settings(cfg)
        self.symbols = list(symbols)                        # "NSE:XYZ-EQ" form
        self.base = Baseline(folder)
        self.log = log
        self.board = []                                     # latest scan, strongest first
        self.scanned_at = None
        self.quality = {"expected": len(self.symbols), "received": 0, "fresh": 0, "stale": 0, "future": 0,
                        "missing": 0, "invalid": 0, "exchange_timestamps": 0, "response_timestamps": 0}

    def scan(self, broker, now):
        """One quotes pass over the whole universe. Fills self.board with the shares that deserve candles."""
        today = now.date()
        self.base.fill(broker, self.symbols, now, self.cfg, self.log)
        quotes = broker.quotes(self.symbols)
        rows, quality = [], {"expected": len(self.symbols), "received": len(quotes), "fresh": 0, "stale": 0, "future": 0,
                             "missing": max(0, len(self.symbols) - len(quotes)), "invalid": 0,
                             "exchange_timestamps": 0, "response_timestamps": 0}
        for sym, q in quotes.items():
            # A quote returned by the broker is not automatically a fresh quote.
            # Prefer the exchange's update time; use request receipt time only as
            # an explicitly labelled fallback for providers that omit it.
            stamp = q.get("ts")
            source = q.get("ts_source") or ("exchange" if stamp is not None else "response")
            if q.get("ts_invalid") or source == "invalid":
                quality["invalid"] += 1
                continue
            if stamp is None:
                stamp = q.get("received_ts") or now.timestamp()
            try:
                age = now.timestamp() - float(stamp)
            except (TypeError, ValueError):
                quality["missing"] += 1
                continue
            if not math.isfinite(age):
                quality["invalid"] += 1
                continue
            if age < -self.cfg["max_future_skew_s"]:
                quality["future"] += 1
                continue
            if age > self.cfg["max_quote_age_s"]:
                quality["stale"] += 1
                continue
            try:
                lp, prev = float(q.get("lp")), float(q.get("prev_close"))
            except (TypeError, ValueError):
                quality["invalid"] += 1
                continue
            if not math.isfinite(lp) or not math.isfinite(prev) or lp <= 0 or prev <= 0:
                quality["invalid"] += 1
                continue
            # Fresh means usable: a recent timestamp paired with a non-finite or
            # non-positive price is not a valid quote for market decisions.
            quality["fresh"] += 1
            quality["exchange_timestamps" if source == "exchange" else "response_timestamps"] += 1
            move = (lp - prev) / prev * 100.0
            ratio = self.base.ratio(sym, q.get("volume"), now)
            by_volume = ratio is not None and ratio >= self.cfg["min_volume_ratio"]
            by_move = abs(move) >= self.cfg["min_move_pct"]
            if not (by_volume or by_move):
                continue
            score = max((ratio or 0) / self.cfg["min_volume_ratio"], abs(move) / self.cfg["min_move_pct"])
            rows.append({"symbol": sym, "name": sym[4:-3], "price": round(lp, 2), "move": round(move, 2),
                         "volx": None if ratio is None else round(ratio, 1), "score": round(score, 2),
                         "why": "volume" if by_volume and not by_move else "move" if by_move and not by_volume else "volume and move",
                         "quote_age_s": round(age), "quote_timestamp_source": source})
        rows.sort(key=lambda r: -r["score"])
        self.board = rows[:self.cfg["max_promoted"]]
        self.scanned_at = now.isoformat(timespec="seconds")
        self.quality = quality
        return self.board

    def promoted(self):
        return [r["symbol"] for r in self.board]

    def public(self):
        return {"at": self.scanned_at, "have_baseline": len(self.base.v), "of": len(self.symbols), "board": self.board[:12],
                "baseline_backing_off": len(self.base.tries), "baseline_last": self.base.last, "quality": self.quality}
