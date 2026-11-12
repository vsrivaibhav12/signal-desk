"""Starts the engine, keeps it running through the session, and handles login.

GitHub's timer starts a run every 10 minutes during market hours. Only one run
does the work: it writes a "heartbeat" to the data folder every two minutes.
Any other run that starts sees a fresh heartbeat and leaves at once. If the
heartbeat goes stale (the working run died), the next run takes over and
carries on from the saved state. That is what keeps the tool awake.
"""
import os
import secrets
import re
import sys
import time
import traceback
from datetime import datetime, timedelta

import requests
import yaml

from . import clock
from .clock import at, clock_text
from .core import Engine
from . import brokers
from .alerts import Alerts
from .leaders import Leaders
from .results import Results
from .tradingcal import TradingCalendar
from .schema import SCHEMA
from .watch import Watch
from .errors import AuthError
from .fyers import Fyers
from .store import Store
from .strategies.desk import StrategyDesk
from .telegram import Telegram

RUN_BUDGET = 5 * 3600 + 5 * 60   # GitHub stops a run at 6 hours, so hand over before that
BEAT_EVERY = 110                 # seconds between heartbeats
STALE_AFTER = 300                # a heartbeat older than this means the other run is dead
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE_FILES = {"feed.json", "secure.bin", "strategies.json", "results.json", "volbase.json", "holidays.json", "options_tape.jsonl"}
RUN_ID = os.environ.get("GITHUB_RUN_ID", "local")
BY_TIMER = os.environ.get("GITHUB_EVENT_NAME") == "schedule"


def load_config():
    with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def sessions(cfg, day):
    """Today's working windows: the morning markets and the evening markets."""
    groups = {"day": [], "evening": []}
    units = [m for m in cfg["markets"] if m.get("enabled", True)]
    if cfg["stocks"].get("enabled"):
        units.append(cfg["stocks"])
    sc = cfg.get("strategies") or {}
    if sc.get("enabled", True):                       # the strategy desk watches shares until its own exit time
        units.append({"range": ["09:15", "09:30"], "exit_by": sc.get("exit_by", "15:15")})
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


def alive_elsewhere(status, now, me=None):
    """True when a different run is doing the work and checked in recently."""
    me = me or RUN_ID
    if not status or not status.get("running") or status.get("run_id") in (None, me):
        return False
    try:
        age = (now - datetime.fromisoformat(status["heartbeat"])).total_seconds()
    except (KeyError, ValueError, TypeError):
        return False
    return age < STALE_AFTER


