import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.errors import AuthError
from engine.fyers import Fyers, FyersError


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        return self.payload


def test_probe_detail_preserves_per_symbol_quote_error(monkeypatch):
    symbol = "MCX:CRUDEOILM26NOVFUT"
    client = Fyers("APP", "SECRET")
    client.token = "access-token"
    response = FakeResponse(200, {
        "s": "ok",
        "d": [{"n": symbol, "s": "error", "v": {"errmsg": "Insufficient API permission", "code": -16}}],
    })
    monkeypatch.setattr("engine.fyers.requests.get", lambda *a, **k: response)
    ok, detail = client.probe_detail(symbol)
    assert ok is False
    assert "Insufficient API permission" in detail
    assert "Market Data permission" in detail
    assert client.probe(symbol) is False


def test_probe_detail_includes_http_permission_error(monkeypatch):
    symbol = "MCX:GOLDM26DECFUT"
    client = Fyers("APP", "SECRET")
    client.token = "access-token"
    response = FakeResponse(403, {"s": "error", "code": -17, "message": "Permission denied"})
    monkeypatch.setattr("engine.fyers.requests.get", lambda *a, **k: response)
    ok, detail = client.probe_detail(symbol)
    assert ok is False
    assert "HTTP 403" in detail
    assert "Permission denied" in detail


def test_probe_detail_does_not_hide_expired_auth(monkeypatch):
    client = Fyers("APP", "SECRET")
    client.token = "expired"
    response = FakeResponse(401, {"s": "error", "code": -8, "message": "Token expired"})
    monkeypatch.setattr("engine.fyers.requests.get", lambda *a, **k: response)
    with pytest.raises(AuthError, match="Token expired"):
        client.probe_detail("MCX:CRUDEOILM26NOVFUT")


def test_probe_detail_never_accepts_ltp_from_error_row(monkeypatch):
    symbol = "MCX:SILVERM26NOVFUT"
    client = Fyers("APP", "SECRET")
    client.token = "access-token"
    response = FakeResponse(200, {
        "s": "ok",
        "d": [{"n": symbol, "s": "error", "v": {"lp": 250000, "errmsg": "Market data permission missing"}}],
    })
    monkeypatch.setattr("engine.fyers.requests.get", lambda *a, **k: response)
    ok, detail = client.probe_detail(symbol)
    assert not ok
    assert "Market data permission missing" in detail


def test_bulk_quotes_ignore_error_rows_even_with_partial_ltp(monkeypatch):
    symbol = "MCX:NATGASMINI26NOVFUT"
    client = Fyers("APP", "SECRET")
    client.token = "access-token"
    response = FakeResponse(200, {
        "s": "ok",
        "d": [
            {"n": symbol, "s": "error", "v": {"lp": 300}},
            {"n": "NSE:RELIANCE-EQ", "s": "ok", "v": {"lp": 1400, "volume": 10}},
        ],
    })
    monkeypatch.setattr("engine.fyers.requests.get", lambda *a, **k: response)
    quotes = client.quotes([symbol, "NSE:RELIANCE-EQ"])
    assert symbol not in quotes
    assert quotes["NSE:RELIANCE-EQ"]["lp"] == 1400
