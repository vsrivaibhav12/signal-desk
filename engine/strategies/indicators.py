"""Indicator maths on lists of 5-minute candles.

A candle is [start_time_in_seconds, open, high, low, close, volume].
Every function returns one value per candle (None until there is enough history).
Only past and present candles are ever used for a value, never future ones.
"""
from collections import deque

from .. import clock

OPEN_MIN = 9 * 60 + 15          # 9:15 in minutes after midnight


def minute_of_day(t):
    """Minutes after midnight, India time, of a timestamp."""
    return (t + 19800) % 86400 // 60


def slot(t):
    """Which 5-minute bar of the session this is: 0 for 9:15, 1 for 9:20 ..."""
    return (minute_of_day(t) - OPEN_MIN) // 5


def hhmm_to_min(text):
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def ema(vals, n):
    k = 2.0 / (n + 1)
    out, e = [], None
    for v in vals:
        e = v if e is None else e + k * (v - e)
        out.append(e)
    return out


def rsi(close, n=14):
    out = [None] * len(close)
    if len(close) <= n:
        return out
    gain = loss = 0.0
    for i in range(1, n + 1):
        d = close[i] - close[i - 1]
        gain += max(d, 0)
        loss += max(-d, 0)
    gain, loss = gain / n, loss / n
    out[n] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    for i in range(n + 1, len(close)):
        d = close[i] - close[i - 1]
        gain = (gain * (n - 1) + max(d, 0)) / n
        loss = (loss * (n - 1) + max(-d, 0)) / n
        out[i] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return out


def true_range(high, low, close):
    out = [high[0] - low[0]]
    for i in range(1, len(close)):
        out.append(max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))
    return out


def wilder(vals, n):
    """Wilder's smoothing (the kind ATR and ADX use). First value appears at index n-1."""
    out = [None] * len(vals)
    if len(vals) < n:
        return out
    s = sum(vals[:n]) / n
    out[n - 1] = s
    for i in range(n, len(vals)):
        s = (s * (n - 1) + vals[i]) / n
        out[i] = s
    return out


def atr(high, low, close, n=14):
    return wilder(true_range(high, low, close), n)


def adx(high, low, close, n=14):
    """Average Directional Index: how strongly the market is trending (not which way)."""
    m = len(close)
    out = [None] * m
    if m < 2 * n + 1:
        return out
    plus, minus = [0.0], [0.0]
    for i in range(1, m):
        up, dn = high[i] - high[i - 1], low[i - 1] - low[i]
        plus.append(up if up > dn and up > 0 else 0.0)
        minus.append(dn if dn > up and dn > 0 else 0.0)
    tr = true_range(high, low, close)
    # Wilder sums, seeded with the first n values after the first bar
    trs = sum(tr[1:n + 1])
    ps, ms = sum(plus[1:n + 1]), sum(minus[1:n + 1])
    dx = []
    for i in range(n, m):
        if i > n:
            trs = trs - trs / n + tr[i]
            ps = ps - ps / n + plus[i]
            ms = ms - ms / n + minus[i]
        pdi = 100 * ps / trs if trs else 0
        mdi = 100 * ms / trs if trs else 0
        dx.append(100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi else 0)
    a = sum(dx[:n]) / n
    out[2 * n - 1] = a
    for j in range(n, len(dx)):
        a = (a * (n - 1) + dx[j]) / n
        out[n + j] = a
    return out


def bollinger(close, n=20, k=2.0):
    """Middle, upper and lower band, plus the band width as a share of the middle line."""
    m = len(close)
    mid, up, lo, width = [None] * m, [None] * m, [None] * m, [None] * m
    s = s2 = 0.0
    for i in range(m):
        s += close[i]
        s2 += close[i] * close[i]
        if i >= n:
            s -= close[i - n]
            s2 -= close[i - n] * close[i - n]
        if i >= n - 1:
            mean = s / n
            sd = max(s2 / n - mean * mean, 0.0) ** 0.5
            mid[i], up[i], lo[i] = mean, mean + k * sd, mean - k * sd
            width[i] = (2 * k * sd) / mean if mean else 0.0
    return mid, up, lo, width


