# Signal Desk: one-time setup

You do this once, on a computer, in about 30 minutes. After that the tool runs by itself and you only use your phone.

Nothing here needs a credit card.

---

## Part 1: Make your Telegram alert bot (5 minutes, on your phone)

1. Open Telegram and search for **BotFather** (it has a blue tick).
2. Tap **Start**, then send: `/newbot`
3. It asks for a name. Type anything, for example: `My Signal Desk`
4. It asks for a username. It must end in `bot`, for example: `vaibhav_signaldesk_bot`
5. BotFather replies with a long **token** that looks like `7412345678:AAH...`. Copy it and keep it somewhere for Part 5.
6. Tap the link to your new bot in that same message and tap **Start**. Do not send any credentials to the bot. The first connection will use a one-time pairing code you add as a GitHub secret in Part 5.

---

## Part 2: Put the tool on GitHub (10 minutes)

1. Go to **github.com** and create a free account. Choose a simple username in lowercase.
2. Click the **+** at the top right, then **New repository**.
3. Repository name: `signal-desk`
4. Choose repository visibility deliberately. GitHub Pages and the `data` branch can expose the dashboard, trade history, status and backtest files publicly. The broker and bot keys belong only in GitHub Actions secrets, but the trading record itself should be treated as publishable. Do not use this setup for sensitive personal or client data.
5. Click **Create repository**.
6. On the next page click the link **uploading an existing file**.
7. Unzip `signal-desk.zip` on your computer and open the folder. Select **everything inside it** and drag it into the GitHub page.
   - Make sure the folder named `.github` is included. On a Mac it may be hidden: press **Cmd + Shift + .** in Finder to show it.
8. Click **Commit changes**.
9. Click the **Actions** tab at the top. You should see **Trade engine** in the list on the left. If GitHub shows a green button asking to enable workflows, click it.

If **Trade engine** is missing, the `.github` folder did not upload. Fix: click **Add file**, then **Create new file**, type the name `.github/workflows/engine.yml`, paste the contents of that file from the zip, and commit.

---

## Upgrading an existing installation to 4.1.0

1. Download and unzip the 4.1.0 release. Replace the source files on your repository's default branch with the contents of the new release, including `.github`, `deploy` and `docs`. Do not delete or recreate the repository.
2. Keep your existing GitHub Actions secrets and variables. Add `FYERS_APP_ID`, `FYERS_SECRET_KEY` and optionally `FYERS_PIN` for the secondary MCX feed. Keep `KOTAK_CONSUMER_KEY` as primary for NSE/BSE.
3. Keep the existing `data` branch. It contains the Telegram pairing file and persisted trading/research records, including the prospective options quote tape. 4.1 adds `intelligence.json` for the Impact Radar.
4. Keep GitHub Pages pointed at the same default branch and root folder. The new `index.html`/dashboard template ships with the release.
5. Run **Kotak check** and **Trade engine**. During an MCX session, complete Fyers login through Telegram; commodity contracts remain paused until a current MCX quote is verified. Kotak NSE/BSE scanning remains independent.
6. Run **News intelligence fallback** once from Actions and confirm a fresh Impact Radar scan. For the full always-on installation, deploy both persistent services (`engine.intelligence --daemon` and `engine.supervisor --daemon`) on an always-on Linux VPS or office PC using `docs/NEWS_INTELLIGENCE.md` and `deploy/README.md`. GitHub scheduled workflows are recovery/fallback only, not daemons.
7. Optionally add `OPENAI_API_KEY` for AI-assisted explanations. The rules-based scanner works without it.

The bundled dashboard preview uses clearly labelled synthetic sample records, including an illustrative Iran/energy event. A live GitHub Pages dashboard reads the `data` branch after the first successful workflow run.

---

## Part 3: Switch on your dashboard (2 minutes)

1. In your repository click **Settings**, then **Pages** in the left menu.
2. Under **Source** choose **Deploy from a branch**.
3. Under **Branch** choose **main** and **/ (root)**, then click **Save**.
4. Your dashboard address is:

   `https://YOUR-USERNAME.github.io/signal-desk/`

   Replace YOUR-USERNAME with your GitHub username in lowercase. Keep the `/` at the end. Save this address; you need it in Part 4. It can take two minutes to come alive, and it shows "Nothing to show yet" until the first run. If you use a custom domain or a user-site root URL, set `DATA_REPOSITORY` near the top of `index.html` to `YOUR-USERNAME/signal-desk` so the dashboard can locate the `data` branch.

