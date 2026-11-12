"""The published contract: manifest, ledger, migrations, live.json, and the single cost model.  Run:  python -m pytest tests/test_contract.py"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
pytest.importorskip("jsonschema")

from engine import clock, contract, costs, live, manifest, migrations  # noqa: E402
from engine.domain import LedgerTrade  # noqa: E402
from engine.ledger import Ledger, sync  # noqa: E402
from engine.schema import SCHEMA  # noqa: E402
from engine.store import Store  # noqa: E402

IST = clock.IST


def rulebook_trade(**over):
    t = {"id": "2026-11-11-NIFTY-1", "date": "2026-11-11", "unit": "NIFTY", "name": "Nifty 50", "book": "Index options", "dir": 1,
         "kind": "option", "action": "BUY", "symbol": "NSE_FO|1", "contract": "NIFTY 25000 CE", "qty": 65, "entry_price": 100.0,
         "entry_time": "2026-11-11T09:50:00+05:30", "status": "CLOSED", "exit_time": "2026-11-11T11:00:00+05:30", "exit_price": 120.0,
         "exit_type": "TARGET", "exit_reason": "Target reached", "pnl": 1300.0, "risk_amount": 1000.0, "result": "WIN"}
    t.update(over)
    return t


def strategy_trade(**over):
    t = {"id": "orb|HAL|1794369000", "strategy": "orb", "symbol": "NSE:HAL-EQ", "name": "HAL", "kind": "stock", "dir": -1,
         "date": "2026-11-11", "entry": 4000.0, "entry_t": "2026-11-11T09:30:00+05:30", "entry_ts": 1794369000, "stop": 4040.0,
         "target": 3920.0, "qty": 12, "risk": 480.0, "status": "CLOSED", "exit": 3950.0, "exit_t": "2026-11-11T10:40:00+05:30",
         "exit_type": "TARGET", "exit_reason": "Target reached", "gross": 600.0, "cost": 70.0, "net": 530.0, "net_r": 1.104,
         "result": "WIN", "risk_approved": True, "dec": {"g": "take", "f": [], "unknown": [], "cr": 0.15, "cv": 3.1, "nm": True}}
    t.update(over)
    return t


# ── costs ────────────────────────────────────────────────────────────────────
def test_cost_model_reproduces_the_formula_the_strategy_simulator_always_used():
    c = costs.DEFAULTS
    expect = c["stock_per_trade"] + 100 * (500 + 510) * (c["stock_pct"] / 2 + c["stock_slippage_pct"]) / 100
    assert costs.round_trip("stock", 100, 500, 510) == pytest.approx(expect)


def test_cost_model_rejects_unknown_kinds_and_ignores_unknown_overrides():
    with pytest.raises(ValueError):
        costs.round_trip("bond", 1, 1, 1)
    assert costs.merged({"nonsense": 1}) == costs.DEFAULTS


def test_options_cost_follows_backtest_config():
    table = costs.from_config({"backtest": {"option_cost_per_trade": 150, "option_slippage_pct": 1.0}})
    assert table["option_per_trade"] == 150 and table["option_slippage_pct"] == 1.0
    assert costs.round_trip("option", 10, 100, 100, table) == pytest.approx(150 + 10 * 200 * 1.0 / 100)


# ── ledger ───────────────────────────────────────────────────────────────────
def test_ledger_is_idempotent_and_valid(tmp_path):
    ledger = Ledger(str(tmp_path))
    trade = LedgerTrade.from_rulebook(rulebook_trade())
    assert ledger.record(trade) is True
    assert ledger.record(trade) is False                        # the engine may call this every cycle
    assert Ledger(str(tmp_path)).record(trade) is False         # and across restarts
    rows = Ledger(str(tmp_path)).read_all()
    assert len(rows) == 1
    contract.assert_valid("ledger-trade", rows[0])
    assert rows[0]["cost_estimated"] is True and rows[0]["net"] == pytest.approx(rows[0]["gross"] - rows[0]["cost"], abs=0.01)


def test_ledger_splits_months_and_heals_a_torn_line(tmp_path):
    ledger = Ledger(str(tmp_path))
    ledger.record(LedgerTrade.from_rulebook(rulebook_trade()))
    ledger.record(LedgerTrade.from_rulebook(rulebook_trade(id="2026-12-01-NIFTY-1", date="2026-12-01")))
    assert ledger.months() == ["2026-11", "2026-12"]
    path = os.path.join(str(tmp_path), "ledger-2026-11.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        f.write('{"id":"torn')                                  # the process died mid-write
    fresh = Ledger(str(tmp_path))
    assert len(fresh.read_month("2026-11")) == 1                 # the torn line is ignored
    assert fresh.record(LedgerTrade.from_rulebook(rulebook_trade(id="2026-11-12-NIFTY-1", date="2026-11-12"))) is True
    assert [r["id"] for r in fresh.read_month("2026-11")] == ["rulebook:2026-11-11-NIFTY-1", "rulebook:2026-11-12-NIFTY-1"]


def test_sync_records_only_settled_and_approved_trades(tmp_path):
    ledger = Ledger(str(tmp_path))
    rb = [rulebook_trade(), rulebook_trade(id="open", status="OPEN"),
          rulebook_trade(id="lost", status="UNRESOLVED", exit_price=None, pnl=None)]
    st = [strategy_trade(), strategy_trade(id="rejected", risk_approved=False), strategy_trade(id="still-open", status="OPEN")]
    assert sync(ledger, rb, st) == 3
    ids = {r["id"] for r in ledger.read_all()}
    assert ids == {"rulebook:2026-11-11-NIFTY-1", "rulebook:lost", "strategy:orb|HAL|1794369000"}
    assert sync(ledger, rb, st) == 0
    lost = next(r for r in ledger.read_all() if r["id"] == "rulebook:lost")
    assert lost["status"] == "UNRESOLVED" and lost["net"] is None and lost["exit_price"] is None   # never invents a P&L
    for row in ledger.read_all():
        contract.assert_valid("ledger-trade", row)


def test_sync_skips_malformed_trades_without_failing(tmp_path):
    seen = []
    n = sync(Ledger(str(tmp_path)), [{"status": "CLOSED"}], [strategy_trade()], log=seen.append)
    assert n == 1 and seen and "malformed" in seen[0]


def test_strategy_trade_maps_to_the_right_desk(tmp_path):
    assert LedgerTrade.from_strategy(strategy_trade()).desk == "intraday_stocks"
    assert LedgerTrade.from_strategy(strategy_trade(kind="index", id="x")).desk == "index_futures"
    assert LedgerTrade.from_rulebook(rulebook_trade(book="Commodity options")).desk == "mcx"


# ── migrations ───────────────────────────────────────────────────────────────
def test_current_schema_is_left_alone(tmp_path):
    feed = {"schema": SCHEMA, "status": {"a": 1}, "trades": [rulebook_trade()]}
    assert migrations.upgrade_feed(feed, str(tmp_path)) is feed
    assert not [n for n in os.listdir(str(tmp_path)) if n.startswith("ledger-")]


def test_an_old_feed_without_a_migration_is_archived_not_lost(tmp_path):
    old = {"schema": SCHEMA - 1, "status": {}, "trades": [rulebook_trade(), rulebook_trade(id="open", status="OPEN")]}
    logs = []
    out = migrations.upgrade_feed(old, str(tmp_path), log=logs.append)
    assert out == {"status": {}, "trades": [], "schema": SCHEMA}
    assert [r["id"] for r in Ledger(str(tmp_path)).read_all()] == ["rulebook:2026-11-11-NIFTY-1"]
    assert logs and "1 settled trade" in logs[0]


def test_a_brand_new_feed_resets_quietly(tmp_path):
    logs = []
    out = migrations.upgrade_feed({"status": {}, "trades": []}, str(tmp_path), log=logs.append)
    assert out["schema"] == SCHEMA and not logs


def test_a_registered_migration_upgrades_in_place(tmp_path, monkeypatch):
    monkeypatch.setitem(migrations.FEED_MIGRATIONS, SCHEMA - 1, lambda d: {**d, "schema": SCHEMA, "migrated": True})
    out = migrations.upgrade_feed({"schema": SCHEMA - 1, "status": {}, "trades": [rulebook_trade()]}, str(tmp_path))
    assert out["migrated"] is True and len(out["trades"]) == 1


def test_a_migration_that_does_not_advance_the_version_is_refused(tmp_path, monkeypatch):
    monkeypatch.setitem(migrations.FEED_MIGRATIONS, SCHEMA - 1, lambda d: d)
    out = migrations.upgrade_feed({"schema": SCHEMA - 1, "status": {}, "trades": [rulebook_trade()]}, str(tmp_path), log=lambda m: None)
    assert out["trades"] == [] and len(Ledger(str(tmp_path)).read_all()) == 1


def test_a_newer_document_is_never_downgraded_in_place(tmp_path):
    out, was_reset = migrations.upgrade_strategy_state({"schema": SCHEMA + 5, "trades": [strategy_trade()]}, str(tmp_path), log=lambda m: None)
    assert was_reset and out == {} and len(Ledger(str(tmp_path)).read_all()) == 1


def test_strategy_desk_keeps_its_closed_trades_across_a_schema_reset(tmp_path):
    from engine.strategies.desk import StrategyDesk
    from engine.run import load_config
    folder = str(tmp_path)
    json.dump({"schema": SCHEMA - 1, "trades": [strategy_trade()], "archive": {}, "status": {}}, open(os.path.join(folder, "strategies.json"), "w"))
    open(os.path.join(folder, "chartlive_orb.json"), "w").write("{}")
    desk = StrategyDesk(load_config(), broker=None, notify=lambda *a, **k: None, folder=folder, universe=["HAL"], master_markets=[])
    assert desk.state["trades"] == [] and desk.state["schema"] == SCHEMA
    assert [r["id"] for r in Ledger(folder).read_all()] == ["strategy:orb|HAL|1794369000"]
    assert not os.path.exists(os.path.join(folder, "chartlive_orb.json"))


# ── manifest ─────────────────────────────────────────────────────────────────
def test_manifest_lists_published_files_only(tmp_path):
    folder = str(tmp_path)
    (tmp_path / "feed.json").write_text("{}")
    (tmp_path / "secure.bin").write_bytes(b"secret")
    (tmp_path / "x.tmp").write_text("t")
    (tmp_path / ".signal-desk-publish.lock").write_text("0")
    (tmp_path / "ledger-2026-11.jsonl").write_text("{}\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "nested.json").write_text("{}")
    (tmp_path / "live.json").write_text("{}")
    assert manifest.write(folder) is True
    doc = json.load(open(os.path.join(folder, "manifest.json")))
    contract.assert_valid("manifest", doc)
    assert [f["path"] for f in doc["files"]] == ["feed.json", "ledger-2026-11.jsonl", "live.json", "sub/nested.json"]
    schemas = {f["path"]: f["schema"] for f in doc["files"]}
    assert schemas == {"feed.json": None, "ledger-2026-11.jsonl": "ledger-trade@1", "live.json": "live@1", "sub/nested.json": None}


def test_manifest_is_idempotent_and_tracks_only_real_changes(tmp_path):
    folder = str(tmp_path)
    (tmp_path / "a.json").write_text("1")
    (tmp_path / "b.json").write_text("2")
    clock_values = iter(f"2026-11-12T10:00:{n:02d}+00:00" for n in range(60))
    stamp = lambda: next(clock_values)
    assert manifest.write(folder, stamp) is True
    first = json.load(open(os.path.join(folder, "manifest.json")))
    assert manifest.write(folder, stamp) is False                # nothing changed: no rewrite, no new commit
    (tmp_path / "b.json").write_text("22")
    assert manifest.write(folder, stamp) is True
    second = json.load(open(os.path.join(folder, "manifest.json")))
    by = lambda d: {f["path"]: f for f in d["files"]}
    assert by(second)["a.json"]["updated_at"] == by(first)["a.json"]["updated_at"]          # untouched file keeps its time
    assert by(second)["b.json"]["updated_at"] != by(first)["b.json"]["updated_at"]
    assert by(second)["b.json"]["bytes"] == 2
    (tmp_path / "a.json").unlink()
    assert manifest.write(folder, stamp) is True
    assert [f["path"] for f in json.load(open(os.path.join(folder, "manifest.json")))["files"]] == ["b.json"]


def test_publish_writes_the_manifest_and_an_idle_publish_adds_no_commit(tmp_path):
    root = str(tmp_path)
    g = lambda cwd, *a: subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)
    remote = os.path.join(root, "remote.git")
    subprocess.run(["git", "init", "--bare", "-b", "data", remote], capture_output=True)
    eng = os.path.join(root, "engine")
    os.makedirs(eng)
    g(eng, "init", "-b", "data")
    g(eng, "config", "user.email", "t@t")
    g(eng, "config", "user.name", "t")
    g(eng, "remote", "add", "origin", remote)
    (tmp_path / "engine" / "feed.json").write_text('{"v":1}')
    Store(eng, "x").publish(own={"feed.json"})
    chk = os.path.join(root, "check")
    os.makedirs(chk)
    g(chk, "init", "-b", "data")
    g(chk, "remote", "add", "origin", remote)
    g(chk, "fetch", "origin", "data")
    g(chk, "reset", "--hard", "FETCH_HEAD")
    doc = json.load(open(os.path.join(chk, "manifest.json")))
    contract.assert_valid("manifest", doc)
    assert [f["path"] for f in doc["files"]] == ["feed.json"]
    head = g(remote, "rev-parse", "data").stdout
    Store(eng, "x").publish(own={"feed.json"})                    # nothing changed
    assert g(remote, "rev-parse", "data").stdout == head


# ── live.json ────────────────────────────────────────────────────────────────
NOW = datetime(2026, 11, 11, 10, 0, tzinfo=IST)


def open_strategy(**over):
    fields = dict(status="OPEN", signal_t="2026-11-11T09:55:00+05:30", entry_t="2026-11-11T09:55:00+05:30", exit=None, exit_t=None,
                  exit_type=None, exit_reason=None, gross=None, cost=None, net=None, net_r=None, result=None, last=3990.0,
                  open_pnl=120.0, why=["a reason"], regime="Opening range breakout", exit_by="15:15")
    fields.update(over)
    return strategy_trade(**fields)


def open_rulebook(**over):
    fields = dict(status="OPEN", exit_time=None, exit_price=None, pnl=None, result=None, stop_price=75.0, stop_underlying=24900.0,
                  target_underlying=25200.0, underlying_entry=25050.0, remaining=65, last_price=104.0, open_pnl=260.0,
                  last_update="2026-11-11T09:59:30+05:30", quote_status="FRESH", reasons=["why"], phase="initial")
    fields.update(over)
    return rulebook_trade(**fields)


def test_live_document_is_valid_with_nothing_yet():
    doc = live.build(NOW, {}, None, None)
    contract.assert_valid("live", doc)
    assert doc["ideas"] == [] and doc["positions"] == [] and doc["risk"]["open_positions"] == 0


def test_live_document_from_both_engines_is_valid_and_consistent():
    feed = {"status": {"phase": "watching", "running": True, "heartbeat": "2026-11-11T09:59:00+05:30"},
            "trades": [open_rulebook(id="open-rb"), rulebook_trade()]}
    desk = {"updated": "2026-11-11T09:59:30+05:30", "trades": [open_strategy(), strategy_trade(id="closed-one")],
            "status": {"cycle_at": "2026-11-11T09:59:30+05:30", "bar_end": "2026-11-11T09:55"}, "watchlist": [{}, {}]}
    doc = live.build(NOW, {"portfolio": {"max_open_positions": 6}}, feed, desk)
    contract.assert_valid("live", doc)
    assert doc["risk"]["limits"]["max_open_positions"] == 6 and doc["risk"]["limits"]["max_open_risk"] == 17500.0
    assert {p["id"] for p in doc["positions"]} == {"rulebook:open-rb", "strategy:orb|HAL|1794369000"}
    assert doc["risk"]["open_positions"] == 2 and doc["risk"]["open_risk"] == pytest.approx(1480.0)
    assert doc["risk"]["open_pnl"] == pytest.approx(380.0)
    assert doc["risk"]["realized_pnl"] == pytest.approx(1300.0 + 530.0)
    assert doc["risk"]["day_pnl"] == pytest.approx(1830.0 + 380.0)
    assert {e["id"] for e in doc["ended"]} == {"rulebook:2026-11-11-NIFTY-1", "strategy:closed-one"}
    assert doc["market"]["watchlist_count"] == 2
    assert all(i["evidence"] and i["evidence"]["headline"] for i in doc["ideas"])
    assert {i["id"] for i in doc["ideas"]} == {"rulebook:open-rb", "strategy:orb|HAL|1794369000"}


def test_unknown_data_is_shown_as_unknown_never_as_a_pass():
    t = open_strategy(dec={"g": "watch", "f": ["vol_data", "mkt_data"], "unknown": ["vol_data", "mkt_data"], "cr": 0.1, "nm": None})
    idea = live.strategy_idea(t, NOW)
    states = {c["id"]: c["state"] for c in idea.checks}
    assert states == {"cost": "pass", "vol": "unknown", "mkt": "unknown", "conf": "pass"}
    assert any("could not be verified" in b for b in idea.blockers)
    assert idea.grade == "watch"
    clean = live.strategy_idea(open_strategy(), NOW)
    assert clean.quality > idea.quality                       # unverified inputs rank below verified ones


def test_index_signals_do_not_need_volume():
    idea = live.strategy_idea(open_strategy(kind="index", symbol="NSE:NIFTY50-INDEX", name="Nifty 50"), NOW)
    assert {c["id"]: c["state"] for c in idea.checks}["vol"] == "na" and idea.desk == "index_futures"


def test_old_ungraded_signals_are_not_shown_but_open_takes_are():
    old = (NOW - timedelta(hours=2)).isoformat()
    watch = open_strategy(signal_t=old, dec={"g": "watch", "f": ["mkt"], "unknown": [], "cr": 0.1, "nm": False})
    take = open_strategy(signal_t=old)
    assert live.strategy_idea(watch, NOW) is None
    assert live.strategy_idea(take, NOW) is not None


def test_rejected_signals_explain_why_in_plain_words():
    t = open_strategy(risk_approved=False, risk_reasons=["max_open_positions", "shared_max_open_risk"])
    idea = live.strategy_idea(t, NOW)
    assert idea.paper_position is False
    assert "the strategy desk is at its position limit" in idea.blockers
    assert "the shared open-risk budget would be exceeded" in idea.blockers


def test_ideas_are_ranked_by_quality_then_recency():
    a = open_strategy(id="a", dec={"g": "take", "f": [], "unknown": [], "cr": 0.20, "cv": 3, "nm": True})
    b = open_strategy(id="b", dec={"g": "take", "f": [], "unknown": [], "cr": 0.05, "cv": 3, "nm": True, "ip": "Results today", "rs": 92})
    c = open_strategy(id="c", signal_t="2026-11-11T09:58:00+05:30", dec={"g": "watch", "f": ["mkt"], "unknown": [], "cr": 0.1, "nm": False})
    doc = live.build(NOW, {}, {"trades": []}, {"trades": [a, b, c], "updated": NOW.isoformat()})
    assert [i["id"] for i in doc["ideas"]] == ["strategy:b", "strategy:a", "strategy:c"]
    qualities = [i["quality"] for i in doc["ideas"]]
    assert qualities == sorted(qualities, reverse=True) and qualities[0] > qualities[1] > qualities[2]
    assert doc["ideas"][0]["context"]["in_play"] == "Results today" and doc["ideas"][-1]["grade"] == "watch"


def test_equal_quality_puts_the_newer_signal_first():
    older = open_strategy(id="older", signal_t="2026-11-11T09:40:00+05:30")
    newer = open_strategy(id="newer", signal_t="2026-11-11T09:55:00+05:30")
    doc = live.build(NOW, {}, {"trades": []}, {"trades": [older, newer], "updated": NOW.isoformat()})
    assert [i["id"] for i in doc["ideas"]] == ["strategy:newer", "strategy:older"]


def test_scanner_watches_below_the_score_bar_are_left_out():
    scan = {"asof": "2026-11-11T09:55", "items": [
        {"id": "NIFTY", "name": "Nifty 50", "side": "CE", "score": 5, "state": "Call side broke out", "market_agrees": True, "grade": "take",
         "why_not": [], "factors": [{"text": "Above VWAP", "ok": True}], "spot": 25000, "warnings": [],
         "idea": {"contract": "NIFTY 25000 CE", "symbol": "NSE_FO|9", "premium": 100, "stop": 75, "target": 137.5, "lot": 65,
                  "risk_per_lot": 1625, "cost_ratio": 0.1}},
        {"id": "BANKNIFTY", "name": "Bank Nifty", "side": "PE", "score": 2, "state": "Inside the opening range", "market_agrees": None,
         "grade": "watch", "why_not": ["price is still inside the opening range"], "factors": [],
         "idea": {"contract": "BN 50000 PE", "symbol": "NSE_FO|10", "premium": 90, "stop": 63, "target": 130, "lot": 30, "risk_per_lot": 810}}]}
    ideas = live.scan_ideas(scan, 4, "2026-11-11")
    assert [i.name for i in ideas] == ["Nifty 50 CE"] and ideas[0].grade == "take" and ideas[0].status == "CANDIDATE"
    assert {c["state"] for c in ideas[0].checks} == {"pass"}


def test_a_stale_option_quote_is_reported_on_the_position():
    pos = live.rulebook_position(open_rulebook(quote_status="STALE"), NOW)
    assert pos.quote_status == "STALE"
    assert live.rulebook_position(open_rulebook(quote_status=None, last_update=(NOW - timedelta(hours=1)).isoformat()), NOW).quote_status == "UNKNOWN"


def test_no_published_number_can_be_nan():
    t = open_strategy(risk=float("nan"), open_pnl=float("inf"))
    closed = strategy_trade(id="nan-closed", net=float("nan"))
    rb = open_rulebook(id="nan-rb", open_pnl=float("nan"), risk_amount=float("inf"))
    doc = live.build(NOW, {}, {"trades": [rb]}, {"trades": [t, closed], "updated": NOW.isoformat()})
    json.dumps(doc, allow_nan=False)                           # raises if any NaN or infinity survived
    contract.assert_valid("live", doc)


def test_a_stale_workflow_cannot_overwrite_newer_ledger_lines(tmp_path):
    """The ledger sits at the top level so the publisher refreshes it from the branch for every workflow that does not own it."""
    from engine.store import ENGINE_PREFIXES
    root = str(tmp_path)
    g = lambda cwd, *a: subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)
    remote = os.path.join(root, "remote.git")
    subprocess.run(["git", "init", "--bare", "-b", "data", remote], capture_output=True)

    def clone(name):
        d = os.path.join(root, name)
        os.makedirs(d)
        g(d, "init", "-b", "data")
        g(d, "config", "user.email", "t@t")
        g(d, "config", "user.name", "t")
        g(d, "remote", "add", "origin", remote)
        return d

    eng = clone("engine")
    Ledger(eng).record(LedgerTrade.from_rulebook(rulebook_trade(id="one")))
    (tmp_path / "engine" / "feed.json").write_text("{}")
    Store(eng, "x").publish(own={"feed.json"}, own_prefix=ENGINE_PREFIXES)

    leaders = clone("leaders")                                   # a research job starts now, with the ledger as it is
    g(leaders, "fetch", "origin", "data")
    g(leaders, "reset", "--hard", "FETCH_HEAD")

    Ledger(eng).record(LedgerTrade.from_rulebook(rulebook_trade(id="two")))      # the engine settles another trade meanwhile
    Store(eng, "x").publish(own={"feed.json"}, own_prefix=ENGINE_PREFIXES)

    (tmp_path / "leaders" / "leaders.json").write_text('{"ok":true}')
    Store(leaders, "x").publish(own={"leaders.json"})            # publishes from its stale clone

    chk = clone("check")
    g(chk, "fetch", "origin", "data")
    g(chk, "reset", "--hard", "FETCH_HEAD")
    assert [r["id"] for r in Ledger(chk).read_all()] == ["rulebook:one", "rulebook:two"]
    assert os.path.exists(os.path.join(chk, "leaders.json"))


# ── the session's publishing step, and a whole simulated day ─────────────────────────────────────
def test_the_session_writes_live_json_the_ledger_and_the_button_registry(tmp_path):
    from types import SimpleNamespace
    from engine import costs as costs_mod
    from engine.journal import Journal, JournalUI
    from engine.run import Session

    folder = str(tmp_path)
    store = Store(folder, "x")
    secure = {}
    ui = JournalUI(Journal(folder, "a passphrase"), registry=secure.setdefault("journal_buttons", {}))
    from engine.journal import button_key
    jr_key = button_key("strategy:orb|HAL|1")
    ui.buttons([{"id": "strategy:orb|HAL|1", "name": "HAL", "desk": "intraday_stocks", "source": "strategy:orb", "symbol": "NSE:HAL-EQ", "action": "BUY"}])
    cfg = {"portfolio": {}}
    stub = SimpleNamespace(
        desk=SimpleNamespace(state={"trades": [strategy_trade()], "updated": NOW.isoformat()}),
        feed={"status": {"phase": "watching"}, "trades": [rulebook_trade()]}, cfg=cfg, cost_table=costs_mod.from_config(cfg),
        ledger=Ledger(folder), store=store, journal=Journal(folder, "a passphrase"), journal_ui=ui, secure=secure)
    Session.write_live(stub)
    doc = json.load(open(os.path.join(folder, "live.json"), encoding="utf-8"))
    contract.assert_valid("live", doc)
    assert doc["journal"] == {"enabled": True}
    assert {r["id"] for r in Ledger(folder).read_all()} == {"rulebook:2026-11-11-NIFTY-1", "strategy:orb|HAL|1794369000"}
    assert ui.changed is False                                                # the registry was saved to the encrypted file ...
    assert list(store.load_secure()["journal_buttons"]) == [jr_key]            # ... and only the encrypted file


def test_the_session_never_stops_for_a_publishing_problem(tmp_path, capsys):
    from types import SimpleNamespace
    from engine.run import Session
    stub = SimpleNamespace(desk=None, feed=None, cfg={}, cost_table={}, ledger=None, store=None, journal=None, journal_ui=None, secure={})
    Session.write_live(stub)                                                     # raises nothing
    assert "live.json not updated" in capsys.readouterr().out


def test_the_morning_brief_mentions_last_nights_swing_setups_only_when_recent(tmp_path):
    from engine.run import Session
    (tmp_path / "swing.json").write_text(json.dumps({"asof": "2026-11-10", "triggered": [{"symbol": "AAA"}, {"symbol": "BBB"}]}))
    line = Session.swing_line(None, datetime(2026, 11, 11, 9, 0, tzinfo=IST), str(tmp_path))
    assert "AAA, BBB" in line and "10 Nov" in line and "unproven" in line
    assert Session.swing_line(None, datetime(2026, 11, 20, 9, 0, tzinfo=IST), str(tmp_path)) is None         # too old
    (tmp_path / "swing.json").write_text(json.dumps({"asof": "2026-11-10", "triggered": []}))
    assert Session.swing_line(None, datetime(2026, 11, 11, 9, 0, tzinfo=IST), str(tmp_path)) is None         # nothing to say
    assert Session.swing_line(None, datetime(2026, 11, 11, 9, 0, tzinfo=IST), str(tmp_path / "missing")) is None


def test_a_whole_simulated_trading_day_produces_valid_published_files(tmp_path):
    from engine.run import load_config
    from tests.simulate import main as simulate
    feed, _ = simulate(verbose=False)
    cfg = load_config()
    closed = [t for t in feed["trades"] if t["status"] == "CLOSED"]
    assert closed
    assert sync(Ledger(str(tmp_path)), feed["trades"], []) == len(closed) + len([t for t in feed["trades"] if t["status"] == "UNRESOLVED"])
    for row in Ledger(str(tmp_path)).read_all():
        contract.assert_valid("ledger-trade", row)
    for day in sorted({t["date"] for t in feed["trades"]})[-4:]:
        when = datetime.fromisoformat(day + "T13:00:00+05:30")
        contract.assert_valid("live", live.build(when, cfg, feed, {"updated": when.isoformat()}))


def test_the_dashboards_sample_data_follows_the_contract():
    """web/public/demo is made by tests/make_web_fixtures.py. If the contract changes and the sample is not regenerated, this fails."""
    demo = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "public", "demo")
    load = lambda name: json.load(open(os.path.join(demo, name), encoding="utf-8"))
    contract.assert_valid("live", load("live.json"))
    contract.assert_valid("swing", load("swing.json"))
    doc = load("manifest.json")
    contract.assert_valid("manifest", doc)
    for entry in doc["files"]:                                     # the manifest describes exactly the files that are there
        path = os.path.join(demo, entry["path"])
        assert os.path.getsize(path) == entry["bytes"], entry["path"]
        assert manifest._sha256(path) == entry["sha256"], entry["path"]
    ledger_files = [f["path"] for f in doc["files"] if f["path"].startswith(("ledger-", "ledger_swing-"))]
    assert ledger_files
    for name in ledger_files:
        for line in open(os.path.join(demo, name), encoding="utf-8"):
            contract.assert_valid("ledger-trade", json.loads(line))
