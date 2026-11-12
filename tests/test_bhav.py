"""Checks the NSE daily-file loader, the split-adjusted series and the point-in-time universe. Run:  python -m tests.test_bhav   (offline)"""
import io
import os
import shutil
import sys
import tempfile
import zipfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import bhav, daily as dl  # noqa: E402
from tests.test_strategies import check  # noqa: E402

OLD_HEAD = "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN,\n"
NEW_HEAD = ("TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,"
            "OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,"
            "TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4\n")


def zipped(name, text):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, text)
    return buf.getvalue()


def test_parse():
    print("NSE daily files: both layouts")
    old = zipped("x.csv", OLD_HEAD + "AAA,EQ,10,11,9,10.5,10.4,10.0,1000,10500,02-JAN-2019,5,INE000A01010,\n"
                 "BBB,BE,5,6,4,5.5,5.4,5.0,200,1100,02-JAN-2019,3,INE000B01010,\n"
                 "GOLD,GB,3000,3010,2990,3005,3005,3000,5,15000,02-JAN-2019,1,IN0000000000,\n"
                 "BAD,EQ,x,y,z,w,1,1,1,1,02-JAN-2019,1,INE000C01010,\n"
                 "ZERO,EQ,0,0,0,0,0,0,0,0,02-JAN-2019,0,INE000D01010,\n")
    rows = bhav.parse_zip(old)
    check("equity series are kept, other instruments and bad rows are not", [r[1] for r in rows] == ["AAA", "BBB"], rows)
    check("the fields are read in order", rows[0] == ("INE000A01010", "AAA", "EQ", 10.0, 11.0, 9.0, 10.5, 10.0, 1000.0, 10500.0), rows[0])
    new = zipped("y.csv", NEW_HEAD + "2025-06-02,2025-06-02,CM,NSE,STK,1,INE000A01010,AAA,EQ,,,,,AAA LTD,10,11,9,10.5,10.4,10.0,,10.5,,,1000,10500,5,F1,1,,,,,\n"
                 "2025-06-02,2025-06-02,CM,NSE,STK,2,IN0000000000,SGB,GB,,,,,GOLD,3000,3010,2990,3005,3005,3000,,3005,,,5,15000,1,F1,1,,,,,\n")
    r2 = bhav.parse_zip(new)
    check("the newer layout gives the same fields", len(r2) == 1 and r2[0] == rows[0], r2)
    try:
        bhav.parse_zip(zipped("z.csv", "A,B,C\n1,2,3\n"))
        check("an unknown layout is refused, not guessed", False)
    except ValueError:
        check("an unknown layout is refused, not guessed", True)
    check("the right file name is tried first for each era", bhav.urls_for(date(2019, 1, 2))[0].endswith("cm02JAN2019bhav.csv.zip")
          and bhav.urls_for(date(2025, 6, 2))[0].endswith("BhavCopy_NSE_CM_0_0_0_20250602_F_0000.csv.zip"))


def make_days():
    """Three shares over 400 sessions. S splits 1:5 on session 200; E stops trading on session 250; T is too thin to matter."""
    days, d, k = {}, date(2020, 1, 1), 0
    price = {"S": 1000.0, "E": 100.0, "T": 10.0, "L": 50.0}
    while k < 400:
        d += timedelta(days=1)
        if d.weekday() >= 5:
            continue
        rows = []
        raw = {s: price[s] for s in price}
        for sym, isin in (("S", "INE0S"), ("E", "INE0E"), ("T", "INE0T"), ("L", "INE0L")):
            if sym == "E" and k >= 250:
                continue
            prev = price[sym]
            c = prev * (1.01 if k % 2 else 0.995)
            qty = 1_000_000 if sym != "T" else 100
            scale = 1.0
            if sym == "S" and k == 200:                      # a 1:5 split: the raw price falls to a fifth, the exchange's previous close follows
                scale = 0.2
                prev_for_file = prev * scale
                c = prev_for_file * 1.01
                qty = 5_000_000
                price[sym] = c
                rows.append(("INE0S", "S", "EQ", c, c, c, c, prev_for_file, qty, c * qty))
                continue
            if sym == "S" and k > 200:
                qty = 5_000_000
            price[sym] = c
            rows.append((isin, sym, "EQ", c, c * 1.01, c * 0.99, c, prev, float(qty), c * qty))
        days[d] = rows
        k += 1
    return days


