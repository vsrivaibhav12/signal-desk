"""Checks the Kotak Neo adapter against a pretend SDK and pretend contract lists. Run:  python -m tests.test_kotak
No internet, no account. What it cannot prove is that the real Kotak servers answer this way: run the
"Kotak check" workflow for that."""
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import brokers, clock  # noqa: E402
from engine.errors import AuthError, BrokerError  # noqa: E402
from engine.kotak import EPOCH_1980_OFFSET, Kotak, KotakError, KotakMaster, read_rows  # noqa: E402
from engine.strategies.data import series_from_broker  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))


def ist_epoch(y, m, d, hh=15, mm=30):
    return int(datetime(y, m, d, hh, mm, tzinfo=IST).timestamp())


CM = ("pSymbol, pGroup ,pExchSeg,pTrdSymbol,pSymbolName,pOptionType,pExpiryDate,dStrikePrice;,lLotSize\n"
      "2885,EQ,nse_cm,RELIANCE-EQ,RELIANCE,,-1,-1,1\n"
      "1333,EQ,nse_cm,HDFCBANK-EQ,HDFCBANK,,-1,-1,1\n"
      "9999,BL,nse_cm,HDFCBANK-BL,HDFCBANK,,-1,-1,1\n"
      "Nifty 50,,nse_cm,Nifty 50,Nifty 50,,-1,-1,1\n"
      "99001,,nse_cm,Nifty Midcap Select,Nifty Midcap Select,,-1,-1,1\n")
NOV, DEC = ist_epoch(2026, 11, 26), ist_epoch(2026, 12, 31)
FO = ("pSymbol,pExchSeg,pTrdSymbol,pSymbolName,pOptionType,pExpiryDate,dStrikePrice;,lLotSize\n"
      f"35001,nse_fo,NIFTY26NOVFUT,NIFTY,XX,{NOV},-1,75\n"
      f"35002,nse_fo,NIFTY26DECFUT,NIFTY,XX,{DEC},-1,75\n"
      f"35101,nse_fo,NIFTY26NOV24400CE,NIFTY,CE,{NOV},2440000,75\n"
      f"35102,nse_fo,NIFTY26NOV24500CE,NIFTY,CE,{NOV},2450000,75\n"
      f"35103,nse_fo,NIFTY26NOV24600CE,NIFTY,CE,{NOV},2460000,75\n"
      f"35104,nse_fo,NIFTY26NOV24500PE,NIFTY,PE,{NOV},2450000,75\n"
      f"35105,nse_fo,NIFTY26DEC24500CE,NIFTY,CE,{DEC},2450000,75\n"
      f"36001,nse_fo,NIFTYNXT5026NOVFUT,NIFTYNXT50,XX,{NOV},-1,25\n"
      f"37001,nse_fo,BANKNIFTY26NOVFUT,BANKNIFTY,XX,{NOV},-1,30\n")
FILES = ["https://x/2026-11-12/transformed/nse_fo.csv", "https://x/2026-11-12/transformed-v1/nse_cm-v1.csv",
         "https://x/2026-11-12/transformed/nse_com.csv", "https://x/2026-11-12/transformed/mcx_fo.csv"]
TEXT = {"nse_fo.csv": FO, "nse_cm-v1.csv": CM, "nse_com.csv": "pSymbol\n", "mcx_fo.csv": "pSymbol\n"}


