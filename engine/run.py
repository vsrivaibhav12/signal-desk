"""Starts the engine, keeps it running through the session, and handles login."""
import os
import re
import sys
import time
from datetime import timedelta

import requests
import yaml

from . import clock
from .clock import at, clock_text
from .core import Engine
from .fyers import AuthError, Fyers, FyersError
from .store import Store
from .symbols import Master
from .telegram import Telegram

RUN_BUDGET = 5 * 3600 + 5 * 60  # GitHub stops a run at 6 hours, so hand over before that
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_config():
    with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def sessions(cfg, day):
    """Today's working windows: the morning markets and the evening markets."""
    groups = {"day": [], "evening": []}
    units = [m for m in cfg["markets"] if m.get("enabled", True)]
    if cfg["stocks"].get("enabled"):
        units.append(cfg["stocks"])
    for u in units:
        groups["day" if u["range"][0] < "15:00" else "evening"].append(u)
    out = []
    for name, us in groups.items():
        if us:
            start = at(day, min(u["range"][0] for u in us)) - timedelta(minutes=45)
            end = at(day, max(u["exit_by"] for u in us)) + timedelta(minutes=8)
            out.append((name, start, end))
    return out


def current_session(cfg, now):
    if now.weekday() >= 5:
        return None
    for name, start, end in sessions(cfg, now.date()):
        if start <= now <= end:
            return name, start, end
    return None


def dashboard_url(cfg):
    if cfg.get("dashboard_url"):
        return cfg["dashboard_url"]
    owner, _, repo = os.environ.get("GITHUB_REPOSITORY", "you/signal-desk").partition("/")
    return f"https://{owner.lower()}.github.io/{repo}/"


def find_code(text):
    m = re.search(r"auth_code=([^&\s]+)", text)
    if m:
        return m.group(1)
    return text if len(text) > 80 and " " not in text else None


