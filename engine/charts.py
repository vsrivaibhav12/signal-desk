"""Charts for the ideas that do not come from the strategy simulator: the rulebook's option trades and the options scanner's ideas.

Every chart has the same shape (contract/schemas/chart.schema.json), so one drawing routine serves every desk:
    k           candles, [open, high, low, close]
    e           the candle that triggered the idea ("el" names it: "Trigger", or "Now" for an idea still being watched)
    ep          the entry price on the chart's own axis
    st / tg     stop and target on the chart's axis, or None. "sc" is True when the stop acts on a candle CLOSE, not on a touch
    tr          the trailing exit line, {"label", "v"}, when there is one
    ln, bx, ov  other levels, a shaded range, indicator lines
    tn          one sentence saying what the chart is (for example that it shows the index, not the option)
Option ideas are drawn on the underlying index or share, because that is where their entry, stop and exit rules are decided.
"""
from __future__ import annotations

from datetime import datetime

from .strategies import sim
from .strategies.indicators import hhmm_to_min

VWAP_TRAIL = "VWAP: after the target is hit the rest trails along it, and a close back across it ends the trade"


def option_trade_chart(S, t):
    """The chart of one rulebook trade (an option, or a share) drawn on its underlying, or None if there are no candles for it yet."""
    if S is None or not S.n or not t.get("entry_min"):
        return None
    sig = [j for j in range(S.n) if S.t[j] + 300 <= t["entry_min"] + 59]
    if not sig:
        return None
    i_sig = sig[-1]
    closed = t["status"] == "CLOSED"
    i_end = S.n - 1
    if closed and t.get("exit_time"):
        x_ts = int(datetime.fromisoformat(t["exit_time"]).timestamp())
        ends = [j for j in range(S.n) if S.t[j] + 300 <= x_ts + 59]
        i_end = ends[-1] if ends else i_end
    a = S.starts[S.day[i_sig]]
    n_range = 3 if t.get("book") == "Stocks" else 6
    lines = [("Range high", t["range_high"]), ("Range low", t["range_low"])]
    box = {"i0": a, "i1": a + n_range - 1, "lo": t["range_low"], "hi": t["range_high"], "label": "Opening range"}
    xp = t.get("underlying_last") if closed else None
    ch = sim.build_chart(S, i_sig, max(i_end, i_sig), ["vwap"], lines, box, t["underlying_entry"], xp)
    ch["dir"], ch["st"], ch["tg"], ch["sc"] = t.get("dir"), t.get("stop_underlying"), t.get("target_underlying"), True
    ch["tr"] = {"label": VWAP_TRAIL, "v": ch["ov"].pop("vwap")}
    if t.get("kind") == "option":
        ch["tn"] = (f"This is the {t['name']} chart, not the option's. The option was bought near {t.get('entry_price')} and has its own stop at "
                    f"{t.get('stop_price')}; the levels below are on {t['name']}.")
    return ch


def scan_chart(S, read, market, today):
    """The chart behind one scanner idea: the index today, its opening range and VWAP, and where price is now. None if there is no range yet."""
    if S is None or not S.n or S.day[-1] != today or read.get("range_high") is None or read.get("range_low") is None:
        return None
    a = S.starts[today]
    need = max(1, (hhmm_to_min(market["range"][1]) - hhmm_to_min(market["range"][0])) // 5)
    i = S.n - 1
    rh, rl = read["range_high"], read["range_low"]
    box = {"i0": a, "i1": min(i, a + need - 1), "lo": rl, "hi": rh, "label": "Opening range"}
    ch = sim.build_chart(S, i, i, ["vwap"], [("Range high", rh), ("Range low", rl)], box, read["spot"], before=100)    # from the open, so the range is in view
    ch["dir"] = 1 if read.get("side") == "CE" else -1
    ch["st"], ch["tg"], ch["el"] = None, None, "Now"
    ch["tn"] = (f"This is the {market['name']} chart. The option's entry, stop and target are option prices (see the numbers). "
                f"The scanner only grades a {'call' if ch['dir'] == 1 else 'put'} TAKE once price has closed "
                f"{'above the range high' if ch['dir'] == 1 else 'below the range low'}.")
    return ch
