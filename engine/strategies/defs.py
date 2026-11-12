"""The ten strategies.

Six come from the Flattrade article on common intraday strategies in Indian markets
(momentum, breakout, reversal, VWAP, gap and go, opening range breakout). Four are the
Bollinger and EMA setups, each switched on only in the market condition it is built for.

Every strategy is a pair of small rules, written on 5-minute candles:
  scan(series, i, params)   looks at candle i (just closed) and says whether to enter
  EXITS[name](series, i, trade)  an extra exit that is checked on each close after entry
Nothing here looks at a candle later than i, so the same code is safe for live use and for tests.
"""
from .indicators import hhmm_to_min


def n2(x, d=2):
    s = f"{x:,.{d}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


# ── shared helpers ───────────────────────────────────────────
def _a(S, i):
    return S.starts[S.day[i]]


def _sig(d, stop, why, regime, target=None, target_r=None, exit=None, lines=None, box=None):
    """lines: [(label, price)] horizontal levels for the chart. box: a shaded range {i0, i1, lo, hi, label}."""
    return {"dir": d, "stop": stop, "target": target, "target_r": target_r,
            "why": why, "regime": regime, "exit": exit, "lines": lines or [], "box": box}


def _vol_ok(S, i, need):
    """Volume check. Indices carry no volume, so for them the check is skipped."""
    if not S.has_volume:
        return True, None
    rv = S.rvol[i]
    if rv is None or rv < need:
        return False, None
    return True, f"Volume on this candle was {rv:.1f}x what it usually is at this time of day."


def _word(d):
    return "above" if d == 1 else "below"


# ── 1. Momentum (article) ────────────────────────────────────
def scan_momentum(S, i, p):
    a, N = _a(S, i), p["lookback"]
    if i - a < N or S.rsi[i] is None:
        return None
    c, r, vw = S.c[i], S.rsi[i], S.vwap[i]
    hi, lo = max(S.h[i - N:i]), min(S.l[i - N:i])
    for d in (1, -1):
        if d == 1 and not (c > hi and r >= p["rsi"] and c > vw):
            continue
        if d == -1 and not (c < lo and r <= 100 - p["rsi"] and c < vw):
            continue
        ok, vtxt = _vol_ok(S, i, p["min_rvol"])
        if not ok:
            continue
        why = [f"Price closed at {n2(c)}, {_word(d)} the {'high' if d == 1 else 'low'} of the previous {N} candles "
               f"({n2(hi if d == 1 else lo)}), a fresh burst of momentum.",
               f"RSI is {r:.0f}, {'above' if d == 1 else 'below'} {p['rsi'] if d == 1 else 100 - p['rsi']}, so the move has strength behind it.",
               f"Price is {_word(d)} the day's average price line (VWAP {n2(vw)}).",
               vtxt or "This is an index, which has no volume, so the volume check does not apply."]
        return _sig(d, c * (1 - d * p["stop_pct"] / 100), why,
                    f"Early-session momentum, RSI {r:.0f}", target_r=p["target_r"], exit="momentum_fade",
                    lines=[(f"Previous {N}-candle {'high' if d == 1 else 'low'}", hi if d == 1 else lo)])
    return None


# ── 2. Breakout (article) ────────────────────────────────────
def _group(S, i, m, a):
    e = i - 3 * m
    s = e - 2
    if s < a or S.slot[s] % 3 != 0 or S.slot[e] - S.slot[s] != 2:
        return None
    return max(S.h[s:e + 1]), min(S.l[s:e + 1]), S.c[e], sum(S.v[s:e + 1])


def scan_breakout(S, i, p):
    if S.slot[i] % 3 != 2:                      # act only when a 15-minute candle completes
        return None
    a = _a(S, i)
    cur = _group(S, i, 0, a)
    prior = [_group(S, i, m, a) for m in range(1, p["range_bars"] + 1)]
    if cur is None or any(g is None for g in prior):
        return None
    H, L = max(g[0] for g in prior), min(g[1] for g in prior)
    width = H - L
    if L <= 0 or width / L * 100 > p["max_range_pct"] or width <= 0:
        return None
    c = cur[2]
    for d in (1, -1):
        level = H if d == 1 else L
        if d * (c - level) <= 0 or abs(c - level) >= width:
            continue
        if S.has_volume:
            avg = sum(g[3] for g in prior) / len(prior)
            if avg <= 0 or cur[3] < p["min_vol"] * avg:
                continue
            vtxt = f"The breakout candle traded {cur[3] / avg:.1f}x the volume of the candles in the range."
        else:
            vtxt = "This is an index, which has no volume, so the volume check does not apply."
        mid = (H + L) / 2
        target = level + d * p["target_x"] * width
        why = [f"A 15-minute candle closed at {n2(c)}, {_word(d)} a tight range that held for {p['range_bars']} candles "
               f"({n2(L)} to {n2(H)}, {width / L * 100:.2f}% wide).", vtxt,
               f"Stop sits at the middle of the range ({n2(mid)}); target is {p['target_x']}x the range height "
               f"beyond the breakout ({n2(target)})."]
        return _sig(d, mid, why, f"Tight range ({width / L * 100:.2f}%) then expansion", target=target,
                    lines=[("Range high", H), ("Range low", L), ("Range middle (stop)", mid)],
                    box={"i0": i - 3 * p["range_bars"] - 2, "i1": i - 3, "lo": L, "hi": H, "label": "Tight range"})
    return None


