"""The swing desk: the research's rule, evaluated nightly, followed in a paper book.  Run:  python -m pytest tests/test_swing.py

The paper book is checked against engine.daily.simulate, the function the research itself used, night by night.
"""
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
pytest.importorskip("jsonschema")

from engine import clock, contract, daily as dl, swing  # noqa: E402
from engine.ledger import SWING_PREFIX, Ledger  # noqa: E402
from tests.test_strategies import weekdays  # noqa: E402

N_PRE, N_BASE = 289, 40
BREAKOUT = N_PRE + N_BASE                    # index of the breakout day
NORMAL_QTY = 2_000_000
END = date(2026, 12, 31)
FILLERS = 60


def brk_rows(post=(), gap_open=None, breakout_volume=3, n_pre=N_PRE):
    """Per-day (open, high, low, close, qty) of a share that trends up, rests in a tight base, then breaks out on volume.

    `post` lists the closes after the breakout day; `gap_open` makes the first of them open that many times the breakout close.
    """
    rows, c = [], 100.0
    for _ in range(n_pre):
        o, c = c, c * 1.005
        rows.append((o, c * 1.0005, o * 0.9995, c, NORMAL_QTY))
    plateau = c
    for j in range(N_BASE):
        o, c = c, plateau * (1.01 if j % 2 == 0 else 0.99)
        rows.append((o, max(o, c) * 1.001, min(o, c) * 0.999, c, NORMAL_QTY))
    pivot = max(r[1] for r in rows[-N_BASE:])
    o, c = c, pivot * 1.03
    rows.append((o, c * 1.001, o * 0.999, c, NORMAL_QTY * breakout_volume))
    for k, close in enumerate(post):
        o = c * gap_open if (k == 0 and gap_open) else c
        c = close
        rows.append((o, max(o, c) * 1.001, min(o, c) * 0.999, c, NORMAL_QTY))
    return rows


def path(rise_days=0, rise=0.0, fall_days=0, fall=0.0, start=1.0):
    """Closes after the breakout, as multiples of a running level: `rise_days` of +rise, then `fall_days` of -fall."""
    out, level = [], start
    for _ in range(rise_days):
        level *= 1 + rise
        out.append(level)
    for _ in range(fall_days):
        level *= 1 - fall
        out.append(level)
    return out


def closes_after_breakout(rows, post_multiples):
    base = rows[BREAKOUT][3]
    return [base * m for m in post_multiples]


def make_days(rows, extra=None, n_total=None):
    """{date: bhav rows} for the whole universe: 60 fillers whose strength spreads from weak to strong, BRK, and any extra shares."""
    n_total = n_total or len(rows)
    days = weekdays(n_total, END)
    out = {d: [] for d in days}
    for k in range(FILLERS):
        p, drift = 100.0 + k, -0.001 + 0.004 * k / (FILLERS - 1)
        for d in days:
            c = p * (1 + drift)
            out[d].append((f"INE{k:05d}", f"S{k:02d}", "EQ", p, max(p, c), min(p, c), c, p, float(NORMAL_QTY), c * NORMAL_QTY))
            p = c
    series = {"BRK": rows}
    series.update(extra or {})
    for sym, rws in series.items():
        prev = rws[0][0]
        for d, (o, h, l, c, q) in zip(days, rws):
            out[d].append((f"INE_{sym}", sym, "EQ", o, h, l, c, prev, float(q), c * q))
            prev = c
    return days, out


def nifty(days, daily_move):
    c, candles = 20000.0, []
    for d in days:
        o, c = c, c * (1 + daily_move)
        candles.append([int(clock.ts(clock.at(d, "09:15"))), o, max(o, c), min(o, c), c, 0])
    return dl.Daily(candles)


def night(days, by_day, length, nifty_obj, book=None, ledger=None, results=None):
    """What the Leaders workflow does on the evening the data reach `days[length - 1]`."""
    return swing.run({d: by_day[d] for d in days[:length]}, nifty_obj, results, book, ledger)


def swing_sma20(days, by_day):
    """The 20-day average of the share BRK on the last day, worked out independently of the desk."""
    closes = []
    for d in days:
        row = next(r for r in by_day[d] if r[1] == "BRK")
        closes.append(row[6])
    return sum(closes[-20:]) / 20


