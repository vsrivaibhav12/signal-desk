"""Persistent Signal Desk supervisor.

This process stays alive continuously. It runs the existing trading Session only
inside configured exchange sessions and sleeps between them. News intelligence
runs as a separate always-on service so headlines continue through nights,
weekends and exchange holidays.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import time
import traceback
import threading
from datetime import timedelta
from pathlib import Path

import yaml

from . import clock
from .run import ROOT, Session, current_session, load_config, sessions
from .store import ENGINE_PREFIXES, Store

STOP = False
STOP_EVENT = threading.Event()
DATA_DIR = Path(ROOT) / "_data"
SUPERVISOR_PATH = DATA_DIR / "supervisor.json"
ENGINE_SHARED_FILES = {
    "feed.json", "secure.bin", "strategies.json", "results.json", "volbase.json",
    "holidays.json", "options_tape.jsonl", "intelligence.json", "supervisor.json", "live.json", "journal.bin",
}


def _signal_handler(signum, frame):
    global STOP
    STOP = True
    STOP_EVENT.set()


def _next_session(cfg, now):
    """Return the next configured trading-session start; no exchange calendar guesswork."""
    day = now.date()
    for offset in range(0, 8):
        candidate = day + timedelta(days=offset)
        if candidate.weekday() >= 5:
            continue
        starts = sorted((start, end) for _, start, end in sessions(cfg, candidate))
        for start, _ in starts:
            if start > now:
                return start
    return None


class TradingSupervisor:
    def __init__(self, root: Path = Path(ROOT)):
        self.root = Path(root)
        self.data_dir = self.root / "_data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "supervisor.json"
        self.cfg = yaml.safe_load((self.root / "config.yaml").read_text(encoding="utf-8")) or {}
        self.store = Store(str(self.data_dir), "")
        self.state = {"schema": 1, "status": {}}
        self.last_publish = 0.0
        self.last_error = ""

    def _write(self, phase: str, next_start=None, detail=""):
        now = clock.now()
        status = self.state.setdefault("status", {})
        status.update({
            "running": phase != "stopped",
            "worker_mode": "continuous",
            "phase": phase,
            "heartbeat": now.isoformat(timespec="seconds"),
            "next_session": next_start.isoformat(timespec="minutes") if next_start else None,
            "detail": detail or None,
            "last_error": self.last_error or None,
        })
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
        if time.monotonic() - self.last_publish >= 115:
            # Keep the engine/news-owned files from being overwritten while
            # refreshing the remote branch tip in a shared always-on checkout.
            self.store.publish(own={"supervisor.json"}, own_prefix=ENGINE_PREFIXES, preserve_local=ENGINE_SHARED_FILES)
            self.last_publish = time.monotonic()

    def once(self):
        """One control-plane evaluation, used by tests and diagnostics."""
        now = clock.now()
        active = current_session(self.cfg, now)
        nxt = None if active else _next_session(self.cfg, now)
        return {"active": active[0] if active else None,
                "next_session": nxt.isoformat(timespec="minutes") if nxt else None}

    def _lock(self):
        """Cross-platform process lock prevents two supervisors on one host."""
        path = self.data_dir / "trading-supervisor.lock"
        handle = open(path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                if not handle.read(1):
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except (ImportError, OSError, BlockingIOError):
            handle.close()
            return None

    def run(self):
        global STOP
        lock = self._lock()
        if lock is None:
            print("Another trading supervisor is already running; exiting.")
            return
        signal.signal(signal.SIGTERM, _signal_handler)
        if hasattr(signal, "SIGINT"):
            signal.signal(signal.SIGINT, _signal_handler)
        print("Signal Desk trading supervisor is running continuously; market scans remain session-aware.")
        try:
            while not STOP:
                now = clock.now()
                try:
                    active = current_session(self.cfg, now)
                    if active:
                        self._write("starting", detail=f"Entering {active[0]} market session")
                        print(f"Starting trading session: {active[0]} until {clock.clock_text(active[2])} IST")
                        try:
                            Session().run()
                            self.last_error = ""
                        except SystemExit as exc:
                            self.last_error = f"Trading session exited: {str(exc)[:180]}"
                            print(self.last_error)
                        except Exception as exc:
                            traceback.print_exc()
                            self.last_error = f"Trading session failed: {type(exc).__name__}: {str(exc)[:140]}"
                        if not STOP:
                            # Session.run returns at session close, on handover,
                            # or after a recoverable setup/auth issue. Re-evaluate
                            # the exchange window rather than killing the daemon.
                            self._write("resting" if not current_session(self.cfg, clock.now()) else "recovering",
                                        _next_session(self.cfg, clock.now()), self.last_error)
                            STOP_EVENT.wait(20 if self.last_error else 5)
                    else:
                        nxt = _next_session(self.cfg, now)
                        self._write("resting", nxt, "Waiting for the next configured exchange session")
                        delay = 60
                        if nxt:
                            delay = max(5, min(delay, int((nxt - now).total_seconds())))
                        STOP_EVENT.wait(delay)
                except Exception as exc:
                    traceback.print_exc()
                    self.last_error = f"Supervisor loop: {type(exc).__name__}: {str(exc)[:140]}"
                    self._write("error", _next_session(self.cfg, clock.now()), self.last_error)
                    STOP_EVENT.wait(30)
        finally:
            now = clock.now()
            self.state.setdefault("status", {}).update({
                "running": False, "phase": "stopped", "heartbeat": now.isoformat(timespec="seconds")
            })
            try:
                self._write("stopped", _next_session(self.cfg, now), "Supervisor stopped")
            except Exception:
                pass
            try:
                if os.name == "nt":
                    import msvcrt
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
            lock.close()


def main():
    parser = argparse.ArgumentParser(description="Signal Desk persistent trading supervisor")
    parser.add_argument("--once", action="store_true", help="report current/next configured session and exit")
    parser.add_argument("--daemon", action="store_true", help="run continuously (default)")
    args = parser.parse_args()
    worker = TradingSupervisor()
    if args.once:
        print(json.dumps(worker.once(), indent=2))
    else:
        worker.run()


if __name__ == "__main__":
    main()
