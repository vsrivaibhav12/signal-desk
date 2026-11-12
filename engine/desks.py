"""Runs every desk once per cycle, each isolated from the others.

Before this, the session ran `engine.tick(now)` and then `desk.tick(now)` inside one try block, so an unexpected error in the rulebook
engine silently skipped the strategy desk for that minute. Now each desk runs on its own: a failure in one is recorded and reported,
and the others still run. A broker authentication error is the exception, because it affects every desk: it stops the cycle at once so
the session can log in again, exactly as before.

A "desk" is anything with `tick(now)`. The rulebook engine (engine/core.py) and the strategy desk (engine/strategies/desk.py) both are.
"""
from __future__ import annotations

import traceback
from typing import Any, Callable

from .errors import AuthError


class DeskRunner:
    def __init__(self, desks: list[tuple[str, Any]], log: Callable[[str], None] = print):
        self.desks = list(desks)
        self.log = log
        self.errors: dict[str, str] = {}              # desk id -> its latest failure, cleared by its next good cycle

    def tick(self, now) -> list[tuple[str, Exception]]:
        """Runs each desk. Returns the (desk id, error) pairs that failed this cycle. AuthError propagates immediately."""
        failed: list[tuple[str, Exception]] = []
        for name, desk in self.desks:
            try:
                desk.tick(now)
            except AuthError:
                raise
            except Exception as exc:                  # one bad minute in one desk must never stop the others
                traceback.print_exc()
                self.log(f"Desk '{name}' failed this cycle: {type(exc).__name__}: {str(exc)[:160]}")
                self.errors[name] = f"{type(exc).__name__}: {str(exc)[:160]}"
                failed.append((name, exc))
            else:
                self.errors.pop(name, None)
        return failed
