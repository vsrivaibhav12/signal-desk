"""The shared portfolio rules, the correlation cap, and the isolating desk runner.  Run:  python -m pytest tests/test_portfolio.py"""
import os
import random
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import portfolio  # noqa: E402
from engine.core import Engine  # noqa: E402
from engine.desks import DeskRunner  # noqa: E402
from engine.errors import AuthError  # noqa: E402
from engine.strategies import risk  # noqa: E402

TODAY = date(2026, 11, 11)


# ── the arithmetic equals what both engines computed before it was shared ────
def old_rulebook(n_open, open_risk, realized, new_risk, max_pos, max_risk, max_loss):
    out = []
    if realized <= -float(max_loss):
        out.append("daily_loss")
    if n_open >= int(max_pos):
        out.append("positions")
    if open_risk + new_risk > float(max_risk):
        out.append("open_risk")
    return out


def old_strategy(n_open, open_risk, realized, new_risk, max_pos, max_risk, max_loss):
    out = []
    if realized <= -float(max_loss):
        out.append("daily_loss")
    if n_open >= int(max_pos):
        out.append("positions")
    if open_risk + new_risk > float(max_risk) + 1e-9:
        out.append("open_risk")
    return out


def test_limit_arithmetic_is_unchanged_across_thousands_of_cases():
    rng = random.Random(7)
    for _ in range(5000):
        n = rng.randint(0, 10)
        cur, new = rng.choice([0, 500, 1000, 2499.9999999999, 2500, 17499.5]), rng.choice([0, 1, 250, 500, 2500])
        realized = rng.choice([0, -1, -1499.99, -1500, -3500, 120, -10000])
        args = (n, cur, realized, new)
        lim = dict(max_positions=rng.choice([1, 5, 8]), max_risk=rng.choice([2500, 17500]), max_daily_loss=rng.choice([1500, 3500]))
        assert portfolio.limit_breaches(*args, **lim) == old_rulebook(*args, lim["max_positions"], lim["max_risk"], lim["max_daily_loss"])
        assert portfolio.limit_breaches(*args, **lim, tolerance=1e-9) == old_strategy(*args, lim["max_positions"], lim["max_risk"], lim["max_daily_loss"])


def test_the_risk_tolerance_applies_only_to_open_risk():
    assert portfolio.limit_breaches(0, 2500.0, 0, 1e-12, max_positions=5, max_risk=2500, max_daily_loss=1500, tolerance=1e-9) == []
    assert portfolio.limit_breaches(0, 2500.0, 0, 1e-12, max_positions=5, max_risk=2500, max_daily_loss=1500) == ["open_risk"]


# ── index families ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", ["Nifty 50", "NIFTY", "Bank Nifty", "BANKNIFTY", "Fin Nifty", "Midcap Nifty", "Sensex",
                                  "NSE:NIFTY50-INDEX", "NSE:NIFTYBANK-INDEX", "BSE:SENSEX-INDEX", "NSE:FINNIFTY-INDEX"])
def test_index_names_are_recognised(name):
    assert portfolio.is_index(name)


@pytest.mark.parametrize("name", ["HAL", "NSE:HAL-EQ", "Crude oil", "CRUDEOILM", "Gold", "NIFTYBEES", None, ""])
def test_shares_and_commodities_are_not_index_exposure(name):
    assert not portfolio.is_index(name)


def test_cap_counts_only_same_direction_index_positions():
    held = [{"name": "Nifty 50", "dir": 1}, {"name": "Bank Nifty", "dir": 1}, {"name": "Sensex", "dir": -1}, {"name": "HAL", "dir": 1}]
    assert portfolio.correlated_exposure_breached("Fin Nifty", 1, held, 2) is True
    assert portfolio.correlated_exposure_breached("Fin Nifty", -1, held, 2) is False       # the other way is a hedge, not more of the same
    assert portfolio.correlated_exposure_breached("Fin Nifty", 1, held, 3) is False
    assert portfolio.correlated_exposure_breached("Crude oil", 1, held, 2) is False       # only index underlyings are limited
    assert portfolio.correlated_exposure_breached("HAL", 1, held, 2) is False
    assert portfolio.correlated_exposure_breached("Fin Nifty", 1, held, None) is False    # off
    assert portfolio.correlated_exposure_breached("Fin Nifty", 1, held, 0) is False
    assert portfolio.correlated_exposure_breached("Fin Nifty", None, held, 2) is False
    assert portfolio.correlated_exposure_breached("Fin Nifty", 1, [{"name": "Nifty 50", "dir": None}] * 5, 2) is False


# ── the rulebook engine ──────────────────────────────────────────────────────
def engine(trades, cfg_portfolio, external=None):
    cfg = {"general": {"portfolio": {"enabled": True, "max_open_positions": 10, "max_open_risk": 1e9, "max_daily_loss": 1e9}},
           "portfolio": {"enabled": True, "max_open_positions": 20, "max_open_risk": 1e9, "max_daily_loss": 1e9, **cfg_portfolio}}
    feed = {"status": {"day": {}}, "trades": trades}
    return Engine(cfg, None, None, lambda *a, **k: None, feed, news=object(), portfolio_provider=lambda: external or [])


def rb(name, direction, status="OPEN", **extra):
    return {"id": f"{name}-{direction}-{status}", "date": TODAY.isoformat(), "name": name, "dir": direction, "status": status,
            "risk_amount": 100.0, "pnl": 0.0, **extra}