# ── 3. Reversal (article) ────────────────────────────────────
def scan_reversal(S, i, p):
    a = _a(S, i)
    if i - a < 6 or i < 3 or S.rsi[i] is None:
        return None
    o, h, l, c = S.o[i], S.h[i], S.l[i], S.c[i]
    rng = h - l
    mv = (c / S.day_open[S.day[i]] - 1) * 100
    vw = S.vwap[i]
    if rng <= 0:
        return None
    recent = [x for x in S.rsi[i - 2:i + 1] if x is not None]
    if not recent:
        return None
    po, pc = S.o[i - 1], S.c[i - 1]
    for d in (1, -1):                           # d = the direction we trade, against the move
        if d == 1:
            ext = mv <= -p["min_move"] and min(recent) <= 100 - p["rsi"]
            wick = (min(o, c) - l) >= 0.5 * rng and (c - l) >= 0.6 * rng
            engulf = pc < po and c > o and c >= po and o <= pc
            stop = min(S.l[i - 2:i + 1]) * (1 - p["stop_pad"] / 100)
            pattern = "a long lower wick (hammer)" if wick else "a bullish engulfing candle"
        else:
            ext = mv >= p["min_move"] and max(recent) >= p["rsi"]
            wick = (h - max(o, c)) >= 0.5 * rng and (h - c) >= 0.6 * rng
            engulf = pc > po and c < o and c <= po and o >= pc
            stop = max(S.h[i - 2:i + 1]) * (1 + p["stop_pad"] / 100)
            pattern = "a long upper wick (shooting star)" if wick else "a bearish engulfing candle"
        if not (ext and (wick or engulf)):
            continue
        risk, reward = d * (c - stop), d * (vw - c)
        if risk <= 0 or reward < p["min_rr"] * risk:
            continue
        why = [f"Price is {abs(mv):.1f}% {'down' if d == 1 else 'up'} from today's open and RSI reached "
               f"{min(recent) if d == 1 else max(recent):.0f}, an exhausted move.",
               f"The candle just closed with {pattern}, a sign the other side is stepping in.",
               f"Target is the day's average price line (VWAP {n2(vw)}), a pull back toward the middle of the day."]
        return _sig(d, stop, why, f"Over-extended {abs(mv):.1f}% move, fading it", target=vw,
                    lines=[("Today's open", S.day_open[S.day[i]])])
    return None


# ── 4. VWAP pullback (article) ───────────────────────────────
def scan_vwap(S, i, p):
    a = _a(S, i)
    if i - a < 12:
        return None
    o, h, l, c, vw = S.o[i], S.h[i], S.l[i], S.c[i], S.vwap[i]
    for d in (1, -1):
        side = [(S.c[j] - S.vwap[j]) * d > 0 for j in range(i - 6, i)]
        if sum(side) < 5:
            continue
        if d == 1:
            far = max(S.h[i - 12:i]) >= vw * (1 + p["ext_pct"] / 100)
            touch = l <= vw * (1 + p["touch_pct"] / 100) and c > vw and c > o
            stop = min(l, vw) * (1 - 0.0005)
        else:
            far = min(S.l[i - 12:i]) <= vw * (1 - p["ext_pct"] / 100)
            touch = h >= vw * (1 - p["touch_pct"] / 100) and c < vw and c < o
            stop = max(h, vw) * (1 + 0.0005)
        if not (far and touch):
            continue
        why = [f"Price has stayed {_word(d)} the day's average price line (VWAP {n2(vw)}) for most of the last 6 candles, "
               f"so the day's trend is {'up' if d == 1 else 'down'}.",
               f"It pulled back to the line (candle {'low' if d == 1 else 'high'} {n2(l if d == 1 else h)}) and "
               f"bounced: the candle closed at {n2(c)}, {_word(d)} the line and in the trend's direction."]
        return _sig(d, stop, why, "Trending day, buying the dip to VWAP" if d == 1 else "Trending day, selling the rally to VWAP",
                    target_r=p["target_r"])
    return None


