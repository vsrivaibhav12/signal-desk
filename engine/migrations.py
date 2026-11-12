"""Schema upgrades that keep the trade record.

Until now a change of `SCHEMA` threw the whole log away (engine/run.py started "with a clean slate") and the strategy desk did the
same to strategies.json. Now:

  1. If a registered migration exists for each version step, the document is upgraded in place.
  2. Otherwise its settled trades are written to the ledger first (engine/ledger.py), and only then is the document reset.

Nothing here is needed while SCHEMA stays the same. To change a document's layout, bump SCHEMA and register a function for the old
version in FEED_MIGRATIONS or STRATEGY_MIGRATIONS that returns the upgraded document.
"""
from __future__ import annotations

from typing import Callable

from . import ledger as ledger_mod
from .schema import SCHEMA

Migration = Callable[[dict], dict]

FEED_MIGRATIONS: dict[int, Migration] = {}
STRATEGY_MIGRATIONS: dict[int, Migration] = {}


class NoMigration(Exception):
    """No registered path from the document's version to the wanted one."""


def migrate(doc: dict, target: int, registry: dict[int, Migration]) -> dict:
    """Applies registry[v] repeatedly until the document is at `target`. Raises NoMigration if a step is missing."""
    version = doc.get("schema")
    if not isinstance(version, int) or version > target:
        raise NoMigration(f"cannot migrate from schema {version!r} to {target}")
    while version < target:
        step = registry.get(version)
        if step is None:
            raise NoMigration(f"no migration from schema {version} to {version + 1}")
        doc = step(doc)
        if doc.get("schema") != version + 1:
            raise NoMigration(f"migration from schema {version} did not produce schema {version + 1}")
        version += 1
    return doc


def upgrade_feed(feed: dict, store_folder: str, cost_table: dict | None = None, log=print) -> dict:
    """The rulebook feed at the current SCHEMA: migrated if possible, otherwise archived to the ledger and reset."""
    if feed.get("schema") == SCHEMA:
        return feed
    try:
        return migrate(feed, SCHEMA, FEED_MIGRATIONS)
    except NoMigration as why:
        trades = feed.get("trades") or []
        if trades:                                       # a brand-new install has nothing to keep and nothing to report
            n = ledger_mod.sync(ledger_mod.Ledger(store_folder), rulebook_trades=trades, cost_table=cost_table, log=log)
            log(f"Feed schema changed ({why}); {n} settled trade(s) moved to the ledger before the reset.")
        return {"status": {}, "trades": [], "schema": SCHEMA}


def upgrade_strategy_state(state: dict, store_folder: str, log=print) -> tuple[dict, bool]:
    """The strategy state at the current SCHEMA. Returns (state, was_reset). A reset archives approved closed trades first."""
    if not state or state.get("schema") == SCHEMA:
        return state, False
    try:
        return migrate(state, SCHEMA, STRATEGY_MIGRATIONS), False
    except NoMigration as why:
        trades = state.get("trades") or []
        if trades:
            n = ledger_mod.sync(ledger_mod.Ledger(store_folder), strategy_trades=trades, log=log)
            log(f"Strategy state schema changed ({why}); {n} settled trade(s) moved to the ledger before the reset.")
        return {}, True
