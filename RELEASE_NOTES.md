# Signal Desk 5.0.0: the decision cockpit

Released 12 November 2026. Upgrade steps: [docs/upgrades/UPGRADE_5.0.0.md](docs/upgrades/UPGRADE_5.0.0.md). How it fits together: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

**Structure**
- **A published contract.** `contract/schemas/` defines `live.json`, `manifest.json`, `swing.json` and the ledger. The dashboard's TypeScript types are generated from them, and the tests validate real engine output (a whole simulated trading day, the sample data, the ledger) against them.
- **`manifest.json`.** Every publish writes an index of the data branch with each file's size, SHA-256 and when its content last changed. The dashboard reads it first and fetches only what changed. An idle publish adds no commit.
- **A permanent ledger** (`ledger-YYYY-MM.jsonl`). Every settled paper trade, written once and never edited. Schema upgrades now migrate the data, or archive settled trades to the ledger before any reset; previously a schema change wiped the log.
- **One shared portfolio module** (`engine/portfolio.py`). The position, open-risk and daily-loss arithmetic that `core.py` and `strategies/risk.py` each wrote is now written once (checked against the originals on 5,000 random cases). **One cost table** (`engine/costs.py`).
- **Isolated desks** (`engine/desks.py`). An unexpected error in one desk no longer skips the other for that minute. A broker authentication error still stops the cycle, as before.
- **Packaging and CI.** `pyproject.toml`, `requirements-dev.txt`, and a CI workflow that runs the Python suite (3.10 and 3.12) and the dashboard's type check, tests and build on every push.

**Logic**
- **Index-correlation cap.** Nifty, Bank Nifty, Fin Nifty, Midcap Nifty and Sensex move together, so no new index position is accepted once two same-direction index positions are open across both engines and options and futures (`portfolio.max_same_direction_index_positions`, set to `0` to turn it off). This is a behaviour change.
- **Ranked ideas with their evidence.** `live.json` ranks ideas by a documented 0 to 100 quality score (not a probability), lists the checks behind each grade as pass, fail, unknown or not applicable, and attaches what the research found for that kind of idea.
- **Swing desk** (`engine/swing.py`, run by the Leaders workflow each weekday evening). Applies the one swing rule taken to the sealed holdout, unchanged, plus a Nifty-above-its-200-day filter, and follows a paper book exactly as `engine.daily.simulate` does (tested night by night against it). Ideas are SETUP, never TAKE. The holdout result (+0.08R over 127 trades, range -0.26 to +0.44) is shown beside every one. Gap-ups on results days are listed for observation only; they are not paper-traded.
- **Journal.** Telegram alerts carry Took it and Skip buttons; after Took it, reply with your fill. Entries (with slippage against the paper price) are stored AES-256-GCM encrypted on the data branch and decrypted in your browser. Off unless `JOURNAL_PASSPHRASE` is set.

**Dashboard** (`web/`, Preact + TypeScript, 68 KB; the previous dashboard is kept at `legacy/`)
- Six screens: Today (one ranked list across all desks, risk budget, market read, Impact Radar), Positions, Market, Swing (with a position-size calculator), Journal, System.
- **Every number carries its age.** When the engine stops publishing or prices go old, the page itself shows TAKE as EXPIRED or WATCH and says why. The page can lower a grade, never raise one. A closed session is shown as closed, not as stale.
- Sample data at `?demo`, and `?demo&stale` shows the stopped-engine state. Light and dark. Phone layout with a bottom tab bar. The data source accepts only a `raw.githubusercontent.com` data branch.

**Verification.** 210 offline Python tests and 65 dashboard tests pass; Python code parses as 3.10; the dashboard type-checks and builds from a clean `npm ci`; the schemas are valid and every workflow file parses. The encrypted journal written by Python was decrypted by Node's WebCrypto and by the real browser. The dashboard was driven in a browser on the sample data at desktop and 375 px width: no page-level horizontal overflow, the idea sheet opens and closes, stale mode removes every TAKE, the journal unlocks with the right passphrase and refuses a wrong one.