def test_rulebook_rejects_a_third_same_direction_index_position():
    eng = engine([rb("Nifty 50", 1), rb("Bank Nifty", 1)], {"max_same_direction_index_positions": 2})
    assert eng._portfolio_rejection(rb("Fin Nifty", 1), TODAY) == ["too many same-direction index positions are already open"]
    assert eng._portfolio_rejection(rb("Fin Nifty", -1), TODAY) == []
    assert eng._portfolio_rejection(rb("Crude oil", 1), TODAY) == []


def test_rulebook_counts_the_strategy_desks_index_futures_too():
    external = [{"date": TODAY.isoformat(), "name": "Nifty 50", "dir": 1, "status": "OPEN", "risk": 100.0, "risk_approved": True}]
    eng = engine([rb("Bank Nifty", 1)], {"max_same_direction_index_positions": 2}, external)
    assert eng._portfolio_rejection(rb("Sensex", 1), TODAY)
    eng_off = engine([rb("Bank Nifty", 1)], {}, external)
    assert eng_off._portfolio_rejection(rb("Sensex", 1), TODAY) == []


def test_rulebook_limit_texts_are_unchanged():
    eng = engine([rb("A", 1), rb("B", 1)], {"max_open_positions": 2, "max_open_risk": 150, "max_daily_loss": 10})
    assert eng._portfolio_rejection(rb("C", 1), TODAY) == ["shared portfolio maximum positions reached",
                                                          "shared portfolio open risk would be exceeded"]


# ── the strategy desk's controller ───────────────────────────────────────────
def candidate(i, name, direction, ts_in, ts_out=None, status="OPEN"):
    t = {"id": f"c{i}", "strategy": "orb", "symbol": f"S{i}", "name": name, "dir": direction, "date": TODAY.isoformat(), "risk": 100.0,
         "entry_ts": ts_in, "status": status, "dec": {"g": "take"}, "net": 0.0}
    if ts_out:
        t.update(status="CLOSED", exit_ts=ts_out)
    return t


def test_controller_applies_the_same_cap_in_time_order():
    trades = [candidate(1, "Nifty 50", 1, 100), candidate(2, "Bank Nifty", 1, 200), candidate(3, "Fin Nifty", 1, 300),
              candidate(4, "Sensex", -1, 400), candidate(5, "HAL", 1, 500)]
    risk.annotate(trades, {"enabled": True, "max_open_positions": 9, "max_total_open_risk": 1e9, "max_daily_loss": 1e9,
                           "max_open_positions_per_symbol": 1},
                  shared_cfg={"max_same_direction_index_positions": 2})
    got = {t["id"]: (t["risk_approved"], t["risk_reasons"]) for t in trades}
    assert got["c1"][0] and got["c2"][0]
    assert got["c3"] == (False, ["correlated_index_exposure"])
    assert got["c4"][0] and got["c5"][0]


def test_controller_frees_the_slot_when_an_index_position_closes():
    trades = [candidate(1, "Nifty 50", 1, 100, 150), candidate(2, "Bank Nifty", 1, 200), candidate(3, "Fin Nifty", 1, 300)]
    risk.annotate(trades, {"enabled": True, "max_open_positions": 9, "max_total_open_risk": 1e9, "max_daily_loss": 1e9,
                           "max_open_positions_per_symbol": 1},
                  shared_cfg={"max_same_direction_index_positions": 2})
    assert all(t["risk_approved"] for t in trades)


def test_controller_counts_the_rulebooks_open_options():
    external = [{"id": "x1", "date": TODAY.isoformat(), "name": "Nifty 50", "dir": 1, "status": "OPEN", "risk_amount": 100.0,
                 "entry_time": "2026-11-11T09:50:00+05:30"}]
    trades = [candidate(1, "Bank Nifty", 1, 2_000_000_000_000), candidate(2, "Sensex", 1, 2_000_000_000_100)]
    risk.annotate(trades, {"enabled": True, "max_open_positions": 9, "max_total_open_risk": 1e9, "max_daily_loss": 1e9,
                           "max_open_positions_per_symbol": 1},
                  external_trades=external, shared_cfg={"max_same_direction_index_positions": 2})
    assert [t["risk_approved"] for t in trades] == [True, False]


def test_without_the_setting_nothing_changes():
    trades = [candidate(i, n, 1, i * 100) for i, n in enumerate(["Nifty 50", "Bank Nifty", "Fin Nifty", "Sensex"], 1)]
    risk.annotate(trades, {"enabled": True, "max_open_positions": 9, "max_total_open_risk": 1e9, "max_daily_loss": 1e9,
                           "max_open_positions_per_symbol": 1}, shared_cfg={})
    assert all(t["risk_approved"] for t in trades)


# ── the desk runner ──────────────────────────────────────────────────────────
class Desk:
    def __init__(self, fail=None):
        self.fail, self.calls = fail, 0

    def tick(self, now):
        self.calls += 1
        if self.fail:
            raise self.fail


def test_one_failing_desk_does_not_skip_the_other():
    a, b = Desk(RuntimeError("boom")), Desk()
    runner = DeskRunner([("rulebook", a), ("strategies", b)], log=lambda m: None)
    failures = runner.tick(None)
    assert b.calls == 1 and [(n, str(e)) for n, e in failures] == [("rulebook", "boom")]
    assert "rulebook" in runner.errors
    a.fail = None
    assert runner.tick(None) == [] and runner.errors == {}


def test_an_authentication_error_stops_the_cycle_at_once():
    a, b = Desk(AuthError("expired")), Desk()
    runner = DeskRunner([("rulebook", a), ("strategies", b)], log=lambda m: None)
    with pytest.raises(AuthError):
        runner.tick(None)
    assert b.calls == 0
