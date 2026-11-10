"""Keeps the trade log and today's status, and publishes them for the dashboard."""
import base64
import hashlib
import json
import os
import subprocess

from cryptography.fernet import Fernet, InvalidToken

from . import clock


class Store:
    def __init__(self, folder, secret_material=""):
        self.folder = folder
        os.makedirs(folder, exist_ok=True)
        key = hashlib.scrypt(secret_material.encode(), salt=b"signal-desk-v1", n=2 ** 14, r=8, p=1, dklen=32)
        self._fernet = Fernet(base64.urlsafe_b64encode(key))

    def _path(self, name):
        return os.path.join(self.folder, name)

    # The public file the dashboard reads: today's status plus every trade.
    def load_feed(self):
        try:
            with open(self._path("feed.json"), encoding="utf-8") as f:
                feed = json.load(f)
        except (OSError, ValueError):
            feed = {}
        feed.setdefault("status", {})
        feed.setdefault("trades", [])
        return feed

    def save_feed(self, feed):
        tmp = self._path("feed.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(feed, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, self._path("feed.json"))

    # The private file: login tokens, locked with a key made from your secrets.
    def load_secure(self):
        try:
            with open(self._path("secure.bin"), "rb") as f:
                return json.loads(self._fernet.decrypt(f.read()))
        except (OSError, ValueError, InvalidToken):
            return {}

    def save_secure(self, data):
        with open(self._path("secure.bin"), "wb") as f:
            f.write(self._fernet.encrypt(json.dumps(data).encode()))

    def publish(self):
        """Push the data folder to the 'data' branch. One commit per day keeps it small."""
        if not os.path.isdir(self._path(".git")):
            return

        def git(*args):
            return subprocess.run(["git", *args], cwd=self.folder, capture_output=True, text=True)

        git("add", "-A")
        if not git("status", "--porcelain").stdout.strip():
            return
        today = "data " + clock.now().date().isoformat()
        last = git("log", "-1", "--pretty=%s").stdout.strip()
        if last == today:
            git("commit", "--amend", "-m", today)
        else:
            git("commit", "-m", today)
        res = git("push", "--force", "origin", "HEAD:data")
        if res.returncode != 0:
            print("Could not publish data:", res.stderr.strip()[-200:])
