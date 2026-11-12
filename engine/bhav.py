"""Survivor-free daily prices from NSE's own daily price files ("bhavcopy"): every share that traded each day, including the ones that
were later delisted, merged or renamed. Yahoo only has shares that exist today, which is what flattered the daily research.

    python -m engine.bhav --cache <folder> [--from 2016-11-01] [--to 2026-11-12]       download (about 2,500 small files, resumable)

What it does with them:
  - keeps equity series EQ, BE and BZ (BE and BZ are where troubled shares end up, which is exactly what survivors hide);
  - identifies a share by its ISIN, so a rename keeps its history;
  - builds a split- and bonus-adjusted price series from the exchange's own "previous close", which NSE adjusts on the ex-date, so
    close / previous close is the true return;
  - adjusts volume the same way, so a split does not look like a volume breakout;
  - a share whose history stops before the end of the data was delisted or merged: it is closed at its last price, never dropped;
  - ranks every share by the traded value of the previous 50 sessions, so the universe on any date is the 200 most traded shares
    THEN, a point-in-time stand-in for the Nifty 200 that needs no index history.

Limits: prices are adjusted for splits and bonuses but not dividends; a merged share's holders received shares of the merged company, so
closing at the last price is an approximation; the stand-in universe is by traded value, not by NSE's free-float market capitalisation.
Downloads are paced (about three a second) and resumable. NSE may block some networks.
"""
import csv
import gzip
import io
import os
import time
import zipfile
from datetime import date, timedelta

import requests

from . import clock
from .nse import HOME, UA

OLD = "https://nsearchives.nseindia.com/content/historical/EQUITIES/{y}/{mon}/cm{d:02d}{mon}{y}bhav.csv.zip"
NEW = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{y}{m:02d}{d:02d}_F_0000.csv.zip"
NEW_FROM = date(2024, 7, 8)                      # NSE changed its file format around here
KEEP = ("EQ", "BE", "BZ")
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def urls_for(d):
    old = OLD.format(y=d.year, mon=MONTHS[d.month - 1], d=d.day)
    new = NEW.format(y=d.year, m=d.month, d=d.day)
    return [new, old] if d >= NEW_FROM else [old, new]


def parse_zip(blob):
    """Rows of one day's file as (isin, symbol, series, open, high, low, close, prevclose, quantity, value). Old and new formats."""
    z = zipfile.ZipFile(io.BytesIO(blob))
    text = z.read(z.namelist()[0]).decode("utf-8", "replace")
    rd = csv.reader(io.StringIO(text))
    head = [h.strip() for h in next(rd)]
    col = lambda *names: next((head.index(n) for n in names if n in head), None)
    c = {"sym": col("SYMBOL", "TckrSymb"), "ser": col("SERIES", "SctySrs"), "o": col("OPEN", "OpnPric"), "h": col("HIGH", "HghPric"),
         "l": col("LOW", "LwPric"), "c": col("CLOSE", "ClsPric"), "p": col("PREVCLOSE", "PrvsClsgPric"),
         "q": col("TOTTRDQTY", "TtlTradgVol"), "v": col("TOTTRDVAL", "TtlTrfVal"), "isin": col("ISIN")}
    if None in c.values():
        raise ValueError(f"unknown file layout: {head[:8]}")
    out = []
    for r in rd:
        try:
            if len(r) <= max(c.values()) or r[c["ser"]].strip() not in KEEP:
                continue
            o, h, l, cl, p = (float(r[c[k]]) for k in ("o", "h", "l", "c", "p"))
            if cl <= 0 or p <= 0:
                continue
            out.append((r[c["isin"]].strip(), r[c["sym"]].strip(), r[c["ser"]].strip(), o, h, l, cl, p,
                        float(r[c["q"]] or 0), float(r[c["v"]] or 0)))
        except (ValueError, IndexError):
            continue
    return out


def _path(cache, d):
    return os.path.join(cache, f"{d.isoformat()}.csv.gz")


def download(start, end, cache, log=print, pause=0.3):
    """Fetches every missing day between two dates into `cache`. A day with no file (holiday) is remembered. Returns (days, holidays)."""
    os.makedirs(cache, exist_ok=True)
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": HOME, "Accept-Language": "en-US,en;q=0.9"})
    try:
        s.get(HOME, timeout=15)
    except requests.RequestException:
        pass
    got = hol = fail = 0
    d = start
    while d <= end:
        if d.weekday() < 5 and not os.path.exists(_path(cache, d)) and not os.path.exists(_path(cache, d) + ".none"):
            blob, missing = None, 0
            for u in urls_for(d):
                for attempt in range(3):
                    try:
                        r = s.get(u, timeout=30)
                    except requests.RequestException:
                        time.sleep(2 * (attempt + 1))
                        continue
                    if r.status_code == 200 and r.content[:2] == b"PK":
                        blob = r.content
                        break
                    if r.status_code in (403, 429):
                        time.sleep(5 * (attempt + 1))
                        try:
                            s.get(HOME, timeout=15)
                        except requests.RequestException:
                            pass
                        continue
                    missing += 1
                    break
                if blob:
                    break
            if blob:
                rows = parse_zip(blob)
                with gzip.open(_path(cache, d), "wt", encoding="utf-8", newline="") as f:
                    csv.writer(f).writerows(rows)
                got += 1
            elif missing >= 2:
                open(_path(cache, d) + ".none", "w").close()          # a holiday: no file under either name
                hol += 1
            else:
                fail += 1
                log(f"  could not read {d} (will retry next run)")
            time.sleep(pause)
            if (got + hol) % 100 == 0 and (got + hol):
                log(f"  {d}: {got} days downloaded, {hol} holidays")
        d += timedelta(days=1)
    return got, hol, fail


