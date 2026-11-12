"""Runs the market engine inside configured exchange sessions and handles login.

The preferred 4.1 deployment calls this Session from engine.supervisor, a
persistent process that remains alive between sessions. GitHub Actions still
provides a scheduled/manual recovery path. Only one engine run owns a session;
heartbeats and the shared data branch coordinate hand-over and recovery.
"""
import json
import os
import secrets
import re
import sys
import time
import traceback
from datetime import datetime, timedelta

import requests
import yaml

from . import clock, costs, migrations
from . import ledger as ledger_mod
from . import live as live_mod
from .clock import at, clock_text
from .core import Engine
from .desks import DeskRunner
from . import brokers
from .alerts import Alerts
from .leaders import Leaders
from .intelligence import equity_catalysts
from .results import Results
from .tradingcal import TradingCalendar
from .watch import Watch
from .errors import AuthError, BrokerError
from .journal import Journal, JournalUI
from .fyers import Fyers
from .store import ENGINE_PREFIXES, Store
from .strategies.desk import StrategyDesk
from .telegram import Telegram

RUN_BUDGET = 5 * 3600 + 5 * 60   # GitHub stops a run at 6 hours, so hand over before that
BEAT_EVERY = 110                 # seconds between heartbeats
STALE_AFTER = 300                # a heartbeat older than this means the other run is dead
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE_FILES = {"feed.json", "secure.bin", "strategies.json", "results.json", "volbase.json", "holidays.json", "options_tape.jsonl",
                "live.json", "journal.bin"}
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
        need = brokers.required_secrets_for_config(self.cfg) + ["TELEGRAM_BOT_TOKEN"]
        missing = [k for k in need if not env.get(k)]
        if missing:
            sys.exit("Setup is not finished. These secrets are missing in GitHub: " + ", ".join(missing))
        self.notes = brokers.adjust_config(self.cfg, self.kind, secondary=brokers.commodity_feed(self.cfg))
        self.pin = env.get("FYERS_PIN", "").strip()
        self.tg = Telegram(env["TELEGRAM_BOT_TOKEN"])
        data_dir = os.path.join(ROOT, "_data")
        current_material = brokers.secret_material(env) + env["TELEGRAM_BOT_TOKEN"]
        self.store = Store(data_dir, current_material)
        self.secure = self.store.load_secure()
        if not self.secure:
            # 4.0 installs that only had Kotak may have encrypted secure.bin with
            # the Kotak key. When Fyers is added for MCX, migrate it in place so
            # the Telegram pairing is not lost.
            for candidate in (env.get("KOTAK_CONSUMER_KEY", "").strip(), env.get("FYERS_SECRET_KEY", "").strip()):
                if not candidate or candidate == brokers.secret_material(env):
                    continue
                legacy = Store(data_dir, candidate + env["TELEGRAM_BOT_TOKEN"])
                recovered = legacy.load_secure()
                if recovered:
                    self.secure = recovered
                    self.store.save_secure(recovered)
                    break
        configured_chat = env.get("TELEGRAM_CHAT_ID", "").strip()
        if configured_chat:
            try:
                configured_chat = int(configured_chat)
            except ValueError:
                sys.exit("TELEGRAM_CHAT_ID must be a numeric Telegram chat ID.")
        self.tg.chat_id = configured_chat or self.secure.get("chat_id")
        self.fy, self.master = brokers.build(self.cfg, env)
        self.secondary_fyers = getattr(self.fy, "secondary", None)
        self.commodity_markets = [m for m in self.cfg.get("markets", []) if m.get("kind") == "commodity" and m.get("enabled", True)]
        self.secondary_fyers_pending = False
        self.url = dashboard_url(self.cfg)
        self.started = time.time()
        self.engine = None
        self.alerts = Alerts(self.tg.send, self.cfg)
        self.cost_table = costs.from_config(self.cfg)
        self.ledger = ledger_mod.Ledger(data_dir)
        # The journal is on only when JOURNAL_PASSPHRASE is set; its button targets live in the encrypted secure file.
        self.journal = Journal(data_dir, env.get("JOURNAL_PASSPHRASE", "").strip())
        self.journal_ui = JournalUI(self.journal, registry=self.secure.setdefault("journal_buttons", {}))
        # A log from an older version is migrated if a migration exists. Otherwise its settled trades are kept in the ledger and only
        # then is the feed reset: an upgrade never throws the trade record away.
        self.feed = migrations.upgrade_feed(self.store.load_feed(), data_dir, self.cost_table)
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
        self.write_live()
        preserve = ("intelligence.json", "supervisor.json") if os.environ.get("SIGNAL_DESK_SHARED_DATA") == "1" else ()
        self.store.publish(own=ENGINE_FILES, own_prefix=ENGINE_PREFIXES, preserve_local=preserve)
        if self.engine:
            self.engine.dirty = False
        self.last_beat = time.time()

    # ── live.json and the ledger ─────────────────────────────
    def write_live(self):
        """Records newly settled trades in the ledger and rewrites live.json. Never allowed to interrupt the engine."""
        try:
            desk_state = getattr(getattr(self, "desk", None), "state", None) or {}
            ledger_mod.sync(self.ledger, self.feed.get("trades") or [], desk_state.get("trades") or [], self.cost_table)
            self.store.write_json("live.json", live_mod.build(clock.now(), self.cfg, self.feed, desk_state,
                                                              extra={"journal": {"enabled": self.journal.enabled}}))
            if self.journal_ui.changed:
                self.store.save_secure(self.secure)
                self.journal_ui.changed = False
        except Exception as exc:
            print("live.json not updated:", type(exc).__name__, str(exc)[:160])

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
    def _secondary_symbols(self):
        """Resolve the nearest Fyers MCX future for every configured commodity."""
        day = clock.now().date()
        symbols = {}
        for market in self.commodity_markets:
            try:
                future = self.master.nearest_future("MCX_COM", market["root"], day)
                if future and future.get("symbol"):
                    symbols[market["id"]] = future["symbol"]
                else:
                    symbols[market["id"]] = None
            except Exception as exc:
                print("Could not resolve MCX contract:", market.get("id"), type(exc).__name__)
                symbols[market["id"]] = None
        return symbols

    def _secondary_symbol(self):
        """First resolvable MCX future, used in concise login/error messages."""
        return next((symbol for symbol in self._secondary_symbols().values() if symbol), None)

    def _verify_secondary_fyers(self, symbol):
        """Require both a current MCX quote and usable recent MCX candle history.

        A contract-master row or a quote alone is not enough: the commodity
        strategy needs historical candles for its opening range and VWAP.
        """
        client = self.secondary_fyers
        if not client or not symbol:
            return False, "No current MCX futures contract could be resolved from Fyers' master."
        detail_probe = getattr(client, "probe_detail", None)
        if callable(detail_probe):
            try:
                quote_ok, quote_detail = detail_probe(symbol)
            except AuthError:
                raise
            except (BrokerError, requests.RequestException) as exc:
                return False, f"Fyers MCX quote request failed for {symbol}: {exc}"
        else:
            quote_ok, quote_detail = bool(client.probe(symbol)), None
        if not quote_ok:
            return False, str(quote_detail or f"Fyers did not return a usable quote for {symbol}.")
        today = clock.now().date()
        try:
            candles = client.history(symbol, "5", today - timedelta(days=10), today)
        except AuthError:
            raise
        except (BrokerError, requests.RequestException) as exc:
            return False, f"Fyers MCX history request failed for {symbol}: {exc}"
        valid = []
        for row in candles or []:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                continue
            try:
                stamp = int(float(row[0]))
                if stamp > 10_000_000_000:
                    stamp //= 1000
                o, h, low, close = (float(row[i]) for i in range(1, 5))
                volume = float(row[5] or 0)
                if stamp <= 0 or min(o, h, low, close) <= 0 or h < max(o, low, close) or low > min(o, h, close):
                    continue
                if volume < 0:
                    continue
                valid.append((stamp, o, h, low, close, volume))
            except (TypeError, ValueError, OverflowError):
                continue
        if len(valid) < 2:
            return False, f"Fyers returned fewer than two valid 5-minute candles for {symbol}."
        latest = clock.from_ts(max(row[0] for row in valid))
        if latest.date() < today - timedelta(days=7):
            return False, f"Fyers MCX history for {symbol} is stale (last candle {latest:%Y-%m-%d %H:%M IST})."
        return True, {"symbol": symbol, "last_candle": latest.isoformat(timespec="seconds"),
                      "valid_candles": len(valid)}

    def _verify_secondary_fyers_markets(self):
        """Verify each configured MCX market independently; one bad contract must not hide others."""
        results = {}
        symbols = self._secondary_symbols()
        for market in self.commodity_markets:
            market_id = market["id"]
            symbol = symbols.get(market_id)
            if not symbol:
                results[market_id] = {"ok": False, "detail": "No current futures contract in Fyers master."}
                continue
            try:
                ready, evidence = self._verify_secondary_fyers(symbol)
            except AuthError:
                # An expired token is a login problem; a quote permission/symbol
                # failure is a market-data problem and must retain its own detail.
                raise
            except Exception as exc:
                ready, evidence = False, f"{type(exc).__name__}: {str(exc)[:180]}"
            results[market_id] = {"ok": ready, "detail": evidence if not ready else None,
                                  "history_check": evidence if ready else None, "symbol": symbol}
        return results

    def _enable_verified_secondary_markets(self, results):
        enabled = []
        for market in self.commodity_markets:
            result = results.get(market["id"], {})
            market["enabled"] = bool(result.get("ok"))
            if market["enabled"]:
                enabled.append(market["id"])
        failed = {key: value.get("detail") for key, value in results.items() if not value.get("ok")}
        self.feed["status"]["brokers"] = {
            "primary": {"name": "Kotak Neo", "status": "connected"},
            "commodity": {"name": "Fyers", "status": "connected" if enabled else "unavailable",
                          "enabled_markets": enabled, "unavailable_markets": failed,
                          "status_detail": "Per-market MCX quote and 5-minute history preflight"},
        }
        return enabled

    def _disable_secondary_markets(self, reason):
        for market in self.commodity_markets:
            market["enabled"] = False
        self.secondary_fyers_pending = True
        self.feed["status"]["brokers"] = {
            "primary": {"name": self.label, "status": "connected"},
            "commodity": {"name": "Fyers", "status": "login_required", "detail": reason},
        }
        self.feed["status"]["phase"] = "secondary_login_required"
        if self.secondary_fyers and self.secondary_fyers.app_id and self.secondary_fyers.secret:
            link = self.secondary_fyers.login_url(self.url)
            self.tg.send("🔑 <b>Fyers MCX data login needed</b>\n"
                         "Kotak remains connected for NSE/BSE. MCX contracts are temporarily paused until Fyers market-data access is verified.\n\n"
                         "1. Open this link and log in to Fyers:\n" + link + "\n\n"
                         "2. Tap <b>Copy login code</b> on the dashboard page.\n"
                         "3. Paste that code into this Telegram chat.\n\n"
                         "The NSE/BSE scanner will continue while MCX waits for login.")
        else:
            self.tg.send("⚠️ <b>MCX feed not configured yet</b>\n"
                         "Kotak remains connected for NSE/BSE. Add FYERS_APP_ID and FYERS_SECRET_KEY to your repository secrets; "
                         "then restart the Trade engine to connect Crude Oil, Gold, Silver and Natural Gas.")
        self.beat(force=True)

    def _secondary_data_unavailable(self, results, source="saved Fyers session"):
        """Keep the authenticated session, but don't ask for another login when data permission failed."""
        enabled = self._enable_verified_secondary_markets(results)
        details = "; ".join(f"{key}: {value.get('detail')}" for key, value in results.items() if not value.get("ok"))
        self.secondary_fyers_pending = False
        self.feed["status"]["phase"] = "watching"  # NSE/BSE remains operational
        self.feed["status"]["brokers"]["commodity"].update({
            "status": "partial" if enabled else "data_unavailable",
            "detail": f"Fyers authenticated via {source}; MCX market-data preflight failed.",
        })
        message = (
            "⚠️ <b>Fyers is authenticated, but MCX market data is unavailable</b>\n"
            "Kotak NSE/BSE scanning continues. This is not a login-code failure. Check the Fyers API app's "
            "Market Data permission (Quotes, Market Depth and Historical Data) and confirm that the MCX "
            "segment is available to the account.\n" + details[:850]
        )
        last_notice = float(self.feed["status"].get("mcx_data_unavailable_notice_ts") or 0)
        if time.time() - last_notice >= 3600:
            self.tg.send(message)
            self.feed["status"]["mcx_data_unavailable_notice_ts"] = time.time()
        self.beat(force=True)
        return bool(enabled)

    def _prepare_secondary_fyers(self):
        """Authenticate the optional MCX feed without stopping Kotak equity/index scanning."""
        if not self.secondary_fyers or not self.commodity_markets:
            return True
        client = self.secondary_fyers
        if not client.app_id or not client.secret:
            self._disable_secondary_markets("Add FYERS_APP_ID and FYERS_SECRET_KEY as repository secrets to enable MCX routing.")
            return False
        today = clock.now().date().isoformat()
        client.token = self.secure.get("access_token")
        if client.token:
            try:
                results = self._verify_secondary_fyers_markets()
            except AuthError as exc:
                print("Saved Fyers access token expired or rejected:", str(exc)[:160])
                client.token = None
            else:
                enabled = self._enable_verified_secondary_markets(results)
                if enabled:
                    return True
                self._secondary_data_unavailable(results, "saved access token")
                return False
        refresh = self.secure.get("refresh_token")
        if refresh and self.pin:
            try:
                client.token = client.refresh(refresh, self.pin)
                results = self._verify_secondary_fyers_markets()
                enabled = self._enable_verified_secondary_markets(results)
                self.secure.update({"access_token": client.token, "access_date": today})
                self.store.save_secure(self.secure)
                if enabled:
                    return True
                self._secondary_data_unavailable(results, "refreshed access token")
                return False
            except AuthError as exc:
                print("Fyers secondary refresh rejected:", str(exc)[:160])
            except (BrokerError, requests.RequestException) as exc:
                print("Fyers secondary preflight failed:", str(exc)[:200])
        self._disable_secondary_markets("Fyers login or MCX quote/history permission has not been verified.")
        return False

    def _accept_secondary_fyers_code(self, text):
        if not (self.secondary_fyers and self.secondary_fyers_pending):
            return False
        code = find_code(text)
        if not code:
            return False
        try:
            access, refresh = self.secondary_fyers.exchange_code(code)
            self.secondary_fyers.token = access
            results = self._verify_secondary_fyers_markets()
            enabled = self._enable_verified_secondary_markets(results)
            if not enabled:
                details = "; ".join(f"{k}: {v.get('detail')}" for k, v in results.items())
                raise BrokerError(
                    "Fyers login was accepted, but the MCX market-data checks failed. "
                    "This is not a login-code problem. Check that the Fyers API app has the "
                    "Market Data permission (Quotes, Market Depth and Historical Data), that "
                    "MCX market data is available to the account, and that the returned symbol "
                    "details below are resolved. " + details
                )
            self.secure.update({"access_token": access, "access_date": clock.now().date().isoformat()})
            if refresh:
                self.secure.update({"refresh_token": refresh, "refresh_date": clock.now().date().isoformat()})
            self.store.save_secure(self.secure)
            self.secondary_fyers_pending = False
            self.feed["status"]["phase"] = "watching"
            self.beat(force=True)
            enabled_names = ", ".join(m["name"] for m in self.commodity_markets if m.get("enabled"))
            unavailable_names = ", ".join(m["name"] for m in self.commodity_markets if not m.get("enabled"))
            note = f"✅ Fyers MCX feed verified for: {enabled_names}. The scanner will begin from its next scheduled scan."
            if unavailable_names:
                note += f"\nNot enabled (quote/history preflight failed): {unavailable_names}."
            self.tg.send(note)
            return True
        except (AuthError, BrokerError, requests.RequestException) as exc:
            message = str(exc)
            if "login was accepted" in message.lower():
                self.tg.send(
                    "⚠️ <b>Fyers login accepted; MCX data access failed</b>\n" +
                    message[:850] +
                    "\n\nOpen your Fyers API app settings and confirm its permission template includes "
                    "Market Data (Quotes, Market Depth and Historical Data). If the app was created without "
                    "Market Data permission, create a new app with that permission, update FYERS_APP_ID "
                    "and FYERS_SECRET_KEY in GitHub secrets, then run the Trade engine again. A fresh "
                    "login code alone will not fix a missing API permission."
                )
            else:
                self.tg.send(f"Fyers did not accept the login code: {message[:220]}\nPlease open a fresh login link and try again.")
            return True

    def login(self, until):
        if self.kind == "kotak":
            if not self.check_prices(until):
                return False
            current = current_session(self.cfg, clock.now())
            if self.secondary_fyers and current and current[0] == "evening":
                self._prepare_secondary_fyers()
            else:
                self.feed["status"]["brokers"] = {"primary": {"name": "Kotak Neo", "status": "connected"},
                                                     "commodity": {"name": "Fyers", "status": "not_required" if not self.secondary_fyers else "outside_session"}}
            return True
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
                extra = ("I will log in by myself for about the next two weeks." if self.pin and refresh
                         else "I will ask again tomorrow morning.")
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
            if self._accept_secondary_fyers_code(text):
                continue
            reply = self.journal_ui.handle(text)                  # a tap on Took it / Skip, or the reply giving your fill
            if reply is not None:
                self.tg.send(reply)
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

    def catalyst_details(self):
        """Recent news-linked shares promoted for normal strategy checks, never direct trades."""
        if not self.engine:
            return []
        out = []
        seen = set()
        for item in (self.engine.day.get("news") or []):
            if item.get("tag") in ("Results", "Order win", "Deal", "Broker view"):
                symbol = str(item.get("symbol") or "").upper()
                if symbol and symbol not in seen:
                    out.append({"symbol": symbol, "why": f"News catalyst · {item.get('tag')}: {item.get('title', 'headline')}"})
                    seen.add(symbol)
        try:
            with open(os.path.join(ROOT, "_data", "intelligence.json"), encoding="utf-8") as f:
                intel = json.load(f)
            news_cfg = self.cfg.get("news_intelligence") or {}
            impacts = equity_catalysts(
                intel, self.engine.universe, now=clock.now(),
                min_score=int(news_cfg.get("watchlist_min_impact_score", 65)),
                max_age_hours=int(news_cfg.get("watchlist_max_age_hours", 36)),
            )
            for item in impacts:
                if item["symbol"] not in seen:
                    out.append(item)
                    seen.add(item["symbol"])
        except (OSError, ValueError, TypeError) as exc:
            print("News impact watchlist unavailable:", type(exc).__name__)
        return out[:20]

    def catalysts(self):
        """Names of shares with recent results, company news or macro-linked event impacts."""
        return [x["symbol"] for x in self.catalyst_details()]

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
        swing = self.swing_line(now)
        if swing:
            lines.append(swing)
        lines.append(f"Dashboard: {self.url}")
        self.alerts.send("\n".join(lines), "brief")

    def swing_line(self, now, folder=None):
        """One line about last night's swing setups, if there are any and the file is recent. Never raises."""
        try:
            with open(os.path.join(folder or os.path.join(ROOT, "_data"), "swing.json"), encoding="utf-8") as f:
                doc = json.load(f)
            asof = datetime.fromisoformat(doc["asof"]).date()
            names = [t["symbol"] for t in doc.get("triggered") or []]
            if not names or (now.date() - asof).days > 4:
                return None
            return (f"Swing setups at the {asof:%d %b} close (one unproven rule; see the Swing screen): "
                    + ", ".join(names[:8]) + (f" and {len(names) - 8} more" if len(names) > 8 else ""))
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def engine_desk_text(self):
        d = getattr(self, "desk", None)
        return d.summary_text(clock.now().date().isoformat()) if d else "The strategy desk has not started yet."

    def hand_over(self):
        """Start a fresh run to continue the session before GitHub's 6-hour limit."""
        # A persistent local supervisor relaunches Session itself. Dispatching
        # another Actions runner would create two trading engines on one session.
        if os.environ.get("SIGNAL_DESK_SUPERVISOR") == "1":
            print("Persistent supervisor will restart the session locally; GitHub hand-over skipped.")
            return
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
                                 catalysts=self.catalyst_details, leaders=self.leaders,
                                 heartbeat=self.beat, options_trades=lambda: self.feed["trades"],
                                 flush=lambda: (self.desk.save(), setattr(self.desk, "dirty", False), self.beat(force=True)))
        self.engine.journal = self.journal_ui
        self.desk.journal = self.journal_ui
        self.runner = DeskRunner([("rulebook", self.engine), ("strategies", self.desk)])
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
                failures = self.runner.tick(now)           # each desk is isolated: one failing never skips the other
                auth_fails = 0
                for _name, e in failures:
                    if str(e) != last_error:
                        last_error = str(e)
                        self.alerts.send(f"⚠️ I hit a problem and will keep going.\nDetails: {str(e)[:200]}", "faults")
            except AuthError as e:
                auth_fails += 1
                print(f"{self.label} problem:", e)
                if self.kind == "kotak" and self.secondary_fyers:
                    if self.fy.probe():
                        self._disable_secondary_markets("Fyers session expired or rejected an MCX request; Kotak remains active.")
                    elif not self.login(end):
                        break
                    auth_fails = 0
                elif auth_fails <= 2:
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