class Session:
    def __init__(self):
        self.cfg = load_config()
        env = os.environ
        self.kind = brokers.primary(self.cfg)
        self.label = brokers.label(self.kind)
        need = brokers.required_secrets(self.kind) + ["TELEGRAM_BOT_TOKEN"]
        missing = [k for k in need if not env.get(k)]
        if missing:
            sys.exit("Setup is not finished. These secrets are missing in GitHub: " + ", ".join(missing))
        self.notes = brokers.adjust_config(self.cfg, self.kind)
        self.pin = env.get("FYERS_PIN", "").strip()
        self.tg = Telegram(env["TELEGRAM_BOT_TOKEN"])
        self.store = Store(os.path.join(ROOT, "_data"), brokers.secret_material(env) + env["TELEGRAM_BOT_TOKEN"])
        self.secure = self.store.load_secure()
        configured_chat = env.get("TELEGRAM_CHAT_ID", "").strip()
        if configured_chat:
            try:
                configured_chat = int(configured_chat)
            except ValueError:
                sys.exit("TELEGRAM_CHAT_ID must be a numeric Telegram chat ID.")
        self.tg.chat_id = configured_chat or self.secure.get("chat_id")
        self.fy, self.master = brokers.build(self.cfg, env)
        self.url = dashboard_url(self.cfg)
        self.started = time.time()
        self.engine = None
        self.alerts = Alerts(self.tg.send, self.cfg)
        self.feed = self.store.load_feed()
        if self.feed.get("schema") != SCHEMA:             # a log from an older version of the tool: start with a clean slate
            self.feed = {"status": {}, "trades": [], "schema": SCHEMA}
        self.last_beat = 0

    # ── heartbeat: proves this run is alive, and lets only one run work ──
    def beat(self, running=True, force=False):
        if not force and time.time() - self.last_beat < BEAT_EVERY and not (self.engine and self.engine.dirty):
            return
        if alive_elsewhere(self.store.remote_status(), clock.now()):
            print("Another run has taken over. Leaving quietly.")
            sys.exit(0)
        st = self.feed["status"]
        st.update({"run_id": RUN_ID, "running": running, "heartbeat": clock.now().isoformat(timespec="seconds")})
        self.store.save_feed(self.feed)
        self.store.publish(own=ENGINE_FILES, own_prefix=("chartlive_",))
        if self.engine:
            self.engine.dirty = False
        self.last_beat = time.time()

    # ── who owns the bot ─────────────────────────────────────
    def claim_owner(self, until):
        if self.tg.chat_id:
            return True
        pairing = os.environ.get("TELEGRAM_PAIRING_CODE", "").strip()
        if not pairing:
            print("No saved Telegram chat. Set TELEGRAM_PAIRING_CODE (a long random secret) or TELEGRAM_CHAT_ID; refusing an unpaired /start.")
            return False
        print(f"Waiting for a paired /start {pairing[:2]}… on your Telegram bot (@{self.tg.me()}) ...")
        expected = "/start " + pairing
        while clock.now() < until:
            self.beat()
            for chat, text, _ in self.tg.poll(wait=20):
                if secrets.compare_digest(text.strip().encode("utf-8"), expected.encode("utf-8")):
                    self.tg.chat_id = self.secure["chat_id"] = chat
                    self.store.save_secure(self.secure)
                    self.tg.send("👋 Connected. I will send your trade alerts here.\n"
                                 f"Your dashboard: {self.url}")
                    return True
        return False

    # ── Kotak Neo: no login, only a check that the consumer key works ──
    def check_prices(self, until):
        for attempt in range(4):
            if self.fy.probe():
                return True
            if clock.now() >= until:
                break
            self.beat()
            time.sleep(15)
        self.alerts.send("⚠️ <b>Kotak Neo is not giving me prices.</b>\nEither the Consumer Key (the KOTAK_CONSUMER_KEY secret) "
                     "was not accepted, or Kotak is down. Check the key in the Kotak Neo app: More, Trade API. "
                     "I will try again at the next check.", "faults")
        return False

    # ── Fyers login ──────────────────────────────────────────
    def login(self, until):
        if self.kind == "kotak":
            return self.check_prices(until)
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
        self.feed["status"]["phase"] = "login"
        self.beat(force=True)
        asked = time.time()
        while clock.now() < until:
            self.beat()
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
                self.feed["status"]["phase"] = "watching"
                self.beat(force=True)
                extra = ("I will log in by myself for about the next two weeks."
                         if self.pin and refresh else "I will ask again tomorrow morning.")
                self.tg.send(f"✅ Logged in to Fyers. {extra}")
                return True
            if time.time() - asked > 15 * 60:
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
            elif word == "strategies":
                self.tg.send(self.engine_desk_text())
            elif word == "status":
                lines = [f"• {t['name']}: {t.get('note') or 'waiting'}" for t in self.feed["status"].get("tiles", [])]
                self.tg.send("<b>Right now</b>\n" + "\n".join(lines) + f"\n\nDashboard: {self.url}")
            else:
                self.tg.send("You can send me:\n• status: what I am watching right now\n"
                             "• strategies: how each strategy is doing today\n"
                             "• skip: no new trades today\n• resume: allow trades again")

    def catalysts(self):
        """Names of shares with news that can move them today (results, orders, deals, broker calls)."""
        if not self.engine:
            return []
        return [i["symbol"] for i in (self.engine.day.get("news") or []) if i.get("tag") in ("Results", "Order win", "Deal", "Broker view")]

    def refresh_results(self, now):
        try:                                                      # both run in the background; neither can hold up the engine
            self.cal.refresh_async(now)
            self.results.refresh_async(now)
        except Exception as e:
            print("Results calendar:", e)

    def brief(self, now):
        """One message at the open: who reports, and what the tool is watching. Sent once a day."""
        today = now.date().isoformat()
        st = self.feed["status"]
        if st.get("brief_sent") == today or now < at(now.date(), "09:00") or now > at(now.date(), "10:30"):
            return
        if not self.cal.is_trading_day(now.date()):
            return                                                # an exchange holiday: nothing to brief
        st["brief_sent"] = today
        g = self.results.groups(now.date())
        name = lambda ev: ", ".join(sorted({e["symbol"] for e in ev})) or "none"
        lines = [f"☀️ <b>Today, {now.strftime('%a %d %b')}</b>",
                 f"Results just out (watch the reaction): {name(g['last'])}",
                 f"Results today: {name(g['today'])}",
                 f"Results before the next session: {name(g['next'])}"]
        heavy = self.results.heavyweights(now.date())
        if heavy:
            lines.append(f"⚠️ Index heavyweights reporting: {', '.join(heavy)}. Index moves can be sharper.")
        if not self.results.state.get("ok"):
            lines.append("(The results calendar could not be read; the list may be incomplete.)")
        news = self.catalysts()
        if news:
            lines.append(f"In the news: {', '.join(sorted(set(news))[:8])}")
        health = self.leaders.health_line() if self.leaders.ok(now.date()) else None
        if health:
            lines.append(health)
            lead = sorted({w["symbol"] for w in self.results.watch(now.date()) if self.leaders.is_leader(w["symbol"])})
            if lead:
                lines.append(f"Reporting and also relative-strength leaders: {', '.join(lead)}")
        lines.append(f"Dashboard: {self.url}")
        self.alerts.send("\n".join(lines), "brief")

    def engine_desk_text(self):
        d = getattr(self, "desk", None)
        return d.summary_text(clock.now().date().isoformat()) if d else "The strategy desk has not started yet."

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
        before = dict(self.feed["status"])
        if alive_elsewhere(before, now):
            print("The engine is already running in another run and is healthy. Nothing to do.")
            if not BY_TIMER and self.tg.chat_id:
                self.tg.send(f"✅ I am already awake and watching. My last check-in was at "
                             f"{clock_text(datetime.fromisoformat(before['heartbeat']))}. No need to start me.")
            return
        sess = current_session(self.cfg, now)
        if not sess and BY_TIMER:
            print("Outside market hours. Nothing to do.")
            return
        until = sess[2] if sess else now + timedelta(minutes=25)
        self.feed["status"].update({"bot": self.tg.me(), "dashboard": self.url, "phase": "starting"})
        self.beat(force=True)          # claim the work
        time.sleep(6)
        self.beat(force=True)          # and make sure no other run claimed it at the same moment
        if not self.claim_owner(until):
            self.beat(running=False, force=True)
            sys.exit(f"Nobody tapped Start on the Telegram bot @{self.tg.me()}. Open the bot, tap Start, and run again.")
        if not self.login(until):
            self.alerts.send(f"I could not get prices from {self.label}, so I am stopping for this session.", "faults")
            self.beat(running=False, force=True)
            sys.exit(f"{self.label} prices were not available.")
        feed = self.feed
        self.engine = Engine(
            self.cfg, self.fy, self.master, self.alerts.sender(), feed,
            portfolio_provider=lambda: getattr(getattr(self, "desk", None), "state", {}).get("trades", []),
        )
        data_dir = os.path.join(ROOT, "_data")
        self.leaders = Leaders(data_dir)
        self.cal = TradingCalendar(data_dir, extra=self.cfg.get("holidays"))
        self.results = Results(data_dir, self.engine.universe, extra=(self.cfg.get("results") or {}).get("extra"), cal=self.cal)
        self.watch = Watch(data_dir, [f"NSE:{n}-EQ" for n in self.engine.universe], self.cfg)
        self.desk = StrategyDesk(self.cfg, self.fy, self.tg.send, data_dir, self.engine.universe,
                                 [m for m in self.cfg["markets"] if m.get("kind") == "index"],
                                 results=self.results, watch=self.watch, master=self.master, alerts=self.alerts,
                                 catalysts=self.catalysts, leaders=self.leaders,
                                 heartbeat=self.beat, options_trades=lambda: self.feed["trades"],
                                 flush=lambda: (self.desk.save(), setattr(self.desk, "dirty", False), self.beat(force=True)))
        if not sess:
            nxt = next((s for s in sessions(self.cfg, now.date()) if s[1] > now), None)
            when = f"today at {clock_text(nxt[1])}" if nxt and now.weekday() < 5 else "on the next trading day"
            self.alerts.send(f"✅ Setup check passed. {self.label} and Telegram are both working.\n"
                         f"Markets are outside my working hours right now. I will start by myself {when}.\n"
                         f"Dashboard: {self.url}", "info")
            feed["status"]["phase"] = "resting"
            self.beat(running=False, force=True)
            return
        name, _, end = sess
        today = now.date().isoformat()
        label = "morning" if name == "day" else "evening"
        same_session = before.get("session") == f"{today}-{name}"
        if same_session and before.get("handover"):
            pass                                   # a planned hand-over: carry on silently
        elif same_session:
            self.alerts.send(f"🔄 I had stopped and have restarted myself. Carrying on with the {label} markets "
                             f"from where I left off. Open trades are being followed again.", "info")
        else:
            self.alerts.send(f"🟢 Watching the {label} markets until {clock_text(end)}." + ("\n" + " ".join(self.notes) if self.notes else ""), "info")
        feed["status"].update({"session": f"{today}-{name}", "handover": False, "phase": "watching"})
        auth_fails, last_error = 0, None
        while True:
            now = clock.now()
            if now > end:
                if feed["status"].get("summary_sent") != f"{today}-{name}":
                    if any(t.get("date") == today for t in feed["trades"]):       # no options trades today: nothing to say
                        self.alerts.send(self.engine.summary(now), "summary")
                    feed["status"]["summary_sent"] = f"{today}-{name}"
                feed["status"]["phase"] = "resting"
                self.beat(running=False, force=True)
                return
            try:
                self.refresh_results(now)
                self.brief(now)
                self.engine.tick(now)
                self.desk.tick(now)
                auth_fails = 0
            except AuthError as e:
                auth_fails += 1
                print(f"{self.label} problem:", e)
                if auth_fails <= 2:
                    if not self.login(end):
                        break
                elif auth_fails == 3:
                    self.alerts.send(f"⚠️ {self.label} keeps turning down my price requests. "
                                     "I will keep trying every minute.", "faults")
            except Exception as e:                 # one bad minute must never stop the engine
                traceback.print_exc()
                if str(e) != last_error:
                    last_error = str(e)
                    self.alerts.send(f"⚠️ I hit a problem and will keep going.\nDetails: {str(e)[:200]}", "faults")
            if self.desk.dirty:
                self.desk.save()
                self.desk.dirty = False
                self.engine.dirty = True
            self.beat()
            if time.time() - self.started > RUN_BUDGET:
                print("Handing over to a fresh run.")
                feed["status"]["handover"] = True
                self.beat(running=False, force=True)
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
        self.beat(running=False, force=True)


if __name__ == "__main__":
    Session().run()
