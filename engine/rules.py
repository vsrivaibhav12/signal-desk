"""Pure rulebook maths: 5-minute candles, VWAP and the opening range.

A candle is a list: [start_time_in_seconds, open, high, low, close, volume].
"""


def five_minute(candles, now_ts):
    """Group 1-minute candles into completed 5-minute candles."""
    out = {}
    for t, o, h, l, c, v in candles:
        k = t - t % 300
        b = out.get(k)
        if b is None:
            out[k] = [k, o, h, l, c, v]
        else:
            b[2] = max(b[2], h)
            b[3] = min(b[3], l)
            b[4] = c
            b[5] += v
    return [b for k, b in sorted(out.items()) if k + 300 <= now_ts]


def vwap(candles, upto_ts=None):
    """Volume-weighted average price from the session start.

    Falls back to a plain average of typical prices when there is no volume.
    """
    pv = vol = plain = n = 0
    for t, o, h, l, c, v in candles:
        if upto_ts is not None and t >= upto_ts:
            break
        tp = (h + l + c) / 3
        pv += tp * v
        vol += v
        plain += tp
        n += 1
    if n == 0:
        return None
    return pv / vol if vol > 0 else plain / n


def last_close(candles, upto_ts=None):
    last = None
    for c in candles:
        if upto_ts is not None and c[0] >= upto_ts:
            break
        last = c[4]
    return last


def opening_range(candles, start_ts, end_ts):
    inside = [c for c in candles if start_ts <= c[0] < end_ts]
    if not inside:
        return None
    hi = max(c[2] for c in inside)
    lo = min(c[3] for c in inside)
    return {"high": hi, "low": lo, "mid": (hi + lo) / 2,
            "pct": (hi - lo) / lo * 100 if lo else 0}
