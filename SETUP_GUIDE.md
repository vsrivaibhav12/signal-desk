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
6. Tap the link to your new bot in that same message and tap **Start**. (This is how the tool knows where to send your alerts.)

---

## Part 2: Put the tool on GitHub (10 minutes)

1. Go to **github.com** and create a free account. Choose a simple username in lowercase.
2. Click the **+** at the top right, then **New repository**.
3. Repository name: `signal-desk`
4. Choose **Public**. (Public is what makes it free. Your passwords are not stored in the files; they go in a locked area in Part 5.)
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

   Replace YOUR-USERNAME with your GitHub username in lowercase. Keep the `/` at the end. Save this address; you need it in Part 4. It can take two minutes to come alive, and it shows "Nothing to show yet" until the first run.

---

## Part 4: Let the tool read prices from Fyers (5 minutes)

Fyers asks for a "static IP" only on its **trading app**, which is the one that places orders. This tool never places orders, so it needs a different kind of app: a **data-only app**. Fyers lets you create as many of those as you like, and they need no static IP.

1. Log in at **fyers.in/web/api-dashboard/user-apps** (or myapi.fyers.in, which redirects there).
2. Do **not** click Activate on the "Algo trading app" shown at the top. That is the trading app and it is what asks for the IP.
3. Click **Create App** instead.
4. Fill in:
   - **App name:** `signaldesk`
   - **Redirect URL:** your dashboard address from Part 3, exactly, including `https://` and the final `/`
   - **Permissions:** choose **Market Data**. If the form offers no choice, leave it at its default.
   - **Webhook:** leave empty
5. Create the app. Fyers shows an **App ID** and a **Secret ID**. Copy both.

If the Create App form also asks for a static IP, stop and tell me exactly what the screen says. Do not type in an IP.

---

## Part 5: Store your keys in GitHub's locked area (5 minutes)

1. In your repository: **Settings**, then **Secrets and variables**, then **Actions**.
2. Click **New repository secret** and add these one at a time. The names must be typed exactly.

| Name | What to paste |
|---|---|
| `FYERS_APP_ID` | The App ID from Part 4 |
| `FYERS_SECRET_KEY` | The Secret ID from Part 4 |
| `TELEGRAM_BOT_TOKEN` | The token from Part 1 |
| `FYERS_PIN` | Your 4-digit Fyers PIN (optional, see below) |

**About the PIN:** with it, the tool logs in to Fyers by itself each morning and asks you to log in only about once every two weeks. Without it, the bot asks you to log in every morning. Once saved, nobody can read a secret back, not even you.

---

## Part 6: First run (3 minutes)

1. Click the **Actions** tab, click **Trade engine** on the left, then **Run workflow**, then the green **Run workflow** button.
2. Within a minute your Telegram bot messages you:
   - First "Connected".
   - Then "Fyers login needed" with a link. Tap it and log in to Fyers.
   - You land on your dashboard with a **Copy login code** button. Tap it.
   - Go back to Telegram, paste the code as a message to the bot, and send.
3. The bot replies "Logged in to Fyers". Setup is finished.

From now on it starts by itself on weekdays, before the morning markets and again before the evening ones. GitHub knocks every 10 minutes during market hours: if the tool is awake the knock does nothing, and if it has stopped the knock restarts it from where it left off.

---

## Daily use

- **You do nothing to start it.** Alerts arrive in Telegram. The dashboard keeps the full record.
- **About every two weeks** the bot asks for a fresh Fyers login (every morning if you skipped the PIN). Same three taps as in Part 6.
- **Words you can send the bot:**
  - `status` shows what it is watching right now
  - `strategies` shows how each of the ten strategies is doing today
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
- **Bot says Fyers did not accept the code:** codes expire in a few minutes. Tap the login link again and paste the new code straight away.
- **Fyers shows an error after login about the redirect:** the Redirect URL in the Fyers app is not exactly your dashboard address. Edit the app on myapi.fyers.in and fix it.
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

