# How Signal Desk is put together (5.0)

Signal Desk reads market data and paper-trades. It places no orders. It is three parts with one boundary between them.

```
engine/      Python. Brokers, strategies, risk, research, publishing.
contract/    JSON Schemas for every file the engine publishes. The only thing the two sides share.
web/         TypeScript dashboard. Reads the published files; never talks to a broker.
```

## The contract

The engine publishes to the `data` branch. The dashboard reads from `raw.githubusercontent.com`. Between them are these files (schemas in `contract/schemas/`, validated in the tests):

| File | Written by | What it is |
|---|---|---|
| `manifest.json` | every publisher | Each published file with size, SHA-256 and when its content last changed. The dashboard reads this first and fetches only what changed. |
| `live.json` | the trade engine, every cycle | The present moment: ideas with the checks behind their grades, open positions, risk used, market read, data health, freshness thresholds. |
| `ledger-YYYY-MM.jsonl` | the trade engine | Every settled paper trade. Append only; written once; never edited. |
| `chartlive_<strategy>.json`, `chartlive_options.json` | the trade engine | One chart per idea, by trade id: candles, entry, stop, target, trailing line (`chart.schema.json`). An idea's `chart` field in `live.json` says which file and key. Swing charts travel inside `swing.json`. |
| `swing.json`, `swing_state.json`, `ledger_swing-YYYY-MM.jsonl` | the Leaders workflow, each weekday evening | Swing setups, the paper book, and its settled trades. |
| `journal.bin` | the trade engine | Your Took it / Skip record, AES-GCM encrypted. |
| `feed.json`, `strategies.json`, … | the engines | Their working state. Kept for the old dashboard and for recovery; the new dashboard does not depend on them. |

TypeScript types are generated from the schemas (`web/scripts/gen-types.mjs`), so a field renamed in Python changes the schema, which breaks the dashboard's type check in CI instead of breaking your screen.

**Why the ledger files are not in a subfolder.** The publisher refreshes other workflows' top-level files from the branch before it pushes, which stops a stale clone from overwriting newer lines. Subfolders are not refreshed. A file's owner is decided by name: `ENGINE_PREFIXES` in `engine/store.py` for the live engine (`chartlive_`, `ledger-`); the swing desk owns `ledger_swing-`.

## The engine

```
feeds (Kotak, Fyers, NSE, news) → desks → grading → portfolio → ledger / live.json → publish
```

| Module | Job |
|---|---|
| `engine/core.py` | The rulebook desk: opening-range breakouts as index and commodity options |
| `engine/strategies/` | The strategy desk: three intraday strategies, the four-check grade, the simulator |
| `engine/swing.py` | The swing desk: one multi-day rule, evaluated nightly, with a paper book |
| `engine/desks.py` | Runs each desk on its own, so one failing never skips another |
| `engine/portfolio.py` | The portfolio rules both engines share: position, risk and loss limits, and the index-correlation cap |
| `engine/costs.py` | One cost model |
| `engine/domain.py` | The shared types: `Idea`, `Position`, `LedgerTrade`, `Check` |
| `engine/live.py` | Builds `live.json`: ranks ideas, attaches checks, research evidence, the plan in words and the chart reference |
| `engine/charts.py` | Charts for rulebook option trades and scanner ideas (strategy and swing charts are made where their trades are) |
| `engine/ledger.py`, `engine/migrations.py` | The permanent trade record, and schema upgrades that never discard it |
| `engine/manifest.py`, `engine/store.py` | The index, and conflict-safe publishing |
| `engine/journal.py` | Telegram buttons and the encrypted journal |
| `engine/evidence.py` | What the research found for each kind of idea, shown next to it |
| `engine/run.py`, `engine/supervisor.py` | Broker login, sessions, Telegram commands; the always-on supervisor |

### Rules the design holds to

- **A TAKE is not a promise.** It means an idea passed this tool's own checks. Every idea carries the research record for its kind. Nothing in the research has shown a reliable edge after costs.
- **Unknown is not a pass.** A check whose data is missing or stale is `unknown`, which counts as failing, and is shown as such.
- **The page may lower a grade, never raise one.** Stale data or an old signal turns a TAKE into WATCH or EXPIRED.
- **Nothing is invented.** A position with no verified exit price is `UNRESOLVED`, with no P&L, never closed at a guess.
- **The record is permanent.** Schema upgrades migrate, or archive settled trades to the ledger before any reset.
- **Strategy rules are frozen.** The holdout is spent, so entry and exit rules are not changed without fresh evidence.

## The dashboard

`web/` is a Preact app built by Vite, deployed to GitHub Pages by `.github/workflows/pages.yml`. It has six screens (Today, Positions, Market, Swing, Journal, System). `?demo` shows sample data made by `python -m tests.make_web_fixtures`; `?demo&stale` shows how it looks when the engine has stopped.

- `src/lib/trust.ts` decides whether the numbers can be acted on (live, degraded, stale, closed, offline).
- `src/lib/ideas.ts` decides what each idea means *now*, and ranks them.
- `src/lib/data.ts` chooses the data source (only `raw.githubusercontent.com` data branches are accepted), fetches only changed files, and keeps the last good copy when a fetch fails.
- `src/lib/journalCrypto.ts` decrypts the journal with the browser's WebCrypto; the passphrase is never stored.

## Testing

```bash
python -m pytest                       # engine, research and contract tests (offline)
cd web && npm test                     # dashboard logic and components
cd web && npm run typecheck && npm run build
```

CI (`.github/workflows/ci.yml`) runs both on every push. Contract tests validate real engine output (a full simulated trading day, the sample data, the ledger) against the schemas. The swing paper book is checked against `engine.daily.simulate`, the research's own simulator, night by night.
