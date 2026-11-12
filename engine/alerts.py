"""What gets sent to Telegram. One place decides, so the phone only buzzes for things you can act on.

Kinds, and whether they are on by default:
    brief    on   one message at the open: results due, market read, how many shares are on the watchlist
    trades   on   a share trade worth taking (entry, stop, target)
    exits    on   how a trade you were told about ended
    options  on   a call or put idea that is worth taking, and the options desk's own entries and exits
    summary  on   one short message after the close
    faults   on   the tool cannot get prices, or something it cannot recover from (the same fault is sent once an hour)
    movers   off  trend-setter notes (they are on the dashboard)
    news     off  news digests (they are on the dashboard and folded into the brief)
    info     off  "started", "restarted", "setup check passed" and similar chatter
Change any of them under  alerts:  in config.yaml.
"""
import time

DEFAULTS = {"brief": True, "trades": True, "exits": True, "options": True, "summary": True, "faults": True,
            "movers": False, "news": False, "info": False}
FAULT_REPEAT = 3600


class Alerts:
    def __init__(self, send, cfg=None):
        self._send = send
        self.on = dict(DEFAULTS)
        self.on.update((cfg or {}).get("alerts") or {})
        self._last = {}
        self.sent = 0
        self.held = 0

    def send(self, text, kind="info"):
        if not self.on.get(kind, False):
            self.held += 1
            return False
        if kind == "faults":
            key = text[:60]
            if time.time() - self._last.get(key, 0) < FAULT_REPEAT:
                self.held += 1
                return False
            self._last[key] = time.time()
        self._send(text)
        self.sent += 1
        return True

    def sender(self):
        """A sender that takes (text, kind), for code that names the kind itself. Marked so it can tell."""
        def send(text, kind="info"):
            return self.send(text, kind)
        send.takes_kind = True
        return send

    def to(self, kind):
        """A one-argument sender fixed to one kind, for code that only knows how to call notify(text)."""
        return lambda text, k=kind: self.send(text, k)
