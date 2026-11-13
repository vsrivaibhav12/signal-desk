"""A manual or timed engine run takes over from a run that has ended, instead of quitting because its last heartbeat is recent.  Run:  python -m pytest tests/test_takeover.py"""
import os
import sys
from datetime import timedelta
from types import SimpleNamespace

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import clock  # noqa: E402
from engine.run import ACTIVE_RUN_STATES, Session, alive_elsewhere, run_is_alive  # noqa: E402


def reply(status, body=None):
    def parse():
        if body is None:
            raise ValueError("not json")
        return body
    return SimpleNamespace(status_code=status, json=parse)


def get_returning(status, body=None):
    return lambda *a, **k: reply(status, body)


@pytest.mark.parametrize("state,alive", [("in_progress", True), ("queued", True), ("waiting", True), ("completed", False), ("cancelled", False)])
def test_a_run_is_alive_only_while_github_says_it_is_going(state, alive):
    assert run_is_alive("123", "o/r", "tok", get_returning(200, {"status": state})) is alive
    assert set(ACTIVE_RUN_STATES) >= {"in_progress", "queued"}


def test_a_run_that_no_longer_exists_is_dead():
    assert run_is_alive("123", "o/r", "tok", get_returning(404)) is False


def test_not_knowing_counts_as_alive_so_a_healthy_session_is_never_taken():
    assert run_is_alive("123", None, None, get_returning(404)) is True            # no token or repository: cannot ask
    assert run_is_alive("local", "o/r", "tok", get_returning(404)) is True        # a run on your own PC has no GitHub run to ask about
    assert run_is_alive(None, "o/r", "tok", get_returning(404)) is True
    assert run_is_alive("123", "o/r", "tok", get_returning(500)) is True          # GitHub having a bad moment
    assert run_is_alive("123", "o/r", "tok", get_returning(403)) is True          # a token without the right to look
    assert run_is_alive("123", "o/r", "tok", get_returning(200)) is True          # an answer that is not understood

    def boom(*a, **k):
        raise requests.ConnectionError("down")
    assert run_is_alive("123", "o/r", "tok", boom) is True


def test_the_request_is_a_read_with_the_workflows_own_token():
    seen = {}

    def get(url, **kw):
        seen.update(url=url, **kw)
        return reply(200, {"status": "completed"})
    run_is_alive("999", "me/signal-desk", "secret", get)
    assert seen["url"] == "https://api.github.com/repos/me/signal-desk/actions/runs/999" and seen["headers"]["Authorization"] == "Bearer secret"


# ── the heartbeat check at every publish ─────────────────────────────────────────────────────────
def status(run_id, age_s=60):
    return {"running": True, "run_id": run_id, "heartbeat": (clock.now() - timedelta(seconds=age_s)).isoformat(timespec="seconds")}


def beat_with(remote, takeover_from):
    published = []
    stub = SimpleNamespace(
        last_beat=0, engine=None, takeover_from=takeover_from, feed={"status": {}}, write_live=lambda: None,
        store=SimpleNamespace(remote_status=lambda: remote, save_feed=lambda feed: None, publish=lambda **k: published.append(k)))
    Session.beat(stub, force=True)
    return published


def test_a_recent_heartbeat_from_another_run_still_makes_a_normal_run_leave():
    with pytest.raises(SystemExit):
        beat_with(status("111"), takeover_from=None)


def test_a_run_that_found_the_old_one_dead_may_publish_over_its_heartbeat():
    assert len(beat_with(status("111"), takeover_from="111")) == 1


def test_the_override_is_only_for_the_run_that_was_found_dead():
    with pytest.raises(SystemExit):
        beat_with(status("222"), takeover_from="111")                              # a different run has claimed it since


def test_nothing_changes_when_the_other_runs_heartbeat_is_old_or_it_is_us():
    assert not alive_elsewhere(status("111", age_s=400), clock.now())
    assert not alive_elsewhere(status("local"), clock.now(), me="local")
    assert len(beat_with(status("111", age_s=400), takeover_from=None)) == 1
