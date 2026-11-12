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

## Upgrading an existing installation to 4.0.0

1. Download and unzip the 4.0.0 release. Replace the source files on your repository's default branch with the contents of the new release, including the `.github` folder. Do not delete the repository or recreate it.
2. Keep your existing GitHub Actions secrets and variables. They are stored in repository settings and are not part of the source ZIP.
3. Keep the existing `data` branch. It contains the Telegram pairing file and persisted trading/research records. The new release adds `options_tape.jsonl` to that branch when complete live option quote observations become available.
4. Keep GitHub Pages pointed at the same default branch and root folder. The new `index.html`/dashboard template ships with the release.
5. Run **Kotak check** from Actions before enabling the Trade engine. This build was tested offline; it was not authenticated against your broker account from the build environment.
6. Open the dashboard and check the **Data health** panel. The first screen shows market posture, data confidence, candidates and open risk. Use the watchlist and options filters, watchlist search/sort, trade detail sheet and trade-log filters when needed. Auto/Dark/Light theme and Compact mode are available in the header.

The 4.0.0 dashboard preview generated in this release uses clearly labelled synthetic sample records. A live GitHub Pages dashboard reads the `data` branch after the first successful workflow run.

---

## Part 3: Switch on your dashboard (2 minutes)

1. In your repository click **Settings**, then **Pages** in the left menu.
2. Under **Source** choose **Deploy from a branch**.
3. Under **Branch** choose **main** and **/ (root)**, then click **Save**.
4. Your dashboard address is:

   `https://YOUR-USERNAME.github.io/signal-desk/`

   Replace YOUR-USERNAME with your GitHub username in lowercase. Keep the `/` at the end. Save this address; you need it in Part 4. It can take two minutes to come alive, and it shows "Nothing to show yet" until the first run. If you use a custom domain or a user-site root URL, set `DATA_REPOSITORY` near the top of `index.html` to `YOUR-USERNAME/signal-desk` so the dashboard can locate the `data` branch.

---

## Part 4: Let the tool read prices from Kotak Neo (3 minutes)

The tool reads prices from **Kotak Neo**. It only reads. It never places an order, so it needs no static IP, no TOTP, no MPIN and no daily login. All it needs is one **Consumer Key**.

1. Open the **Kotak Neo app** (or neo.kotaksecurities.com) and log in.
2. Go to **More**, then the **Trade API** card.
3. Tap **Generate application** (or open your existing application) and **copy the token**. This is your Consumer Key.
4. Keep it private. Anyone with it can use your Trade API application. You will paste it into GitHub in the next part, and nowhere else.

That is all. Do not type your Kotak password, MPIN or TOTP anywhere in this tool.

> **Prefer Fyers?** Set `broker: primary: fyers` in `config.yaml`, create a Fyers data-only app (fyers.in/web/api-dashboard/user-apps, **Create App**, Market Data permission, Redirect URL = your dashboard address) and add the `FYERS_APP_ID` and `FYERS_SECRET_KEY` secrets instead. Fyers needs a login through Telegram each day, and it is the only one of the two that provides commodity (MCX) price history.

---

## Part 5: Store your keys in GitHub's locked area (5 minutes)

1. In your repository: **Settings**, then **Secrets and variables**, then **Actions**.
2. Click **New repository secret** and add these one at a time. The names must be typed exactly.

| Name | What to paste |
|---|---|
| `KOTAK_CONSUMER_KEY` | The Consumer Key from Part 4 |
| `TELEGRAM_BOT_TOKEN` | The token from Part 1 |
| `TELEGRAM_PAIRING_CODE` | A long random one-time pairing secret (required for the first bot connection unless you provide `TELEGRAM_CHAT_ID`) |
| `TELEGRAM_CHAT_ID` | Optional numeric chat ID; use this instead of pairing if you already know the bot chat ID |

(Only if you chose Fyers: `FYERS_APP_ID`, `FYERS_SECRET_KEY` and optionally `FYERS_PIN`.)

**Switching from Fyers to Kotak Neo:** keep your `FYERS_SECRET_KEY` secret in place for now. It is also the key that locks the tool's private file, so removing it would make the tool forget your Telegram link (you would pair again). Everything else about Fyers can be deleted.