class Resp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class FakeNeo:
    """Answers like the Kotak SDK documents. `script` lets a test make the next calls fail."""

    def __init__(self):
        self.calls, self.script = [], []
        self.candles = {}
        self.invalid = set()

    def _next(self):
        return self.script.pop(0) if self.script else None

    def scrip_master(self):
        self.calls.append(("scrip_master",))
        return self._next() or {"baseFolder": "https://x", "filesPaths": FILES}

    def quotes(self, instrument_tokens, quote_type="all"):
        self.calls.append(("quotes", len(instrument_tokens)))
        bad = self._next()
        if bad:
            return bad
        out = []
        for t in instrument_tokens:
            n = float(len(out) + 100)
            out.append({"exchange_token": t["instrument_token"], "exchange": t["exchange_segment"], "ltp": f"{n:.4f}",
                        "last_volume": "5000", "lstup_time": str(int(time.time()) - 30), "open_int": "1000",
                        "ohlc": {"open": "1", "high": "2", "low": "1", "close": f"{n - 2:.4f}"},
                        "depth": {"buy": [{"price": f"{n - 1:.4f}", "quantity": "300", "orders": "2"}],
                                  "sell": [{"price": f"{n + 1:.4f}", "quantity": "250", "orders": "1"}]}})
        return out

    def historical_data(self, neosymbol, interval, from_date, to_date):
        self.calls.append(("hist", neosymbol, interval, from_date, to_date))
        bad = self._next()
        if bad:
            return bad
        if neosymbol in self.invalid:
            return {"status": "ERROR", "fault": {"code": 422, "message": "Invalid neosymbol"}}
        lo, hi = date.fromisoformat(from_date), date.fromisoformat(to_date)
        rows, d = [], lo
        while d <= hi:
            if d.weekday() < 5:
                for k in range(3):
                    t = datetime(d.year, d.month, d.day, 9, 15 + 5 * k, tzinfo=IST)
                    rows.append([t.strftime("%Y-%m-%dT%H:%M:%S%z"), 10 + k, 12, 9, 11 + k, 100 * (k + 1), 0])
            d += timedelta(days=1)
        return {"status": "success", "interval": interval, "data": {"candles": rows}}

    def expiries(self, exchange, underlying, instrument_type=None):
        self.calls.append(("expiries", exchange, underlying, instrument_type))
        return {"exchange": exchange, "underlying": underlying, "expiries": ["2026-11-26", "2026-12-31"]}


def make(**kw):
    fake = FakeNeo()
    k = Kotak("KEY", client=fake, http_get=lambda url: Resp(TEXT[url.rsplit("/", 1)[-1]]), sleep=lambda s: None,
              log=lambda m: None, **kw)
    return k, fake


def test_csv_and_symbols():
    rows = list(read_rows(CM))
    assert "dStrikePrice" in rows[0] and "pGroup" in rows[0], rows[0].keys()    # stray ';' and spaces are removed
    k, _ = make()
    assert k.resolve("NSE:RELIANCE-EQ") == ("nse_cm", "2885")
    assert k.resolve("NSE:HDFCBANK-EQ") == ("nse_cm", "1333"), "the EQ row must win over the BL row"
    assert k.resolve("NSE:NIFTY50-INDEX") == ("nse_cm", "Nifty 50")
    assert k.resolve("BSE:SENSEX-INDEX") == ("bse_cm", "SENSEX")
    assert k.resolve("nse_fo|35001") == ("nse_fo", "35001")
    try:
        k.resolve("NSE:NOPE-EQ")
        assert False
    except KotakError:
        pass
    k2, _ = make(index_tokens={"NSE:NIFTY50-INDEX": "nse_cm|NIFTY 50"})
    assert k2.resolve("NSE:NIFTY50-INDEX") == ("nse_cm", "NIFTY 50"), "config overrides win"
    # nse_cm must pick the cash list, never the commodity list that also starts with 'nse_c'
    assert k.csv_text("nse_cm") == CM


def check_quote_detail(q):
    assert q["bid"] == 99.0 and q["ask"] == 101.0 and q["bid_qty"] == 300 and q["ask_qty"] == 250 and q["oi"] == 1000, q
    assert q["ts"] is not None and 20 <= time.time() - q["ts"] <= 60, q["ts"]           # when the exchange last updated it


