# Signal Desk

A rulebook-driven trade alert tool for Nifty, Sensex, Bank Nifty, crude oil and gold options, plus Nifty 100 stocks.

- Reads prices from your Fyers account (read-only, never places orders)
- Runs free on GitHub's timer, weekdays, morning and evening
- Sends every trigger, stop, target and exit to Telegram, with the reasons
- Logs every suggestion as a paper trade and scores itself on the dashboard

Start with **SETUP_GUIDE.md**. Settings live in **config.yaml**.

## What is in here

| File | What it does |
|---|---|
| `index.html` | The dashboard |
| `config.yaml` | Your settings, with notes on every line |
| `engine/core.py` | The rulebook: range, trend filter, trigger, stop, target, trailing, scoring |
| `engine/run.py` | Keeps it running through the session, handles login and bot commands |
| `engine/fyers.py`, `engine/symbols.py` | Price data and contract lookup |
| `engine/telegram.py`, `engine/store.py` | Alerts, and the trade log |
| `.github/workflows/engine.yml` | The timer that starts it |
| `tests/` | A replay that runs made-up market days through the real engine |

## Checking it yourself

    pip install -r requirements.txt
    python -m tests.test_engine      # checks the special rules
    python -m tests.simulate         # replays made-up days and prints every trade

The sample trades shown on the dashboard before setup come from that replay. They are made up and say nothing about real results.
