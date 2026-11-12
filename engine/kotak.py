"""Kotak Neo prices, through the official SDK (kotakneoapi). Read-only: this tool never places an order.

Kotak's price endpoints (quotes, history, contract lists) need only your Consumer Key. There is no daily
login, no TOTP and no MPIN, so nothing in this file ever asks you to log in.

This class answers the same two questions the Fyers class does, in the same shape:
    history(symbol, resolution, day_from, day_to) -> [[epoch, open, high, low, close, volume], ...]
    quotes([symbol, ...])                          -> {symbol: {"lp", "prev_close", "volume"}}
and KotakMaster answers the contract questions (futures, expiries, which option to buy).

Symbols stay in the form the rest of the tool already uses ("NSE:RELIANCE-EQ", "NSE:NIFTY50-INDEX").
Contracts are written "nse_fo|<token>". Both are turned into Kotak's "segment|token" here and nowhere else.

Run  python -m engine.kotak --check  to test every assumption below against your own account.
"""
import csv
import io
import os
import sys
import time
from datetime import date, datetime, time as dtime, timedelta, timezone

import requests

from . import clock
from .errors import AuthError, BrokerError
from .symbols import Master

IST = timezone(timedelta(hours=5, minutes=30))
EPOCH_1980_OFFSET = 315511200          # some Kotak contract lists count seconds from 1980, not 1970


class KotakError(BrokerError):
    pass


# Index names as Kotak spells them in its cash-market list. Override any of these in config.yaml under
# broker: kotak: index_tokens:   (for example  "NSE:NIFTY50-INDEX": "nse_cm|Nifty 50").
DEFAULT_INDEX = {
    "NSE:NIFTY50-INDEX": "nse_cm|Nifty 50",
    "NSE:NIFTYBANK-INDEX": "nse_cm|Nifty Bank",
    "NSE:FINNIFTY-INDEX": "nse_cm|Nifty Fin Service",
    "NSE:MIDCPNIFTY-INDEX": "nse_cm|NIFTY MIDCAP SELECT",
    "BSE:SENSEX-INDEX": "bse_cm|SENSEX",
}

INTERVAL = {"1": "1min", "3": "3min", "5": "5min", "10": "10min", "15": "15min",
            "30": "30min", "60": "60min", "D": "D", "1D": "D"}
# The most days Kotak accepts in one history request, per interval (kept a little under its stated limits).
MAX_DAYS = {"1min": 28, "3min": 28, "5min": 28, "10min": 58, "15min": 58, "30min": 88, "60min": 88, "D": 175}
MASTER_SEGMENT = {"NSE_FO": "nse_fo", "BSE_FO": "bse_fo", "MCX_COM": "mcx_fo"}
NO_HISTORY = {"mcx_fo", "nse_com"}     # Kotak does not serve price history for these


def _num(x):
    try:
        v = float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return v


def _epoch(stamp):
    """'2026-08-20T09:15:00+0530' -> epoch seconds. Candles are stamped with their start time."""
    if isinstance(stamp, (int, float)):
        return int(stamp)
    text = str(stamp)
    try:
        d = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        d = datetime.fromisoformat(text)
    if d.tzinfo is None:
        d = d.replace(tzinfo=IST)
    return int(d.timestamp())


def _problem(body):
    """(code, message) when a response is an error, else None. Kotak reports errors in a few different shapes."""
    if not isinstance(body, dict):
        return None
    fault = body.get("fault")
    if isinstance(fault, dict):
        return int(_num(fault.get("code")) or 0), str(fault.get("message") or "")
    err = body.get("error")
    if isinstance(err, list) and err and isinstance(err[0], dict):
        return int(_num(err[0].get("code")) or 0), str(err[0].get("message") or "")
    if isinstance(err, (str, dict)) and err:
        return 0, str(err)
    if "Error" in body:
        return int(_num(body.get("StatusCode")) or 0), f"{body.get('Error')} {str(body.get('ResponseText') or '')[:120]}".strip()
    if str(body.get("status", "")).lower() in ("error", "failure", "failed"):
        return int(_num(body.get("code")) or 0), str(body.get("message") or body.get("status"))
    return None


def _looks_like_login(code, message):
    m = message.lower()
    return code in (401, 403) or any(w in m for w in ("re-login", "relogin", "invalid session", "unauthor", "consumer", "invalid token"))