# ── 5. Gap and go (article) ──────────────────────────────────
def scan_gap(S, i, p):
    day = S.day[i]
    a, pc = S.starts[day], S.prev_close.get(day)
    if pc is None or i - a < 3 or S.slot[a] != 0 or S.slot[a + 2] != 2:
        return None
    op = S.day_open[day]
    gap = (op / pc - 1) * 100
    if abs(gap) < p["min_gap"]:
        return None
    if S.has_volume:
        ratio = S.first15_ratio.get(day)
        if ratio is None or ratio < p["min_vol"]:
            return None
        vtxt = f"Volume in the first 15 minutes was {ratio:.1f}x normal, so the gap has real buying/selling behind it."
    else:
        vtxt = "This is an index, which has no volume, so the volume check does not apply."
    d = 1 if gap > 0 else -1
    fh, fl = max(S.h[a:a + 3]), min(S.l[a:a + 3])
    fc = S.c[a + 2]
    held = (fl >= pc + 0.5 * (op - pc)) if d == 1 else (fh <= pc + 0.5 * (op - pc))
    if not held or d * (fc - op) < 0:
        return None
    c = S.c[i]
    level = fh if d == 1 else fl
    if d * (c - level) <= 0 or any(d * (S.c[j] - level) > 0 for j in range(a + 3, i)):
        return None
    why = [f"The stock opened {abs(gap):.1f}% {'above' if d == 1 else 'below'} yesterday's close ({n2(pc)}) and the gap "
           f"held: less than half of it was filled in the first 15 minutes.", vtxt,
           f"After waiting 15 minutes, price closed at {n2(c)}, {_word(d)} the first 15-minute {'high' if d == 1 else 'low'} "
           f"({n2(level)}), so the gap is continuing."]
    return _sig(d, (fh + fl) / 2, why, f"Gap {'up' if d == 1 else 'down'} of {abs(gap):.1f}% that is holding",
                target_r=p["target_r"],
                lines=[("Yesterday's close", pc), ("Today's open", op), ("First 15-min " + ("high" if d == 1 else "low"), level)],
                box={"i0": a, "i1": a + 2, "lo": fl, "hi": fh, "label": "First 15 minutes"})


# ── 6. Opening range breakout (article) ──────────────────────
def scan_orb(S, i, p):
    day = S.day[i]
    a = S.starts[day]
    if i - a < 3 or S.slot[a] != 0 or S.slot[a + 2] != 2:
        return None
    fh, fl = max(S.h[a:a + 3]), min(S.l[a:a + 3])
    width = fh - fl
    if fl <= 0 or width <= 0 or width / fl * 100 > p["max_range_pct"]:
        return None
    c = S.c[i]
    for d in (1, -1):
        level = fh if d == 1 else fl
        if d * (c - level) <= 0 or abs(c - level) >= width:
            continue
        if any(d * (S.c[j] - level) > 0 for j in range(a + 3, i)):
            continue                              # an earlier candle already broke out this way
        target = level + d * p["target_x"] * width
        why = [f"The first 15 minutes (9:15 to 9:30) set a range of {n2(fl)} to {n2(fh)} ({width / fl * 100:.2f}% wide).",
               f"A 5-minute candle closed at {n2(c)}, {_word(d)} the range {'high' if d == 1 else 'low'}.",
               f"Stop is the other end of the range ({n2(fl if d == 1 else fh)}); target is {p['target_x']}x the range "
               f"height beyond the breakout ({n2(target)})."]
        return _sig(d, fl if d == 1 else fh, why, "Opening range breakout", target=target,
                    lines=[("Opening range high", fh), ("Opening range low", fl)],
                    box={"i0": a, "i1": a + 2, "lo": fl, "hi": fh, "label": "Opening range (9:15 to 9:30)"})
    return None


def _daily_atr(S, day, n=14):
    """Average true range of the previous sessions (up to n), from the candles. None if fewer than five sessions are known."""
    prior = [d for d in S.days if d < day][-n:]
    trs = []
    for d in prior:
        a, b = S.starts[d], S.ends[d]
        hi, lo, pc = max(S.h[a:b + 1]), min(S.l[a:b + 1]), S.prev_close.get(d)
        trs.append(max(hi - lo, abs(hi - pc), abs(lo - pc)) if pc else hi - lo)
    return sum(trs) / len(trs) if len(trs) >= 5 else None