class Session:
    def __init__(self):
        self.cfg = load_config()
        env = os.environ
        need = ["FYERS_APP_ID", "FYERS_SECRET_KEY", "TELEGRAM_BOT_TOKEN"]
        missing = [k for k in need if not env.get(k)]
        if missing:
            sys.exit("Setup is not finished. These secrets are missing in GitHub: " + ", ".join(missing))
        self.pin = env.get("FYERS_PIN", "").strip()
        self.tg = Telegram(env["TELEGRAM_BOT_TOKEN"])
        self.store = Store(os.path.join(ROOT, "_data"), env["FYERS_SECRET_KEY"] + env["TELEGRAM_BOT_TOKEN"])
        self.secure = self.store.load_secure()
        self.tg.chat_id = self.secure.get("chat_id")
        self.fy = Fyers(env["FYERS_APP_ID"], env["FYERS_SECRET_KEY"])
        self.url = dashboard_url(self.cfg)
        self.started = time.time()
        self.engine = None

    # ── who owns the bot ─────────────────────────────────────
    def claim_owner(self, until):
        if self.tg.chat_id:
            return True
        print(f"Waiting for you to open your Telegram bot (@{self.tg.me()}) and tap Start ...")
        while clock.now() < until:
            for chat, text, _ in self.tg.poll(wait=20):
                if text.lower().startswith("/start"):
                    self.tg.chat_id = self.secure["chat_id"] = chat
                    self.store.save_secure(self.secure)
                    self.tg.send("👋 Connected. I will send your trade alerts here.\n"
                                 f"Your dashboard: {self.url}")
                    return True
        return False

    # ── Fyers login ──────────────────────────────────────────
    def login(self, until):
        today = clock.now().date().isoformat()
        self.fy.token = self.secure.get("access_token")
        if self.fy.token and self.fy.probe():
            return True
        rt = self.secure.get("refresh_token")
        if rt and self.pin:
            try:
                self.fy.token = self.fy.refresh(rt, self.pin)
                if self.fy.probe():
                    self.secure.update({"access_token": self.fy.token, "access_date": today})
                    self.store.save_secure(self.secure)
                    return True
            except (AuthError, requests.RequestException) as e:
                print("Automatic login did not work:", e)
        link = self.fy.login_url(self.url)
        ask = (f"🔑 <b>Fyers login needed</b>\n1. Tap this link and log in to Fyers:\n{link}\n\n"
               "2. You will land on your dashboard showing a <b>Copy login code</b> button. Tap it.\n"
               "3. Come back here and paste the code as a message to me.")
        self.tg.send(ask)
        asked = time.time()
        while clock.now() < until:
            for chat, text, _ in self.tg.poll(wait=20):
                if chat != self.tg.chat_id:
                    continue
                code = find_code(text)
                if not code:
                    self.tg.send("That does not look like a login code. Tap the link above, log in, "
                                 "then use the Copy login code button and paste it here.")
                    continue
                try:
                    access, refresh = self.fy.exchange_code(code)
                except (AuthError, requests.RequestException) as e:
                    self.tg.send(f"Fyers did not accept that code ({e}). Codes expire quickly, "
                                 f"so please tap the link and try again:\n{link}")
                    continue
                self.fy.token = access
                self.secure.update({"access_token": access, "access_date": today})
                if refresh:
                    self.secure.update({"refresh_token": refresh, "refresh_date": today})
                self.store.save_secure(self.secure)
                self.store.publish()
                extra = ("I will log in by myself for about the next two weeks."
                         if self.pin and refresh else "I will ask again tomorrow morning.")
                self.tg.send(f"✅ Logged in to Fyers. {extra}")
                return True
            if time.time() - asked > 20 * 60:
                self.tg.send("⏳ Still waiting for your Fyers login. I cannot watch the market until then.\n" + link)
                asked = time.time()
        return False

    # ── words you can send the bot ───────────────────────────
    def commands(self, wait):
        for chat, text, sent in self.tg.poll(wait=wait):
            if chat != self.tg.chat_id or sent < self.started - 60:
                continue
            word = text.lower().strip("/ ")
            now = clock.now()
            if word == "skip":
                self.engine.set_skip(now, True)
                self.tg.send("⏸ No new trades for the rest of today. Open trades are still followed. "
                             "Send  resume  to undo.")
            elif word == "resume":
                self.engine.set_skip(now, False)
                self.tg.send("▶️ New trades are allowed again today.")
            elif word == "status":
                lines = [f"• {t['name']}: {t.get('note') or 'waiting'}" for t in self.engine.feed["status"].get("tiles", [])]
                self.tg.send("<b>Right now</b>\n" + "\n".join(lines) + f"\n\nDashboard: {self.url}")
            else:
                self.tg.send("You can send me:\n• status: what I am watching right now\n"
                             "• skip: no new trades today\n• resume: allow trades again")

    def hand_over(self):
        """Start a fresh run to continue the session before GitHub's 6-hour limit."""
        repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
        if not (repo and token):
            return
        r = requests.post(f"https://api.github.com/repos/{repo}/actions/workflows/engine.yml/dispatches",
                          headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                          json={"ref": os.environ.get("GITHUB_REF_NAME", "main")}, timeout=20)
        print("Hand-over to a fresh run:", r.status_code)

    # ── main loop ────────────────────────────────────────────
    def run(self):
        now = clock.now()
        sess = current_session(self.cfg, now)
        until = sess[2] if sess else now + timedelta(minutes=25)
        if not self.claim_owner(until):
            sys.exit(f"Nobody tapped Start on the Telegram bot @{self.tg.me()}. Open the bot, tap Start, and run again.")
        if not self.login(until):
            self.tg.send("I could not log in to Fyers in time, so I am stopping for this session.")
            sys.exit("Fyers login was not completed.")
        feed = self.store.load_feed()
        feed["status"]["bot"] = self.tg.me()
        feed["status"]["dashboard"] = self.url
        self.engine = Engine(self.cfg, self.fy, Master(), self.tg.send, feed)
        if not sess:
            nxt = next((s for s in sessions(self.cfg, now.date()) if s[1] > now), None)
            when = f"today at {clock_text(nxt[1])}" if nxt and now.weekday() < 5 else "on the next trading day"
            self.tg.send(f"✅ Setup check passed. Fyers and Telegram are both working.\n"
                         f"Markets are outside my working hours right now. I will start by myself {when}.\n"
                         f"Dashboard: {self.url}")
            self.store.save_feed(feed)
            self.store.publish()
            return
        name, _, end = sess
        self.tg.send(f"🟢 Watching the {'morning' if name == 'day' else 'evening'} markets until {clock_text(end)}.")
        last_push = 0
        while True:
            now = clock.now()
            if now > end:
                self.tg.send(self.engine.summary(now))
                feed["status"]["running"] = False
                break
            try:
                self.engine.tick(now)
            except AuthError:
                if not self.login(end):
                    break
                continue
            except FyersError as e:
                print("Fyers problem this minute:", e)
            feed["status"]["running"] = True
            if self.engine.dirty or time.time() - last_push > 300:
                self.store.save_feed(feed)
                self.store.publish()
                self.engine.dirty = False
                last_push = time.time()
            if time.time() - self.started > RUN_BUDGET:
                print("Handing over to a fresh run.")
                self.store.save_feed(feed)
                self.store.publish()
                self.hand_over()
                return
            # wait until 6 seconds past the next minute, listening for your messages meanwhile
            target = (time.time() // 60 + 1) * 60 + 6
            while True:
                left = target - time.time()
                if left <= 0:
                    break
                if left > 3:
                    self.commands(wait=min(int(left) - 1, 25))
                else:
                    time.sleep(left)
        self.store.save_feed(feed)
        self.store.publish()


if __name__ == "__main__":
    Session().run()
