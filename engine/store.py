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

    def remote_status(self):
        """The status last published by any run, read fresh from GitHub."""
        if not os.path.isdir(self._path(".git")):
            return None
        run = lambda *a: subprocess.run(["git", *a], cwd=self.folder, capture_output=True, text=True)
        if run("fetch", "--quiet", "--depth", "1", "origin", "data").returncode != 0:
            return None
        shown = run("show", "FETCH_HEAD:feed.json")
        try:
            return json.loads(shown.stdout).get("status") if shown.returncode == 0 else None
        except ValueError:
            return None

    def publish(self, own=None):
        """Push the data folder to the 'data' branch. One commit per day keeps it small.

        own: the files this program is allowed to overwrite. Every other file is first
        refreshed from what is already online, so one program can never wipe out another's
        work (for example a backtest overwriting the live trade log).
        """
        if not os.path.isdir(self._path(".git")):
            return

        def git(*args, raw=False):
            return subprocess.run(["git", *args], cwd=self.folder, capture_output=True, text=not raw)

        if own is not None and git("fetch", "--quiet", "--depth", "1", "origin", "data").returncode == 0:
            names = git("ls-tree", "-r", "--name-only", "FETCH_HEAD").stdout.split("\n")
            for name in filter(None, names):
                if name in own or "/" in name:
                    continue
                blob = git("show", f"FETCH_HEAD:{name}", raw=True)
                if blob.returncode == 0:
                    with open(self._path(name), "wb") as f:
                        f.write(blob.stdout)
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
