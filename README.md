# Signal Desk — hardened paper-trading research platform

Signal Desk combines two read-only/paper-trading engines:

1. **Rulebook engine** — opening-range logic for index, commodity and stock options, plus cash stocks. Kotak Neo (or Fyers) is used for market data; the application does not submit broker orders.
2. **Strategy desk** — eleven intraday strategies across Nifty 200 shares and major indices, with five-minute signal replay, trade simulation, scoring, charts and Telegram notifications.

The release hardens signal-data validation, position sizing, next-candle execution assumptions, per-book risk limits, shared cross-engine exposure controls, stale-option quote handling, trade archival and safe Git publishing. It also adds portfolio-level research reporting and dashboard panels.

> **Research/paper-trading software only.** It does not place orders and is not evidence of a profitable strategy. Historical results are sensitive to data quality, execution assumptions and estimated costs. The options backtest estimates premiums with a model rather than replaying historical option-chain quotes. Do not connect real capital based only on these reports.

## Start here

- Read **[SETUP_GUIDE.md](SETUP_GUIDE.md)** for GitHub Actions, Kotak Neo read-only prices, Telegram pairing and dashboard setup.
- Review **[RELEASE_NOTES.md](RELEASE_NOTES.md)** for the hardening changes and known limitations.
- Runtime settings are in **`config.yaml`**.

## Main capabilities

| Area | What it does |
|---|---|
| Market data | Kotak Neo quotes/history through the official SDK (Fyers optional), and Yahoo Finance historical data |
| Rulebook | Opening range, market direction, option selection, stops, targets, partial exits and time exits |
| Strategy research | Eleven reusable strategies, indicators, decision grading, trade simulation and charts |
| Execution modelling | Next-five-minute-candle-open entry by default; optional signal-close reference model |
| Risk management | Position sizing, per-engine limits and a shared cross-engine position/risk/daily-loss ceiling |
| Data integrity | Missing required confirmation fails closed; stale option premiums are never represented as fresh |
| Portfolio research | Aggregate approval/rejection, P&L, profit factor, drawdown, chronological diagnostics and stability blocks |
| Dashboard | Static HTML, auto-refreshing data, signal details, charts and portfolio views |
| Notifications | Telegram alerts, gated pairing and explicit stale-quote warnings |
| Automation | GitHub Actions for intraday polling and scheduled backtests |

## Local checks

Python 3.10+ is recommended; the GitHub workflows currently use Python 3.12.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m compileall -q engine tests
python -m tests.test_strategies
python -m tests.test_backtest
python -m tests.test_store_publish
python -m tests.test_lifecycle
python -m tests.test_engine
```

`tests.test_engine` is the slower multi-market rulebook replay suite. All listed test suites run offline using the small contract-master fixtures under `tests/fixtures/contract_masters/`. Live Fyers market-data workflows require internet access and your own Fyers credentials.

To preview the static dashboard locally:

```bash
python -m http.server 8000
```

Open `http://localhost:8000`. A local preview uses clearly-labelled sample data; it does not authenticate to a broker or run the market scanner.

## Important architecture notes

- The two trading engines share a global portfolio ceiling, but retain separate, stricter sub-limits. Current global defaults are eight concurrent positions, ₹17,500 aggregate initial risk and ₹3,500 realised daily loss. The strategy desk additionally limits itself to five concurrent positions, one per underlying, ₹2,500 aggregate strategy risk and ₹1,500 strategy daily loss. Rulebook defaults are in `config.yaml` under `general.portfolio`.
- The strategy simulator enters at the next five-minute candle open by default. A gap through the original stop invalidates the entry. `signal_close` is retained only as an optimistic comparison model.
- The portfolio controller currently limits position count, aggregate initial risk and realised daily loss. It does **not** model full portfolio covariance, sector concentration, options Greeks, intraday margin changes or liquidity at each price level.
- The options rulebook and strategy desk are paper systems. Broker integration (Kotak Neo, Fyers) is read-only; real broker order placement is not implemented.
- Dashboard data is published to a Git branch named `data`. Treat that branch as publishable/public when using GitHub Pages. Do not put personal or account-sensitive data in the trade log.

## Project map

| Path | Purpose |
|---|---|
| `index.html` / `index.template.html` | Dashboard and template |
| `config.yaml` | Market, strategy, cost and risk settings |
| `engine/core.py` | Rulebook engine and trade lifecycle |
| `engine/run.py` | GitHub session runner, broker check and Telegram commands |
| `engine/kotak.py`, `engine/brokers.py` | Kotak Neo prices and contract lists through the official SDK (read-only); which broker is used |
| `engine/fyers.py` | Fyers prices and login (optional, `broker: primary: fyers`) |
| `engine/fyers.py`, `engine/symbols.py` | Market data and contract master access |
| `engine/strategies/` | Strategy definitions, indicators, grading, simulation, portfolio risk and backtest |
| `engine/store.py` | Local state, encrypted credentials and conflict-safe publishing |
| `engine/news.py`, `engine/telegram.py` | Headline retrieval and notifications |
| `.github/workflows/` | Intraday engine and backtest workflows |
| `tests/` | Offline simulations and regression suites |

## Updating

Back up your `config.yaml` and any local `_data` folder before replacing files. Upload the release contents to your repository, including `.github`. Keep your existing GitHub Actions secrets and the `data` branch. Review the new config defaults before enabling the scheduled workflow.