def rolling_min(vals, n):
    """Lowest value over the last n entries, ignoring None."""
    out, dq = [], deque()
    for i, v in enumerate(vals):
        if v is not None:
            while dq and dq[-1][1] >= v:
                dq.pop()
            dq.append((i, v))
        while dq and dq[0][0] <= i - n:
            dq.popleft()
        out.append(dq[0][1] if dq else None)
    return out


class Series:
    """One symbol's candles with every indicator the strategies use, worked out once."""

    def __init__(self, candles, vol_days=10):
        cs = sorted(candles, key=lambda c: c[0])
        self.t = [c[0] for c in cs]
        self.o = [c[1] for c in cs]
        self.h = [c[2] for c in cs]
        self.l = [c[3] for c in cs]
        self.c = [c[4] for c in cs]
        self.v = [c[5] or 0 for c in cs]
        self.n = len(cs)
        self.has_volume = sum(self.v) > 0
        # day boundaries
        self.day = [clock.from_ts(t).date() for t in self.t]
        self.slot = [slot(t) for t in self.t]
        self.starts = {}                      # date -> index of the day's first bar
        self.ends = {}                        # date -> index of the day's last bar
        for i, d in enumerate(self.day):
            self.starts.setdefault(d, i)
            self.ends[d] = i
        self.days = sorted(self.starts)
        # indicators
        self.ema9, self.ema21 = ema(self.c, 9), ema(self.c, 21)
        self.ema20, self.ema50 = ema(self.c, 20), ema(self.c, 50)
        self.rsi = rsi(self.c, 14)
        self.atr = atr(self.h, self.l, self.c, 14)
        self.adx = adx(self.h, self.l, self.c, 14)
        self.bb_mid, self.bb_up, self.bb_lo, self.bb_w = bollinger(self.c, 20, 2.0)
        low100 = rolling_min(self.bb_w, 100)
        self.squeeze = [(w is not None and m is not None and w <= m * 1.15) for w, m in zip(self.bb_w, low100)]
        self._vwap()
        self._relative_volume(vol_days)
        self._gap_info()

    def _vwap(self):
        """Average price of the day so far. Indices carry no volume, so they get a plain average."""
        self.vwap = [None] * self.n
        for d in self.days:
            a, b = self.starts[d], self.ends[d]
            pv = vol = plain = 0.0
            for i in range(a, b + 1):
                tp = (self.h[i] + self.l[i] + self.c[i]) / 3
                pv += tp * self.v[i]
                vol += self.v[i]
                plain += tp
                self.vwap[i] = pv / vol if vol > 0 else plain / (i - a + 1)

    def _relative_volume(self, days_back):
        """Each bar's volume compared with the same time of day over the previous sessions."""
        self.rvol = [None] * self.n
        self.first15_ratio = {}
        if not self.has_volume:
            return
        by_slot = {}                          # slot -> list of volumes from earlier days
        first15 = []
        for d in self.days:
            a, b = self.starts[d], self.ends[d]
            for i in range(a, b + 1):
                hist = by_slot.get(self.slot[i], [])[-days_back:]
                if len(hist) >= 3:
                    avg = sum(hist) / len(hist)
                    self.rvol[i] = self.v[i] / avg if avg > 0 else None
            f15 = sum(self.v[i] for i in range(a, b + 1) if self.slot[i] < 3)
            past = first15[-days_back:]
            if len(past) >= 3 and sum(past) > 0:
                self.first15_ratio[d] = f15 / (sum(past) / len(past))
            first15.append(f15)
            for i in range(a, b + 1):
                by_slot.setdefault(self.slot[i], []).append(self.v[i])

    def _gap_info(self):
        self.prev_close = {}
        prev = None
        for d in self.days:
            self.prev_close[d] = prev
            prev = self.c[self.ends[d]]
        self.day_open = {d: self.o[self.starts[d]] for d in self.days}