def test_quote_time_and_stats():
    from engine import kotak as kmod
    now = time.time()
    assert kmod._quote_time(now - 5, now) == int(now - 5)
    assert kmod._quote_time(now - 5 - kmod.EPOCH_1980_OFFSET, now) == int(now - 5), "a 1980-based stamp is corrected"
    assert kmod._quote_time("junk", now) is None and kmod._quote_time(0, now) is None
    assert kmod._quote_time(now - 5 * 86400, now) is None, "a stamp from days ago is not trusted"
    assert kmod._quote_time(now + 3600, now) is None, "a future quote cannot be treated as fresh"
    assert kmod._quote_time(now + 5, now) == int(now + 5), "small clock skew is tolerated"
    assert kmod._num("nan") is None and kmod._num("inf") is None, "non-finite broker numbers are not data"
    k, fake = make()
    quote_rows = k.quotes(["NSE:RELIANCE-EQ"])
    q = quote_rows.get("NSE:RELIANCE-EQ")
    assert q and q.get("received_ts"), q
    assert q.get("ts_source") in ("exchange", "response"), q
    # A supplied but impossible exchange timestamp must not silently fall back
    # to receipt time and be treated as a fresh market observation.
    fake.script = [[{"exchange_token": "2885", "exchange": "nse_cm", "ltp": "101", "last_volume": "5000",
                     "lstup_time": str(int(time.time()) + 3600), "open_int": "1000", "ohlc": {"close": "100"},
                     "depth": {"buy": [{"price": "100", "quantity": "300"}], "sell": [{"price": "101", "quantity": "300"}]}}]]
    invalid = k.quotes(["NSE:RELIANCE-EQ"])["NSE:RELIANCE-EQ"]
    assert invalid["ts"] is None and invalid["ts_invalid"] is True and invalid["ts_source"] == "invalid", invalid
    k.history("NSE:RELIANCE-EQ", "5", date(2026, 11, 12), date(2026, 11, 12))
    st = k.stats_snapshot()
    assert st["by_method"]["quotes"]["calls"] == 2 and st["by_method"]["historical_data"]["ok"] == 1, st
    assert st["total"] == sum(x["calls"] for x in st["by_method"].values()) and st["by_method"]["scrip_master"]["calls"] == 1, st
    fake.script = [{"code": 429, "error": "Rate limit exceeded", "message": "too many request received"}]
    k.history("NSE:RELIANCE-EQ", "5", date(2026, 11, 12), date(2026, 11, 12))
    st = k.stats_snapshot()
    assert st["by_method"]["historical_data"]["refused"] == 1 and st["by_method"]["historical_data"]["calls"] == 3, st


def test_quotes():
    k, fake = make()
    out = k.quotes(["NSE:RELIANCE-EQ", "NSE:HDFCBANK-EQ", "NSE:NIFTY50-INDEX", "NSE:MISSING-EQ"])
    assert set(out) == {"NSE:RELIANCE-EQ", "NSE:HDFCBANK-EQ", "NSE:NIFTY50-INDEX"}, out
    q = out["NSE:RELIANCE-EQ"]
    assert q["lp"] == 100.0 and q["prev_close"] == 98.0 and q["volume"] == 5000.0, q
    check_quote_detail(q)
    # more than 50 symbols are split into batches of 50
    k._equities["nse_cm"] = {f"S{i}-EQ": str(i) for i in range(120)}
    fake.calls.clear()
    out = k.quotes([f"NSE:S{i}-EQ" for i in range(120)])
    sizes = [c[1] for c in fake.calls if c[0] == "quotes"]
    assert sizes == [50, 50, 20] and len(out) == 120, (sizes, len(out))


