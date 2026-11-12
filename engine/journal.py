"""The journal: which ideas you took or skipped, at what real price, kept encrypted on the public data branch.

How you use it. Alerts that carry an idea have "Took it" and "Skip" buttons in Telegram. "Took it" asks for your real fill in one reply
("182.5 65" for price and quantity, or "-" to log it without a price). The engine writes it to journal.bin. Because the data branch is
public when the repository is, that file holds only ciphertext. The dashboard decrypts it in your browser when you type the passphrase.

What it gives you. Your real fill beside the paper fill, which measures real slippage (the gap the research's cost estimates stand in for),
and a record of the ideas you ignored. It is the evidence needed before any real money is trusted to these signals.

Encryption. AES-256-GCM with a key derived from your passphrase by PBKDF2-HMAC-SHA256 (200,000 rounds), a fresh random salt and nonce on
every save. These are the standard primitives of the browser's WebCrypto API, so the page decrypts it with no extra library. The passphrase
is the JOURNAL_PASSPHRASE secret; without it the journal is switched off and no buttons are shown. Choose a long passphrase: anyone can
download the file and try guesses against it offline.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import time
from datetime import datetime
from typing import Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from .clock import IST

FILE = "journal.bin"
VERSION = 1
ITERATIONS = 200_000
AAD = b"signal-desk-journal-v1"
MAX_BUTTONS = 6               # ideas offered per message
MAX_REGISTRY = 300            # remembered button targets
PENDING_SECONDS = 1800        # how long the bot waits for your fill price
COMMAND_WORDS = {"skip", "resume", "status", "strategies"}      # the bot's own commands still work while it waits for a fill


class JournalError(Exception):
    pass


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _key(passphrase: str, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt(passphrase: str, document: dict) -> dict:
    """The JSON envelope the dashboard reads."""
    salt, nonce = os.urandom(16), os.urandom(12)
    plain = json.dumps(document, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ct = AESGCM(_key(passphrase, salt, ITERATIONS)).encrypt(nonce, plain, AAD)
    return {"v": VERSION, "kdf": "pbkdf2-sha256", "iter": ITERATIONS, "salt": _b64(salt), "nonce": _b64(nonce), "ct": _b64(ct)}


def decrypt(passphrase: str, envelope: dict) -> dict:
    if envelope.get("v") != VERSION or envelope.get("kdf") != "pbkdf2-sha256":
        raise JournalError("unsupported journal format")
    try:
        key = _key(passphrase, base64.b64decode(envelope["salt"]), int(envelope["iter"]))
        plain = AESGCM(key).decrypt(base64.b64decode(envelope["nonce"]), base64.b64decode(envelope["ct"]), AAD)
        return json.loads(plain)
    except (InvalidTag, KeyError, ValueError, TypeError) as exc:
        raise JournalError("wrong passphrase or damaged journal") from exc


def slippage_pct(action: str, paper: Optional[float], fill: Optional[float]) -> Optional[float]:
    """How much worse your fill was than the paper fill, in percent of the paper price (negative when it was better)."""
    if not paper or fill is None or paper <= 0 or fill <= 0:
        return None
    gap = (fill - paper) if action == "BUY" else (paper - fill)
    return round(gap / paper * 100, 3)


class Journal:
    """The encrypted file. Reads and writes whole; it is small."""

    def __init__(self, folder: str, passphrase: str):
        self.path = os.path.join(folder, FILE)
        self.passphrase = passphrase

    @property
    def enabled(self) -> bool:
        return bool(self.passphrase)

    def load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                envelope = json.load(f)
        except (OSError, ValueError):
            return {"schema": 1, "entries": []}
        doc = decrypt(self.passphrase, envelope)                  # a wrong passphrase raises rather than silently starting a new journal
        doc.setdefault("entries", [])
        return doc

    def save(self, doc: dict) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(encrypt(self.passphrase, doc), f, separators=(",", ":"))
        os.replace(tmp, self.path)

    def record(self, entry: dict) -> dict:
        """Adds an entry, replacing an earlier one for the same idea (you may change your mind). Returns the stored entry."""
        doc = self.load()
        doc["entries"] = [e for e in doc["entries"] if e.get("id") != entry["id"]] + [entry]
        doc["entries"] = doc["entries"][-2000:]
        self.save(doc)
        return entry


# ── summaries of an idea, as the buttons need them ───────────────────────────────────────────────
def summary_from_strategy(t: dict) -> dict:
    return {"id": "strategy:" + str(t["id"]), "name": t["name"], "desk": "intraday_stocks" if t.get("kind") == "stock" else "index_futures",
            "source": "strategy:" + str(t["strategy"]), "symbol": t["symbol"], "action": "BUY" if t["dir"] == 1 else "SELL",
            "grade": (t.get("dec") or {}).get("g"), "paper_entry": t.get("entry"), "stop": t.get("stop"), "target": t.get("target")}


def summary_from_rulebook(t: dict) -> dict:
    from .domain import rulebook_desk
    return {"id": "rulebook:" + str(t["id"]), "name": t["name"], "desk": rulebook_desk(t), "source": "rulebook",
            "symbol": t.get("symbol") or t["name"], "action": t.get("action") or "BUY", "grade": "take", "paper_entry": t.get("entry_price"),
            "stop": t.get("stop_price"), "target": None, "contract": t.get("contract")}


def summary_from_scan(read: dict, market: dict, date_iso: str) -> dict:
    idea = read.get("idea") or {}
    return {"id": f"scan:{date_iso}:{read.get('id')}:{read.get('side')}", "name": f"{market['name']} {read.get('side')}", "desk": "index_options",
            "source": "options_scan", "symbol": idea.get("symbol") or market["id"], "action": "BUY", "grade": read.get("grade"),
            "paper_entry": idea.get("premium"), "stop": idea.get("stop"), "target": idea.get("target"), "contract": idea.get("contract")}


def button_key(idea_id: str) -> str:
    return hashlib.sha1(idea_id.encode("utf-8")).hexdigest()[:10]


def parse_fill(text: str):
    """'182.5 65' -> (182.5, 65.0); '182.5' -> (182.5, None); '-' -> (None, None). Raises ValueError on anything else."""
    parts = text.replace(",", " ").replace("@", " ").split()
    if len(parts) == 1 and parts[0].lower() in ("-", "none", "na", "n/a"):
        return None, None
    if not 1 <= len(parts) <= 2:
        raise ValueError("expected a price and optionally a quantity")
    price = float(parts[0])
    qty = float(parts[1]) if len(parts) == 2 else None
    for v in (price, qty):
        if v is not None and (not math.isfinite(v) or v <= 0):
            raise ValueError("price and quantity must be positive numbers")
    return price, qty


class JournalUI:
    """The Telegram side: buttons on alerts, and the conversation that turns a tap into a journal entry."""

    def __init__(self, journal: Journal, registry: Optional[dict] = None, now_fn=time.time, log=print):
        self.journal = journal
        self.registry = registry if registry is not None else {}      # button key -> idea summary; the caller persists it
        self.pending: Optional[dict] = None
        self.now_fn = now_fn
        self.log = log
        self.changed = False

    @property
    def enabled(self) -> bool:
        return self.journal.enabled

    def buttons(self, summaries) -> Optional[list]:
        """Inline-keyboard rows for the ideas in one alert, or None when the journal is off or there is nothing to offer."""
        if not self.enabled:
            return None
        rows = []
        for s in list(summaries)[:MAX_BUTTONS]:
            key = button_key(s["id"])
            self.registry[key] = {**s, "registered": int(self.now_fn())}
            label = str(s["name"])[:18]
            rows.append([{"text": f"✅ Took {label}", "callback_data": f"j:t:{key}"}, {"text": "⏭ Skip", "callback_data": f"j:s:{key}"}])
        while len(self.registry) > MAX_REGISTRY:
            self.registry.pop(next(iter(self.registry)))
        self.changed = True
        return rows or None

    def _entry(self, s: dict, took: bool, fill=None, qty=None) -> dict:
        return {"id": s["id"], "t": datetime.fromtimestamp(self.now_fn(), IST).isoformat(timespec="seconds"), "took": took,
                "name": s["name"], "desk": s["desk"], "source": s["source"], "symbol": s["symbol"], "action": s["action"],
                "grade": s.get("grade"), "contract": s.get("contract"), "paper_entry": s.get("paper_entry"), "stop": s.get("stop"),
                "target": s.get("target"), "fill_price": fill, "fill_qty": qty,
                "slippage_pct": slippage_pct(s["action"], s.get("paper_entry"), fill) if took else None}

    def handle(self, text: str) -> Optional[str]:
        """Consumes a button tap ('cb:j:t:<key>') or the reply to a fill question. Returns the reply to send, or None if it is not ours."""
        text = text.strip()
        if text.startswith("cb:j:"):
            if not self.enabled:
                return "The journal is switched off (no JOURNAL_PASSPHRASE is set)."
            try:
                _, _, action, key = text.split(":", 3)
            except ValueError:
                return None
            s = self.registry.get(key)
            if s is None:
                return "I no longer have that idea (it is too old). Nothing was recorded."
            if action == "s":
                self.pending = None
                return self._store(self._entry(s, False)) or f"⏭ Skipped {s['name']}. Noted in your journal."
            if action == "t":
                self.pending = {"summary": s, "until": self.now_fn() + PENDING_SECONDS}
                paper = s.get("paper_entry")
                hint = f" (paper price {paper:g})" if isinstance(paper, (int, float)) else ""
                return (f"✅ <b>{s['name']}</b>: what was your real fill{hint}? Reply with price and quantity, for example "
                        f"<code>182.5 65</code>, or <code>-</code> to log it without a price.")
            return None
        if self.pending is None or text.lower().strip("/ ") in COMMAND_WORDS:
            return None
        if self.now_fn() > self.pending["until"]:
            self.pending = None
            return None
        try:
            price, qty = parse_fill(text)
        except ValueError:
            return "I could not read that. Reply with the price and quantity, for example 182.5 65, or - to skip the price."
        s = self.pending["summary"]
        self.pending = None
        entry = self._entry(s, True, price, qty)
        problem = self._store(entry)
        if problem:
            return problem
        if price is None:
            return f"📒 Logged {s['name']} as taken, without a fill price."
        slip = entry["slippage_pct"]
        extra = "" if slip is None else f" That is {abs(slip):.2f}% {'worse' if slip > 0 else 'better'} than the paper price."
        return f"📒 Logged {s['name']} at {price:g}.{extra}"

    def _store(self, entry: dict) -> Optional[str]:
        """Writes the entry. Returns None on success, or a message for the user when it could not be written."""
        try:
            self.journal.record(entry)
        except JournalError as exc:
            self.log("Journal not written:", exc)
            return f"⚠️ I could not write your journal: {exc}. Check the JOURNAL_PASSPHRASE secret matches the one you used before."
        except OSError as exc:
            self.log("Journal not written:", type(exc).__name__)
            return "⚠️ I could not save your journal just now. Please try again."
        return None
