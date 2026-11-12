"""manifest.json: the dashboard's index of everything published on the data branch.

For every published file it lists the size, a SHA-256 and when the content last changed, so the page reads this one small file
first, knows how old each file is, and fetches only what changed. The timestamp moves only when a file's content changes, and the
manifest itself is rewritten only when something in it changed, so an idle publish creates no new commit.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

CONTRACT = 1
MANIFEST_NAME = "manifest.json"

# Files the dashboard must not see listed, and never-published scratch files.
SKIP_NAMES = {MANIFEST_NAME, "secure.bin"}
SKIP_SUFFIXES = (".lock", ".tmp")
SKIP_DIRS = {".git"}

# Which contract schema (contract/schemas/<name>.schema.json) a published path follows. Everything else is listed with no schema.
SCHEMAS = {"live.json": "live@1", "swing.json": "swing@1"}
SCHEMA_PREFIXES = (("ledger-", "ledger-trade@1"), ("ledger_swing-", "ledger-trade@1"))


def schema_for(path: str):
    if path in SCHEMAS:
        return SCHEMAS[path]
    for prefix, name in SCHEMA_PREFIXES:
        if path.startswith(prefix):
            return name
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def scan(folder: str):
    """Yields (published path, absolute path) for every file the dashboard may read."""
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if name in SKIP_NAMES or name.endswith(SKIP_SUFFIXES) or name.startswith("."):
                continue
            full = os.path.join(root, name)
            yield os.path.relpath(full, folder).replace(os.sep, "/"), full


def build(folder: str, previous: dict | None = None, now_fn=_now_iso) -> dict:
    """The manifest for what is in `folder`. `updated_at` is carried over for any file whose content did not change."""
    old = {f["path"]: f for f in (previous or {}).get("files", []) if isinstance(f, dict) and "path" in f}
    files = []
    for path, full in scan(folder):
        try:
            sha = _sha256(full)
            size = os.path.getsize(full)
        except OSError:
            continue                                    # vanished between listing and reading
        before = old.get(path)
        stamp = before["updated_at"] if before and before.get("sha256") == sha and before.get("updated_at") else now_fn()
        files.append({"path": path, "bytes": size, "sha256": sha, "updated_at": stamp, "schema": schema_for(path)})
    files.sort(key=lambda f: f["path"])
    generated = (previous or {}).get("generated_at") or now_fn()
    return {"contract": CONTRACT, "generated_at": generated, "files": files}


def write(folder: str, now_fn=_now_iso) -> bool:
    """Rewrites folder/manifest.json if (and only if) its content changed. Returns True when it was rewritten."""
    path = os.path.join(folder, MANIFEST_NAME)
    previous = None
    try:
        with open(path, encoding="utf-8") as f:
            previous = json.load(f)
    except (OSError, ValueError):
        previous = None
    fresh = build(folder, previous, now_fn)
    if previous and previous.get("contract") == fresh["contract"] and previous.get("files") == fresh["files"]:
        return False
    fresh["generated_at"] = now_fn()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(fresh, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
    return True