---

### Optional: the new cockpit (5.0.0)

Version 5.0.0 ships a new dashboard in `web/`. To use it instead of the old one, in **Settings, Pages** set **Source** to **GitHub Actions**, then in **Settings, Secrets and variables, Actions, Variables** add `PAGES_FROM_ACTIONS` = `true`, and run the **Dashboard** workflow. The old dashboard stays available at `…/legacy/`. Full steps and how to go back: [docs/upgrades/UPGRADE_5.0.0.md](docs/upgrades/UPGRADE_5.0.0.md). Until you do this, the steps above keep working unchanged.

---

## Part 4: Let the tool read prices from Kotak Neo (3 minutes)

The tool reads prices from **Kotak Neo**. It only reads. It never places an order, so it needs no static IP, no TOTP, no MPIN and no daily login. All it needs is one **Consumer Key**.

1. Open the **Kotak Neo app** (or neo.kotaksecurities.com) and log in.
2. Go to **More**, then the **Trade API** card.
3. Tap **Generate application** (or open your existing application) and **copy the token**. This is your Consumer Key.
4. Keep it private. Anyone with it can use your Trade API application. You will paste it into GitHub in the next part, and nowhere else.

That is all. Do not type your Kotak password, MPIN or TOTP anywhere in this tool.

> **For MCX commodities, keep Kotak as primary and add Fyers as the secondary feed.** The supplied 4.1 config sets `broker.primary: kotak` and `broker.commodity_feed: fyers`. Create a Fyers data-only app at `fyers.in/web/api-dashboard/user-apps`, choose Market Data permission, enable the MCX commodity segment for your account, and set its Redirect URL to your dashboard address. Add `FYERS_APP_ID` and `FYERS_SECRET_KEY` as repository secrets. The engine checks a current MCX future before enabling Crude Oil, Gold, Silver and Natural Gas. It does not silently fall back to Kotak for MCX history.

---

## Part 5: Store your keys in GitHub's locked area (5 minutes)

1. In your repository: **Settings**, then **Secrets and variables**, then **Actions**.
2. Click **New repository secret** and add these one at a time. The names must be typed exactly.

| Name | What to paste |
|---|---|
| `KOTAK_CONSUMER_KEY` | Kotak Neo Consumer Key (primary NSE/BSE data feed) |
| `FYERS_APP_ID` | Fyers app ID (secondary MCX market-data feed) |
| `FYERS_SECRET_KEY` | Fyers app secret; required with the app ID |
| `FYERS_PIN` | Optional Fyers PIN used for automatic refresh-token renewal |
| `TELEGRAM_BOT_TOKEN` | The token from Part 1 |
| `TELEGRAM_PAIRING_CODE` | A long random one-time pairing secret (unless you provide `TELEGRAM_CHAT_ID`) |
| `TELEGRAM_CHAT_ID` | Optional numeric chat ID; use instead of pairing if already known |
| `OPENAI_API_KEY` | Optional; enables bounded AI-assisted event explanations; omit for deterministic rules-only mode |

**Important credential rule:** keep `FYERS_SECRET_KEY` once you add it. Signal Desk encrypts its private Telegram/Fyers session file using the active broker secret plus the Telegram bot token. When you add Fyers, the new version attempts to migrate the existing file from the previous Kotak key. Removing the Fyers secret later can make that migrated file unreadable and require Telegram pairing again. Never put keys in `config.yaml` or commit them to Git.

**About Telegram pairing:** create a long random value (at least 32 characters) and save it as `TELEGRAM_PAIRING_CODE`. On first run, message your bot `/start YOUR_PAIRING_CODE`. Alternatively, set `TELEGRAM_CHAT_ID` to your numeric chat ID. The news worker can reuse this saved chat destination when it can decrypt the pairing file, or you can set `TELEGRAM_CHAT_ID` explicitly in its environment.

---

## Part 5b: Turn on the leader list (2 minutes, optional)