def test_history():
    k, fake = make()
    day = date(2026, 11, 12)
    c = k.history("NSE:RELIANCE-EQ", "5", day - timedelta(days=69), day)
    hist = [x for x in fake.calls if x[0] == "hist"]
    assert len(hist) == 3, "70 days of 5-minute candles must be split into requests of at most 30 days"
    assert all(h[1] == "nse_cm|2885" and h[2] == "5min" for h in hist), hist
    spans = [(date.fromisoformat(h[4]) - date.fromisoformat(h[3])).days + 1 for h in hist]
    assert max(spans) <= 30, spans
    assert c == sorted(c, key=lambda r: r[0]) and len({r[0] for r in c}) == len(c)
    first = c[0]
    assert len(first) == 6 and clock.from_ts(first[0]).strftime("%H:%M") == "09:15", first
    assert first[4] == 11 and first[5] == 100
    # a derivative contract goes straight through as segment|token
    fake.calls.clear()
    k.history("nse_fo|35001", "1", day - timedelta(days=2), day)
    assert fake.calls[0][1:3] == ("nse_fo|35001", "1min"), fake.calls
    # commodities have no history at Kotak
    try:
        k.history("mcx_fo|555", "1", day, day)
        assert False
    except KotakError:
        pass


def test_errors_and_retries():
    k, fake = make()
    day = date(2026, 11, 12)
    fake.script = [{"status": "ERROR", "fault": {"code": 429, "message": "Too many requests"}}]
    assert k.history("NSE:RELIANCE-EQ", "5", day, day), "a rate limit is waited out and retried"
    fake.script = [{"status": "ERROR", "fault": {"code": 403, "message": "Invalid session, please re-login"}}]
    try:
        k.history("NSE:RELIANCE-EQ", "5", day, day)
        assert False
    except AuthError:
        pass
    fake.script = [{"status": "ERROR", "fault": {"code": 400, "message": "Invalid interval value"}}]
    try:
        k.history("NSE:RELIANCE-EQ", "5", day, day)
        assert False
    except KotakError as e:
        assert "Invalid interval" in str(e) and not isinstance(e, AuthError)
    fake.script = [{"error": [{"code": "10300", "message": "Validation Errors!"}]}]
    try:
        k.quotes(["NSE:RELIANCE-EQ"])
        assert False
    except KotakError:
        pass
    # an empty answer is just no candles, not a failure
    fake.script = [{"status": "success", "data": {"candles": []}}]
    assert k.history("NSE:RELIANCE-EQ", "5", day, day) == []
    # AuthError and the other errors share a base, so run.py's one handler covers both brokers
    assert issubclass(AuthError, BrokerError) and issubclass(KotakError, BrokerError)
    # the probe is a plain yes or no
    k3, f3 = make()
    assert k3.probe() is True
    f3.script = [{"status": "ERROR", "fault": {"code": 403, "message": "Invalid session"}}]
    k3._files_cache = {}
    assert k3.probe() is False


def test_master():
    k, fake = make()
    m = KotakMaster(k)
    futs = m.futures("NSE_FO", "NIFTY")
    assert [f["desc"] for f in futs] == ["NIFTY26NOVFUT", "NIFTY26DECFUT"], "NIFTYNXT50 and BANKNIFTY must not match NIFTY"
    assert futs[0]["symbol"] == "nse_fo|35001" and futs[0]["lot"] == 75 and futs[0]["type"] == "FUT"
    day = date(2026, 11, 12)
    assert m.nearest_future("NSE_FO", "NIFTY", day)["desc"] == "NIFTY26NOVFUT"
    assert m.nearest_future("NSE_FO", "NIFTY", date(2026, 11, 27))["desc"] == "NIFTY26DECFUT"
    exp, why = m.choose_expiry("NSE_FO", "NIFTY", day, 1, "roll")
    assert exp and clock.from_ts(exp).date() == date(2026, 11, 26), (exp, why)
    exp2, _ = m.choose_expiry("NSE_FO", "NIFTY", date(2026, 11, 26), 1, "roll")
    assert clock.from_ts(exp2).date() == date(2026, 12, 31), "on expiry day, roll to the next contract"
    exp3, why3 = m.choose_expiry("NSE_FO", "NIFTY", date(2026, 11, 26), 1, "skip")
    assert exp3 is None and "close to expiry" in why3
    opt = m.pick_option("NSE_FO", "NIFTY", exp, 24512.0, "CE", "ATM")
    assert opt["strike"] == 24500.0 and opt["symbol"] == "nse_fo|35102" and opt["lot"] == 75, opt
    itm = m.pick_option("NSE_FO", "NIFTY", exp, 24512.0, "CE", "ITM1")
    assert itm["strike"] == 24400.0, itm
    pe = m.pick_option("NSE_FO", "NIFTY", exp, 24512.0, "PE", "ATM")
    assert pe["symbol"] == "nse_fo|35104"
    # an option's price is asked the same way as a share's
    q = k.quotes([opt["symbol"]])
    assert opt["symbol"] in q
    # contract dates counted from 1980 are corrected, then snapped to Kotak's own expiries call
    assert KotakMaster._expiry_seconds(NOV - EPOCH_1980_OFFSET) == NOV
    assert KotakMaster._expiry_seconds("junk") is None and KotakMaster._expiry_seconds(-1) is None
    odd = NOV + 6 * 3600                     # a few hours into the next day in India would give a different date
    assert clock.from_ts(m._snap("nse_fo", "NIFTY", odd)).date() == date(2026, 11, 26)


