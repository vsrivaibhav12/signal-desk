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
  - `strategies` shows how each of the eleven strategies is doing today
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
- The backtest covers indices and stocks. Crude oil, gold, silver and natural gas are left out, because expired commodity contracts have no price history to test on. Option prices are estimated, because brokers keep no history for expired options.

## The strategy desk (eleven strategies, one decision)

Besides the options rulebook, the tool runs **eleven intraday strategies at the same time**, all on the Nifty 200 shares and five indices (as index futures, one lot). Every trade is a **paper trade**: shares bought or sold intraday, sized so a stop-loss costs about ₹500, or one lot of an index future. Nothing is ordered.

| Strategy | Where it comes from |
|---|---|
| Opening drive | Built from a missed entry: gets in near the start of a thrust in the first 15 minutes |
| Momentum, Breakout, Reversal, VWAP pullback, Gap and go, Opening range breakout | The Flattrade article on common intraday strategies |
| Bollinger bounce, Bollinger squeeze breakout | Bollinger bands. The bounce only trades in a ranging market, the squeeze after a quiet period |
| EMA 9/21 crossover, EMA trend pullback | Moving averages. The crossover looks for a new trend, the pullback only trades an established strong one |

**The tool decides what is a candidate and what can enter the paper portfolio.** Every signal is graded either **TAKE** or **WATCH**, using four checks. Missing required data is marked unknown and fails closed to WATCH; it is never treated as a pass:

1. **Costs:** the round-trip cost is at most 25% of the amount risked. A stop too tight for the costs means the trade starts behind. This was by far the most important check.
2. **Volume:** the stock trades at 2x its usual volume or more for the time of day (indices pass).
3. **Nifty agrees:** Nifty is on the same side of its VWAP as the trade.
4. **No conflict:** no other strategy has already taken the opposite side of the same stock today.

Then the portfolio controller decides whether an otherwise eligible trade can fit the available risk budget:

- Strategy-desk sub-limit: five concurrent positions, one per underlying, ₹2,500 aggregate initial risk and ₹1,500 realised daily loss.
- Shared cross-engine limit: eight concurrent positions and ₹17,500 aggregate initial risk across both engines, with a ₹3,500 combined realised daily-loss stop.
- Rulebook sub-limit: five concurrent positions, ₹15,000 aggregate open risk and ₹3,000 realised daily loss.

A rejected candidate remains in research history with its rejection reason, but it is not announced as an approved paper trade. These are simulated admission limits, not broker margin checks or a guarantee against gaps.

Only risk-approved **TAKE** trades appear at the top of the dashboard and trigger Telegram alerts. Everything else remains available under **More**.

**TAKE is not a profitability claim.** In the research sample reviewed before this release, even the filtered signals did not demonstrate a reliable profitable edge. The backtest now reports standalone strategy candidates separately from the risk-approved portfolio. The chronological 60/20/20 split and stability blocks are descriptive diagnostics, not a parameter-optimised or untouched holdout. Do not change thresholds until you have defined a proper training, validation and untouched test process.

**What you see.** One page, decision first. A line under the title shows how fresh the data is, and the page re-reads it **every 2 minutes** (or press **Refresh now**).
- **Worth taking:** the trades that passed all four checks, each with entry, stop, target, risk, the four checks, **Copy order details** and **I took this trade**. Tap one for its chart, ladder, reasons and news.
- **My trades:** every trade you ticked, with its live result, in one place.
- **Trend setters:** stocks making a **genuine** big move, up or down: 2% or more, on at least twice the usual volume, on the right side of VWAP, and still holding near the day's high or low. Each shows what the tool did with it, and how much of the move had already happened when the first signal came. Spikes that faded, or moves on ordinary volume, are left out.
- **Market:** Nifty and Bank Nifty, and how many shares are trending.
- **More** (collapsed): the signals the tool is only watching and why, the strategy scoreboard, the stock watchlist and opening ranges, every trade, news, and the activity log. Tap a strategy in the scoreboard for its rules, live trades and backtest.

