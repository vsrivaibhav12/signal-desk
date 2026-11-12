"""Replays made-up market days through the real engine, with no Fyers account needed.

It proves the rulebook logic end to end and produces the sample data the
dashboard shows before your own trades exist.  Run:  python -m tests.simulate
"""
import json
import math
import os
import random
import sys
from datetime import date, datetime, timedelta

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import clock  # noqa: E402
from engine.clock import IST, at, ts  # noqa: E402
from engine.core import Engine, score  # noqa: E402
from engine.symbols import Master  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeNews:
    """Stands in for the real news feeds during the replay. Headlines are placeholders."""
    names = {"TATASTEEL": "Tata Steel", "INFY": "Infosys", "BEL": "Bharat Electronics", "LT": "Larsen & Toubro"}
    text = {"TATASTEEL": ("Results", "Sample headline: quarterly results announced before the open"),
            "INFY": ("Order win", "Sample headline: company wins a large new contract"),
            "BEL": ("Order win", "Sample headline: fresh orders received this week"),
            "LT": ("Broker view", "Sample headline: brokerage changes its target price")}

    def refresh(self, max_age_hours=18):
        t = (clock.now() - timedelta(minutes=50)).isoformat(timespec="minutes")
        return [{"symbol": s, "name": self.names[s], "tag": tag, "title": title, "source": "Sample source",
                 "link": "", "t": t} for s, (tag, title) in self.text.items()]

    def for_stock(self, symbol, limit=2):
        return []