**Not verified here.** Live Kotak or Fyers sessions; a real Telegram button tap; a real GitHub Actions run, the Pages deployment and the Leaders workflow's Yahoo fetch of Nifty; a long unattended session. Screenshots could not be taken (the browser pane was hidden), so the visual design was checked through the page structure and layout measurements, not by eye.

**Boundaries that remain.** No strategy has shown a reliable edge after costs; a TAKE means an idea passed this tool's own checks. The browser cannot reach a broker, so the cockpit is as fresh as the engine's last publish. The data branch is public when the repository is; the journal is encrypted, but the ledger and the trade record are not.

---

# Signal Desk 4.1.1: Kotak and Fyers workflow hotfix

Released 12 November 2026.

- **Kotak Midcap index fix:** changes the default Kotak quote symbol for `NSE:MIDCPNIFTY-INDEX` to the exact case-sensitive `nse_cm|NIFTY MID SELECT`. The previous `NIFTY MIDCAP SELECT` value caused HTTP 400 invalid-neosymbol responses and repeated failures in each market cycle. The Kotak check now treats configured index probes as essential.
- **Kotak history throttling:** lowers the initial history request rate, applies per-request-class adaptive pacing and escalating 30/60/120/180-second cooldowns after HTTP 429, honours `Retry-After` up to the configured cap, and no longer resets rate-limit history after one successful unrelated call. This reduces repeated 429 pressure but cannot override Kotak's account-level/server-side limits.
- **Fyers per-symbol diagnostics:** reads each quote row's status and retains provider error details instead of silently reporting only “no usable quote”. A row marked `s=error` is never accepted as a live quote even if the provider includes partial price fields.
- **Fyers permission/auth distinction:** HTTP 401 remains an authentication failure; HTTP 403 is surfaced as a permission/request error even when its body contains a token-like numeric code. Fyers MCX diagnostics now direct the user to verify the API app's Market Data permission and MCX segment access rather than repeatedly requesting a new login when authentication already succeeded.
- **Preflight evidence:** MCX markets continue to be independently enabled only after quote and valid recent five-minute history checks. A successful OAuth exchange alone does not enable the market.
- **Tests:** adds regressions for exact Kotak Midcap mapping, 403 permission classification, per-symbol error rows carrying partial LTP, and escalating Kotak cooldowns.

**Operational boundary:** these are offline-tested source fixes. They do not prove that the user's Fyers app has Market Data permission or that Kotak's live rate limits will permit the configured scan frequency. Complete the account-specific checks after deploying this patch.

---

# Signal Desk 4.1.0 — continuous news intelligence and dual-broker routing

Released 12 November 2026.

