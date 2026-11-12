"""The encrypted journal and its Telegram buttons.  Run:  python -m pytest tests/test_journal.py"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import journal as jr  # noqa: E402
from engine.alerts import Alerts  # noqa: E402
from engine.telegram import Telegram  # noqa: E402

PASS = "correct horse battery staple"
FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web", "test", "fixtures", "journal.bin")


def summary(**over):
    s = {"id": "strategy:orb|HAL|1", "name": "HAL", "desk": "intraday_stocks", "source": "strategy:orb", "symbol": "NSE:HAL-EQ",
         "action": "BUY", "grade": "take", "paper_entry": 4000.0, "stop": 3960.0, "target": 4080.0}
    s.update(over)
    return s


def ui(tmp_path, passphrase=PASS, clock=None):
    t = clock or [1_000_000.0]
    return jr.JournalUI(jr.Journal(str(tmp_path), passphrase), now_fn=lambda: t[0], log=lambda *a: None), t


# ── encryption ───────────────────────────────────────────────────────────────────────────────────
def test_round_trip_and_a_fresh_salt_and_nonce_every_time():
    doc = {"schema": 1, "entries": [{"id": "a", "took": True}]}
    a, b = jr.encrypt(PASS, doc), jr.encrypt(PASS, doc)
    assert jr.decrypt(PASS, a) == doc and jr.decrypt(PASS, b) == doc
    assert a["salt"] != b["salt"] and a["nonce"] != b["nonce"] and a["ct"] != b["ct"]
    assert a["v"] == 1 and a["kdf"] == "pbkdf2-sha256" and a["iter"] == 200_000


def test_the_envelope_reveals_nothing_readable():
    envelope = jr.encrypt(PASS, {"entries": [{"name": "HALSECRETNAME", "fill_price": 4012.5}]})
    text = json.dumps(envelope)
    assert "HALSECRETNAME" not in text and "4012" not in text


def test_a_wrong_passphrase_or_tampering_is_refused():
    envelope = jr.encrypt(PASS, {"entries": []})
    with pytest.raises(jr.JournalError):
        jr.decrypt("wrong", envelope)
    tampered = dict(envelope, ct=envelope["ct"][:-4] + ("AAAA" if envelope["ct"][-4:] != "AAAA" else "BBBB"))
    with pytest.raises(jr.JournalError):
        jr.decrypt(PASS, tampered)
    with pytest.raises(jr.JournalError):
        jr.decrypt(PASS, dict(envelope, v=2))


def test_the_committed_fixture_the_browser_tests_use_still_decrypts():
    with open(FIXTURE, encoding="utf-8") as f:
        envelope = json.load(f)
    doc = jr.decrypt(PASS, envelope)
    assert doc["entries"] and doc["entries"][0]["id"]


def test_the_file_never_starts_a_new_journal_over_the_wrong_passphrase(tmp_path):
    jr.Journal(str(tmp_path), PASS).record({"id": "a", "took": True})
    with pytest.raises(jr.JournalError):
        jr.Journal(str(tmp_path), "other").load()                 # it must not silently overwrite what is there
    assert [e["id"] for e in jr.Journal(str(tmp_path), PASS).load()["entries"]] == ["a"]


def test_recording_the_same_idea_again_replaces_it(tmp_path):
    j = jr.Journal(str(tmp_path), PASS)
    j.record({"id": "a", "took": False})
    j.record({"id": "b", "took": True})
    j.record({"id": "a", "took": True})
    assert [(e["id"], e["took"]) for e in j.load()["entries"]] == [("b", True), ("a", True)]


# ── reading what you type ────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,expected", [("182.5 65", (182.5, 65.0)), ("182.5", (182.5, None)), ("-", (None, None)), ("none", (None, None)),
                                            ("182.5 @ 65", (182.5, 65.0)), ("  182.5   65  ", (182.5, 65.0))])
def test_parse_fill(text, expected):
    assert jr.parse_fill(text) == expected


def test_a_thousands_separator_is_refused_not_guessed():
    with pytest.raises(ValueError):
        jr.parse_fill("1,250.50 10")


@pytest.mark.parametrize("text", ["", "abc", "0", "-5 10", "100 0", "nan", "inf 1", "1 2 3", "100 -2"])
def test_parse_fill_rejects_nonsense(text):
    with pytest.raises(ValueError):
        jr.parse_fill(text)


def test_slippage_is_adverse_positive():
    assert jr.slippage_pct("BUY", 100.0, 101.0) == 1.0 and jr.slippage_pct("BUY", 100.0, 99.0) == -1.0
    assert jr.slippage_pct("SELL", 100.0, 99.0) == 1.0 and jr.slippage_pct("SELL", 100.0, 101.0) == -1.0
    assert jr.slippage_pct("BUY", None, 101.0) is None and jr.slippage_pct("BUY", 100.0, None) is None


# ── the conversation ─────────────────────────────────────────────────────────────────────────────
def test_took_it_then_a_fill_is_logged_with_slippage(tmp_path):
    u, _ = ui(tmp_path)
    rows = u.buttons([summary()])
    key = jr.button_key("strategy:orb|HAL|1")
    assert rows == [[{"text": "✅ Took HAL", "callback_data": f"j:t:{key}"}, {"text": "⏭ Skip", "callback_data": f"j:s:{key}"}]]
    assert all(len(b["callback_data"].encode()) <= 64 for row in rows for b in row)
    assert "real fill" in u.handle(f"cb:j:t:{key}")
    reply = u.handle("4010 12")
    assert "Logged HAL at 4010" in reply and "0.25% worse" in reply
    [e] = u.journal.load()["entries"]
    assert e["took"] is True and e["fill_price"] == 4010.0 and e["fill_qty"] == 12.0 and e["slippage_pct"] == 0.25
    assert e["paper_entry"] == 4000.0 and e["stop"] == 3960.0 and e["t"].endswith("+05:30")
    assert u.handle("hello") is None                              # nothing is pending any more


def test_skip_is_recorded_and_took_without_a_price_is_allowed(tmp_path):
    u, _ = ui(tmp_path)
    u.buttons([summary(), summary(id="strategy:orb|ITC|2", name="ITC")])
    assert "Skipped HAL" in u.handle(f"cb:j:s:{jr.button_key('strategy:orb|HAL|1')}")
    u.handle(f"cb:j:t:{jr.button_key('strategy:orb|ITC|2')}")
    assert "without a fill price" in u.handle("-")
    got = {e["name"]: e for e in u.journal.load()["entries"]}
    assert got["HAL"]["took"] is False and got["HAL"]["slippage_pct"] is None
    assert got["ITC"]["took"] is True and got["ITC"]["fill_price"] is None


def test_a_bad_reply_keeps_waiting_and_the_bots_own_commands_still_work(tmp_path):
    u, _ = ui(tmp_path)
    u.buttons([summary()])
    u.handle(f"cb:j:t:{jr.button_key('strategy:orb|HAL|1')}")
    assert "could not read" in u.handle("about four thousand")
    assert u.handle("status") is None and u.handle("/skip") is None and u.handle("resume") is None   # pass through to the bot's commands
    assert "Logged" in u.handle("4001")


def test_the_wait_for_a_fill_expires(tmp_path):
    u, t = ui(tmp_path)
    u.buttons([summary()])
    u.handle(f"cb:j:t:{jr.button_key('strategy:orb|HAL|1')}")
    t[0] += jr.PENDING_SECONDS + 1
    assert u.handle("4010") is None and u.journal.load()["entries"] == []


def test_an_unknown_or_old_button_says_so_and_records_nothing(tmp_path):
    u, _ = ui(tmp_path)
    assert "no longer have" in u.handle("cb:j:t:deadbeef00")
    assert u.handle("cb:other:thing") is None and u.handle("cb:j:malformed") is None
    assert u.journal.load()["entries"] == []


def test_without_a_passphrase_there_are_no_buttons_and_taps_explain_why(tmp_path):
    u, _ = ui(tmp_path, passphrase="")
    assert u.buttons([summary()]) is None and not u.enabled
    assert "switched off" in u.handle("cb:j:t:abc")
    assert not os.path.exists(os.path.join(str(tmp_path), "journal.bin"))


def test_the_wrong_passphrase_is_reported_not_crashed_on(tmp_path):
    jr.Journal(str(tmp_path), PASS).record({"id": "x", "took": True})
    u, _ = ui(tmp_path, passphrase="not it")
    u.buttons([summary()])
    u.handle(f"cb:j:t:{jr.button_key('strategy:orb|HAL|1')}")
    assert "could not write your journal" in u.handle("4010 1")
    assert [e["id"] for e in jr.Journal(str(tmp_path), PASS).load()["entries"]] == ["x"]       # the real journal is untouched


def test_button_targets_are_capped_and_at_most_six_are_offered(tmp_path):
    u, _ = ui(tmp_path)
    rows = u.buttons([summary(id=f"id{i}", name=f"N{i}") for i in range(10)])
    assert len(rows) == jr.MAX_BUTTONS
    for i in range(jr.MAX_REGISTRY + 50):
        u.buttons([summary(id=f"many{i}")])
    assert len(u.registry) == jr.MAX_REGISTRY and u.changed


def test_summaries_from_each_engine_carry_what_the_entry_needs():
    st = {"id": "orb|HAL|1", "strategy": "orb", "symbol": "NSE:HAL-EQ", "name": "HAL", "kind": "stock", "dir": -1, "entry": 4000.0, "stop": 4040.0,
          "target": 3920.0, "dec": {"g": "take"}}
    s = jr.summary_from_strategy(st)
    assert s["id"] == "strategy:orb|HAL|1" and s["action"] == "SELL" and s["desk"] == "intraday_stocks" and s["paper_entry"] == 4000.0
    rb = {"id": "d-N-1", "name": "Nifty 50", "book": "Index options", "symbol": "NSE_FO|1", "action": "BUY", "entry_price": 100.0, "stop_price": 75.0,
          "contract": "NIFTY 25000 CE"}
    assert jr.summary_from_rulebook(rb)["desk"] == "index_options" and jr.summary_from_rulebook(rb)["contract"] == "NIFTY 25000 CE"
    scan = jr.summary_from_scan({"id": "NIFTY", "side": "CE", "grade": "take", "idea": {"symbol": "X", "premium": 100, "stop": 75, "target": 137, "contract": "c"}},
                                {"name": "Nifty 50", "id": "NIFTY"}, "2026-11-11")
    assert scan["id"] == "scan:2026-11-11:NIFTY:CE" and scan["name"] == "Nifty 50 CE" and scan["action"] == "BUY"


# ── Telegram and alerts ──────────────────────────────────────────────────────────────────────────
def test_telegram_sends_an_inline_keyboard_and_reads_taps(monkeypatch):
    tg = Telegram("token")
    tg.chat_id = 42
    calls = []

    def fake(method, **params):
        calls.append((method, params))
        if method == "getUpdates":
            return [{"update_id": 1, "callback_query": {"id": "q1", "data": "j:t:abc", "message": {"chat": {"id": 42}}}},
                    {"update_id": 2, "message": {"chat": {"id": 42}, "text": " 182.5 65 ", "date": 5}},
                    {"update_id": 3, "callback_query": {"id": "q2", "message": {"chat": {"id": 42}}}}]
        return {}
    monkeypatch.setattr(tg, "_call", fake)
    rows = [[{"text": "a", "callback_data": "j:t:abc"}]]
    tg.send("hi", rows)
    tg.send("plain")
    sends = [p for m, p in calls if m == "sendMessage"]
    assert sends[0]["reply_markup"] == {"inline_keyboard": rows} and "reply_markup" not in sends[1]
    got = tg.poll()
    assert [(c, t) for c, t, _ in got] == [(42, "cb:j:t:abc"), (42, "182.5 65")]
    assert tg.offset == 4
    assert [p["callback_query_id"] for m, p in calls if m == "answerCallbackQuery"] == ["q1", "q2"]
    assert next(p for m, p in calls if m == "getUpdates")["allowed_updates"] == ["message", "callback_query"]


def test_alerts_pass_buttons_only_when_there_are_some():
    sent = []
    send = lambda text, buttons=None: sent.append((text, buttons))
    a = Alerts(send, {"alerts": {"trades": True}})
    a.send("one", "trades")
    a.send("two", "trades", [[{"text": "x", "callback_data": "y"}]])
    a.sender()("three", "trades", [[{"text": "x", "callback_data": "z"}]])
    assert sent == [("one", None), ("two", [[{"text": "x", "callback_data": "y"}]]), ("three", [[{"text": "x", "callback_data": "z"}]])]
    plain = []
    Alerts(plain.append, {"alerts": {"trades": True}}).send("old-style sender", "trades")              # a one-argument sender still works
    assert plain == ["old-style sender"]