# ── The 5-minute opening range breakout of Zarattini, Barbon and Aziz (2024), as far as our simulator can follow it ──
def scan_orb5(S, i, p):
    """The first 5-minute candle sets the direction (green = long, red = short) and the range. The first candle to close beyond its
    high (or low) in that direction is the signal. The stop is a fraction of the daily ATR (the paper uses 10%); the trade is held to
    the end of the day. The paper's stop order fills at the break; here the fill is at the next candle's open, which is later and worse."""
    day = S.day[i]
    a = S.starts[day]
    if i - a < 1 or S.slot[a] != 0:
        return None
    o, h, l, c0 = S.o[a], S.h[a], S.l[a], S.c[a]
    if c0 == o or h <= l:
        return None
    d = 1 if c0 > o else -1
    level = h if d == 1 else l
    c = S.c[i]
    if d * (c - level) <= 0 or any(d * (S.c[j] - level) > 0 for j in range(a + 1, i)):
        return None
    atr = _daily_atr(S, day)
    if not atr:
        return None
    stop = c - d * p["stop_atr"] * atr
    why = [f"The first 5-minute candle ({'green' if d == 1 else 'red'}) set a range of {n2(l)} to {n2(h)}.",
           f"A candle closed at {n2(c)}, {_word(d)} the first candle's {'high' if d == 1 else 'low'}, in the direction of that candle.",
           f"Stop is {p['stop_atr'] * 100:.0f}% of the daily average range ({n2(atr)}) from the entry; the trade is held to the end of the day."]
    return _sig(d, stop, why, "Opening range breakout (5-minute)",
                lines=[("First candle high", h), ("First candle low", l)],
                box={"i0": a, "i1": a, "lo": l, "hi": h, "label": "First 5 minutes"})


# ── Opening drive: get in early on a thrust, instead of waiting for the breakout at the high ──
def scan_drive(S, i, p):
    day = S.day[i]
    a = S.starts[day]
    if i - a > 2 or S.slot[a] != 0:
        return None
    o, c, h, l = S.day_open[day], S.c[i], S.h[i], S.l[i]
    for d in (1, -1):
        move = d * (c - o) / o * 100
        if move < p["min_move"]:
            continue
        if S.has_volume:
            cr = S.cumratio[i]
            if cr is None or cr < p["min_vol"]:
                continue
            vtxt = f"Volume so far today is {cr:.1f}x what it normally is by this time, so this is real buying or selling, not a drift."
        else:
            vtxt = "This is an index, which has no volume, so the volume check does not apply."
        rng = h - l
        pos = ((c - l) if d == 1 else (h - c)) / rng if rng > 0 else 0
        if pos < p["close_pos"] or d * (c - S.vwap[i]) <= 0:
            continue
        prior = S.c[a:i]
        if prior and d * (c - (max(prior) if d == 1 else min(prior))) < 0:
            continue                                   # not still pushing to a new extreme
        extreme = min(S.l[a:i + 1]) if d == 1 else max(S.h[a:i + 1])
        stop = c - d * abs(c - extreme) * p["stop_frac"]
        why = [f"The stock is {move:.1f}% {'up' if d == 1 else 'down'} from the open after only {(i - a + 1) * 5} minutes: a thrust.", vtxt,
               f"The candle closed in the {'top' if d == 1 else 'bottom'} {int((1 - p['close_pos']) * 100)}% of its range and {_word(d)} VWAP ({n2(S.vwap[i])}), so buyers/sellers kept control to the close.",
               f"Entering now, near the start of the move, instead of waiting for a breakout that would come at the high. Stop is {n2(stop)}; the trade is held while price keeps making progress and ends when a candle closes through the low of the last {p['trail']} candles."]
        return _sig(d, stop, why, f"Opening thrust of {move:.1f}% on {S.cumratio[i]:.1f}x volume" if S.has_volume else f"Opening thrust of {move:.1f}%",
                    exit="drive_trail", lines=[("Today's open", o), ("VWAP", S.vwap[i])])
    return None


# ── 7. Bollinger bounce ──────────────────────────────────────
def scan_bb_bounce(S, i, p):
    if i < 120 or S.bb_lo[i] is None or S.adx[i] is None or S.atr[i] is None:
        return None
    c, o = S.c[i], S.o[i]
    if S.adx[i] >= p["max_adx"]:
        return None
    for d in (1, -1):
        edge = S.bb_lo if d == 1 else S.bb_up
        was_out = d * (edge[i - 1] - S.c[i - 1]) > 0               # last close was outside the band
        back_in = d * (c - edge[i]) > 0 and d * (c - o) > 0         # this one is back inside and in our direction
        walking = all(d * (edge[j] - S.c[j]) > 0 for j in (i - 2, i - 3))
        if not (was_out and back_in) or walking:
            continue
        extreme = min(S.l[i], S.l[i - 1]) if d == 1 else max(S.h[i], S.h[i - 1])
        stop = extreme - d * p["stop_atr"] * S.atr[i]
        target = S.bb_mid[i]
        risk = d * (c - stop)
        if risk <= 0 or d * (target - c) < p["min_rr"] * risk:
            continue
        why = [f"The market is ranging, not trending: ADX is {S.adx[i]:.0f}, below {p['max_adx']}.",
               f"The previous candle closed {'under the lower' if d == 1 else 'over the upper'} Bollinger band and this one "
               f"closed back inside it at {n2(c)}, a stretched move snapping back.",
               f"Target is the middle band ({n2(target)}); stop is beyond the extreme ({n2(stop)})."]
        return _sig(d, stop, why, f"Ranging market (ADX {S.adx[i]:.0f}), fading the band edge", target=target)
    return None