- **24/7 News Impact service:** adds `engine/intelligence.py`, a separate daemon that polls seven configured RSS topic groups every five minutes, including evenings, weekends and exchange holidays. It persists/deduplicates events, source evidence, a 21-day event history, health and next-scan metadata, and publishes `intelligence.json` to the shared `data` branch.
- **Macro transmission mapping:** deterministic rules cover Middle East/energy supply risk, central-bank/rates/FX, China/industrial metals and Indian monsoon/agriculture. Example pathways include Iran/Strait of Hormuz escalation → conditional crude/gold/LNG risk and first-/second-order effects on Indian upstream producers, refiners, airlines and shipping.
- **Company-specific news:** known Indian company names/tickers can map to positive, negative or mixed corporate catalysts (for example, a named company order win or regulatory probe). Unmapped headlines stay unclassified instead of inventing instruments.
- **Optional structured AI analysis:** when `OPENAI_API_KEY` is present and `news_intelligence.ai.enabled` is true, a small bounded number of new headlines per scan can receive Responses API JSON-schema interpretation. Only catalogued asset IDs are accepted. Without the key, deterministic rules continue. AI can refine explanatory mapping; it cannot supply numerical probabilities, trade instructions or override hard risk gates.
- **News-to-stock discovery:** fresh, high-relevance mapped equities in the current Nifty 200 universe can enter the strategy desk's in-play list with an event-specific reason. Normal stock candle, participation, market context, cost, strategy-conflict and portfolio-risk checks remain mandatory; news never produces a TAKE on its own.
- **Impact Radar dashboard:** adds a filterable all/commodities/equities/indices view with headline, sources, impact relevance score, mapping confidence, horizon, affected instruments, causal explanations, confirmation/invalidation conditions, service heartbeat and errors. It explicitly states that impact score is not price probability.
- **Dual-broker routing:** adds `broker.commodity_feed: fyers`. Kotak remains the primary provider for NSE/BSE; `MCX:` price requests and `MCX_COM` contract-master lookups route to Fyers. Broker statistics are aggregated. A missing or unauthenticated Fyers secondary feed disables only MCX, not Kotak's equity/index scanner. Each configured commodity is enabled independently only after its Fyers futures contract returns a usable quote and recent valid five-minute history.
- **Fyers instrumentation:** quote/history calls are counted by method, failures and rate-limit responses. MCX preflight now requires a positive live price on a current contract; there is no hard-coded expired-contract fallback.
- **Persistent worker deployment:** adds hardened systemd units and Windows Task Scheduler guidance for both the 24/7 news worker and the 24/7 trading supervisor. The supervisor sleeps between configured market sessions and suppresses GitHub runner hand-over when running as the persistent host. GitHub Actions remains a best-effort recovery/fallback path.
- **Shared data safety:** `Store.publish` now serialises publishers on the same host, supports preservation of files owned by the other persistent service, uses process-local GitHub token configuration and excludes runtime locks/temporary files from the public data branch.
- **Always-on dashboard status:** reads `supervisor.json` and `intelligence.json` separately, distinguishes news-worker health from trading-session status, and no longer labels an expected out-of-session rest as a missed engine heartbeat.
- **Dashboard heartbeat fix:** normal resting states and a fresh recent heartbeat no longer trigger an immediate red “not checking in” banner. A genuine missed-heartbeat window is required; secondary Fyers login is surfaced separately.
- **Test coverage:** added multi-broker routing/contract-master tests, per-commodity Fyers quote/history preflight tests, macro/company event mapping, event deduplication, safe conditional classification and watchlist promotion tests.

**Operational boundary:** both persistent daemons can remain alive continuously; actual equity/index/MCX strategy scans remain bounded by their exchange sessions. The release was verified offline; it has not been authenticated against the user's Kotak/Fyers accounts or deployed on an always-on host from this build environment.

---

# Signal Desk 4.0.0: decision cockpit, hardened data and prospective options research

Released 12 November 2026.