# ── tonight's signal ─────────────────────────────────────────────────────────────────────────────
def test_a_leader_breaking_out_of_a_tight_base_in_an_up_market_is_a_setup_not_a_take():
    days, by_day = make_days(brk_rows())
    doc, book = night(days, by_day, len(days), nifty(days, 0.001))
    assert [t["symbol"] for t in doc["triggered"]] == ["BRK"]
    t = doc["triggered"][0]
    assert t["rs"] == 100.0 and t["volx"] == pytest.approx(3.0, abs=0.05) and t["risk_pct"] == 8.0
    assert t["stop_hint"] == round(t["close"] * 0.92, 2) and t["close"] > t["pivot"]
    assert doc["regime"]["nifty_above_200"] is True
    [idea] = doc["ideas"]
    assert idea["grade"] == "setup" and idea["desk"] == "swing" and idea["status"] == "CANDIDATE" and idea["qty"] is None
    assert {c["state"] for c in idea["checks"]} == {"pass"} and "Unproven" in idea["evidence"]["headline"]
    assert "next open" in " ".join(idea["why"]).lower() and idea["target"] is None
    assert [p["status"] for p in doc["positions"]] == ["PENDING"] and book["asof"] == days[-1].isoformat()
    assert "indistinguishable from zero" in doc["note"]
    contract.assert_valid("swing", doc)
    ref = idea["chart"]                                           # the idea says where its chart is, and it is there
    assert ref == {"file": "swing.json", "in": "charts", "key": "BRK:" + days[-1].isoformat()}
    ch = doc["charts"][ref["key"]]
    contract.assert_valid("chart", ch)
    assert ch["unit"] == "day" and ch["dir"] == 1 and ch["tg"] is None and ch["el"] == "Signal" and ch["e"] == len(ch["k"]) - 1
    assert ch["st"] == pytest.approx(ch["ep"] * 0.92, abs=0.02) and ch["ln"][0][0].startswith("Break level") and ch["ln"][0][1] == doc["triggered"][0]["pivot"]
    assert ch["tr"]["label"].startswith("Trail") and ch["tr"]["v"][-1] == pytest.approx(swing_sma20(days, by_day), abs=0.05)
    assert len(ch["t"]) == len(ch["k"]) == len(ch["tr"]["v"])
    assert set(idea["plan"]) == {"enter", "stop", "target", "trail", "exit_by"} and "tomorrow's open" in idea["plan"]["enter"] and "20-day average" in idea["plan"]["trail"]
    wrap = {"$defs": contract.schema("live")["$defs"], "$ref": "#/$defs/idea"}
    from jsonschema import Draft202012Validator
    Draft202012Validator(wrap).validate(idea)


def test_nothing_triggers_when_the_market_filter_fails_or_is_unknown():
    days, by_day = make_days(brk_rows())
    down, _ = night(days, by_day, len(days), nifty(days, -0.001))
    assert down["triggered"] == [] and down["ideas"] == [] and down["regime"]["nifty_above_200"] is False
    unknown, book = night(days, by_day, len(days), None)
    assert unknown["triggered"] == [] and unknown["regime"]["nifty_above_200"] is None
    assert "market filter is unknown" in unknown["note"] and book["positions"] == []
    contract.assert_valid("swing", unknown)


def test_a_stale_nifty_history_counts_as_unknown():
    days, by_day = make_days(brk_rows())
    old = nifty(days[:-30], 0.001)
    assert swing.nifty_regime(old, days[-1])["nifty_above_200"] is None


def test_running_twice_in_one_night_adds_nothing():
    days, by_day = make_days(brk_rows())
    n = nifty(days, 0.001)
    doc1, book = night(days, by_day, len(days), n)
    doc2, book2 = night(days, by_day, len(days), n, book)
    assert len(book2["positions"]) == 1 and doc2["triggered"] == []      # already in the book, so not announced again


def test_a_breakout_on_ordinary_volume_is_not_a_setup():
    days, by_day = make_days(brk_rows(breakout_volume=1))
    doc, _ = night(days, by_day, len(days), nifty(days, 0.001))
    assert doc["triggered"] == []