def test_build():
    print("NSE daily files: adjusted series, delisting, universe")
    days = make_days()
    candles, meta, last = bhav.build(days, min_value_cr=0.5, min_bars=100, log=lambda m: None)
    check("a share too thin to matter is left out", "INE0T" not in candles and {"INE0S", "INE0E", "INE0L"} <= set(candles), sorted(candles))
    s = candles["INE0S"]
    rets = [s[i][4] / s[i - 1][4] - 1 for i in range(1, len(s))]
    check("a 1:5 split leaves no jump in the adjusted price", max(abs(r) for r in rets) < 0.02, max(abs(r) for r in rets))
    check("volume is in the same share units as price (no volume spike at the split)", 0.9 < s[200][5] / s[199][5] < 1.1, (s[199][5], s[200][5]))
    check("price x volume is the real traded value", abs(s[100][4] * s[100][5] - days[sorted(days)[100]][0][9]) / days[sorted(days)[100]][0][9] < 1e-6)
    check("a share that stopped trading is marked ended", meta["INE0E"]["ended"] is True and meta["INE0S"]["ended"] is False, meta)
    check("its history is kept, not dropped", len(candles["INE0E"]) == 250, len(candles["INE0E"]))
    # point-in-time universe: ranks by the previous 50 sessions
    data = {k: dl.Daily(v, ended=meta[k]["ended"]) for k, v in candles.items()}
    members = bhav.point_in_time_members(data, top=2)
    some_day = sorted(members)[150]
    check("the universe has the requested size", all(len(v) <= 2 for v in members.values()) and len(members[some_day]) == 2)
    check("it uses only the past: nothing is ranked before 50 sessions of history", min(members) > sorted(days)[49])
    check("a share still trading is a member while it trades, then drops out", "INE0E" in members.get(sorted(days)[240], set())
          and all("INE0E" not in members[d] or d <= sorted(days)[250 + 1] for d in members))


def test_download():
    print("NSE daily files: downloading is resumable and tells holidays from failures")
    folder = tempfile.mkdtemp()
    start, end = date(2024, 6, 3), date(2024, 6, 7)                  # Monday to Friday
    good = zipped("a.csv", OLD_HEAD + "AAA,EQ,10,11,9,10.5,10.4,10.0,1000,10500,03-JUN-2024,5,INE000A01010,\n")
    calls = []

    class Resp:
        def __init__(self, code, content=b""):
            self.status_code, self.content = code, content

    class FakeSession:
        def __init__(self):
            self.headers = {}

        def get(self, url, timeout=0, **kw):
            calls.append(url)
            if "bhav" not in url.lower() and "BhavCopy" not in url:
                return Resp(200, b"home")
            if "05JUN2024" in url or "20240605" in url:           # a holiday: no file under either name
                return Resp(404)
            if "06JUN2024" in url and calls.count(url) == 1:       # one temporary refusal, then it works
                return Resp(403)
            return Resp(200, good)

    real_sess, real_sleep = bhav.requests.Session, bhav.time.sleep
    bhav.requests.Session, bhav.time.sleep = FakeSession, lambda s: None
    try:
        got, hol, fail = bhav.download(start, end, folder, log=lambda m: None, pause=0)
        check("every trading day is saved, the holiday is remembered, nothing fails", (got, hol, fail) == (4, 1, 0), (got, hol, fail))
        check("a saved day can be read back", len(bhav.load_days(folder)) == 4)
        n = len(calls)
        again = bhav.download(start, end, folder, log=lambda m: None, pause=0)
        check("a second run asks for nothing (resumable)", again == (0, 0, 0) and len(calls) == n + 1, (again, len(calls) - n))
    finally:
        bhav.requests.Session, bhav.time.sleep = real_sess, real_sleep
        shutil.rmtree(folder, ignore_errors=True)


def test_delisted_trades():
    print("Daily research: a share that stops trading is closed, not dropped")
    base = []
    p = 100.0
    for k in range(400):
        c = p * (1.0006 if k % 2 else 0.9995)
        base.append((p, max(p, c) * 1.002, min(p, c) * 0.998, c, 1_000_000))
        p = c
    from tests.test_daily import candles as mk
    rows = base[:300] + [(p, p * 1.1, p, p * 1.05, 5_000_000)] + [(p * 1.05, p * 1.06, p * 1.04, p * 1.05, 1_000_000)] * 3
    ended = dl.Daily(mk(rows), ended=True)
    live = dl.Daily(mk(rows), ended=False)
    sig = [s for s in dl.signals(ended, {}) if s["i"] == 300]
    check("the breakout is found", len(sig) == 1, sig)
    t_end = dl.simulate(ended, 300, sig[0]["pivot"], "trail")
    t_live = dl.simulate(live, 300, sig[0]["pivot"], "trail")
    check("a delisted share is closed at its last price and counted", t_end["reason"] == "delisted or merged" and t_end["open"] is False, t_end)
    check("a share whose data merely ends is still flagged open", t_live["open"] is True and t_live["reason"] == "end of data", t_live)
    members = {d: {"IN"} for d in ended.d}
    keep = dl.signals(ended, {}, members, "IN")
    drop = dl.signals(ended, {}, members, "OUT")
    check("only shares in the universe on that date can signal", len(keep) == len(dl.signals(ended, {})) and drop == [])


def main():
    test_parse()
    test_build()
    test_download()
    test_delisted_trades()
    print("\nALL BHAV CHECKS PASSED" if not check.failed else f"\n{check.failed} CHECK(S) FAILED")
    sys.exit(1 if check.failed else 0)


if __name__ == "__main__":
    main()