def test_series_from_broker():
    k, _ = make()
    out, missing = series_from_broker(k, ["NSE:RELIANCE-EQ", "NSE:MISSING-EQ"], 250, log=lambda m: None, label="Kotak Neo")
    assert "NSE:RELIANCE-EQ" in out and len(out["NSE:RELIANCE-EQ"].days) > 100, "a year of candles becomes a Series"
    assert missing == ["NSE:MISSING-EQ"], missing


def test_rate_limit_and_healing():
    from engine import kotak as kmod
    body = {"code": 429, "error": "Rate limit exceeded", "message": "too many request received"}
    code, msg = kmod._problem(body)
    assert code == 429 and "rate limit" in msg.lower(), (code, msg)         # the exact reply seen in the first live run
    k, fake = make()
    day = date(2026, 11, 12)
    k.resolve("NSE:RELIANCE-EQ")                       # load the contract list first so the refusal lands on a history call
    start = k._rate["history"]
    fake.script = [body]
    assert k.history("NSE:RELIANCE-EQ", "5", day, day), "one refusal is waited out and retried"
    assert k._rate["history"] < start and k.cooling() > 0, "it slows down and pauses"
    # a quiet stretch speeds it up again, but never past the ceiling
    k2, _ = make()
    k2._rate["history"] = 1.0
    for _ in range(400):
        k2._accepted("history")
    assert 1.0 < k2._rate["history"] <= kmod.RATE["history"][2], k2._rate
    # it never goes below the floor, however often it is refused
    k3, f3 = make()
    k3.resolve("NSE:RELIANCE-EQ")
    f3.script = [body] * 20
    for _ in range(6):
        try:
            k3.history("NSE:RELIANCE-EQ", "5", day, day)
        except KotakError as e:
            assert "rate limit" in str(e).lower()
    assert k3._rate["history"] >= kmod.RATE["history"][1]
    # the pause is bounded, so the engine's heartbeat is never starved
    waited = []
    k4, f4 = make()
    k4._sleep = waited.append
    k4.resolve("NSE:RELIANCE-EQ")
    f4.script = [body] * 3
    try:
        k4.history("NSE:RELIANCE-EQ", "5", day, day)
    except KotakError:
        pass
    assert all(w <= kmod.MAX_PAUSE for w in waited), waited
    # a refused index name is looked up in Kotak's own list and retried
    k5, f5 = make()
    f5.invalid = {"nse_cm|NIFTY MIDCAP SELECT"}
    got = k5.history("NSE:MIDCPNIFTY-INDEX", "5", day, day)
    assert got and k5.resolve("NSE:MIDCPNIFTY-INDEX") == ("nse_cm", "99001"), k5.resolve("NSE:MIDCPNIFTY-INDEX")
    # an index that cannot be matched fails clearly, naming what Kotak does list
    k6, f6 = make(index_tokens={"NSE:NIFTYBANK-INDEX": "nse_cm|Nope"})
    f6.invalid = {"nse_cm|Nope"}
    logged = []
    k6.log = logged.append
    try:
        k6.history("NSE:NIFTYBANK-INDEX", "5", day, day)
        assert False
    except KotakError as e:
        assert "invalid neosymbol" in str(e).lower()
    assert any("Nifty 50" in m for m in logged), logged
    n = len([c for c in f6.calls if c[0] == "hist"])
    try:
        k6.history("NSE:NIFTYBANK-INDEX", "5", day, day)
        assert False
    except KotakError:
        pass
    assert len([c for c in f6.calls if c[0] == "hist"]) == n, "a dead index is not asked for again every pass"