def base_price(master, m, expiry):
    ks = sorted(r["strike"] for r in master.rows(m["master"], m["root"]) if r["expiry"] == expiry and r["type"] == "CE")
    return ks[len(ks) // 2]



def path(day, key, start, minutes, base, plan, rng):
    """One day's 1-minute candles: a random walk with a scripted push after the opening range.

    plan: up, down, fail_up, fail_down (breaks out then reverses), wide, flat
    """
    out, p = [], base
    t0 = ts(at(day, start))
    rmin = 15 if key == "stock" else 30
    noise = base * (0.00075 if plan == "wide" else 0.00028)
    begin = rmin + rng.randint(3, 25)
    for i in range(minutes):
        drift = 0
        if key != "pre" and i >= begin:
            k = i - begin
            if plan in ("up", "down"):
                drift = (1 if plan == "up" else -1) * base * (0.00016 if k < 55 else 0.00001)
            elif plan in ("fail_up", "fail_down"):
                s = 1 if plan == "fail_up" else -1
                drift = s * base * (0.00030 if k < 12 else -0.00030 if k < 45 else 0)
        if i == rmin:
            noise = base * 0.00024
        o = p
        p = p + drift + rng.gauss(0, noise)
        h = max(o, p) + abs(rng.gauss(0, noise * 0.4))
        l = min(o, p) - abs(rng.gauss(0, noise * 0.4))
        out.append([t0 + i * 60, round(o, 2), round(h, 2), round(l, 2), round(p, 2), int(rng.uniform(800, 3000))])
    return out


class FakeBroker:
    def __init__(self, master):
        self.master = master
        self.days = {}      # (day, symbol) -> candles
        self.prev = {}      # (day, symbol) -> previous close
        self.opts = {}      # option symbol -> (underlying symbol, strike, cp)
        self.stock15 = {}   # (day, symbol) -> first-15-minute volume multiple

    def _visible(self, day, symbol):
        now_ts = ts(clock.now())
        return [c for c in self.days.get((day, symbol), []) if c[0] <= now_ts]

    def history(self, symbol, resolution, day_from, day_to):
        if resolution == "1":
            return self._visible(day_to, symbol)
        # 15-minute history for the stock volume check: ten ordinary days plus today
        out = []
        for k in range(12, 0, -1):
            d = day_to - timedelta(days=k)
            out.append([ts(at(d, "09:15")), 0, 0, 0, 0, 100000])
        mult = self.stock15.get((day_to, symbol), 1.0)
        out.append([ts(at(day_to, "09:15")), 0, 0, 0, 0, int(100000 * mult)])
        return out

    def quotes(self, symbols):
        day = clock.now().date()
        out = {}
        for s in symbols:
            if s in self.opts:
                und, strike, cp = self.opts[s]
                c = self._visible(day, und)
                if not c:
                    continue
                spot = c[-1][4]
                tv = spot * 0.0045
                intrinsic = (spot - strike) if cp == "CE" else (strike - spot)
                prem = max(tv * 0.15, tv + 0.55 * intrinsic if abs(intrinsic) < tv * 3 else max(intrinsic, 0) + tv * 0.3)
                out[s] = {"lp": round(round(prem / 0.05) * 0.05, 2), "prev_close": None, "volume": 0}
            else:
                c = self._visible(day, s)
                pc = self.prev.get((day, s))
                if c or pc:
                    out[s] = {"lp": c[-1][4] if c else pc, "prev_close": pc, "volume": 0}
        return out


def build(cfg, master, days, plans, rng):
    fb = FakeBroker(master)
    for name in ("NSE_FO", "BSE_FO", "MCX_COM"):
        master.rows(name, "__warm__")
    for day in days:
        plan = plans[day]
        for m in cfg["markets"]:
            if not m.get("enabled", True):
                continue
            p = plan.get(m["id"], "flat")
            if p == "closed":
                continue
            expiry, _ = master.choose_expiry(m["master"], m["root"], day, m["min_days_to_expiry"], m["near_expiry"])
            if m["kind"] == "index":
                fut = master.nearest_future(m["master"], m["root"], day)["symbol"]
                if not expiry:
                    expiry = sorted(r["expiry"] for r in master.rows(m["master"], m["root"]) if r["type"] == "CE")[-1]
                base = base_price(master, m, expiry) * (1 + rng.uniform(-0.01, 0.01))
                c = path(day, "idx", "09:15", 375, base, p, rng)
                fb.days[(day, m["spot"])] = c
                fb.days[(day, fut)] = [[x[0], x[1] + 40, x[2] + 40, x[3] + 40, x[4] + 40, x[5]] for x in c]
                und = m["spot"]
                gap = rng.choice([-0.004, -0.002, 0.002, 0.004])
                fb.prev[(day, und)] = round(base * (1 + gap), 2)
            else:
                if not expiry:
                    continue
                und = master.future_for_expiry(m["master"], m["root"], expiry, day)["symbol"]
                base = base_price(master, m, expiry) * (1 + rng.uniform(-0.01, 0.01))
                pre = path(day, "pre", "09:00", 540, base, "flat", rng)
                eve = path(day, "idx", "18:00", 340, pre[-1][4], p, rng)
                fb.days[(day, und)] = pre + eve
                gap = rng.choice([-0.004, -0.002, 0.002, 0.004])
                fb.prev[(day, und)] = round(base * (1 + gap), 2)
            if expiry:
                for r in master.rows(m["master"], m["root"]):
                    if r["expiry"] == expiry and r["type"] in ("CE", "PE"):
                        fb.opts[r["symbol"]] = (und, r["strike"], r["type"])
        for sym, (p, chg, mult) in plan.get("stocks", {}).items():
            full = f"NSE:{sym}-EQ"
            base = 1500 * (1 + rng.uniform(-0.3, 0.3))
            fb.days[(day, full)] = path(day, "stock", "09:15", 375, base, p, rng)
            fb.prev[(day, full)] = round(base / (1 + chg / 100), 2)
            fb.stock15[(day, full)] = mult
    return fb


def replay(engine, cfg, day, stop_at=None):
    t = at(day, "09:10")
    end = stop_at or at(day, "22:40")
    while t <= end:
        if not (at(day, "15:10") < t < at(day, "17:55")):
            now = t + timedelta(seconds=6)
            clock.set_fake(now)
            engine.tick(now)
        t += timedelta(minutes=1)


def main(out_path=None, verbose=True, seed=4):
    rng = random.Random(seed)
    with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cache = os.path.join(ROOT, "tests", ".master_cache")
    os.makedirs(cache, exist_ok=True)

    def loader(name):
        p = os.path.join(cache, name + ".csv")
        if not os.path.exists(p):
            open(p, "w", encoding="utf-8").write(Master._download(name))
        return open(p, encoding="utf-8").read()

    master = Master(loader)
    days, d = [], datetime.now(IST).date()
    while len(days) < 14:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days.reverse()
    menu = ["up"] * 2 + ["down"] * 2 + ["fail_up"] * 6 + ["fail_down"] * 6 + ["flat"] * 6 + ["wide"] * 2
    names = ["TATASTEEL", "INFY", "SBIN", "LT", "MARUTI", "ITC", "BEL", "TITAN"]
    plans = {}
    for day in days:
        plans[day] = {m["id"]: rng.choice(menu) for m in cfg["markets"]}
        plans[day]["stocks"] = {n: (rng.choice(menu[:22]), rng.choice([-2.1, -1.4, 1.3, 1.9, 0.5]),
                                    rng.choice([2.4, 3.1, 1.2])) for n in rng.sample(names, 4)}
    fb = build(cfg, master, days, plans, rng)
    alerts = []
    feed = {"status": {"bot": "your_signal_bot"}, "trades": []}
    engine = Engine(cfg, fb, master, alerts.append, feed, news=FakeNews())
    for day in days[:-1]:
        replay(engine, cfg, day)
    replay(engine, cfg, days[-1], stop_at=at(days[-1], "10:52"))  # leave the last day mid-session
    feed["status"]["running"] = True
    clock.set_fake(None)
    if verbose:
        for t in feed["trades"]:
            print(f"{t['date']} {t['name']:<10} {t['headline']:<42} entry {t['entry_price']:>9} "
                  f"{t['status']:<6} {t.get('exit_reason', '')[:44]:<44} pnl {t.get('pnl')} R {t.get('r')}")
        print(score(feed["trades"]), "alerts:", len(alerts))
    if out_path:
        json.dump(feed, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    return feed, alerts


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