# ── the rule is the research's rule ──────────────────────────────────────────────────────────────
def test_the_nightly_rule_matches_the_research_signals_on_every_day():
    post = closes_after_breakout(brk_rows(), path(5, 0.002))
    days, by_day = make_days(brk_rows(post))
    data, _ = swing.build_data(by_day)
    D = data["BRK"]
    sigs = dl.signals(D, {d: True for d in D.d}, key="k", rs_map={"k": {d: 100.0 for d in D.d}})
    research = {s["i"]: s for s in sigs}
    mine = {i: swing.evaluate(D, i, 100.0, True) for i in range(dl.RULES["warmup"], D.n - 1)}
    assert {i for i, v in mine.items() if v} == set(research) == {BREAKOUT}
    a, b = mine[BREAKOUT], research[BREAKOUT]
    assert (a["pivot"], a["trend"], a["tight"], a["regime"], a["rs_ok"]) == (b["pivot"], b["trend"], b["tight"], b["regime"], b["rs_ok"])
    assert a["volx"] == pytest.approx(b["volx"]) and swing.triggered(a)


# ── the paper book follows trades the way the research simulated them ────────────────────────────
def follow(post_multiples, folder, gap_open=None):
    """Runs every night from the breakout until the data ends, as the workflow would. Returns (book, full data, days)."""
    base_rows = brk_rows()
    rows = brk_rows(closes_after_breakout(base_rows, post_multiples), gap_open=gap_open)
    days, by_day = make_days(rows)
    n = nifty(days, 0.001)
    ledger = Ledger(folder, SWING_PREFIX)
    book = None
    for length in range(BREAKOUT + 1, len(days) + 1):
        _, book = night(days, by_day, length, n, book, ledger)
    return book, swing.build_data(by_day)[0]["BRK"], days


def agree_with_research(book, D):
    [p] = book["positions"]
    t = dl.simulate(D, BREAKOUT, D.pivot[BREAKOUT], "trail")
    assert t is not None and p["status"] == "CLOSED"
    assert (p["entry_date"], p["exit_date"]) == (t["entry_date"].isoformat(), t["exit_date"].isoformat())
    assert p["entry"] == pytest.approx(t["entry"], abs=0.01) and p["exit"] == pytest.approx(t["exit"], abs=0.01)
    assert p["reason"] == t["reason"] and p["r"] == pytest.approx(t["net_r"], abs=0.02)
    return p


def test_a_trade_that_runs_and_then_breaks_trend_exits_exactly_as_the_research_does(tmp_path):
    book, D, days = follow(path(30, 0.012, 14, 0.015), str(tmp_path))
    p = agree_with_research(book, D)
    assert p["reason"] == "trend break" and p["breakeven"] is True and p["r"] > 0
    [row] = Ledger(str(tmp_path), SWING_PREFIX).read_all()
    contract.assert_valid("ledger-trade", row)
    assert row["desk"] == "swing" and row["id"] == "swing:BRK:" + days[BREAKOUT].isoformat() and row["result"] == "WIN"
    assert row["cost_estimated"] is True and row["net"] == pytest.approx(row["gross"] - row["cost"], abs=0.01)


def test_a_trade_that_falls_is_stopped_out_exactly_as_the_research_does(tmp_path):
    book, D, _ = follow([0.88, 0.87, 0.86, 0.85], str(tmp_path))       # a 12 percent drop on the first day: through the 8 percent stop
    p = agree_with_research(book, D)
    assert p["reason"] == "stop" and p["r"] < -1.0 and p["breakeven"] is False
    assert Ledger(str(tmp_path), SWING_PREFIX).read_all()[0]["result"] == "LOSS"


def test_an_entry_that_opens_far_above_the_pivot_is_cancelled_not_chased(tmp_path):
    book, D, _ = follow(path(5, 0.001), str(tmp_path), gap_open=1.10)
    [p] = book["positions"]
    assert dl.simulate(D, BREAKOUT, D.pivot[BREAKOUT], "trail") is None
    assert p["status"] == "CANCELLED" and "8 percent above the pivot" in p["reason"]
    assert Ledger(str(tmp_path), SWING_PREFIX).read_all() == []


def test_an_open_position_reports_its_progress_in_r(tmp_path):
    base = brk_rows()
    rows = brk_rows(closes_after_breakout(base, path(3, 0.01)))
    days, by_day = make_days(rows)
    n = nifty(days, 0.001)
    book = None
    for length in range(BREAKOUT + 1, len(days) + 1):
        doc, book = night(days, by_day, length, n, book)
    [pos] = doc["positions"]
    assert pos["status"] == "OPEN" and pos["entry"] is not None and pos["open_r"] is not None and pos["open_r"] > 0
    assert pos["stop"] < pos["entry"] and pos["breakeven"] is False
    contract.assert_valid("swing", doc)
    ch = doc["charts"][pos["id"]]                                  # an open position is drawn with its real entry and its current stop
    contract.assert_valid("chart", ch)
    assert ch["el"] == "Entry" and ch["ep"] == pos["entry"] and ch["st"] == pos["stop"] and ch["k"][ch["e"]][0] == pytest.approx(pos["entry"], abs=0.02)
    assert "8 percent below the entry" in ch["tn"]