In the Actions tab choose **Leaders** and press **Run workflow**. It downloads NSE's daily price files (a few minutes the first time), rates every liquid share's relative strength and market health, and publishes the result. It then runs by itself every weekday evening. The Trade engine flags relative-strength leaders on the watchlist, on trade cards, in alerts and in the morning brief. It needs no login and makes no broker requests.

---

## Part 5b-2: Swing setups and the trade journal (optional)

The **Leaders** workflow now also looks for swing setups each weekday evening and follows a paper book (`swing.json`, shown on the Swing screen and in the morning brief). It uses Nifty's daily history from Yahoo Finance for its market filter; if that cannot be read, nothing is marked as a setup.

To record which ideas you take: add a secret **JOURNAL_PASSPHRASE** (a long passphrase you will remember; it cannot be recovered). Telegram alerts then carry **Took it** and **Skip** buttons; after *Took it*, reply with your real fill, for example `182.5 65` (price and quantity) or `-`. The journal is stored encrypted on the data branch and unlocked in the dashboard's Journal screen with the same passphrase.

---

## Part 5c: Turn on the 24/7 News Impact Radar

The **News intelligence fallback** workflow appears in Actions after you upload 4.1.0. Run it once. It fetches the configured RSS topics, maps relevant events to Indian instruments and publishes `intelligence.json` to the existing `data` branch. The Impact Radar in the dashboard will show the latest scan, mapped instruments, conditional direction, confirmation cues and invalidation cues.

For the always-on installation, deploy **both** services on one always-on Linux VPS or office PC:

- News worker: `python -m engine.intelligence --daemon` — polls every five minutes through nights, weekends and holidays.
- Trading supervisor: `python -m engine.supervisor --daemon` — stays alive continuously and starts the trading engine only during configured exchange sessions.

Follow [`docs/NEWS_INTELLIGENCE.md`](docs/NEWS_INTELLIGENCE.md) and [`deploy/README.md`](deploy/README.md). Both services need `GITHUB_TOKEN` with Contents read/write access to publish the dashboard. The `data` branch and `_data` working directory must be shared between these services; the release serialises publishers and protects their respective files. `OPENAI_API_KEY` is optional; deterministic rules continue without it. GitHub Actions is a best-effort recovery path, not the always-on host.

**How it treats news:** for example, an Iran / Strait of Hormuz escalation headline can create conditional crude, gold and natural-gas scenarios, plus possible second-order effects on Indian oil producers, refiners, airlines and shipping. This is not a statement that crude will rise. `Impact score` is headline relevance, not a price-move probability; `confidence` describes confidence in the mapping. The scanner lists what would confirm or weaken the scenario. A high-relevance mapped share may enter the strategy watchlist, but it still must pass every normal price, volume, liquidity, strategy and risk check.

The news service never places orders and never trades when exchanges are closed. Market price scans remain tied to NSE/MCX sessions.

---

## Part 6: Check the connection, then first run (5 minutes)

1. Click **Kotak check** and run it. This validates the Kotak NSE/BSE feed; it does not validate MCX.
2. Click **Trade engine** and run it. The first run pairs Telegram if needed.
3. During the evening MCX window, the engine sends a Fyers login link if its saved token is not valid. Log in, tap **Copy login code**, and paste that code into the same Telegram chat. The bot tests a current MCX future before enabling commodity markets.
4. Confirm the dashboard **Broker status** shows Kotak connected and Fyers MCX connected. If Fyers is not connected, NSE/BSE continues, but MCX remains paused. Review the Fyers/MCX preflight log rather than assuming the Kotak check covered it.

With GitHub Actions only, scheduled workflows provide best-effort starts during market hours. With the persistent deployment, the trading supervisor itself stays running and starts market scans at the configured sessions, without relying on scheduled runner starts. The News Impact worker remains active outside market sessions.

---

## Daily use

- **You do nothing to start it.** Alerts arrive in Telegram. The dashboard keeps the full record.
- **Kotak Neo remains the primary NSE/BSE feed.** Fyers is used only for the MCX secondary feed and may need re-authentication. If Fyers is waiting for login, Kotak equity/index scanning continues while commodities remain paused.
- **Always-on services:** with both persistent daemons deployed, the news worker runs 24/7 and the trading supervisor remains alive 24/7 while scanning only during configured exchange sessions. Without them, the GitHub workflows are best-effort scheduled jobs.
- **Words you can send the bot:**
  - `status` shows what it is watching right now
  - `strategies` shows how each of the three strategies is doing today
  - `skip` stops new trades for today (use it on RBI policy, Budget or other event days)
  - `resume` undoes skip