**Longer version (uses your Fyers account):**
1. Run it the same way, but change the first choice to **fyers** and set **days** to `180` (or more).
2. If your Fyers login is still valid it just runs. If not, the bot sends you the usual login link: tap it, log in, tap Copy login code, and paste the code to the bot.
3. It takes 30 to 90 minutes. Run it after the market closes, so it does not compete with the live tool for your Telegram messages.
4. Fyers decides how far back its one-minute prices go. The backtest uses whatever Fyers returns and says on the dashboard which dates it covered.

**How to read the result**
- **Net result after costs** is the number that matters. Costs are estimates (brokerage, taxes and the gap between the price you see and the price you get), and they are listed under "What this test assumes".
- **Win rate "could be X% to Y%"** shows how uncertain the win rate is with that many trades. Fewer trades means a wider range.
- **Luck check** reruns the same rules on shuffled prices, which have no real pattern. If the real result is not clearly better than that, there is no sign of a real edge yet.
- **Does changing the settings help?** compares the current settings with no early exit, and with the old daily limits.
- The backtest covers indices and stocks. Crude oil, gold, silver and natural gas are left out, because expired commodity contracts have no price history to test on. Option prices are estimated, because Fyers has no history for expired options.

## The strategy desk (ten strategies side by side)

Besides the options rulebook, the tool now runs **ten intraday strategies at the same time**, each with its own tab on the dashboard:

| Strategy | Where it comes from |
|---|---|
| Momentum, Breakout, Reversal, VWAP pullback, Gap and go, Opening range breakout | The Flattrade article on common intraday strategies |
| Bollinger bounce, Bollinger squeeze breakout | Bollinger bands. The bounce only trades when the market is ranging, the squeeze only after a quiet period |
| EMA 9/21 crossover, EMA trend pullback | Moving averages. The crossover looks for a new trend, the pullback only trades an established strong one |

All ten look at the same Nifty 200 shares and five indices (as index futures, one lot). Every trade is a **paper trade**: shares bought or sold intraday, with the quantity set so that a stop-loss costs about ₹500, or one lot of an index future. Nothing is ordered. Costs are deducted so the strategies can be compared fairly.

**What you see**
- **All strategies** tab: a scoreboard with every strategy's backtest result before and after costs, a plain-language reading of it, and everything open right now.
- One tab per strategy: the rules in plain words, when it should work and what can go wrong, the trades it is triggering today with the stop, target and reasons for each, its **live record**, and its **backtest** (win rate, profit before and after costs, first half against second half of the period, by market, by how trades ended, by month).
- A strategy is only called **Promising** if it is profitable after costs in both halves of the test period and its result is too large to be luck. Otherwise it is **Unproven** or **No edge so far**. Treat the live record as the fair test: nothing in it was known in advance.

**How it runs**
- The live desk checks every 5-minute candle from 9:20 am to 3:20 pm inside the same engine run, with the same Fyers login. It works each day out afresh from the day's candles, so a restart loses nothing.
- Telegram: alerts are on for all ten strategies. Whenever any of them triggers, you get one grouped message (the count per strategy, then up to ten example trades with entry, stop and target) when a 5-minute candle closes, followed by a summary after the close. The strategies can trigger hundreds of trades a day, so to quieten it set `alert_for: [orb, vwap]` (any list of strategy names) or `telegram: summary` under `strategies` in `config.yaml`. Send the bot the word `strategies` for today's tally at any time.
- The **Strategy backtest** workflow (Actions tab) tests all ten on past prices. It runs by itself every weekday at 6:00 pm, and on demand with **Run workflow**. With a saved Fyers login it uses Fyers prices (up to the days you ask for, about 250 is a trading year). Without one it uses free Yahoo prices (about 60 days). It runs on its own computer, so it works alongside the live engine.
- Settings are under `strategies:` in `config.yaml`.

## Updating to a newer version

When you get a new `signal-desk.zip`:

1. Unzip it on your computer.
2. Open your repository on GitHub, click **Add file**, then **Upload files**.
3. Drag in **everything inside** the unzipped folder, including `.github`, and click **Commit changes**. The new files replace the old ones.

Your trade history, Telegram link and Fyers login are stored separately and are not touched. If you had changed `config.yaml` yourself, make those changes again after uploading.

Also delete the old file `engine/nifty100.txt` if it is still there (open it, click the bin icon, commit). It is no longer used.
