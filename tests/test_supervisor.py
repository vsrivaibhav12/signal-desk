from datetime import datetime, time, timedelta
from pathlib import Path

import yaml

from engine import supervisor
from engine.clock import IST


def tiny_config():
    return {
        "stocks": {"enabled": False},
        "strategies": {"enabled": True, "exit_by": "15:15"},
        "markets": [
            {"kind": "index", "enabled": True, "range": ["09:15", "09:45"], "exit_by": "15:00"},
            {"kind": "commodity", "enabled": True, "range": ["18:00", "18:30"], "exit_by": "22:30"},
        ],
    }


def test_supervisor_reports_active_market_session(monkeypatch, tmp_path):
    worker = supervisor.TradingSupervisor.__new__(supervisor.TradingSupervisor)
    worker.cfg = tiny_config()
    worker.root = tmp_path
    worker.data_dir = tmp_path / "_data"
    worker.data_dir.mkdir()
    worker.path = worker.data_dir / "supervisor.json"
    worker.store = type("NoopStore", (), {"publish": lambda *a, **k: None})()
    worker.state = {"schema": 1, "status": {}}
    worker.last_publish = float("inf")
    worker.last_error = ""
    monkeypatch.setattr(supervisor.clock, "now", lambda: datetime(2026, 11, 12, 10, 0, tzinfo=IST))
    result = worker.once()
    assert result["active"] == "day"
    assert result["next_session"] is None


def test_supervisor_waits_for_evening_session_without_stopping(monkeypatch):
    cfg = tiny_config()
    now = datetime(2026, 11, 12, 16, 0, tzinfo=IST)
    nxt = supervisor._next_session(cfg, now)
    assert nxt == datetime(2026, 11, 12, 17, 15, tzinfo=IST)


def test_supervisor_skips_weekend_and_finds_next_weekday_session():
    cfg = tiny_config()
    now = datetime(2026, 11, 14, 12, 0, tzinfo=IST)  # Saturday
    nxt = supervisor._next_session(cfg, now)
    assert nxt == datetime(2026, 11, 16, 8, 30, tzinfo=IST)
