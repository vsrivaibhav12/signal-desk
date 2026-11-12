"""Run a backtest.

    python -m engine.backtest --source yahoo            quick: last ~60 days, 5-minute data, no login
    python -m engine.backtest --source kotak --days 180  longer: 1-minute data from Kotak Neo (needs KOTAK_CONSUMER_KEY)
    python -m engine.backtest --source fyers --days 180  longer: 1-minute data from your Fyers account
"""
import argparse
import copy
import json
import os
import sys
import time

import yaml

from .. import clock
from . import report
from .data import load_fyers, load_yahoo
from .luck import luck_check
from .runner import DEFAULTS, run_variant

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def universe():
    with open(os.path.join(ROOT, "engine", "nifty200.txt"), encoding="utf-8") as f:
        return [x.split("|")[0] for x in f if x.strip() and not x.startswith("#")]


def fyers_login(log):
    """Uses the login the live engine already saved. Asks on Telegram only if that has run out."""
    from ..fyers import AuthError, Fyers
    from ..run import find_code
    from ..store import Store
    from ..telegram import Telegram
    env = os.environ
    fy = Fyers(env["FYERS_APP_ID"], env["FYERS_SECRET_KEY"])
    store = Store(os.path.join(ROOT, "_data"), env["FYERS_SECRET_KEY"] + env["TELEGRAM_BOT_TOKEN"])
    secure = store.load_secure()
    tg = Telegram(env["TELEGRAM_BOT_TOKEN"])
    tg.chat_id = secure.get("chat_id")
    fy.token = secure.get("access_token")
    if fy.token and fy.probe():
        return fy, tg
    pin = env.get("FYERS_PIN", "").strip()
    if secure.get("refresh_token") and pin:
        try:
            fy.token = fy.refresh(secure["refresh_token"], pin)
            if fy.probe():
                return fy, tg
        except Exception as e:
            log(f"Automatic login did not work: {e}")
    if not tg.chat_id:
        sys.exit("No Telegram chat is linked yet. Run the Trade engine once first, then run the backtest again.")
    link = fy.login_url(f"https://{env['GITHUB_REPOSITORY'].split('/')[0].lower()}.github.io/{env['GITHUB_REPOSITORY'].split('/')[1]}/")
    tg.send("🔑 <b>The backtest needs a Fyers login</b>\n1. Tap this link and log in:\n" + link +
            "\n2. Tap Copy login code on the page you land on.\n3. Paste the code here as a message.")
    end = time.time() + 20 * 60
    while time.time() < end:
        for chat, text, _ in tg.poll(wait=20):
            code = find_code(text) if chat == tg.chat_id else None
            if code:
                try:
                    fy.token, _ = fy.exchange_code(code)
                    tg.send("✅ Logged in. Starting the backtest.")
                    return fy, tg
                except Exception as e:
                    tg.send(f"Fyers did not accept that code ({e}). Tap the link and try again.")
    sys.exit("Fyers login was not completed in time.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["yahoo", "kotak", "fyers"], default="yahoo")
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--out", default=os.path.join(ROOT, "_data", "backtest.json"))
    ap.add_argument("--cache", default=None)
    ap.add_argument("--luck", type=int, default=5, help="how many shuffled-price runs for the luck check (0 = skip)")
    ap.add_argument("--stocks", type=int, default=0, help="only the first N stocks (for quick tests)")
    a = ap.parse_args()
    log = lambda m: print(m, flush=True)
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
    bt = copy.deepcopy(DEFAULTS)
    for k, v in (cfg.get("backtest") or {}).items():
        if isinstance(v, dict):
            bt[k].update(v)
        else:
            bt[k] = v
    uni = universe()[:a.stocks] if a.stocks else universe()
    markets = [m for m in cfg["markets"] if m.get("kind") == "index" and m["root"] in bt["rules"] and m.get("enabled", True)]
    tg = None
    log(f"Loading price history from {a.source} ...")
    if a.source == "yahoo":
        bars, n_stocks, missing = load_yahoo(markets, uni, cache_dir=a.cache, log=log)
        source = "Yahoo Finance, 5-minute candles"
        fine = False
    elif a.source == "kotak":
        from ..strategies.data import quiet_kotak
        kt = quiet_kotak(log)
        if kt is None:
            sys.exit("Kotak Neo prices are not available. Check that the KOTAK_CONSUMER_KEY secret is set and correct.")
        bars, n_stocks, missing = load_fyers(kt, markets, uni, a.days, log=log)
        source = "Kotak Neo, 1-minute candles"
        fine = True
    else:
        fy, tg = fyers_login(log)
        bars, n_stocks, missing = load_fyers(fy, markets, uni, a.days, log=log)
        source = "Fyers, 1-minute candles"
        fine = True
    nifty = cfg["markets"][0]["spot"]
    days = [d for d in bars[nifty].days]
    if a.source in ("fyers", "kotak"):
        cut = clock.now().date().fromordinal(clock.now().date().toordinal() - a.days)
        days = [d for d in days if d >= cut]
    # the first days only warm up the 10-day volume average used by the stock scan
    warm = 12
    days = days[warm:] if len(days) > warm + 20 else days
    log(f"Replaying {len(days)} trading days ({days[0]} to {days[-1]}), {n_stocks} stocks, "
        f"{len([m for m in markets if m['spot'] in bars])} indices.")

    variants = [
        ("main", "Current settings", "Every breakout that passes the rules is taken, with early exit on.", {}),
        ("noearly", "No early exit", "Same, but trades are only left at the stop-loss, target or time exit.",
         {"early_exit": {"enabled": False}}),
        ("limits", "With daily limits", "Early exit on, but at most 2 trades a day per market and a stop after one loss.",
         {"general": {"max_trades_per_day": 2, "stop_after_losses": 1}}),
    ]
    out_variants, main_trades, notes = [], None, []
    for vid, name, desc, over in variants:
        log(f"  variant: {name}")
        c = copy.deepcopy(cfg)
        for k, v in over.items():
            c[k].update(v) if isinstance(v, dict) and k in c else c.__setitem__(k, v)
        trades, still_open, n_idx = run_variant(c, bars, days, bt, fine_when_open=fine, log=log)
        s = report.summarize(trades)
        s["gross_pnl"] = report.summarize(trades, "pnl").get("pnl", 0) if trades else 0
        out_variants.append({"id": vid, "name": name, "desc": desc, "summary": s})
        if still_open:
            notes.append(f"{still_open} trade(s) in '{name}' were still open at the end of the data and are left out.")
        if vid == "main":
            main_trades = trades
    ms = out_variants[0]["summary"]
    luck = None
    if a.luck:
        log("  luck check (same rules on shuffled prices)")
        luck = luck_check(cfg, bars, days, bt, main_trades, runs=a.luck, fine_when_open=fine, log=log)
    result = {
        "generated": clock.now().isoformat(timespec="minutes"), "source": source,
        "period": {"from": days[0].isoformat(), "to": days[-1].isoformat(), "days": len(days)},
        "universe": {"stocks": n_stocks, "stocks_missing": len([m for m in missing if m.endswith("-EQ")]),
                     "indices": [m["name"] for m in markets if m["spot"] in bars]},
        "variants": out_variants, "verdict": report.verdict(ms, luck), "luck": luck,
        "by_market": report.group(main_trades, report.market_of),
        "by_exit": report.group(main_trades, lambda t: t.get("exit_type", "OTHER")),
        "by_side": report.group(main_trades, lambda t: "Buy / calls" if t["dir"] == 1 else "Sell / puts"),
        "by_month": report.group(main_trades, report.month_of, order=None),
        "underlying": report.underlying_check(main_trades),
        "curve": [round(x, 2) for x in __import__("itertools").accumulate(t["net"] for t in main_trades)],
        "assumptions": [
            "Option prices are estimated from the index with the Black-Scholes formula, because expired options have no price history. Real prices will differ, especially around events.",
            f"Costs deducted: ₹{bt['option_cost_per_trade']:.0f} per option trade plus {bt['option_slippage_pct']}% slippage on each leg; "
            f"₹{bt['stock_cost_per_trade']:.0f} plus {bt['stock_cost_pct']}% of turnover and {bt['stock_slippage_pct']}% slippage per leg on stocks.",
            "Expiry days, strike gaps and lot sizes use today's exchange rules for every past day.",
            "Index average-price lines are plain averages of the index, because an index has no volume. Live, the tool uses the index future.",
            "Crude oil, gold, silver and natural gas are not in this backtest: expired commodity contracts have no history to test on.",
            "News has no effect on trades (it is shown for information only), so it is not part of the test.",
            "Nothing was tuned to this data. The rules, stops and filters are exactly the ones used live.",
        ] + ([f"Replay checks on {'every 5-minute close' if not fine else 'every 5-minute close, and every minute while a trade is open'}."] ),
        "notes": notes,
        "trades": [[t["date"], t["name"], t["dir"], t["headline"], t["entry_time"][11:16], t["exit_time"][11:16],
                    t["entry_price"], t["exit_price"], t["pnl"], t["cost"], t["net"], t["net_r"],
                    t.get("exit_type", "OTHER")] for t in main_trades],
    }
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(result, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    log(f"\nWritten to {a.out}")
    if os.path.isdir(os.path.join(ROOT, "_data", ".git")) and os.path.abspath(a.out).startswith(os.path.join(ROOT, "_data")):
        from ..store import Store
        Store(os.path.join(ROOT, "_data"), "unused").publish(own={"backtest.json"})
        log("Published to your dashboard.")
    log(result["verdict"])
    if tg and tg.chat_id:
        tg.send(f"📊 <b>Backtest finished</b> ({result['period']['from']} to {result['period']['to']})\n{result['verdict']}\n"
                "The full breakdown is on your dashboard.")
    return result


if __name__ == "__main__":
    main()
