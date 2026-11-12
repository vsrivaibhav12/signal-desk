# Signal Desk 4.0.0 — hardened paper-trading research platform

**4.0.0 is a dashboard and data-integrity release.** It introduces the trader-first decision cockpit, stricter quote/candle/option validation, stale-data gates, and a prospective live options quote tape. See [the 4.0.0 release notes](RELEASE_NOTES.md) and [the upgrade guide](UPGRADE_4.0.0.md).

Signal Desk combines two read-only/paper-trading engines:

1. **Rulebook engine** — opening-range logic for index, commodity and stock options, plus cash stocks. Kotak Neo (or Fyers) is used for market data; the application does not submit broker orders.
2. **Strategy desk** — three researched intraday strategies on a results- and volume-driven watchlist, plus a call and put scanner for the indices, with five-minute signal replay, trade simulation, scoring, charts and Telegram notifications.

The release hardens timestamp, quote, candle and option-contract validation; position sizing; next-candle execution assumptions; per-book risk limits; shared cross-engine exposure controls; prospective options quote capture; trade archival; and safe Git publishing. The redesigned dashboard foregrounds market context, data confidence, candidates and open risk, while keeping research detail and logs accessible in secondary panels.

> **Research/paper-trading software only.** It does not place orders and is not evidence of a profitable strategy. Historical results are sensitive to data quality, execution assumptions and estimated costs. The options backtest estimates premiums with a model rather than replaying historical option-chain quotes. The new option quote tape only captures observations prospectively from 4.0.0 onward; it does not backfill history. Do not connect real capital based only on these reports.

## Start here

- Read **[SETUP_GUIDE.md](SETUP_GUIDE.md)** for GitHub Actions, Kotak Neo read-only prices, Telegram pairing and dashboard setup.
- Review **[RELEASE_NOTES.md](RELEASE_NOTES.md)** for the hardening changes and known limitations.
- Runtime settings are in **`config.yaml`**.

## Main capabilities

| Area | What it does |
|---|---|
| Market data | Kotak Neo quotes/history through the official SDK (Fyers optional), and Yahoo Finance historical data |
| Rulebook | Opening range, market direction, option selection, stops, targets, partial exits and time exits |
| Strategy research | Three strategies, indicators, decision grading, trade simulation and charts |
| Execution modelling | Next-five-minute-candle-open entry by default; optional signal-close reference model |
| Risk management | Position sizing, per-engine limits and a shared cross-engine position/risk/daily-loss ceiling |
| Data integrity | Exchange/receipt timestamps are distinguished; malformed or stale quotes/candles are rejected; mandatory missing option inputs fail closed |
| Options research capture | Prospective bid/ask, depth, OI and quote-time observations are saved for future historical premium analysis; no historical backfill |
| Portfolio research | Aggregate approval/rejection, P&L, profit factor, drawdown, chronological diagnostics and stability blocks |
| Dashboard | Redesigned interactive decision cockpit, market pulse, data-health gates, searchable/filterable watchlist and options, trade detail sheets, searchable/exportable logs, theme and compact controls |
| Notifications | Telegram alerts, gated pairing and explicit stale-quote warnings |
| Automation | GitHub Actions for intraday polling and scheduled backtests |

## Research limitations to keep in view

- The live intraday replay still uses today's Nifty 200 membership for historical dates. The daily bhavcopy research has a separate point-in-time liquidity universe; do not conflate the two.
- Historical news is not replayed point-in-time. Results-calendar events are included where historical data is available.
- Sector rotation is not implemented because the project does not yet have reliable point-in-time sector classifications.
- The historical options backtest remains an underlying/premium proxy. The prospective `options_tape.jsonl` captures real observed quote fields from this release onward, subject to broker data availability; it cannot recreate expired contracts' historical premiums, Greeks or IV.
- Portfolio covariance, sector concentration, option-Greek exposure, dynamic broker margin and order-book impact are not yet fully modelled.
- The previous holdout has been consumed. New strategy changes need a new untouched period or prospective paper-trading evidence.

## Local checks

Python 3.10+ is recommended; the GitHub workflows currently use Python 3.12.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m compileall -q engine tests
python -m tests.test_archetypes
python -m tests.test_leaders
python -m tests.test_playbook
python -m tests.test_context
python -m tests.test_bhav
python -m tests.test_daily
python -m tests.test_replay
python -m tests.test_segments
python -m tests.test_kotak
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
| `engine/results.py`, `engine/watch.py`, `engine/optscan.py`, `engine/alerts.py` | The four segments: results calendar, quotes-based watchlist, call and put scanner, and the Telegram alert policy |
| `engine/leaders.py` | Daily relative-strength leaders and market health from NSE's daily file, flagged on the watchlist |
| `engine/archetypes.py` | The Market research document's claims as pre-registered tests: mean reversion, bursts, episodic pivots, exits, sizing |
| `engine/playbook.py` | The Leader Playbook ideas as pre-registered tests: pivot, pullback, partial exit, bear gauge, sizing |
| `engine/context.py` | Relative strength, breadth, relative-strength portfolio and index trend benchmark, tested with the daily research |
| `engine/bhav.py` | NSE's daily price files for every share that traded (survivor-free), adjusted prices and a point-in-time universe |
| `engine/daily.py` | Daily base-and-breakout research over ten years: fixed rules, placebo, portfolio, sealed holdout |
| `engine/replay.py` | Point-in-time replay of the whole decision process, with holdout, intervals and cost sensitivity |
| `engine/fyers.py` | Fyers prices and login (optional, `broker: primary: fyers`) |
| `engine/fyers.py`, `engine/symbols.py` | Market data and contract master access |
| `engine/strategies/` | Strategy definitions, indicators, grading, simulation, portfolio risk and backtest |
| `engine/store.py` | Local state, encrypted credentials and conflict-safe publishing |
| `engine/news.py`, `engine/telegram.py` | Headline retrieval and notifications |
| `.github/workflows/` | Intraday engine and backtest workflows |
| `tests/` | Offline simulations and regression suites |

## Updating

Back up your `config.yaml` and any local `_data` folder before replacing files. Upload the release contents to your repository, including `.github`. Keep your existing GitHub Actions secrets and the `data` branch. Review the new config defaults before enabling the scheduled workflow.