## What an alert tells you

Every trade alert has the contract to buy, the entry price, the stop-loss, the target, the exit time, the planned risk in rupees, and the list of reasons it was triggered. Later messages tell you when the target is reached, when to move the stop, and when to exit, with the result.

The tool **never places orders**. You decide whether to take a trade in your broker app. The tool keeps score of its own suggestions as paper trades, whether or not you took them.

## Changing settings

Open `config.yaml` in your repository, click the pencil icon, change a value, and click **Commit changes**. Every line has a note explaining it. Common changes:

- Turn a market off: set its `enabled` to `false`
- Trade two lots: set `lots: 2` (half is booked at the target, the rest is trailed)
- Bring back daily limits: set `max_trades_per_day: 2` and `stop_after_losses: 1`
- Switch early exits off: under `early_exit` set `enabled: false`
- Switch the news scan off: under `news` set `enabled: false`
- Add no-trade dates: fill in `skip_dates`
- Use the big crude contract: change `CRUDEOILM` to `CRUDEOIL` and `multiplier` to `100`

## If something looks wrong

- **No morning message from the bot:** GitHub's timer sometimes starts late. The tool is asked to start every 10 minutes from 8:30 am, so one late knock no longer matters. If nothing has arrived by 9:10, start it by hand as in Part 6, step 1.
- **The dashboard says "Not checking in":** the engine stopped. It restarts by itself within about 10 minutes and tells you on Telegram. You do not need to do anything unless the message stays for 20 minutes.
- **Lots of short runs in the Actions tab:** that is the every-10-minutes knock. Runs that finish in under a minute found the tool already awake.
- **Bot says Kotak Neo is not giving me prices:** the Consumer Key was not accepted, or Kotak is down. Open the Kotak Neo app, More, Trade API, copy the token again and update the `KOTAK_CONSUMER_KEY` secret. Then run **Kotak check**.
- **Kotak check says an index name was not found:** Kotak spells index names its own way. The check prints what it tried. Put the right names under `broker: kotak: index_tokens:` in `config.yaml` (examples are in the file).
- **The log says "Rate limit exceeded":** Kotak limits how fast prices can be asked for. The tool slows itself down and pauses; a few of these lines when it starts are normal. If they never stop, tell me how many per minute you see.
- **The bot says commodity markets are paused:** Kotak Neo gives no price history for MCX commodities. Use `broker: primary: fyers` if you want crude, gold, silver and natural gas.
- **Bot says Fyers did not accept the code (Fyers only):** codes expire in a few minutes. Tap the login link again and paste the new code straight away.
- **The backtest says no Telegram chat is linked yet:** run the Trade engine once first, so the bot knows who you are.
- **A red cross on a run in the Actions tab:** click the run, then **run**, and read the last lines. They say in plain words what is missing.
- **GitHub emails that the scheduled workflow was disabled:** open the Actions tab and click **Enable workflow**.
- **Nifty 200 list changed:** edit `engine/nifty200.txt` (NSE revises the index twice a year).

## Running a backtest (testing the rules on past prices)

A backtest replays old market days through the same rules the live tool uses, then shows what would have happened.

**Quick version (start here, no login, about 3 minutes):**
1. Open your repository on GitHub and click the **Actions** tab.
2. Click **Backtest** in the list on the left, then **Run workflow**.
3. Leave the choice on **quick** and press the green **Run workflow** button.
4. When it finishes, open your dashboard and scroll to **Backtest: the same rules on past prices**. You also get a short Telegram message.

The quick version uses a free price source and covers about the last 60 days of market days, with prices every 5 minutes.

