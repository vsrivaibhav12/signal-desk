"""Replays history through the real engine and measures the result."""
import copy
import math
from datetime import timedelta

from .. import clock
from ..clock import at, ts
from ..core import Engine
from .model import SyntheticMaster, option_price

DEFAULTS = {
    "iv": {"NIFTY": 0.13, "SENSEX": 0.13, "BANKNIFTY": 0.145, "FINNIFTY": 0.145, "MIDCPNIFTY": 0.16},
    "rules": {
        "NIFTY": {"weekday": 1, "weekly": True, "step": 50, "lot": 65},
        "SENSEX": {"weekday": 3, "weekly": True, "step": 100, "lot": 20},
        "BANKNIFTY": {"weekday": 1, "weekly": False, "step": 100, "lot": 30},
        "FINNIFTY": {"weekday": 1, "weekly": False, "step": 50, "lot": 60},
        "MIDCPNIFTY": {"weekday": 1, "weekly": False, "step": 25, "lot": 120},
    },
    # Costs are estimates: brokerage, taxes and the gap between the price you see and the price you get.
    "option_cost_per_trade": 100.0,      # rupees per round trip (brokerage, STT, exchange fees, GST)
    "option_slippage_pct": 0.5,          # % of the option price, on each of entry and exit
    "stock_cost_per_trade": 40.0,        # rupees per round trip
    "stock_cost_pct": 0.03,              # % of the money traded, both legs together
    "stock_slippage_pct": 0.03,          # % of the share price, on each of entry and exit
}


class NullNews:
    def refresh(self, *a, **k):
        return []

    def for_stock(self, *a, **k):
        return []


class HistoricalBroker:
    """Looks like the live Fyers connection, but only ever shows what had already happened."""

    def __init__(self, bars, bt):
        self.bars, self.bt = bars, bt
        self.root_spot = {}

    def history(self, symbol, resolution, day_from, day_to):
        b = self.bars.get(symbol)
        if b is None:
            return []
        now_ts = ts(clock.now())
        if resolution == "1":
            return list(b.visible(day_to, now_ts))
        out = []                                  # "15": the engine only reads the 9:15 candle's volume
        for d in b.days:
            if day_from <= d <= day_to and now_ts >= ts(at(d, "09:30")):
                out.append([ts(at(d, "09:15")), 0, 0, 0, 0, b.first15_volume(d)])
        return out

    def quotes(self, symbols):
        now = clock.now()
        out = {}
        for s in symbols:
            if s.startswith("BT|"):
                q = self._option(s, now)
                if q:
                    out[s] = q
                continue
            b = self.bars.get(s)
            if b is None:
                continue
            c = b.visible(now.date(), ts(now))
            pc = b.prev_close(now.date())
            if c or pc:
                out[s] = {"lp": c[-1][4] if c else pc, "prev_close": pc, "volume": 0}
        return out

    def _option(self, sym, now):
        _, root, expiry, strike, cp = sym.split("|")
        b = self.bars.get(self.root_spot[root])
        c = b.visible(now.date(), ts(now)) if b else []
        if not c:
            return None
        left = int(expiry) - ts(now)
        p = option_price(c[-1][4], float(strike), left, self.bt["iv"].get(root, 0.14), cp)
        return {"lp": p, "prev_close": None, "volume": 0}


def with_costs(trades, bt):
    """Adds net result (after estimated costs) and the plain underlying move to every trade."""
    for t in trades:
        qty = sum(f["qty"] for f in t["fills"]) or t["qty"]
        if t["kind"] == "option":
            cost = bt["option_cost_per_trade"] + qty * (t["entry_price"] + t["exit_price"]) * bt["option_slippage_pct"] / 100
        else:
            cost = bt["stock_cost_per_trade"] + qty * (t["entry_price"] + t["exit_price"]) * (
                bt["stock_cost_pct"] / 2 + bt["stock_slippage_pct"]) / 100
        t["cost"] = round(cost, 2)
        t["net"] = round(t["pnl"] - cost, 2)
        t["net_r"] = round(t["net"] / t["risk_amount"], 2) if t.get("risk_amount") else 0
        u0, u1 = t["underlying_entry"], t.get("underlying_last") or t["underlying_entry"]
        t["u_pct"] = round(t["dir"] * (u1 - u0) / u0 * 100, 3)
    return trades


def run_variant(cfg, bars, days, bt, fine_when_open=False, log=print):
    """fine_when_open: with one-minute data, check every minute while a trade is open
    (so stops and exits are caught promptly); otherwise check on each 5-minute close."""
    cfg = copy.deepcopy(cfg)
    cfg["markets"] = [m for m in cfg["markets"] if m.get("kind") == "index" and m["root"] in bt["rules"]
                      and m.get("enabled", True) and m["spot"] in bars]
    cfg["news"] = {"enabled": False}
    spot_of_root = {m["root"]: m["spot"] for m in cfg["markets"]}
    nifty = next((m["spot"] for m in cfg["markets"] if m["root"] == "NIFTY"), "NSE:NIFTY50-INDEX")
    spot_of_root.setdefault("NIFTY", nifty)
    master = SyntheticMaster(bt["rules"], spot_of_root)
    broker = HistoricalBroker(bars, bt)
    broker.root_spot = spot_of_root
    feed = {"status": {}, "trades": []}
    eng = Engine(cfg, broker, master, lambda text: None, feed, news=NullNews())
    for n, day in enumerate(days):
        first = len(feed["trades"])
        t, end = at(day, "09:15"), at(day, "15:30")
        while t <= end:
            busy = fine_when_open and any(x["status"] == "OPEN" for x in feed["trades"][first:])
            if busy or t.minute % 5 == 0:
                now = t + timedelta(seconds=25)
                clock.set_fake(now)
                eng.tick(now)
            t += timedelta(minutes=1)
        if (n + 1) % 20 == 0:
            log(f"    replayed {n + 1} of {len(days)} days, {len(feed['trades'])} trades so far")
    clock.set_fake(None)
    still_open = [x for x in feed["trades"] if x["status"] != "CLOSED"]
    trades = [x for x in feed["trades"] if x["status"] == "CLOSED"]
    return with_costs(trades, bt), len(still_open), len(cfg["markets"])