- **Dashboard redesign:** a trader-first decision cockpit with market pulse, breadth and index context, data-health gates, quote coverage, open positions/risk/P&L, stock candidates, filtered options ideas, a searchable/sortable watchlist, relative-strength leaders, trade-detail sheets, and searchable/exportable trade logs. Adds Auto/Dark/Light themes, compact mode, accessible focus states, responsive layouts, remembered filters/expanded panels, manual refresh and freshness-aware refresh behaviour. Deep research and logs are kept behind secondary sections so the first screen stays decision-focused.
- **Per-quote integrity:** Kotak and Fyers adapters preserve receipt time separately from exchange time, identify malformed or materially future exchange timestamps, and avoid silently replacing an invalid supplied timestamp with a fresh local timestamp. Numeric inputs reject NaN and infinity.
- **Watchlist quality:** individual quotes are checked for freshness, future skew and valid positive prices. Coverage and timestamp-source counts are published. A quote scan being recent no longer implies every quote in that scan is recent.
- **Candle integrity:** stock/index candle series are normalised before indicators run. Invalid timestamp/OHLC bars are excluded, impossible OHLC relationships are rejected, duplicate timestamps resolve deterministically to the last returned row, and invalid volume is zeroed so it cannot appear as unusual volume. Empty history is handled safely.
- **Fail-closed strategy grading:** selected stock candle freshness and integrity are checked; invalid/stale candidates are skipped. Current-cycle series are rebuilt rather than carrying an old index series into a later failed data fetch. Stale relative-strength data is not attached to current trade/watchlist cards.
- **Options checks:** the scanner requires a current, finite quote with valid bid/ask, acceptable spread, sufficient bid and ask displayed depth for the lot, and minimum open interest. Missing, invalid, stale or future-dated mandatory data cannot produce TAKE. The Nifty direction confirmation remains three-state (PASS/FAIL/UNKNOWN); UNKNOWN is WATCH.
- **Prospective options quote tape:** complete live bid/ask/depth/OI/quote timestamp/decision observations are recorded to `options_tape.jsonl` for future option-premium research. It is persisted to the `data` branch, capped in size and removed by Reset data. A tape write failure is visible in health and does not stop the decision cycle. This does not backfill historical option chains.
- **Data-health dashboard:** includes quote coverage, exchange-vs-response timestamp counts, malformed/stale selected candles, broker/API status and option quote-capture status. Statuses distinguish passing, partial, stale, invalid and unavailable information.
- **Regression coverage:** added tests for malformed/future broker timestamps, non-finite prices, per-quote freshness, bad OHLC/timestamps/volume, duplicate candles, future/stale options quotes, depth/OI gates, tape capture and tape-write failure.
- **Verification:** 70 offline `pytest` tests passed; Python `compileall` and YAML configuration parsing passed. The local environment did not run an authenticated live Kotak/Fyers session or a live market session.

## Important boundaries that remain

- Signal Desk reads market data and paper-trades only. It does not place broker orders.
- Historical options research still uses an underlying-price/premium proxy. The option quote tape starts prospectively from 4.0.0; it cannot create the missing historical option chains, premiums, Greeks or implied volatility for past dates.
- Intraday replay still uses the current Nifty 200 membership for its historical stock universe; the daily bhavcopy research uses a survivor-free point-in-time liquidity universe, but that does not yet make the intraday replay survivor-free.
- Historical news is not replayed point-in-time. Results events are available, but a timestamped historical news archive is not.
- Sector rotation is not implemented because reliable point-in-time sector classifications are not available in the current free data set.
- Portfolio-wide covariance, sector concentration, option Greeks, dynamic broker margin and order-book impact modelling remain future work.
- Relative-strength, breadth and strategy statistics are research context, not predictions or proof of future profitability. The previously sealed holdout has been consumed and must not be reused to tune new rules.

# Signal Desk 3.7.0: the "Market research" document, tested

- **`engine/archetypes.py`**: the document's mean-reversion (RSI 2), momentum-burst, episodic-pivot, exit, pilot-sizing and rolling-profit-factor claims written as exact rules before any result was seen, tested on the survivor-free universe with costs, a random-entry placebo and the sealed holdout. Uses NSE's results history (73,508 announcements) for the episodic test.
- **Result:** dip-buying has a tiny edge (+0.05% a trade after costs, interval spans zero, beats random entry); tight stops destroy it (confirming the document); bursts lose; time stop, squat exit and pilot sizing add nothing per unit of risk; no setup reaches the document's 75%-of-windows profit-factor mark. A post-hoc broader run of episodic pivots hints at gap-ups on results days (28 trades, +0.51R, interval spans zero): a question for new data only.
- `simulate2` in the playbook gained optional time-stop and squat exits. Dashboard: new archetypes block under Daily research.
- Details in `docs/STRATEGY_RESEARCH.md`.

# Signal Desk 3.6.0: the Leader Playbook, tested, and a live leader list