# ── building adjusted series ─────────────────────────────────────────────────────────────────────
def load_days(cache, start=None, end=None):
    """{date: rows} for every saved day."""
    out = {}
    for name in sorted(os.listdir(cache)):
        if not name.endswith(".csv.gz"):
            continue
        d = date.fromisoformat(name[:10])
        if (start and d < start) or (end and d > end):
            continue
        with gzip.open(os.path.join(cache, name), "rt", encoding="utf-8", newline="") as f:
            out[d] = [(r[0], r[1], r[2], *map(float, r[3:])) for r in csv.reader(f)]
    return out


def build(days, min_value_cr=10.0, min_bars=320, log=print):
    """From the raw days to {isin: Daily-style candles}, adjusted for splits and bonuses, plus metadata.

    Returns (candles, meta, last_day). candles[isin] = [[ts, o, h, l, c, v], ...] with adjusted prices and volume.
    meta[isin] = {"symbol", "first", "last", "ended": True if it stopped trading before the last day}.
    Only shares that ever traded at least `min_value_cr` crore a day on average over 50 sessions are kept (the rest are never in any
    top-200 and only slow things down)."""
    dates = sorted(days)
    last_day = dates[-1]
    rows = {}
    for d in dates:
        for isin, sym, ser, o, h, l, c, p, q, v in days[d]:
            rows.setdefault(isin, []).append((d, sym, o, h, l, c, p, q, v))
    candles, meta = {}, {}
    for isin, lst in rows.items():
        if len(lst) < min_bars:
            continue
        vals = [x[8] for x in lst]
        best, run = 0.0, 0.0
        for i, v in enumerate(vals):                      # best 50-session average traded value, in rupees
            run += v
            if i >= 50:
                run -= vals[i - 50]
            if i >= 49:
                best = max(best, run / 50)
        if best < min_value_cr * 1e7:
            continue
        adj, prev_adj = [], None
        for d, sym, o, h, l, c, p, q, v in lst:
            a = c if prev_adj is None else prev_adj * (c / p)        # the exchange's previous close carries splits and bonuses
            adj.append(a)
            prev_adj = a
        f = [a / x[5] for a, x in zip(adj, lst)]                      # raw-to-adjusted factor on each day
        series = []
        for (d, sym, o, h, l, c, p, q, v), fa in zip(lst, f):
            # prices are in first-day share units; volume is converted to the same units, so price x volume is the true traded value
            series.append([int(clock.ts(clock.at(d, "09:15"))), o * fa, h * fa, l * fa, c * fa, q / fa])
        candles[isin] = series
        meta[isin] = {"symbol": lst[-1][1], "first": lst[0][0].isoformat(), "last": lst[-1][0].isoformat(),
                      "ended": lst[-1][0] < last_day - timedelta(days=5)}
    log(f"  {len(candles)} shares ever traded at least ₹{min_value_cr:g} crore a day; {sum(1 for m in meta.values() if m['ended'])} of them stopped trading before {last_day}.")
    return candles, meta, last_day


def point_in_time_members(daily, top=200, volume_days=50):
    """{date: set(isin)}: the `top` most traded shares on each date, ranked by the traded value of the previous 50 sessions, which is
    all that was known at the time. `daily` is {isin: engine.daily.Daily}; Daily.val_prev is the average of close x volume."""
    by_date = {}
    for isin, D in daily.items():
        for i, d in enumerate(D.d):
            v = D.val_prev[i]
            if v:
                by_date.setdefault(d, []).append((v, isin))
    return {d: {isin for _, isin in sorted(lst, reverse=True)[:top]} for d, lst in by_date.items()}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--from", dest="frm", default=None)
    ap.add_argument("--to", dest="to", default=None)
    a = ap.parse_args(argv)
    end = date.fromisoformat(a.to) if a.to else clock.now().date()
    start = date.fromisoformat(a.frm) if a.frm else end - timedelta(days=int(10.05 * 365))
    got, hol, fail = download(start, end, a.cache)
    print(f"Done: {got} new days, {hol} holidays, {fail} to retry.")


if __name__ == "__main__":
    main()
