# Signal Desk

A rulebook-driven trade alert tool for options on Nifty, Sensex, Bank Nifty, Fin Nifty, Midcap Nifty, crude oil, gold, silver and natural gas, plus Nifty 200 stocks with a news scan.

- Reads prices from your Fyers account (read-only, never places orders)
- Runs free on GitHub's timer, weekdays, morning and evening
- Sends every trigger, stop, target and exit to Telegram, with the reasons
- Logs every suggestion as a paper trade and scores itself on the dashboard
- Backtests the same rules on past prices (Actions tab, Backtest workflow)
- Runs ten more intraday strategies side by side (six from the Flattrade article, two Bollinger, two EMA), each with its own dashboard tab, live trades and backtest (Strategy backtest workflow)

Start with **SETUP_GUIDE.md**. Settings live in **config.yaml**.

## What is in here

| File | What it does |
|---|---|
| `index.html` | The dashboard |
| `config.yaml` | Your settings, with notes on every line |
| `engine/core.py` | The rulebook: range, trend filter, trigger, stop, target, trailing, scoring |
| `engine/run.py` | Keeps it running through the session, handles login and bot commands |
| `engine/fyers.py`, `engine/symbols.py` | Price data and contract lookup |
| `engine/backtest/` | Replays past days through the same rulebook, with a luck check |
| `engine/strategies/` | The ten strategies: indicators, rules and chart levels (`defs.py`), trade simulator, live desk (market conditions, trade charts), backtest |
| `engine/news.py` | Free headline scan for Nifty 200 stocks |
| `engine/telegram.py`, `engine/store.py` | Alerts, and the trade log |
| `.github/workflows/engine.yml` | The timer that starts it |
| `tests/` | A replay that runs made-up market days through the real engine |

## Checking it yourself

    pip install -r requirements.txt
    python -m tests.test_engine      # checks the special rules
    python -m tests.test_backtest    # checks the backtest
    python -m tests.test_strategies  # checks the ten strategies, fills, costs, charts and the live desk
    python -m tests.test_store_publish  # checks the engine and the backtest never overwrite each other's files
    python -m tests.simulate         # replays made-up days and prints every trade

The sample trades shown on the dashboard before setup come from that replay. They are made up and say nothing about real results.