- **Live relative-strength leaders** (`engine/leaders.py`, a **Leaders** workflow every weekday at 7:15 pm): from NSE's daily price file, rates every liquid share's relative strength, lists the leaders (strength 80+, above the 200-day average), and measures market health (share above the 50-day and 200-day averages, advances). One download a day, no broker requests. The Trade engine flags leaders on the watchlist, trade cards (a "Leader 92" chip), alerts and the morning brief. It is a flag, not a gate. The dashboard has a market-health and leaders block under Results and watchlist.
- **`engine/playbook.py`**: the notes' tight pivot breakout, pullback to the 21-day average, partial-profit exit, bear gauge and position-size ideas, written as exact rules before any result was seen and tested on the survivor-free universe with a random-entry placebo and the sealed holdout. A test proves later prices cannot change any result.
- **Result:** the pivot and pullback entries do not beat random entry; the partial-profit exit does not help; the bear gauge fired on 24 of 1,764 days; sizing scales return and fall together. The relative-strength tight-base breakout at 1.5% risk beat Nifty in both periods but lost in 2018, 2022 and January 2025, and is the most examined result, so it is a hypothesis for the holdout.
- Fixed a bug found by the tests: a local variable in the alert code shadowed the rupee formatter.
- Details and numbers in `docs/STRATEGY_RESEARCH.md`.

# Signal Desk 3.5.0: market context and the 5-minute opening range, tested

- **`engine/context.py`**: relative strength (percentile of the 40/20/20/20 weighted 3/6/9/12-month return among the 200 most traded shares on each date), breadth (share above their 50-day average), a monthly equal-weight relative-strength portfolio, an equal-weight benchmark, and a trend-following index benchmark. The settings and the list of tests were fixed before any result was seen. Sector rotation is not built: no free point-in-time sector classification exists.
- **Daily research** now adds six context breakout tests (with the random-entry placebo) and seven portfolio curves, all computed with the holdout cut away first (a test proves later prices cannot change any result).
- **Result:** relative strength improves the tight-base breakout and beats random entry; breadth adds nothing. The strength portfolio beat Nifty over the whole period (16.1% against 11.7% a year) only because of 2023 to January 2025; it trailed Nifty in 2017 to April 2023 and lost to it in four of nine years, with deeper falls.
- **`orb5`**: the 5-minute opening range breakout of the 2024 paper, as a research candidate (not live), and `python -m engine.replay --strategies orb,orb5`. The replay gains the paper's stock selection (top 20 by opening volume). Result: +0.23R before costs but -0.54R after, because the stop is so tight that costs are about three quarters of the risk.
- Details and the numbers are in `docs/STRATEGY_RESEARCH.md`.

# Signal Desk 3.4.0: survivor-free daily research

- **`python -m engine.bhav`** downloads NSE's own daily price file for every trading day (about 2,500 small files, resumable, paced), which lists every share that traded, including those later delisted, merged or renamed. It builds split- and bonus-adjusted prices from the exchange's own previous close, identifies shares by ISIN, adjusts volume so a split is not a volume breakout, and ranks every share each day by the previous 50 sessions' traded value, giving a point-in-time universe of the 200 most traded shares on each date.
- **`python -m engine.daily --universe nse --bhav <folder>`** runs the same pre-registered rules on that universe. A share that stops trading is closed at its last price, never dropped. The Daily research workflow now does this (and caches the files between runs).
- **Result:** removing survivorship took away between about a third and all of the apparent edge. The shares in today's Nifty 200 averaged 18.5% a year over the period, all shares that traded 8.4%, Nifty 11.7%. A small effect remains in the tight-base rows (+0.09R and +0.19R, beating random entry narrowly), concentrated in a few trades and strong markets, and as a portfolio it does not beat the index. Details in `docs/STRATEGY_RESEARCH.md`. The holdout is still sealed.
- The dashboard's daily block shows the universe used and, for the survivor-free universe, how many shares later stopped trading.

# Signal Desk 3.3.0: daily research, and a correction to the replay

