# Signal Desk 2.1.2: Kotak rate limit and index names (from the first live log)

- **Kotak refused almost every history request.** The first live run was turned down with "Rate limit exceeded" (HTTP 429) at the flat 8 requests a second the adapter used. Two faults of mine made it worse: Kotak's refusal has a different shape from the one the adapter expected (`{"code": 429, "error": "Rate limit exceeded", ...}`), so no back-off or retry ever ran, and the pacing was one fixed rate for everything.
- **Now:** history requests start at 2.5 a second (quotes and contract lists have their own, faster budget). A refusal cuts the rate by 40 percent and pauses every caller for up to 30 seconds (one pause covers all of them); after 40 accepted calls in a row the rate creeps back up, never past a ceiling. The strategy desk stops its pass while Kotak is cooling and carries on at the next candle, and a pause is never long enough to starve the heartbeat.
- **Midcap Select index.** Kotak answered "Invalid neosymbol" for `NIFTY MIDCAP SELECT`. When an index name is refused, the adapter now looks for the real name in Kotak's cash-market list and retries. If it cannot, it logs the index names Kotak does list. **Kotak check** also prints them.
- The restart message in that log ("I had stopped and have restarted myself") was most likely the engine being held up by the rate-limit retries; the pauses are now bounded.

# Signal Desk 2.1.1: two fixes to 2.0.0

- **Live signals are no longer a candle late.** With next-candle fills, a signal on the newest closed candle used to wait for the following candle before appearing. It now appears at once, filled at that candle's close as the best estimate of the next open (marked `provisional`). The next pass replays the day and replaces it with the real open; the trade keeps the same id. If the next candle gaps through the stop, the setup is dropped, as it always was. Finished days and backtests still use the real next open only.
- **The data branch no longer gains a commit on every publish.** All of a day's publishes are folded into one commit (about one commit a day instead of hundreds). It is still never overwritten blindly: the push is a lease that succeeds only if the branch is exactly what was just read. If another workflow pushed in between, the branch is read again, the commit rebuilt and retried, up to three times.

# Signal Desk 2.1.0: Kotak Neo as the primary broker

## What changed

- **Prices now come from Kotak Neo by default**, through the official `kotakneoapi` SDK (quotes, 5-minute and 1-minute history, contract lists, expiries). `config.yaml` has a new `broker:` block; `primary: fyers` keeps the old behaviour.
- **No daily login.** Kotak's price endpoints need only the Consumer Key (`KOTAK_CONSUMER_KEY`). There is no TOTP, MPIN or static IP involved, and the tool still places no orders.
- New `engine/kotak.py` (price source and contract master with the same interface as Fyers), `engine/brokers.py` (choice of broker), `engine/errors.py` (errors shared by both).
- The strategy backtest and the options backtest accept `kotak` as a source. The `auto` source order is Kotak, then a saved Fyers login, then Yahoo.
- New **Kotak check** workflow (Actions tab): tests the Consumer Key, the share and index names, quotes, history, futures and an option against your own account, and prints PASS or FAIL for each.
- Telegram pairing no longer fails on a non-ASCII message.

## Limits you should know about

- **MCX commodities are paused under Kotak**: Kotak provides no price history for them, and the opening-range rules need it. Use `primary: fyers` to trade them.
- **Not tested against Kotak's servers.** Everything here was tested offline against a stand-in for the SDK that answers the way the SDK documentation describes. The index names (`Nifty 50`, `Nifty Bank`, `Nifty Fin Service`, `NIFTY MIDCAP SELECT`, `SENSEX`), the contract-list columns and the expiry date format are assumptions taken from the SDK's documentation and source. Run **Kotak check** first.
- How far back Kotak's 5-minute history goes is not documented. A symbol with too little history is filled from Yahoo in `auto` mode.
- The private file is locked with `FYERS_SECRET_KEY` if it is set, otherwise with `KOTAK_CONSUMER_KEY`.

# Signal Desk 2.0.0: hardening release

## Included

- Missing volume or required market confirmation now fails closed to WATCH.
- Index signals are explicitly exempt from stock-volume checks, and Nifty is not checked against itself.
- Stock sizing returns no trade when one share exceeds the rupee-risk or position-value limit.
- Strategy simulation defaults to next-five-minute-candle-open entries; a gap through the original stop invalidates the trade.
- Strategy candidates pass a chronological portfolio admission controller with TAKE-grade, same-underlying, open-position, aggregate-risk and daily-loss checks.
- Rulebook and strategy engines now contribute to shared cross-engine limits for position count, aggregate risk and realised daily loss.
- Stale option premiums are explicitly marked stale and are not reused as fresh management prices. If the tool rolls to a new day without a verified option exit price, the position is retained as UNRESOLVED and excluded from realised P&L.
- Unresolved old open strategy trades are retained rather than silently discarded by archival.
- Publishing no longer force-pushes; non-fast-forward conflicts fail safely or rebase and retry.
- Telegram chat pairing requires an explicit random pairing secret unless a numeric `TELEGRAM_CHAT_ID` is configured; both settings are now passed from GitHub Actions secrets into the engine workflow.
- RSS URLs and dashboard links are restricted to HTTP/HTTPS; dynamic news headline text is HTML-escaped before Telegram delivery.
- Dashboard now separates standalone strategy results from portfolio-admitted results and includes a chronological diagnostic view.

## Default risk controls

- Shared across engines: 8 concurrent positions, ₹17,500 aggregate initial risk, ₹3,500 realised daily loss.
- Strategy desk: 5 concurrent positions, one position per underlying, ₹2,500 aggregate initial strategy risk, ₹1,500 realised daily strategy loss.
- Rulebook desk: 5 concurrent positions, ₹15,000 aggregate initial rulebook risk, ₹3,000 realised daily rulebook loss.

These are paper-admission controls, not broker margin controls. Change them in `config.yaml` only after reviewing the implications.

## Known limitations

- No broker order placement or live execution adapter is included.
- Fyers live login, token refresh and quote availability require verification in the user's own account; this environment has no Fyers credentials.
- Strategy fills/costs are approximations. Actual bid/ask spread, queue position, partial fills and market impact are not fully modelled.
- Index backtests use spot index data as a proxy for futures.
- Options backtests use model-estimated premiums rather than historical options-chain data.
- Portfolio covariance, sector exposure, options Greeks and real-time broker margin are not modelled.
- The chronological 60/20/20 split is a report diagnostic, not an untouched holdout or a walk-forward optimisation.
- Public GitHub Pages deployments publish dashboard data through the `data` branch. Treat trade history and status as public.

## Verification

The release is checked with Python compilation, strategy regression tests, historical simulation/backtest tests, publishing collision tests, unresolved-option lifecycle tests and dashboard JavaScript syntax validation. The complete `tests.test_engine` replay is self-contained and offline: it uses a compact fixture set under `tests/fixtures/contract_masters/` and deterministic representative market-limit scenarios. Separate scenarios cover the other contract families and the news path. Live Fyers and Telegram operations have not been exercised here.
