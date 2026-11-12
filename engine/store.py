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

    def publish(self, own=None, own_prefix=()):
        """Publish the data folder to the 'data' branch, keeping the branch small and never clobbering anyone.

        History stays small: all of one day's publishes are folded into a single commit for that day, so the branch
        gains one commit a day however often this runs (about every two minutes), instead of hundreds.

        Nothing is overwritten blindly. Files owned by another workflow are first refreshed from the branch, and the
        replacement is pushed with a lease: it only goes through if the branch is still exactly what was just read. If
        another job pushed in the meantime, the branch is read again and the commit rebuilt, up to three times. A plain
        force push is never used.
        """
        if not os.path.isdir(self._path(".git")):
            return

        def git(*args, raw=False):
            return subprocess.run(["git", *args], cwd=self.folder, capture_output=True, text=not raw)

        def refresh_unowned():
            """Reads the branch and copies in the files other workflows own. Returns its tip, or None if unreadable."""
            if git("fetch", "--quiet", "origin", "data").returncode != 0:
                return None
            tip = git("rev-parse", "FETCH_HEAD").stdout.strip()
            names = git("ls-tree", "-r", "--name-only", "FETCH_HEAD").stdout.split("\n")
            owned = set(own or ())
            for name in filter(None, names):
                if name in owned or name.startswith(tuple(own_prefix)) or "/" in name:
                    continue
                blob = git("show", f"FETCH_HEAD:{name}", raw=True)
                if blob.returncode == 0:
                    with open(self._path(name), "wb") as f:
                        f.write(blob.stdout)
            return tip

        today = "data " + clock.now().date().isoformat()
        for attempt in range(3):
            tip = refresh_unowned() if own is not None or attempt else None
            git("add", "-A")
            dirty = bool(git("status", "--porcelain").stdout.strip())
            if not dirty and attempt == 0:
                return
            if dirty:
                have_head = git("rev-parse", "--verify", "HEAD").returncode == 0
                same_day = have_head and git("log", "-1", "--pretty=%s").stdout.strip() == today
                made = git("commit", "--amend", "-m", today) if same_day else git("commit", "-m", today)
                if made.returncode != 0:
                    print("Could not create data commit:", made.stderr.strip()[-200:])
                    return
            if tip:
                push = git("push", f"--force-with-lease=data:{tip}", "origin", "HEAD:data")
            else:
                push = git("push", "origin", "HEAD:data")          # the branch does not exist yet, or could not be read
            if push.returncode == 0:
                return
            if attempt == 2:
                print("Could not publish data (the branch kept changing underneath):", push.stderr.strip()[-300:])
                return
