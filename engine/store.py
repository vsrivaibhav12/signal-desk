"""Keeps the trade log and today's status, and publishes them for the dashboard."""
import base64
import hashlib
import json
import os
import subprocess
import threading
from contextlib import contextmanager

from cryptography.fernet import Fernet, InvalidToken

from . import clock


class Store:
    _publish_locks_guard = threading.Lock()
    _publish_locks = {}
    _publish_local = threading.local()

    def __init__(self, folder, secret_material=""):
        self.folder = folder
        os.makedirs(folder, exist_ok=True)
        self._configure_git_auth()
        key = hashlib.scrypt(secret_material.encode(), salt=b"signal-desk-v1", n=2 ** 14, r=8, p=1, dklen=32)
        self._fernet = Fernet(base64.urlsafe_b64encode(key))

    def _path(self, name):
        return os.path.join(self.folder, name)

    @staticmethod
    def _configure_git_auth():
        """Inject a GitHub token into process-local Git config, never into origin URLs."""
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        if not token:
            return
        try:
            count = int(os.environ.get("GIT_CONFIG_COUNT", "0") or 0)
        except ValueError:
            count = 0
        for index in range(count):
            key = os.environ.get(f"GIT_CONFIG_KEY_{index}", "")
            if key.startswith("url.https://x-access-token:") and key.endswith("@github.com/.insteadOf"):
                return
        os.environ[f"GIT_CONFIG_KEY_{count}"] = f"url.https://x-access-token:{token}@github.com/.insteadOf"
        os.environ[f"GIT_CONFIG_VALUE_{count}"] = "https://github.com/"
        os.environ["GIT_CONFIG_COUNT"] = str(count + 1)

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

    @contextmanager
    def _publish_lock(self):
        """Serialise publishers sharing one local _data checkout (VPS/office deployment)."""
        lock_path = os.path.join(os.path.abspath(self.folder), ".signal-desk-publish.lock")
        key = os.path.abspath(lock_path)
        with self._publish_locks_guard:
            local_lock = self._publish_locks.setdefault(key, threading.RLock())
        local_lock.acquire()
        depths = getattr(self._publish_local, "depths", {})
        depth = depths.get(key, 0)
        handle = None
        try:
            if depth == 0:
                os.makedirs(os.path.dirname(lock_path), exist_ok=True)
                handle = open(lock_path, "a+b")
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    if not handle.read(1):
                        handle.write(b"0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            depths[key] = depth + 1
            self._publish_local.depths = depths
            yield
        finally:
            if depth == 0 and handle is not None:
                try:
                    if os.name == "nt":
                        import msvcrt
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except (ImportError, OSError):
                    pass
                handle.close()
            if depth:
                depths[key] = depth
            else:
                depths.pop(key, None)
            local_lock.release()

    def publish(self, own=None, own_prefix=(), preserve_local=()):
        """Publish the data folder to the 'data' branch, serialising local writers.

        ``preserve_local`` lists files owned by another process sharing this checkout.
        They are not overwritten with the remote copy during refresh; all publishers
        still fetch the remote tip and use a lease before pushing.
        """
        with self._publish_lock():
            return self._publish_unlocked(own=own, own_prefix=own_prefix, preserve_local=preserve_local)

    def _publish_unlocked(self, own=None, own_prefix=(), preserve_local=()):
        """Implementation for publish(); caller holds the local publication lock.

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

        # Runtime lock files and atomic-write temporaries must never enter the public data branch.
        exclude = self._path(".git/info/exclude")
        try:
            existing = set()
            if os.path.exists(exclude):
                with open(exclude, encoding="utf-8") as reader:
                    existing = set(reader.read().splitlines())
            with open(exclude, "a", encoding="utf-8") as writer:
                for pattern in ("*.lock", "*.tmp"):
                    if pattern not in existing:
                        writer.write(pattern + "\n")
        except OSError:
            pass

        def refresh_unowned():
            """Reads the branch and copies in the files other workflows own. Returns its tip, or None if unreadable."""
            if git("fetch", "--quiet", "origin", "data").returncode != 0:
                return None
            tip = git("rev-parse", "FETCH_HEAD").stdout.strip()
            names = git("ls-tree", "-r", "--name-only", "FETCH_HEAD").stdout.split("\n")
            owned = set(own or ())
            protected = set(preserve_local or ())
            for name in filter(None, names):
                if name in owned or name in protected or name.startswith(tuple(own_prefix)) or "/" in name:
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