**About Telegram pairing:** create a long random value (at least 32 characters) and save it as the `TELEGRAM_PAIRING_CODE` secret. Do not paste it into the repository or share it. On first run, message your bot `/start YOUR_PAIRING_CODE` (replace the words with the secret). The tool accepts only that exact command to bind the chat. As an alternative, you can set `TELEGRAM_CHAT_ID` to your numeric Telegram chat ID and skip pairing. Once paired, the bot stores the chat ID in its encrypted credential file. That file is locked with a key made from your secrets, so if you ever regenerate your Kotak Consumer Key (and have no `FYERS_SECRET_KEY` secret), set `TELEGRAM_CHAT_ID` first, or you will need to pair again.

---

## Part 5b: Turn on the leader list (2 minutes, optional)

In the Actions tab choose **Leaders** and press **Run workflow**. It downloads NSE's daily price files (a few minutes the first time), rates every liquid share's relative strength and market health, and publishes the result. It then runs by itself every weekday evening. The Trade engine flags relative-strength leaders on the watchlist, on trade cards, in alerts and in the morning brief. It needs no login and makes no broker requests.

---

## Part 6: Check the connection, then first run (5 minutes)

1. Click the **Actions** tab, click **Kotak check** on the left, then **Run workflow**. After about two minutes open the run and read the last lines. Each line says PASS or FAIL. This tests your own account: the Consumer Key, the share and index names, prices, 5-minute history, futures and an option. It sends nothing and places no order. If any line says FAIL, copy the lines and send them to me.
2. Click **Trade engine** on the left, then **Run workflow**, then the green **Run workflow** button.
3. Within a minute your Telegram bot messages you:
   - First it waits for the secure Telegram pairing command. Send `/start YOUR_PAIRING_CODE` to your bot.
   - Then it sends "Connected". There is no price login to do.
4. Setup is finished. If Kotak does not accept the key, the bot says so in plain words.

From now on it starts by itself on weekdays, before the morning markets and again before the evening ones. GitHub knocks every 10 minutes during market hours: if the tool is awake the knock does nothing, and if it has stopped the knock restarts it from where it left off.

---

## Daily use

- **You do nothing to start it.** Alerts arrive in Telegram. The dashboard keeps the full record.
- **There is no daily login with Kotak Neo.** If you chose Fyers instead, the bot asks for a Fyers login from time to time and tells you how.
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
- The backtest covers indices and stocks. Crude oil, gold, silver and natural gas are left out, because expired commodity contracts have no price history to test on. Option prices are estimated, because brokers keep no history for expired options. 4.0.0 starts a prospective bid/ask/depth/OI tape; it does not backfill expired option chains.

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

The strategy desk uses historical underlying candles. Index instruments are modelled using spot index series as a proxy rather than date-specific futures contracts. The options backtest estimates option premiums using a pricing model; it is not a replay of historical option-chain quotes. Fyers and Telegram connectivity must be tested in your own GitHub account. The uploaded release has been verified offline, not against your Fyers credentials or live market feed.

## Local preview and tests

To preview the sample dashboard, run `python -m http.server 8000` in the project folder and open `http://localhost:8000`. The preview uses sample records only.

Run the offline regression checks with `python -m tests.test_archetypes`, `python -m tests.test_leaders`, `python -m tests.test_playbook`, `python -m tests.test_context`, `python -m tests.test_bhav`, `python -m tests.test_daily`, `python -m tests.test_replay`, `python -m tests.test_segments`, `python -m tests.test_kotak`, `python -m tests.test_strategies`, `python -m tests.test_backtest`, `python -m tests.test_store_publish` and `python -m tests.test_lifecycle`. The broader `python -m tests.test_engine` replay is slower because it exercises multiple market sessions and contract paths.

## Updating to a newer version

When you get a new `signal-desk.zip`:

1. Unzip it on your computer.
2. Open your repository on GitHub, click **Add file**, then **Upload files**.
3. Drag in **everything inside** the unzipped folder, including `.github`, and click **Commit changes**. The new files replace the old ones.

Your trade history and Telegram link are stored separately and are not touched. If you had changed `config.yaml` yourself, make those changes again after uploading.

Also delete the old file `engine/nifty100.txt` if it is still there (open it, click the bin icon, commit). It is no longer used.