# ── 8. Bollinger squeeze breakout ────────────────────────────
def scan_bb_squeeze(S, i, p):
    if i < 120 or S.bb_up[i] is None:
        return None
    if sum(S.squeeze[i - 6:i]) < p["min_squeeze"]:
        return None
    c = S.c[i]
    for d in (1, -1):
        edge, prev_edge = (S.bb_up, S.bb_up) if d == 1 else (S.bb_lo, S.bb_lo)
        if not (d * (c - edge[i]) > 0 and d * (S.c[i - 1] - prev_edge[i - 1]) <= 0):
            continue
        ok, vtxt = _vol_ok(S, i, p["min_rvol"])
        if not ok:
            continue
        why = [f"The bands had been squeezed tight for {sum(S.squeeze[i - 6:i])} of the last 6 candles, "
               f"a quiet period that usually ends in a big move.",
               f"Price just closed at {n2(c)}, outside the {'upper' if d == 1 else 'lower'} band: the squeeze is breaking "
               f"{'up' if d == 1 else 'down'}.",
               vtxt or "This is an index, which has no volume, so the volume check does not apply.",
               f"Stop is the middle band ({n2(S.bb_mid[i])}); target is {p['target_r']}x the risk."]
        return _sig(d, S.bb_mid[i], why, "Low-volatility squeeze breaking out", target_r=p["target_r"])
    return None


# ── 9. EMA 9/21 crossover ────────────────────────────────────
def scan_ema_cross(S, i, p):
    if i < 120 or S.adx[i] is None or S.adx[i - 3] is None or S.atr[i] is None:
        return None
    c = S.c[i]
    for d in (1, -1):
        crossed = d * (S.ema9[i] - S.ema21[i]) > 0 and d * (S.ema9[i - 1] - S.ema21[i - 1]) <= 0
        if not crossed or d * (c - S.ema21[i]) <= 0 or d * (S.ema21[i] - S.ema21[i - 3]) < 0:
            continue
        if S.adx[i] < p["min_adx"] or S.adx[i] <= S.adx[i - 3]:
            continue
        why = [f"The fast 9-period average crossed {_word(d)} the slow 21-period average, the start of a possible new trend.",
               f"The trend is strengthening: ADX is {S.adx[i]:.0f} and rising (it was {S.adx[i - 3]:.0f} three candles ago), "
               f"so this is less likely to be a sideways whipsaw.",
               f"Stop is {p['stop_atr']}x the typical candle range from entry ({n2(c - d * p['stop_atr'] * S.atr[i])}). "
               f"There is no fixed target: the trade runs until the averages cross back."]
        return _sig(d, c - d * p["stop_atr"] * S.atr[i], why, f"New trend forming (ADX {S.adx[i]:.0f}, rising)", exit="ema_cross_back")
    return None


# ── 10. EMA trend pullback ───────────────────────────────────
def scan_ema_pullback(S, i, p):
    if i < 120 or S.adx[i] is None or S.atr[i] is None:
        return None
    o, h, l, c = S.o[i], S.h[i], S.l[i], S.c[i]
    if S.adx[i] < p["min_adx"]:
        return None
    for d in (1, -1):
        trend = d * (S.ema20[i] - S.ema50[i]) > 0 and d * (S.ema50[i] - S.ema50[i - 6]) > 0 and d * (c - S.ema50[i]) > 0
        if not trend:
            continue
        touch = (l <= S.ema20[i] * (1 + p["touch_pct"] / 100) if d == 1 else h >= S.ema20[i] * (1 - p["touch_pct"] / 100))
        held = d * (c - S.ema20[i]) > 0 and d * (c - o) > 0
        before = sum(1 for j in range(i - 6, i) if d * (S.c[j] - S.ema20[j]) > 0)
        if not (touch and held and before >= 3):
            continue
        stop = (l if d == 1 else h) - d * p["stop_atr"] * S.atr[i]
        why = [f"A strong trend is in place: the 20-period average is {_word(d)} the 50-period and rising/falling, "
               f"and ADX is {S.adx[i]:.0f}.",
               f"Price pulled back to the 20-period average (candle {'low' if d == 1 else 'high'} {n2(l if d == 1 else h)}) and "
               f"bounced: it closed at {n2(c)}, back {_word(d)} the average and in the trend's direction.",
               f"Stop is {p['stop_atr']}x the typical candle range past the pullback extreme ({n2(stop)}); target is "
               f"{p['target_r']}x the risk; the trade also ends if price closes through the 50-period average."]
        return _sig(d, stop, why, f"Established trend (ADX {S.adx[i]:.0f}), buying the pullback" if d == 1
                    else f"Established trend (ADX {S.adx[i]:.0f}), selling the rally", target_r=p["target_r"], exit="trend_fail")
    return None