**The trade chart.** Opening a trade shows its 5-minute candles with the trigger candle highlighted, the entry, stop and target, any exit, and the levels that made it trigger: the opening range, the tight range before a breakout, the gap levels, VWAP, the moving averages or the Bollinger bands. Charts are kept for the last three days of live trades and the 100 most recent backtest trades of each strategy, and load only when you open a trade. Options desk charts cover indices and shares, not commodity options.

**Taking a trade yourself.** **Copy order details** copies quantity, entry, stop, target and risk, ready to paste when you place it in your broker app. **I took this trade** is remembered on this device only and powers the **My trades** section. The tool never places orders.

**About entry timing.** The strategy desk now models entries at the next five-minute candle open after the signal candle closes. A gap through the original stop invalidates the setup instead of widening the stop. This is still a simulation: spread, queue position, partial fills and market impact are approximated through configured costs. Most strategies wait for a completed-candle confirmation, which can mean entering after part of a move has already happened. The **Opening drive** strategy is intended to research early-session thrusts, but no strategy should be preferred from an older backtest snapshot. Re-run the backtest with this release's next-candle fill model and evaluate it on a genuinely untouched period before drawing conclusions.

**How it runs**
- The live desk checks every 5-minute candle from 9:20 am to 3:20 pm inside the same engine run, with the same price source. Each pass looks at the interesting stocks first (open trades, trend setters, indices) and publishes, then does the rest, so important signals appear sooner.
- **Telegram** sends only trades that are worth taking, plus a note when a new genuine trend setter appears (once per stock per day), plus a summary after the close. To also get every signal, set `alert_grade: all` under `strategies` in `config.yaml`. To limit alerts to some strategies, set `alert_for: [orb, drive]`. Send the bot the word `strategies` for today's tally.
- The **Strategy backtest** workflow (Actions tab) tests all eleven on past prices. It runs by itself every weekday at 6:00 pm, and on demand with **Run workflow**. With a saved Fyers login it uses Fyers prices (about 365 calendar days (roughly 250 market sessions) is a trading year); without one it uses free Yahoo prices (about 60 days). It runs on its own computer, so it works alongside the live engine.
- Settings are under `strategies:` in `config.yaml`.

## Portfolio risk, backtesting and limitations

The strategy backtest's default fill is the next five-minute candle open. `signal_close` is available only as an optimistic comparison. The simulator assumes the stop is hit first when one candle touches both stop and target. Transaction costs and slippage are estimates; configure them conservatively.

The two engines now see one another's approved open positions when enforcing shared position-count, aggregate-risk and realised-daily-loss limits. Each engine also retains its own tighter sub-limits. This controller does not yet calculate portfolio covariance, sector exposure, options Greeks, dynamic broker margin or order-book liquidity.

The strategy desk uses historical underlying candles. Index instruments are modelled using spot index series as a proxy rather than date-specific futures contracts. The options backtest estimates option premiums using a pricing model; it is not a replay of historical option-chain quotes. Fyers and Telegram connectivity must be tested in your own GitHub account. The uploaded release has been verified offline, not against your Fyers credentials or live market feed.

## Local preview and tests

To preview the sample dashboard, run `python -m http.server 8000` in the project folder and open `http://localhost:8000`. The preview uses sample records only.

Run the offline regression checks with `python -m tests.test_strategies`, `python -m tests.test_backtest`, `python -m tests.test_store_publish` and `python -m tests.test_lifecycle`. The broader `python -m tests.test_engine` replay is slower because it exercises multiple market sessions and contract paths.

## Updating to a newer version

When you get a new `signal-desk.zip`:

1. Unzip it on your computer.
2. Open your repository on GitHub, click **Add file**, then **Upload files**.
3. Drag in **everything inside** the unzipped folder, including `.github`, and click **Commit changes**. The new files replace the old ones.

Your trade history and Telegram link are stored separately and are not touched. If you had changed `config.yaml` yourself, make those changes again after uploading.

Also delete the old file `engine/nifty100.txt` if it is still there (open it, click the bin icon, commit). It is no longer used.
