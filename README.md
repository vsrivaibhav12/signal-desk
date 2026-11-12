# Signal Desk 4.1.0 — multi-broker paper-trading and market-intelligence platform

**4.1.0 adds two persistent 24/7 services, dual-broker market-data routing and an event-driven Impact Radar.** Kotak Neo remains the primary NSE/BSE provider; Fyers can be configured as the dedicated MCX feed. The News Impact Radar runs independently of exchange sessions, maps macro and company headlines to conditional instrument pathways, and can promote relevant Nifty 200 equities into the ordinary strategy watchlist. See [4.1.0 release notes](RELEASE_NOTES.md), [upgrade guide](UPGRADE_4.1.0.md) and [news-intelligence guide](docs/NEWS_INTELLIGENCE.md).

Signal Desk combines two persistent services, session-aware market engines and its research workflows:

1. **News Intelligence (24/7):** polls configured RSS searches every five minutes, classifies macro/corporate events using deterministic rules and optionally structured AI analysis, maps affected commodities/indices/Indian equities, and publishes an Impact Radar. It never generates a trade by itself.
2. **Trading Supervisor (24/7 process):** stays alive continuously, starts the existing market engine during configured NSE/BSE/MCX sessions, and sleeps between sessions. The scanners do not poll closed markets or invent prices when an exchange is shut.
3. **Market engines (exchange sessions):** a rulebook engine for index, commodity and stock-option paper trades, plus a strategy desk for the researched stock strategies. Kotak Neo and Fyers are read-only data providers; no broker orders are submitted.
4. **Research:** historical bhavcopy data, point-in-time result replay, strategy/backtest experiments, prospective option quote capture, charts and paper-trade evaluation.

The default broker routing is Kotak Neo for NSE/BSE and Fyers as an optional MCX secondary feed. Without verified Fyers MCX access, commodity markets remain disabled while Kotak equity/index scanning continues. Fyers login is checked per configured commodity: each MCX contract must return a usable quote and recent valid five-minute futures candles before that market is enabled.

> **Research/paper-trading software only.** It does not place orders and is not evidence of a profitable strategy. Impact scores rank headline relevance; they are not price-move probabilities. Confidence labels describe the confidence in an instrument mapping, not the probability of a price move. News impacts are conditional hypotheses that need source verification and independent market confirmation. Historical option premiums are not fully replayed from past option chains; the quote tape is prospective only. Do not connect real capital based only on these reports.

## Start here

- Read **[SETUP_GUIDE.md](SETUP_GUIDE.md)** for the existing GitHub Actions setup, Kotak/Fyers configuration and dashboard.
- Read **[docs/NEWS_INTELLIGENCE.md](docs/NEWS_INTELLIGENCE.md)** and **[deploy/README.md](deploy/README.md)** to deploy both persistent workers on a Linux VPS or always-on office PC.
- Review **[RELEASE_NOTES.md](RELEASE_NOTES.md)** and **[UPGRADE_4.1.0.md](UPGRADE_4.1.0.md)** before replacing source files.
- Runtime settings are in **`config.yaml`**.

## Main capabilities

| Area | What it does |
|---|---|
| 24/7 news intelligence | Seven RSS query groups, five-minute polling, event deduplication, source evidence, macro transmission rules, company-specific catalysts, optional OpenAI structured analysis and Telegram alerts |
| News-to-watchlist | High-relevance event-linked Nifty 200 equities can be promoted with a reason; the standard candle freshness, volume, market, liquidity, strategy and risk gates still apply |
| Broker routing | Kotak Neo for NSE/BSE and optional Fyers secondary for MCX; exact instrument prefix determines provider, with no silent fallback |
| Equity/index markets | Kotak-based quotes/history and option strategy scanning during exchange sessions |
| MCX markets | Fyers quotes/history and current MCX contract master for Crude Oil, Gold, Silver and Natural Gas, after login and market-data preflight |
| Research | Three intraday strategies, daily relative strength/breadth, historical replay, results watchlist, archetype/playbook research and trade lifecycle tracking |
| Data integrity | Exchange/receipt timestamps are separated; stale, malformed and materially future-dated prices/candles are rejected; mandatory unknown data fails closed |
| Options research capture | Prospective bid/ask, depth, OI and quote-time observations saved for future option-premium research |
| Dashboard | Decision cockpit, Impact Radar, broker status, market context, data health, filtered candidates/options, open risk, trades and research logs |
| Automation | 24/7 systemd/Task Scheduler trading supervisor and news worker; GitHub Actions for recovery/research and best-effort news fallback |

## Always-on means the services stay alive; market scans remain session-aware

For a genuinely continuously running installation, deploy both `engine.intelligence --daemon` and `engine.supervisor --daemon` using the two systemd units or Windows Task Scheduler tasks in `deploy/README.md`. The news worker polls through evenings, weekends and holidays. The trading supervisor stays alive but invokes the price/strategy engine only in configured exchange sessions, sleeping between sessions. GitHub Actions remains a recovery and best-effort fallback path, not the primary daemon; scheduled Actions can be delayed or dropped.

## Research limitations to keep in view

- The live intraday replay still uses today's Nifty 200 membership for historical dates. The daily bhavcopy research has a separate point-in-time liquidity universe.
- Historical news is not yet replayed point-in-time. New event impacts are captured prospectively; results-calendar events are included where historical data is available.
- Sector rotation is not fully implemented because reliable point-in-time sector classifications are unavailable in the current dataset.
- The historical options backtest remains an underlying/premium proxy. `options_tape.jsonl` captures real observed quote fields prospectively; it cannot recreate expired contracts' historical premiums, Greeks or implied volatility.
- News impact `confidence` is not calibrated against historical event outcomes. Do not interpret it as an estimated probability. A calibrated probability requires an event dataset, explicit outcome definition and out-of-sample validation.
- Portfolio covariance, sector concentration, option-Greek exposure, dynamic broker margin and order-book impact are not yet fully modelled.
- The previous strategy holdout has been consumed. New strategy changes need a new untouched period or prospective paper-trading evidence.

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