# ── extra exits, checked at each close after entry ───────────
def exit_momentum_fade(S, i, tr):
    r = S.rsi[i]
    if r is not None and ((tr["dir"] == 1 and r < 50) or (tr["dir"] == -1 and r > 50)):
        return f"Momentum faded: RSI fell back to {r:.0f}"
    return None


def exit_ema_cross_back(S, i, tr):
    if tr["dir"] * (S.ema9[i] - S.ema21[i]) < 0:
        return "The 9-period average crossed back through the 21-period average, so the new trend is over"
    return None


def exit_trend_fail(S, i, tr):
    if tr["dir"] * (S.c[i] - S.ema50[i]) < 0:
        return "Price closed through the 50-period average, so the trend has failed"
    return None


def exit_drive_trail(S, i, tr):
    n = tr.get("trail", 3)
    if n <= 0 or i - n < 0:
        return None
    if tr["dir"] == 1 and S.c[i] < min(S.l[i - n:i]):
        return f"Price closed below the low of the last {n} candles, so the thrust is over"
    if tr["dir"] == -1 and S.c[i] > max(S.h[i - n:i]):
        return f"Price closed above the high of the last {n} candles, so the thrust is over"
    return None


EXITS = {"momentum_fade": exit_momentum_fade, "ema_cross_back": exit_ema_cross_back, "trend_fail": exit_trend_fail,
         "drive_trail": exit_drive_trail}