**Longer version (uses your broker's prices):**
1. Run it the same way, but change the first choice to **kotak** (or **fyers** if you use Fyers) and set **days** to `180` (or more).
2. Kotak Neo needs no login, so it just runs. Fyers may send you its usual login link first.
3. It takes 30 to 90 minutes. Run it after the market closes.
4. The broker decides how far back its one-minute prices go. The backtest uses whatever it returns and says on the dashboard which dates it covered.

**How to read the result**
- **Net result after costs** is the number that matters. Costs are estimates (brokerage, taxes and the gap between the price you see and the price you get), and they are listed under "What this test assumes".
- **Win rate "could be X% to Y%"** shows how uncertain the win rate is with that many trades. Fewer trades means a wider range.
- **Luck check** reruns the same rules on shuffled prices, which have no real pattern. If the real result is not clearly better than that, there is no sign of a real edge yet.
- **Does changing the settings help?** compares the current settings with no early exit, and with the old daily limits.
- The backtest covers indices and stocks. Crude oil, gold, silver and natural gas are left out of historical strategy validation because expired commodity contracts do not have a complete long-term history in the current research data. Option prices are estimated, because brokers keep no history for expired options. 4.0.0 starts a prospective bid/ask/depth/OI tape; it does not backfill expired option chains.

## How the tool fits together (four segments that depend on each other)

The tool is one decision machine made of four segments. Each feeds the others, so a decision rests on several facts at once.

1. **Results and news.** Every few hours the tool reads NSE's public calendar of board meetings that will consider financial results, and keeps the Nifty 200 companies. A company is watched from the day before its results until the day after: *results just out* (watch the reaction at the open), *results today*, and *results before the next session*. News headlines (results, orders, deals, broker calls) add shares too. These go first on the watchlist, so the tool is already looking when the move starts, not catching up to it.
2. **Watchlist and load.** Asking the broker for every share's candles every five minutes is what overloaded Kotak. Now one quick quotes pass reads price and volume for all 200 shares, and only the watchlist gets candles and strategy scans: the indices, open trades, results and news shares, and any share on unusual volume (1.5 times normal for the time of day) or moving 1.5 percent or more. A share that is not moving on volume is not in play, and none of the strategies would take it. By estimate this cuts the broker requests by about three quarters (not yet measured live).
3. **Share strategies (three, not eleven).** Opening range breakout, opening drive and EMA trend pullback, run only on the watchlist. The choice and the evidence are in `docs/STRATEGY_RESEARCH.md`. Each signal is graded **worth taking** or **only watching** by four checks (costs at most 25% of the risk, volume at least twice normal, Nifty on the same side of VWAP, no opposing strategy on the same share), and a share that is on the watchlist carries the reason: *Results today*, *Volume 2.6x normal*, *In the news*.
4. **Calls and puts.** For Nifty, Bank Nifty, Sensex, Fin Nifty and Midcap Nifty, the tool reads the index candles it already has and scores a call or a put out of five: broke the opening range, above or below VWAP, 9 EMA against 21 EMA, up or down on the day, near the top or bottom of the day's range. At 4 or more, inside the entry window, with costs that fit, it names the contract (at the money, nearest suitable expiry), its premium, stop, target, and the risk and money needed per lot. Banks reporting results today add a warning. The older options rulebook still runs its own paper trades.

Everything is a **paper trade**. The tool never places an order.

**What reaches Telegram.** Only what you can act on:
- one **morning brief** at the open (results just out, due today, due next, index heavyweights reporting, shares in the news);
- a share **trade worth taking**, and how it ended;
- a **call or put idea** worth taking, and the options desk's entries and exits;
- one short **summary after the close**;
- a **fault** when prices stop arriving (the same fault at most once an hour).

Trend-setter notes, news digests and "started" or "restarted" messages are off. Change any of this under `alerts:` in `config.yaml`.

**What you see.** One page, decision first: *Results and watchlist*, *Worth taking*, *Calls and puts*, *My trades*, *Trend setters*, *Market*, and *More* (collapsed). It re-reads every 2 minutes. **Copy order details** copies quantity, entry, stop and target for pasting into your broker app; **I took this trade** is remembered on this device only.

**Starting clean.** The first run of this version replaces any log saved by an older version, so old trades and results disappear. To wipe everything by hand later (all trades, charts and backtests; your Telegram link is kept), cancel any running Trade engine, then run **Reset data** from the Actions tab and type `RESET`. It cannot be undone.

**Does the whole process have an edge?** Run **Research replay** from the Actions tab. It replays the watchlist, results, volume, strategies and costs on past prices using only what was known at the time, and shows what each piece adds, with ranges and a sealed holdout (leave `final` unticked until the rules are fixed). On the first run, nothing was positive after costs: see `docs/STRATEGY_RESEARCH.md`. **Daily research** (also in the Actions tab, no login needed) tests a daily-timeframe swing method on ten years of prices, where costs matter much less; run on every share that traded (including delisted ones) it shows at best a small, marginal effect that does not beat holding the index, so it is not a recommendation. The first run downloads about 2,500 small NSE files and takes 20 to 40 minutes.

**Honest results.** On 60 sessions of real prices the three strategies lost money after costs, even when only the shares graded "worth taking" are counted: about -0.11R a trade for the opening range breakout (644 trades), -0.06R for the opening drive (190) and +0.10R for the EMA pullback (77, which swung from +0.44R in the first half to -0.24R in the second, so it proves nothing). Nothing here is proven to make money. The research behind the choice is in `docs/STRATEGY_RESEARCH.md`; run the **Strategy backtest** on Kotak's longer history before trusting any of it.

**How it runs**
- The desk checks every 5-minute candle from 9:20 am to 3:20 pm in the same engine run. Each pass takes the watchlist first and publishes, so what matters appears quickly.
- The **Strategy backtest** workflow (Actions tab) tests the three strategies on past prices. It runs by itself every weekday at 6:00 pm, and on demand.
- Settings are under `results:`, `watch:`, `options_scan:`, `alerts:` and `strategies:` in `config.yaml`.

## Portfolio risk, backtesting and limitations

The strategy backtest's default fill is the next five-minute candle open. `signal_close` is available only as an optimistic comparison. The simulator assumes the stop is hit first when one candle touches both stop and target. Transaction costs and slippage are estimates; configure them conservatively.

The two engines now see one another's approved open positions when enforcing shared position-count, aggregate-risk and realised-daily-loss limits. Each engine also retains its own tighter sub-limits. This controller does not yet calculate portfolio covariance, sector exposure, options Greeks, dynamic broker margin or order-book liquidity.

The strategy desk uses historical underlying candles. Index instruments are modelled using spot index series as a proxy rather than date-specific futures contracts. The options backtest estimates option premiums using a pricing model; it is not a replay of historical option-chain quotes. Fyers MCX access and Telegram connectivity must be tested in your own account. The uploaded release has been verified offline, not against your Fyers credentials, live MCX market feed or a deployed 24/7 host. The Impact Radar is rules/AI-assisted scenario mapping, not a statistically calibrated price-probability model.

## Local preview and tests

To preview the new dashboard with sample data: `cd web`, `npm ci`, `npm run dev`, and open `http://localhost:5173/?demo`. The previous dashboard can still be previewed with `python -m http.server 8000`. Both previews use sample records only.

Run the whole offline suite with `python -m pytest` (and `cd web && npm test` for the dashboard). The individual suites can also be run one at a time, for example `python -m tests.test_archetypes`, `python -m tests.test_leaders`, `python -m tests.test_playbook`, `python -m tests.test_context`, `python -m tests.test_bhav`, `python -m tests.test_daily`, `python -m tests.test_replay`, `python -m tests.test_segments`, `python -m tests.test_kotak`, `python -m tests.test_strategies`, `python -m tests.test_backtest`, `python -m tests.test_store_publish` and `python -m tests.test_lifecycle`. The broader `python -m tests.test_engine` replay is slower because it exercises multiple market sessions and contract paths.

## Updating to a newer version

When you get a new `signal-desk.zip`:

1. Unzip it on your computer.
2. Open your repository on GitHub, click **Add file**, then **Upload files**.
3. Drag in **everything inside** the unzipped folder, including `.github`, and click **Commit changes**. The new files replace the old ones.

Your trade history and Telegram link are stored separately and are not touched. If you had changed `config.yaml` yourself, make those changes again after uploading.

Also delete the old file `engine/nifty100.txt` if it is still there (open it, click the bin icon, commit). It is no longer used.

## 4.1.1 hotfix for Kotak/Fyers workflow errors

For users upgrading from 4.1.0, follow [docs/upgrades/UPGRADE_4.1.1.md](docs/upgrades/UPGRADE_4.1.1.md). This patch corrects the exact Kotak Midcap index symbol, adds longer adaptive cooldowns for Kotak HTTP 429 responses, and records Fyers per-symbol quote errors so Market Data permission problems are not mistaken for failed OAuth login. It does not require deleting the `data` branch or resetting saved trades.