def _clean_header(name):
    return name.strip().rstrip(";").strip()


def read_rows(text):
    """The rows of a Kotak contract list, with tidy column names (one of them is spelled 'dStrikePrice;')."""
    reader = csv.reader(io.StringIO(text))
    try:
        head = [_clean_header(h) for h in next(reader)]
    except StopIteration:
        return
    for row in reader:
        if len(row) >= len(head) - 2:
            yield dict(zip(head, (c.strip() for c in row)))


class Kotak:
    name = "kotak"
    label = "Kotak Neo"
    can_login = False                  # prices need no login, so there is never anything to ask you for

    def __init__(self, consumer_key, client=None, environment="prod", index_tokens=None,
                 http_get=None, sleep=time.sleep, log=print):
        if not consumer_key and client is None:
            raise KotakError("KOTAK_CONSUMER_KEY is not set")
        self.consumer_key = consumer_key
        self.environment = environment
        self._client = client
        self._http_get = http_get or (lambda url: requests.get(url, timeout=90))
        self._sleep = sleep
        self.log = log
        self._last = 0.0
        self._index = dict(DEFAULT_INDEX)
        for k, v in (index_tokens or {}).items():
            self._index[k] = v
        self._files_cache = None
        self._csv = {}                 # segment -> contract list text
        self._equities = {}            # segment -> {trading symbol: token}
        self._resolved = {}
        self._reverse = {}
        self.unresolved = set()

    # ── the SDK ──────────────────────────────────────────────
    @property
    def client(self):
        if self._client is None:
            try:
                from neo_api_client import NeoAPI
            except ImportError as e:
                raise KotakError("The Kotak SDK is not installed. Run:  pip install kotakneoapi") from e
            self._client = NeoAPI(consumer_key=self.consumer_key, environment=self.environment)
        return self._client

    def _pace(self):
        # Kotak allows 25 quote requests a second. Stay far below it.
        wait = 0.12 - (time.monotonic() - self._last)
        if wait > 0:
            self._sleep(wait)
        self._last = time.monotonic()

    def _call(self, method, **kwargs):
        """One SDK call with pacing, retries and the errors turned into ours."""
        last = None
        for attempt in range(3):
            self._pace()
            try:
                body = getattr(self.client, method)(**kwargs)
            except KotakError:
                raise
            except Exception as e:                          # the SDK raises its own exception classes
                kind = type(e).__name__
                if kind in ("AuthenticationError", "InvalidSessionError"):
                    raise AuthError(str(e)[:160]) from e
                last = KotakError(f"Kotak {method} failed ({kind})")
                self._sleep(2 + 3 * attempt)
                continue
            problem = _problem(body)
            if problem is None:
                return body
            code, message = problem
            if code == 429 or "too many" in message.lower():
                retry = _num(((body.get("rateLimit") or {}).get("Retry-After"))) if isinstance(body, dict) else None
                last = KotakError("Kotak is limiting requests")
                self._sleep(min(30, retry or 5 * (attempt + 1)))
                continue
            if _looks_like_login(code, message):
                raise AuthError(message[:160] or "Kotak turned down the consumer key")
            if 500 <= code < 600:
                last = KotakError(f"Kotak is busy ({code})")
                self._sleep(2 + 3 * attempt)
                continue
            raise KotakError(message[:200] or f"Kotak {method} error {code}")
        raise last or KotakError(f"Kotak {method} failed")

    # ── contract lists ───────────────────────────────────────
    def _files(self):
        if self._files_cache is None:
            body = self._call("scrip_master")
            paths = []
            if isinstance(body, dict):
                paths = body.get("filesPaths") or (body.get("data") or {}).get("filesPaths") or []
            elif isinstance(body, (list, tuple)):
                paths = list(body)
            if not paths:
                raise KotakError("Kotak returned no contract list files")
            self._files_cache = paths
        return self._files_cache

    def csv_text(self, segment):
        """The day's contract list for one segment (nse_cm, bse_cm, nse_fo, bse_fo, mcx_fo)."""
        if segment not in self._csv:
            url = None
            for p in self._files():
                base = str(p).rsplit("/", 1)[-1].lower().split(".")[0]
                if base == segment or base.startswith(segment + "-"):
                    url = p
                    break
            if not url:
                raise KotakError(f"Kotak has no contract list for {segment}")
            r = self._http_get(url)
            r.raise_for_status()
            self._csv[segment] = r.text
        return self._csv[segment]

    def _equity_token(self, segment, trading_symbol):
        if segment not in self._equities:
            table = {}
            for r in read_rows(self.csv_text(segment)):
                sym = (r.get("pTrdSymbol") or "").upper()
                if not sym or not r.get("pSymbol"):
                    continue
                if sym not in table or (r.get("pGroup") or "").upper() == "EQ":
                    table[sym] = r["pSymbol"]
            self._equities[segment] = table
        return self._equities[segment].get(trading_symbol.upper())

    def resolve(self, symbol):
        """Our symbol -> (segment, token)."""
        hit = self._resolved.get(symbol)
        if hit:
            return hit
        if "|" in symbol:
            seg, tok = symbol.split("|", 1)
        else:
            exchange, _, name = symbol.partition(":")
            seg = {"NSE": "nse_cm", "BSE": "bse_cm"}.get(exchange.upper())
            if seg is None:
                raise KotakError(f"Unknown exchange in {symbol}")
            if name.upper().endswith("-INDEX"):
                spec = self._index.get(symbol)
                if not spec or "|" not in spec:
                    raise KotakError(f"No Kotak index name is set for {symbol}")
                seg, tok = spec.split("|", 1)
            else:
                tok = self._equity_token(seg, name)
                if not tok:
                    self.unresolved.add(symbol)
                    raise KotakError(f"{symbol} was not found in Kotak's contract list")
        seg, tok = seg.strip().lower(), str(tok).strip()
        self._resolved[symbol] = (seg, tok)
        self._reverse[(seg, tok.lower())] = symbol
        return seg, tok

    # ── prices ───────────────────────────────────────────────
    def history(self, symbol, resolution, day_from, day_to):
        seg, tok = self.resolve(symbol)
        if seg in NO_HISTORY:
            raise KotakError(f"Kotak does not provide price history for {seg}")
        interval = INTERVAL.get(str(resolution))
        if not interval:
            raise KotakError(f"Unsupported candle size {resolution}")
        step = MAX_DAYS[interval]
        out, lo = {}, day_from
        while lo <= day_to:
            hi = min(day_to, lo + timedelta(days=step - 1))
            body = self._call("historical_data", neosymbol=f"{seg}|{tok}", interval=interval,
                              from_date=lo.isoformat(), to_date=hi.isoformat())
            data = (body.get("data") or {}) if isinstance(body, dict) else {}
            for c in data.get("candles") or []:
                if len(c) < 6:
                    continue
                o, h, l, cl = (_num(x) for x in c[1:5])
                if None in (o, h, l, cl):
                    continue
                out[_epoch(c[0])] = [_epoch(c[0]), o, h, l, cl, _num(c[5]) or 0]
            lo = hi + timedelta(days=1)
        return [out[k] for k in sorted(out)]

    def quotes(self, symbols):
        want = []
        for s in symbols:
            try:
                want.append((s, *self.resolve(s)))
            except KotakError as e:
                if s not in self.unresolved or len(self.unresolved) <= 3:
                    self.log(f"  skipping {s}: {e}")
        out = {}
        for i in range(0, len(want), 50):
            batch = want[i:i + 50]
            body = self._call("quotes", instrument_tokens=[{"instrument_token": t, "exchange_segment": g} for _, g, t in batch],
                              quote_type="all")
            if not isinstance(body, list):
                continue
            for n, q in enumerate(body):
                if not isinstance(q, dict):
                    continue
                key = self._reverse.get((str(q.get("exchange") or "").lower(), str(q.get("exchange_token") or "").lower()))
                if key is None and len(body) == len(batch):
                    key = batch[n][0]                       # same order as asked
                lp = _num(q.get("ltp"))
                if key is None or not lp:
                    continue
                ohlc = q.get("ohlc") or {}
                out[key] = {"lp": lp, "prev_close": _num(ohlc.get("close")), "volume": _num(q.get("last_volume"))}
        return out

    def probe(self):
        """True when Kotak accepts the consumer key and gives a price."""
        try:
            self._files_cache = None
            return bool(self.quotes(["NSE:RELIANCE-EQ"]))
        except BrokerError:
            return False

    # ── contract details used by the options desk ────────────
    def expiry_dates(self, segment, underlying):
        """Authoritative expiry dates from Kotak's own expiries call. [] when it cannot be asked."""
        out = []
        for kind in ("option", "fut"):
            try:
                body = self._call("expiries", exchange=segment, underlying=underlying, instrument_type=kind)
            except BrokerError:
                continue
            for d in (body.get("expiries") or []) if isinstance(body, dict) else []:
                try:
                    out.append(date.fromisoformat(str(d)[:10]))
                except ValueError:
                    pass
        return sorted(set(out))


