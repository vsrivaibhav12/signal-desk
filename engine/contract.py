"""Validation against the published contract (contract/schemas/*.schema.json).

The engine does not validate at run time (it must never stop because a check library is missing); the tests and CI do. Needs the
`jsonschema` package, which is a development dependency only (requirements-dev.txt).
"""
from __future__ import annotations

import json
import os

SCHEMA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "contract", "schemas")


def schema(name: str) -> dict:
    """The schema called `name` ('live', 'manifest' or 'ledger-trade')."""
    with open(os.path.join(SCHEMA_DIR, f"{name}.schema.json"), encoding="utf-8") as f:
        return json.load(f)


def errors(name: str, document) -> list[str]:
    """Every way `document` breaks the schema, as readable lines. Empty means valid."""
    from jsonschema import Draft202012Validator
    validator = Draft202012Validator(schema(name))
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
            for e in sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))]


def assert_valid(name: str, document) -> None:
    problems = errors(name, document)
    if problems:
        raise AssertionError(f"{name} contract broken:\n  " + "\n  ".join(problems[:15]))