# ── the catalogue ────────────────────────────────────────────
# Windows are the times a signal candle may CLOSE in. stock/index hold the numbers for each kind of market.
ALL_STRATEGIES = [
    {"id": "drive", "name": "Opening drive", "source": "Lesson from a missed entry", "scan": scan_drive,
     "window": ["09:20", "09:30"],
     "best_for": "A stock that opens and runs hard in the first 5 to 15 minutes on heavy volume, when waiting for a breakout means buying the high.",
     "risk": "A thrust that fades after the open, or a gap-and-fail. The stop is wide because it sits at the base of the move.",
     "rules": ["Only in the first 15 minutes (candles closing 9:20 to 9:30).",
               "The stock must already be up (or down) 1.5% or more from the open, on at least 3x its usual volume by this time.",
               "The candle must close in the top 30% of its range (bottom 30% for a sell), on the right side of VWAP, still making a new high (or low) for the day.",
               "Enter at that candle's close, near the start of the move. Stop at the base of the move (the low of the day so far for a buy).",
               "No fixed target: the trade ends when a candle closes below the low of the last 3 candles (above the high for a sell), or at 3:15 pm."],
     "stock": {"min_move": 1.5, "min_vol": 3.0, "close_pos": 0.7, "stop_frac": 1.0, "trail": 3},
     "index": {"min_move": 0.5, "min_vol": 0.0, "close_pos": 0.7, "stop_frac": 1.0, "trail": 3}},
    {"id": "momentum", "name": "Momentum", "source": "Flattrade article", "scan": scan_momentum,
     "window": ["09:35", "10:30"],
     "best_for": "The first hour of the session, when news and volume drive fast moves.",
     "risk": "Chasing a move that has already run out of steam.",
     "rules": ["Wait for a candle to close above the highest high (or below the lowest low) of the previous 4 candles.",
               "RSI must show strength: 60 or more to buy, 40 or less to sell.",
               "Price must be on the same side of VWAP, and volume at least 1.5x its usual for that time of day.",
               "Stop 0.75% from entry (0.3% on indices), target 1.5x the risk.",
               "Leave early if RSI falls back through 50, which means the momentum has faded."],
     "stock": {"lookback": 4, "rsi": 60, "min_rvol": 1.5, "stop_pct": 0.75, "target_r": 1.5},
     "index": {"lookback": 4, "rsi": 60, "min_rvol": 1.5, "stop_pct": 0.3, "target_r": 1.5}},
    {"id": "breakout", "name": "Breakout", "source": "Flattrade article", "scan": scan_breakout,
     "window": ["10:45", "14:00"],
     "best_for": "A stock that has gone quiet in a tight range and then breaks out on heavy volume.",
     "risk": "False breakouts that snap straight back into the range.",
     "rules": ["Find a tight range: the last six 15-minute candles together span no more than 0.9% (0.45% on indices).",
               "A 15-minute candle must close outside the range with at least 2x the volume of the range candles.",
               "Stop at the middle of the range, target 1.5x the range height beyond the breakout.",
               "Skip if the move has already gone a full range height past the level."],
     "stock": {"range_bars": 6, "max_range_pct": 0.9, "min_vol": 2.0, "target_x": 1.5},
     "index": {"range_bars": 6, "max_range_pct": 0.45, "min_vol": 2.0, "target_x": 1.5}},
    {"id": "reversal", "name": "Reversal", "source": "Flattrade article", "scan": scan_reversal,
     "window": ["09:50", "14:30"],
     "best_for": "A sharp over-extended move that shows signs of running out of fuel.",
     "risk": "Stepping in front of a move that keeps going. The article rates this the second hardest strategy.",
     "rules": ["The stock must be at least 2% (0.8% for indices) away from today's open, in one direction.",
               "RSI must have reached an extreme (25 or less, or 75 or more) in the last 3 candles.",
               "The latest candle must show rejection: a hammer or shooting star wick, or an engulfing candle.",
               "Trade against the move. Target is VWAP, stop is just past the extreme; skip unless the reward is at least 1.2x the risk."],
     "stock": {"min_move": 2.0, "rsi": 75, "stop_pad": 0.1, "min_rr": 1.2},
     "index": {"min_move": 0.8, "rsi": 75, "stop_pad": 0.05, "min_rr": 1.2}},
    {"id": "vwap", "name": "VWAP pullback", "source": "Flattrade article", "scan": scan_vwap,
     "window": ["10:00", "14:15"],
     "best_for": "A steady trending day where price keeps returning to the day's average price line.",
     "risk": "Choppy days when price keeps crossing VWAP back and forth.",
     "rules": ["Price must have stayed on one side of VWAP for at least 5 of the previous 6 candles.",
               "It must have moved at least 0.3% (0.15% on indices) away from VWAP in the last hour.",
               "Then a candle pulls back to touch VWAP and closes back on the trend side, in the trend's direction.",
               "Stop just beyond the pullback extreme, target 2x the risk."],
     "stock": {"ext_pct": 0.3, "touch_pct": 0.1, "target_r": 2.0},
     "index": {"ext_pct": 0.15, "touch_pct": 0.05, "target_r": 2.0}},
    {"id": "gap", "name": "Gap and go", "source": "Flattrade article", "scan": scan_gap,
     "window": ["09:35", "10:30"],
     "best_for": "Stocks that open far from yesterday's close on news and keep going.",
     "risk": "The gap fills and price drifts back to yesterday's close.",
     "rules": ["The stock must open at least 1% (0.5% for indices) away from yesterday's close.",
               "Volume in the first 15 minutes must be at least 2x normal.",
               "The gap must hold: less than half of it may be filled in the first 15 minutes.",
               "After the 15-minute wait, enter when a candle closes beyond the first 15-minute high (gap up) or low (gap down).",
               "Stop at the middle of the first 15-minute range, target 2x the risk."],
     "stock": {"min_gap": 1.0, "min_vol": 2.0, "target_r": 2.0},
     "index": {"min_gap": 0.5, "min_vol": 2.0, "target_r": 2.0}},
    {"id": "orb", "name": "Opening range breakout", "source": "Flattrade article", "scan": scan_orb,
     "window": ["09:35", "11:30"],
     "best_for": "Liquid large stocks and indices on days that trend from the open.",
     "risk": "Range-bound days where price pokes out of the range and falls back.",
     "rules": ["Mark the high and low of the first 15 minutes (9:15 to 9:30).",
               "Skip the day if that range is wider than 2.5% (0.6% on indices).",
               "Enter when a 5-minute candle closes outside the range, once per direction per day.",
               "Stop at the opposite end of the range, target 1.5x the range height beyond the breakout."],
     "stock": {"max_range_pct": 2.5, "target_x": 1.5},
     "index": {"max_range_pct": 0.6, "target_x": 1.5}},
    {"id": "orb5", "name": "Opening range breakout, 5-minute (research)", "source": "Zarattini, Barbon, Aziz (2024)", "scan": scan_orb5,
     "window": ["09:20", "11:30"],
     "best_for": "Research only. The published US version trades the 20 shares with the heaviest first-5-minute volume.",
     "risk": "Tight ATR-based stops: with Indian costs the stop is so close that costs can be a large part of the risk.",
     "rules": ["The first 5-minute candle (9:15 to 9:20) sets the direction: green is long, red is short.",
               "Enter when a candle closes beyond that candle's high (long) or low (short), once per day.",
               "Stop at 10% of the daily average range from the entry. Hold to the end of the day."],
     "stock": {"stop_atr": 0.10}, "index": {"stop_atr": 0.10}},
    {"id": "bb_bounce", "name": "Bollinger bounce", "source": "Bollinger bands", "scan": scan_bb_bounce,
     "window": ["10:00", "14:30"],
     "best_for": "Ranging, sideways markets. The strategy switches itself off when the market is trending (ADX 22 or more).",
     "risk": "Band walking: in a strong trend price rides the band and the bounce never comes.",
     "rules": ["Only trade when ADX is below 22, meaning the market is not trending.",
               "The previous candle must have closed outside a Bollinger band (20 candles, 2 standard deviations) "
               "and this candle closes back inside it, in the bounce direction.",
               "Skip if price was already outside the band for the two candles before that (band walking).",
               "Target the middle band; stop half a typical candle range past the extreme. Skip unless reward is at least 1x risk."],
     "stock": {"max_adx": 22, "stop_atr": 0.5, "min_rr": 1.0},
     "index": {"max_adx": 22, "stop_atr": 0.5, "min_rr": 1.0}},
    {"id": "bb_squeeze", "name": "Bollinger squeeze breakout", "source": "Bollinger bands", "scan": scan_bb_squeeze,
     "window": ["10:00", "14:30"],
     "best_for": "Low-volatility consolidation just before a big expansion.",
     "risk": "Fake breakouts: the squeeze breaks one way, traps traders, and reverses.",
     "rules": ["The Bollinger bands must have been squeezed (at their narrowest in the last 100 candles) "
               "for at least 3 of the last 6 candles.",
               "Then the first candle to close outside a band, with volume at least 1.3x normal for that time.",
               "Stop at the middle band, target 2x the risk."],
     "stock": {"min_squeeze": 3, "min_rvol": 1.3, "target_r": 2.0},
     "index": {"min_squeeze": 3, "min_rvol": 1.3, "target_r": 2.0}},
    {"id": "ema_cross", "name": "EMA 9/21 crossover", "source": "Moving averages", "scan": scan_ema_cross,
     "window": ["09:50", "14:30"],
     "best_for": "The early phase of a new trend. It switches itself off in sideways markets using ADX.",
     "risk": "Whipsaws: frequent false crosses in choppy conditions.",
     "rules": ["The 9-period exponential average must cross the 21-period average, with price on the same side of the 21.",
               "ADX must be at least 18 and rising over the last 3 candles, so a trend is actually building.",
               "Stop 1.5x the typical candle range from entry. No fixed target: exit when the averages cross back."],
     "stock": {"min_adx": 18, "stop_atr": 1.5},
     "index": {"min_adx": 18, "stop_atr": 1.5}},
    {"id": "ema_pullback", "name": "EMA trend pullback", "source": "Moving averages", "scan": scan_ema_pullback,
     "window": ["10:15", "14:30"],
     "best_for": "Established strong trends, bullish or bearish. The strategy only works when ADX is 22 or more.",
     "risk": "Missing the trade if the trend is too strong to ever pull back.",
     "rules": ["The 20-period average must be above the 50-period (below it for sells), and the 50-period must be sloping the same way.",
               "ADX must be at least 22, and price on the trend side of the 50-period average.",
               "A candle dips to the 20-period average and closes back above it (below it for sells), in the trend's direction.",
               "Stop half a typical candle range past the dip extreme, target 2x the risk. Exit early if price closes through the 50-period."],
     "stock": {"min_adx": 22, "touch_pct": 0.05, "stop_atr": 0.5, "target_r": 2.0},
     "index": {"min_adx": 22, "touch_pct": 0.03, "stop_atr": 0.5, "target_r": 2.0}},
]