class KotakMaster(Master):
    """Same questions as symbols.Master, answered from Kotak's contract lists.

    A contract's `symbol` is "nse_fo|<token>", which Kotak.history and Kotak.quotes understand directly.
    """

    def __init__(self, kotak):
        super().__init__(loader=lambda name: "")
        self.k = kotak
        self._rows = {}
        self._dates = {}

    def _snap(self, segment, root, when):
        """Expiry dates in the contract lists can be off by an epoch quirk; Kotak's expiries call is the truth."""
        key = (segment, root)
        if key not in self._dates:
            try:
                self._dates[key] = self.k.expiry_dates(segment, root)
            except BrokerError:
                self._dates[key] = []
        d = clock.from_ts(when).date()
        near = [x for x in self._dates[key] if abs((x - d).days) <= 1]
        if near:
            best = min(near, key=lambda x: abs((x - d).days))
            return int(datetime.combine(best, dtime(15, 30), IST).timestamp())
        return when

    @staticmethod
    def _expiry_seconds(raw):
        v = _num(raw)
        if v is None or v <= 0:
            return None
        if datetime.fromtimestamp(v, IST).year < 2020:
            v += EPOCH_1980_OFFSET
        if datetime.fromtimestamp(v, IST).year > 2100:
            return None
        return int(v)

    def rows(self, name, root):
        key = (name, root.upper())
        if key in self._rows:
            return self._rows[key]
        seg = MASTER_SEGMENT.get(name, name.lower())
        out = []
        for r in read_rows(self.k.csv_text(seg)):
            if (r.get("pSymbolName") or "").upper() != root.upper():
                continue
            ot = (r.get("pOptionType") or "").upper()
            kind = "FUT" if ot == "XX" else ot if ot in ("CE", "PE") else None
            exp = self._expiry_seconds(r.get("pExpiryDate") or r.get("lExpiryDate"))
            if kind is None or exp is None or not r.get("pSymbol"):
                continue
            strike = _num(r.get("dStrikePrice")) or 0.0
            lot = int(_num(r.get("lLotSize") or r.get("iLotSize") or r.get("iBoardLotQty")) or 1)
            token = r["pSymbol"]
            out.append({"desc": r.get("pTrdSymbol") or r.get("pDesc") or "", "lot": lot,
                        "expiry": self._snap(seg, root.upper(), exp), "symbol": f"{seg}|{token}",
                        "scrip": token, "und_scrip": "", "strike": strike / 100.0 if strike > 0 else 0.0, "type": kind})
        self._rows[key] = out
        return out

    def futures(self, name, root):
        return sorted((r for r in self.rows(name, root) if r["type"] == "FUT"), key=lambda r: r["expiry"])

    def future_for_expiry(self, name, root, expiry, day):
        """Futures contract behind an option expiry: the first one that expires on or after it."""
        for f in self.futures(name, root):
            if f["expiry"] >= expiry:
                return f
        return self.nearest_future(name, root, day)


