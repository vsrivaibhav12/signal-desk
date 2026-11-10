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

1. Go to **myapi.fyers.in** and log in with your Fyers account.
2. Open the **Dashboard** and click **Create App**.
3. Fill in:
   - **App name:** `signaldesk`
   - **Redirect URL:** your dashboard address from Part 3, exactly, including `https://` and the final `/`
   - **Description:** anything
   - **Permissions:** the tool only reads prices. If you can leave order placement unticked, leave it unticked.
4. Create the app. Fyers shows an **App ID** (looks like `AB12CD34EF-100`) and a **Secret ID**. Copy both.

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

From now on it starts by itself at 8:45 am and 5:40 pm on weekdays.

---

## Daily use

- **You do nothing to start it.** Alerts arrive in Telegram. The dashboard keeps the full record.
- **About every two weeks** the bot asks for a fresh Fyers login (every morning if you skipped the PIN). Same three taps as in Part 6.
- **Words you can send the bot:**
  - `status` shows what it is watching right now
  - `skip` stops new trades for today (use it on RBI policy, Budget or other event days)
  - `resume` undoes skip

## What an alert tells you

Every trade alert has the contract to buy, the entry price, the stop-loss, the target, the exit time, the planned risk in rupees, and the list of reasons it was triggered. Later messages tell you when the target is reached, when to move the stop, and when to exit, with the result.

The tool **never places orders**. You decide whether to take a trade in your broker app. The tool keeps score of its own suggestions as paper trades, whether or not you took them.

## Changing settings

Open `config.yaml` in your repository, click the pencil icon, change a value, and click **Commit changes**. Every line has a note explaining it. Common changes:

- Turn a market off: set its `enabled` to `false`
- Trade two lots: set `lots: 2` (half is booked at the target, the rest is trailed)
- Add no-trade dates: fill in `skip_dates`
- Use the big crude contract: change `CRUDEOILM` to `CRUDEOIL` and `multiplier` to `100`

## If something looks wrong

- **No morning message from the bot:** GitHub's timer sometimes starts late, usually by 5 to 20 minutes. If nothing arrives by 9:15, start it by hand as in Part 6, step 1.
- **Bot says Fyers did not accept the code:** codes expire in a few minutes. Tap the login link again and paste the new code straight away.
- **Fyers shows an error after login about the redirect:** the Redirect URL in the Fyers app is not exactly your dashboard address. Edit the app on myapi.fyers.in and fix it.
- **A red cross on a run in the Actions tab:** click the run, then **run**, and read the last lines. They say in plain words what is missing.
- **GitHub emails that the scheduled workflow was disabled:** open the Actions tab and click **Enable workflow**.
- **Nifty 100 list changed:** edit `engine/nifty100.txt` (NSE revises the index twice a year).