- **Daily research** (`python -m engine.daily`, a **Daily research** workflow, and a dashboard block): a published daily base-and-breakout method tested on about ten years of daily prices for today's Nifty 200. The rules and the eight experiments (four cumulative filters, two exits) are fixed in the code and were not tuned. It includes a **placebo** (random entry on the same shares with the same exits), confidence ranges that resample whole months, results by year and by concentration, a daily marked-to-market portfolio, a survivorship probe, and a sealed holdout that is cut away before any signal is computed (a test proves that changing every later price changes nothing).
- **First result:** positive after costs (+0.14R to +0.31R per trade), but random entry on the same shares did as well, shares in today's index grew 18.5% a year against 11.7% for Nifty (survivorship), and the result swings by year and rests on a few trades. So the test does not show that the breakout adds anything. Details in `docs/STRATEGY_RESEARCH.md`.
- **Correction to the intraday replay.** Version 3.2.0 said its last sessions were a sealed holdout, but its totals, breakdowns and cost table included them. That is fixed (the holdout is removed before any number is computed), the figures are corrected (full system -0.14R, not -0.09R; 0.00R, not +0.05R, with no costs), and that run's holdout counts as used. The replay also takes `--holdout-from DATE` to fix a fresh holdout.
- The portfolio test handles a trade that stops out on its entry day.

# Signal Desk 3.2.0: point-in-time replay of the whole process