def test_a_new_book_starts_tonight_and_never_back_fills_history():
    days, by_day = make_days(brk_rows(closes_after_breakout(brk_rows(), path(5, 0.002))))
    doc, book = night(days, by_day, len(days), nifty(days, 0.001))
    assert book["positions"] == [] and doc["triggered"] == []              # the breakout was five nights ago; it is not a setup tonight


# ── setups near the pivot, and the gap-ups under observation ─────────────────────────────────────
def test_a_leader_just_below_its_pivot_is_listed_as_a_setup_to_watch():
    rows = brk_rows()
    rows[BREAKOUT] = (rows[BREAKOUT][0], rows[BREAKOUT][1] * 0.95, rows[BREAKOUT][2], rows[BREAKOUT][0] * 1.01, NORMAL_QTY)   # no breakout tonight
    days, by_day = make_days(rows)
    doc, _ = night(days, by_day, len(days), nifty(days, 0.001))
    names = [s["symbol"] for s in doc["setups"]]
    assert "BRK" in names and doc["triggered"] == []
    s = next(s for s in doc["setups"] if s["symbol"] == "BRK")
    assert 0 <= s["pct_to_pivot"] <= 5 and s["rs"] >= 80 and s["base_depth_pct"] <= 25
    assert doc["ideas"] == []                                                # watching is not an idea
    ch = doc["charts"]["BRK:setup"]
    contract.assert_valid("chart", ch)
    assert ch["el"] == "Now" and ch["st"] is None and "Not a setup yet" in ch["tn"]
    contract.assert_valid("swing", doc)


def test_results_day_gap_ups_are_only_observed_never_traded():
    flat = [(200.0, 200.4, 199.6, 200.0 + (0.2 if j % 2 else -0.2), NORMAL_QTY) for j in range(BREAKOUT)]
    gap = flat + [(220.0, 224.0, 219.0, 222.0, NORMAL_QTY * 4)]
    days, by_day = make_days(brk_rows(), extra={"GAPX": gap})
    day = days[-1]
    with_results, _ = night(days, by_day, len(days), nifty(days, 0.001), results={("GAPX", day)})
    plain, _ = night(days, by_day, len(days), nifty(days, 0.001))
    [g] = [x for x in with_results["gap_watch"] if x["symbol"] == "GAPX"]
    assert g["hypothesis"] is True and g["results"] is True and g["gap_pct"] == pytest.approx(10.0, abs=0.2) and g["volx"] == pytest.approx(4.0, abs=0.2)
    assert [x["results"] for x in plain["gap_watch"] if x["symbol"] == "GAPX"] == [False]
    assert all(i["name"] != "GAPX" for i in with_results["ideas"]) and all(p["symbol"] != "GAPX" for p in with_results["positions"])


def test_too_little_history_gives_nothing_rather_than_a_guess():
    days, by_day = make_days(brk_rows())
    assert swing.run({d: by_day[d] for d in days[:100]}, nifty(days, 0.001)) is None


def test_a_share_that_stops_trading_is_closed_at_its_last_price():
    book = {"schema": 1, "asof": "2026-11-10", "positions": [{"id": "GONE:2026-11-09", "symbol": "GONE", "signal_date": "2026-11-09",
            "pivot": 100.0, "status": "OPEN", "entry_date": "2026-11-10", "entry": 100.0, "stop": 92.0, "initial_stop": 92.0, "breakeven": False}]}
    candles = [[int(clock.ts(clock.at(date(2026, 11, d), "09:15"))), 100.0, 101.0, 99.0, 100.0 + d / 100, 1000] for d in (9, 10)]
    D = dl.Daily(candles, ended=True)
    closed = swing.advance(book, {"GONE": D}, date(2026, 11, 12))
    assert [p["reason"] for p in closed] == ["delisted or merged"] and book["positions"][0]["exit"] == round(D.c[-1], 2)