def test_broker_choice():
    cfg = {"markets": [{"id": "NIFTY", "kind": "index", "enabled": True},
                       {"id": "CRUDE", "kind": "commodity", "enabled": True}]}
    assert brokers.primary({}) == "kotak" and brokers.primary({"broker": {"primary": "FYERS"}}) == "fyers"
    assert brokers.required_secrets("kotak") == ["KOTAK_CONSUMER_KEY"]
    notes = brokers.adjust_config(cfg, "kotak")
    assert notes and cfg["markets"][1]["enabled"] is False and cfg["markets"][0]["enabled"] is True
    cfg2 = {"markets": [{"id": "CRUDE", "kind": "commodity", "enabled": True}]}
    assert brokers.adjust_config(cfg2, "fyers") == [] and cfg2["markets"][0]["enabled"] is True
    assert brokers.secret_material({"FYERS_SECRET_KEY": "old", "KOTAK_CONSUMER_KEY": "new"}) == "old"
    assert brokers.secret_material({"KOTAK_CONSUMER_KEY": "new"}) == "new"
    b, m = brokers.build({"broker": {"primary": "kotak", "kotak": {"index_tokens": {"NSE:NIFTY50-INDEX": "nse_cm|X"}}}},
                         {"KOTAK_CONSUMER_KEY": " abc "})
    assert isinstance(b, Kotak) and isinstance(m, KotakMaster) and b.consumer_key == "abc"
    assert b.resolve("NSE:NIFTY50-INDEX") == ("nse_cm", "X")
    f, mm = brokers.build({"broker": {"primary": "fyers"}}, {"FYERS_APP_ID": "a", "FYERS_SECRET_KEY": "b"})
    assert type(f).__name__ == "Fyers" and type(mm).__name__ == "Master"
    try:
        brokers.build({"broker": {"primary": "zerodha"}}, {})
        assert False
    except ValueError:
        pass
    try:
        Kotak("")
        assert False
    except KotakError:
        pass


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok  ", t.__name__)
    print(f"All {len(tests)} Kotak checks passed.")


def test_fyers_quote_timestamp_quality(monkeypatch):
    """The optional Fyers adapter must distinguish absent from malformed exchange timestamps."""
    from engine.fyers import Fyers
    fy = Fyers("app", "secret")
    fy.token = "token"
    monkeypatch.setattr(fy, "_get", lambda *a, **k: {"d": [
        {"n": "NSE:BAD-EQ", "s": "ok", "v": {"lp": 100, "prev_close_price": 98, "exch_feed_time": "NaN"}},
        {"n": "NSE:GOOD-EQ", "s": "ok", "v": {"lp": 100, "prev_close_price": 98}},
    ]})
    got = fy.quotes(["NSE:BAD-EQ", "NSE:GOOD-EQ"])
    assert got["NSE:BAD-EQ"]["ts_invalid"] is True and got["NSE:BAD-EQ"]["ts_source"] == "invalid", got
    assert got["NSE:GOOD-EQ"]["ts"] is None and got["NSE:GOOD-EQ"]["ts_source"] == "response", got