- **`python -m engine.replay`** and a **Research replay** workflow. It replays the watchlist as it would have looked at each 5-minute candle (NSE's results calendar for the past dates with real holidays, volume against the ten earlier sessions on the live curve, the same thresholds and per-candle limit), then the strategies with next-candle fills and costs, and counts a trade for the full system only if its share was on the watchlist when the signal closed.
- It reports what each piece adds (every signal, volume promotion, results, either, the grade alone, the full system), with 95 percent ranges that resample whole days, a 60/20/20 split into development, validation and a **sealed holdout** (shown only with `--final`, refused a second time), sensitivity to costs, breakdowns by month, hour, side, exit and reason on the list, the load of the watchlist, and the full system under the portfolio limits.
- First result (60 Yahoo sessions): no row is positive after costs. Full system -0.09R per trade (range -0.17 to +0.01), validation -0.23R; grade alone -0.06R; with no costs the full system is +0.05R, so costs are the problem. The watchlist cuts scanning to about 20 percent of the shares but does not improve expectancy. Details and the BananaPatterns assessment are in `docs/STRATEGY_RESEARCH.md`.
- The dashboard has a **Research replay** block under More. The price loading of the strategy backtest is shared with the replay (`load_series`).
- Not replayed, and stated in the output: news, and historical index membership (today's Nifty 200 is used).

# Signal Desk 3.1.0: hardening, from an outside review

An independent review of 3.0.0 found real defects. I reproduced each before fixing it.

- **Missing Nifty data could approve an index trade (fail-open).** For Bank Nifty, Sensex, Fin Nifty and Midcap, a missing Nifty reading counted as "Nifty agrees", so a 5-of-5 setup was graded worth taking. Nifty agreement is now three-state: pass, fail or unknown, and unknown is only ever watch. A Nifty reading must also be recent (default 10 minutes) and from a candle within 5 minutes of the index it is confirming.
- **Stale index candles are rejected.** An index whose newest candle is older than the limit shows "Data is out of date" and is only watched.
- **The option price must be tradable, not just a last price.** Kotak's quote now keeps its timestamp, best bid and ask, the size offered, and open interest. A contract is only worth taking if the price is recent (default 120 s), has a bid and an ask, the spread is within 5 percent, the lot can be bought at the ask, and costs including the spread are at most 25 percent of the risk. The idea is priced at the ask. If the timestamp cannot be read it counts as unknown, never as fresh.
- **A latent bug of mine:** a score of 4 or more while still inside the opening range could be graded "take" with no contract and then crash the alert. It is now watch ("price is still inside the opening range").
- **Volume baseline queue starvation.** The first 40 shares that failed were retried forever and the 41st was never attempted. Failed shares now go to the back with a back-off (30 minutes, doubling, up to a day).
- **NSE results refresh could retry every minute and block the engine.** The last good read and the last attempt are now separate. A failed read backs off (10 minutes, doubling, up to 2 hours), the read runs in a background thread with short timeouts, and the last good calendar is kept.
- **Exchange holidays.** The next and previous session now skip NSE's published trading holidays (read from NSE, saved, with `holidays:` in `config.yaml` for any it misses). Results are grouped by real sessions, and the morning brief is skipped on a holiday.
- **Measured, not estimated:** every broker request is counted by kind with failures and average time. The dashboard's new **Data health** block shows the requests the last 5-minute pass made, and the status of each input (index candles, quotes scan, volume baseline, results calendar, broker). If an index's candles or the quotes scan are stale, the decision is WATCH.

Not addressed in this release, and still the biggest gap: **the backtest does not replay the watchlist** (results, news, volume promotion, universe on that date), so it still does not test the system as it now runs. The 5-minute opening range of the research is not what the 15-minute opening range here does. Both are on the roadmap in `docs/STRATEGY_RESEARCH.md`. Relative strength, sector rotation and market breadth are not built.

# Signal Desk 3.0.0: four segments, three strategies, quiet alerts

- **Results segment** (`engine/results.py`): reads NSE's public event calendar of results, keeps the Nifty 200 companies, and puts them first on the watchlist from the day before to the day after. If NSE cannot be reached it keeps the last copy and says so; names can be added under `results: extra:` in `config.yaml`.
- **Watchlist and load** (`engine/watch.py`): one quotes pass over the whole Nifty 200 picks the shares on unusual volume or a big move. Only the watchlist gets candles and strategy scans, so broker requests should drop by roughly three quarters (an estimate, not yet measured live). Each share's normal volume is learned from its daily candles and saved (`volbase.json`).
- **Three strategies, not eleven**: opening range breakout, opening drive, EMA trend pullback. The other eight stay in the code for research but do not run. See `docs/STRATEGY_RESEARCH.md` for the evidence and what our own test found (all three lost money after costs).
- **Calls and puts scanner** (`engine/optscan.py`): a five-point score per index, and the contract, premium, stop, target and cost check when it is worth taking. It reads candles the desk already has, plus one price for the suggested contract.
- **Decisions use several facts together**: results and news decide who is watched, volume decides who is scanned and graded, Nifty's side of VWAP and conflicting strategies decide the grade, heavyweight results warn on the index ideas, and every trade carries why it was in play.
- **Alerts reduced to what is actionable** (`engine/alerts.py`): a morning brief, trades worth taking and their endings, call or put ideas worth taking, a short after-close summary, and faults (once an hour each). Trend setters, news digests and start/restart chatter are off by default.
- **The older stock rulebook is off** (`stocks: enabled: false`), since the strategy desk now covers shares with results and volume built in.
- **Clean start**: saved files carry a layout number; an older file is replaced on first run. A new **Reset data** workflow wipes every log, chart and backtest result on demand (type `RESET`).
- **Dashboard** reorganised into segments: Results and watchlist, Worth taking, Calls and puts, My trades, Trend setters, Market, More.

Not tested against the real NSE calendar from GitHub's servers (NSE blocks some networks), nor against Kotak's real servers. The calendar worked from a home connection.

# Signal Desk 2.1.3: index names

- The second live log showed the Midcap Select index still refused ("Invalid neosymbol") and the lookup finding no names. Index rows in Kotak's cash list do not have name-style tokens as assumed. The adapter now searches every row's trading symbol, name and description for the index (exact aliases first, then loose word matches), and tries each name or number it finds. When one works it is used from then on.
- An index with no working name is skipped for the rest of the run instead of being asked for again every pass (that wasted rate-limit budget). The log lists the index-like names Kotak does have, and **Kotak check** prints them, so the right one can be set under `broker: kotak: index_tokens:` in `config.yaml`.
- After a rate-limit refusal the history rate recovers a little faster (25 accepted calls, not 40).

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