# Indicator lines drawn on each strategy's trade chart (names match Series attributes)
OVERLAYS = {"drive": ["vwap"], "momentum": ["vwap"], "reversal": ["vwap"], "vwap": ["vwap"], "gap": ["vwap"],
            "bb_bounce": ["bb_up", "bb_mid", "bb_lo"], "bb_squeeze": ["bb_up", "bb_mid", "bb_lo"],
            "ema_cross": ["ema9", "ema21"], "ema_pullback": ["ema20", "ema50"]}
for _s in ALL_STRATEGIES:
    _s["overlay"] = OVERLAYS.get(_s["id"], [])

# Only these run live and in the backtest. The rest stay defined (for research and the tests) but are not run.
# Chosen from research and our own 60-session test; see docs/STRATEGY_RESEARCH.md.
#   orb           5/15-minute opening range breakout, traded only on stocks that are "in play" (Zarattini, Barbon, Aziz 2024)
#   drive         enters the first strong thrust of a stock already moving on heavy volume (results gaps, news)
#   ema_pullback  buys or sells the first pullback in a strong, established trend
ACTIVE_IDS = ("orb", "drive", "ema_pullback")
STRATEGIES = [s for s in ALL_STRATEGIES if s["id"] in ACTIVE_IDS]
BY_ID = {s["id"]: s for s in ALL_STRATEGIES}


def window_minutes(strat):
    return hhmm_to_min(strat["window"][0]), hhmm_to_min(strat["window"][1])
