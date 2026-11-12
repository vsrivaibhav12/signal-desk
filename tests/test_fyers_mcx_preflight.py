from datetime import datetime, timedelta

from engine import run
from engine.clock import IST


class FakeFyers:
    def __init__(self, quote=True, candles=None):
        self.quote = quote
        self.candles = candles if candles is not None else []
        self.probed = []
        self.history_calls = []

    def probe(self, symbol):
        self.probed.append(symbol)
        return self.quote

    def probe_detail(self, symbol):
        self.probed.append(symbol)
        if self.quote:
            return True, {"symbol": symbol, "ltp": 100.0}
        return False, f"Permission denied for {symbol}; check Market Data permission."

    def history(self, symbol, resolution, start, end):
        self.history_calls.append((symbol, resolution, start, end))
        return self.candles


def make_session(client):
    session = run.Session.__new__(run.Session)
    session.secondary_fyers = client
    return session


def test_mcx_preflight_requires_quote_and_recent_valid_five_minute_history(monkeypatch):
    now = datetime(2026, 11, 12, 19, 0, tzinfo=IST)
    monkeypatch.setattr(run.clock, "now", lambda: now)
    ts = int((now - timedelta(minutes=5)).timestamp())
    client = FakeFyers(candles=[
        [ts, 100, 102, 99, 101, 1200],
        [ts + 300, 101, 103, 100, 102, 1500],
    ])
    ready, evidence = make_session(client)._verify_secondary_fyers("MCX:CRUDEOILM26NOVFUT")
    assert ready is True
    assert evidence["valid_candles"] == 2
    assert client.probed == ["MCX:CRUDEOILM26NOVFUT"]
    assert client.history_calls[0][1] == "5"


def test_mcx_preflight_rejects_quote_without_history(monkeypatch):
    now = datetime(2026, 11, 12, 19, 0, tzinfo=IST)
    monkeypatch.setattr(run.clock, "now", lambda: now)
    ready, reason = make_session(FakeFyers(candles=[]))._verify_secondary_fyers("MCX:CRUDEOILM26NOVFUT")
    assert ready is False
    assert "fewer than two" in reason


def test_mcx_preflight_rejects_stale_history(monkeypatch):
    now = datetime(2026, 11, 12, 19, 0, tzinfo=IST)
    monkeypatch.setattr(run.clock, "now", lambda: now)
    ts = int((now - timedelta(days=9)).timestamp())
    candles = [[ts, 100, 102, 99, 101, 1200], [ts + 300, 101, 103, 100, 102, 1500]]
    ready, reason = make_session(FakeFyers(candles=candles))._verify_secondary_fyers("MCX:CRUDEOILM26NOVFUT")
    assert ready is False
    assert "stale" in reason


def test_mcx_preflight_rejects_invalid_ohlc(monkeypatch):
    now = datetime(2026, 11, 12, 19, 0, tzinfo=IST)
    monkeypatch.setattr(run.clock, "now", lambda: now)
    ts = int((now - timedelta(minutes=5)).timestamp())
    candles = [[ts, 100, 90, 95, 101, 1200], [ts + 300, 101, 103, 100, 102, 1500]]
    ready, reason = make_session(FakeFyers(candles=candles))._verify_secondary_fyers("MCX:CRUDEOILM26NOVFUT")
    assert ready is False
    assert "fewer than two" in reason


def test_mcx_preflight_surfaces_fyers_quote_permission_detail(monkeypatch):
    now = datetime(2026, 11, 12, 19, 0, tzinfo=IST)
    monkeypatch.setattr(run.clock, "now", lambda: now)
    ready, reason = make_session(FakeFyers(quote=False))._verify_secondary_fyers("MCX:CRUDEOILM26NOVFUT")
    assert ready is False
    assert "Permission denied" in reason
    assert "Market Data permission" in reason
