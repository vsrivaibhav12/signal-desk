"""Regression tests for trade lifecycle integrity. Run: python -m tests.test_lifecycle"""
from datetime import datetime

from engine.core import Engine, score


def test_option_without_verified_exit_is_unresolved():
    engine = Engine.__new__(Engine)
    engine.cfg = {"skip_dates": []}
    engine.feed = {
        "status": {"day": {"date": "2026-11-11", "log": []}},
        "trades": [{
            "id": "option-1", "status": "OPEN", "kind": "option", "name": "NIFTY",
            "entry_price": 100.0, "last_price": 93.0, "open_pnl": -7.0, "events": [],
        }],
    }
    engine.dirty = False
    alerts = []
    engine.notify = alerts.append

    Engine._roll_day(engine, datetime(2026, 11, 12, 9, 0))

    trade = engine.feed["trades"][0]
    assert trade["status"] == "UNRESOLVED"
    assert trade["pnl"] is None and trade["open_pnl"] is None
    assert engine.feed["status"]["day"]["date"] == "2026-11-12"
    assert any(event["kind"] == "UNRESOLVED" for event in trade["events"])
    assert "unresolved" in alerts[0].lower()
    assert score(engine.feed["trades"])["closed"] == 0


def main():
    test_option_without_verified_exit_is_unresolved()
    print("ok  option without a verified exit remains unresolved and is excluded from realised P&L")
    print("ALL LIFECYCLE CHECKS PASSED")


if __name__ == "__main__":
    main()