# ── python -m engine.kotak --check ───────────────────────────
def check(log=print):
    """Tests each assumption this adapter makes against your own Kotak account. Returns True if all essentials pass."""
    import yaml
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = yaml.safe_load(open(os.path.join(root, "config.yaml"), encoding="utf-8"))
    key = os.environ.get("KOTAK_CONSUMER_KEY", "").strip()
    if not key:
        log("FAIL  KOTAK_CONSUMER_KEY is not set (add it as a GitHub secret).")
        return False
    bc = ((cfg.get("broker") or {}).get("kotak") or {})
    k = Kotak(key, environment=bc.get("environment", "prod"), index_tokens=bc.get("index_tokens"), log=log)
    master = KotakMaster(k)
    bad = []

    def step(title, fn, essential=True):
        try:
            detail = fn()
            log(f"PASS  {title}" + (f": {detail}" if detail else ""))
            return True
        except Exception as e:
            log(f"{'FAIL' if essential else 'WARN'}  {title}: {type(e).__name__}: {e}")
            if essential:
                bad.append(title)
            return False

    step("consumer key accepted and contract lists found", lambda: ", ".join(
        sorted({str(p).rsplit('/', 1)[-1] for p in k._files()})))
    names = [x.split("|")[0] for x in open(os.path.join(root, "engine", "nifty200.txt"), encoding="utf-8")
             if x.strip() and not x.startswith("#")]

    def stocks():
        miss = []
        for n in names:
            try:
                k.resolve(f"NSE:{n}-EQ")
            except KotakError:
                miss.append(n)
        if miss:
            log(f"      not found in Kotak's list ({len(miss)}): {', '.join(miss[:30])}")
        if len(miss) > len(names) * 0.1:
            raise KotakError(f"{len(miss)} of {len(names)} shares could not be matched")
        return f"{len(names) - len(miss)} of {len(names)} shares matched"
    step("Nifty 200 shares found in the contract list", stocks)

    today = clock.now().date()
    for sym in list(DEFAULT_INDEX) + ["NSE:RELIANCE-EQ", "NSE:HDFCBANK-EQ"]:
        def quote(sym=sym):
            q = k.quotes([sym]).get(sym)
            if not q:
                raise KotakError("no price returned")
            return f"last {q['lp']}, previous close {q['prev_close']}"
        step(f"price for {sym}", quote, essential=sym in ("NSE:NIFTY50-INDEX", "NSE:RELIANCE-EQ"))

        def hist(sym=sym):
            c = k.history(sym, "5", today - timedelta(days=6), today)
            if not c:
                raise KotakError("no 5-minute candles in the last week")
            return f"{len(c)} candles, last at {clock.from_ts(c[-1][0]).strftime('%d %b %H:%M')}"
        step(f"5-minute history for {sym}", hist, essential=sym in ("NSE:NIFTY50-INDEX", "NSE:RELIANCE-EQ"))

    def fut(root_name, name):
        f = master.nearest_future(name, root_name, today)
        if not f:
            raise KotakError("no futures contract found")
        c = k.history(f["symbol"], "5", today - timedelta(days=6), today)
        return f"{f['desc']} lot {f['lot']} expires {clock.from_ts(f['expiry']).date()}, {len(c)} candles"
    step("Nifty futures contract and its history", lambda: fut("NIFTY", "NSE_FO"))
    step("Sensex futures contract", lambda: fut("SENSEX", "BSE_FO"), essential=False)

    def opt():
        exp, why = master.choose_expiry("NSE_FO", "NIFTY", today, 1, "roll")
        if not exp:
            raise KotakError(why)
        q = k.quotes(["NSE:NIFTY50-INDEX"]).get("NSE:NIFTY50-INDEX")
        o = master.pick_option("NSE_FO", "NIFTY", exp, q["lp"], "CE", "ATM")
        pr = k.quotes([o["symbol"]]).get(o["symbol"])
        if not pr:
            raise KotakError(f"no price for {o['desc']}")
        return f"{o['desc']} strike {o['strike']:g} expires {clock.from_ts(exp).date()}, premium {pr['lp']}"
    step("Nifty at-the-money option and its price", opt)

    def lots():
        want = {m["root"] for m in cfg["markets"] if m.get("kind") == "index" and m.get("enabled", True)}
        got = {}
        for m in cfg["markets"]:
            if m.get("kind") == "index" and m["root"] in want:
                f = master.nearest_future(m["master"], m["root"], today)
                got[m["root"]] = f["lot"] if f else None
        return ", ".join(f"{r}: {v}" for r, v in got.items())
    step("lot sizes for the indices", lots, essential=False)

    log("")
    if bad:
        log("Some essential checks failed: " + "; ".join(bad))
        log("Send this output back and the adapter can be corrected against what Kotak really returns.")
        return False
    log("All essential checks passed.")
    return True


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(0 if check() else 1)
    print(__doc__)
