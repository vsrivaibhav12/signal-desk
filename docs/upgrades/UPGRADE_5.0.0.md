# Upgrading to Signal Desk 5.0.0

5.0.0 changes what the engine publishes, adds a new dashboard, a swing desk and an encrypted trade journal. **Your existing trade history, Telegram link and `data` branch are kept.** Nothing here requires deleting the `data` branch or resetting saved trades.

## What to do, in order

1. **Upload the new files** to your repository as you did before (everything inside the folder, including `.github`). Keep your `config.yaml` edits; the only new setting is `portfolio.max_same_direction_index_positions` (see below).
2. **Nothing else is needed for the engine.** It starts publishing three new things next to the old files: `live.json` (what the new dashboard reads), `manifest.json` (an index with each file's age) and `ledger-YYYY-MM.jsonl` (every settled paper trade, kept for good). The old dashboard keeps working because the old files are still published.
3. **Switch on the new dashboard (optional, 2 minutes).** Until you do, GitHub Pages keeps serving the old dashboard from your repository root.
   1. Settings, **Pages**, **Source**: choose **GitHub Actions**.
   2. Settings, **Secrets and variables**, **Actions**, **Variables**: add a variable `PAGES_FROM_ACTIONS` with the value `true`.
   3. In the **Actions** tab run **Dashboard**. The new dashboard appears at the same address. The old one stays at `…/legacy/` until you no longer need it.
   4. To go back, set the Pages source to **Deploy from a branch** again.
4. **Switch on the journal (optional).** Add a repository secret `JOURNAL_PASSPHRASE` (and the same value in `/etc/signal-desk.env` on a VPS). Choose a long passphrase: the encrypted journal sits on the public `data` branch, so anyone can download it and try guesses offline. Alerts then carry **Took it** and **Skip** buttons. Without the secret the journal is simply off.
5. **Run the Leaders workflow once** (Actions tab). It now also looks for swing setups and writes `swing.json`; after that it runs by itself every weekday evening.

## What changed for you

| Area | Before | Now |
|---|---|---|
| Trade record | Wiped whenever the schema number changed; strategy trades older than ten days were reduced to totals | Every settled trade goes to `ledger-YYYY-MM.jsonl` and is never edited or removed. A schema change migrates the data, or archives settled trades first. |
| Dashboard | One 1.3 MB HTML file with sample data baked in | A small component app in `web/` (68 KB), types generated from the engine's contract, sample data at `?demo` |
| Stale data | Shown with a warning | Every number carries its age. When the engine stops publishing, TAKE badges are removed by the page itself. |
| Portfolio rules | The same arithmetic written in two places | One module (`engine/portfolio.py`) used by both engines |
| Index exposure | Five correlated index positions could be open together | At most **2 same-direction index positions** at a time, across both engines (`portfolio.max_same_direction_index_positions`; set it to `0` to turn this off) |
| One desk failing | An error in the rulebook engine skipped the strategy desk for that minute | Each desk runs on its own; a broker login error still stops the cycle |
| Costs | Defined separately by the simulator, scanner and backtest | One table (`engine/costs.py`); the ledger estimates costs for every desk |
| Swing | Research only | A nightly desk that applies the one swing rule taken to the sealed holdout, unchanged, and follows a paper book |
| Journal | None | Telegram buttons, an encrypted file, and a page that decrypts it in your browser |

## Things to know

- **The journal passphrase is not recoverable.** If you lose it the file cannot be read; remove `journal.bin` from the `data` branch and start again.
- **Costs in the ledger are estimates**, and are labelled as such (`cost_estimated`). The options desk's own P&L (`feed.json`) is still before costs; the ledger's `net` is after the estimate.
- **The swing desk's paper trades are measured per share, in R**, with the research's 0.40 percent cost. They are not sized.
- **The swing rule is unproven.** The sealed holdout gave +0.08R over 127 trades (range −0.26 to +0.44). The desk adds a Nifty-above-its-200-day filter that the tested rule did not have; that only removes trades.
- **Ideas the page shows as EXPIRED** were TAKE when published. The page can lower a grade (stale data, an old signal) but never raise one.
- The browser cannot reach Kotak or Fyers, so the dashboard is as fresh as the engine's last publish (about every two minutes while a session runs). It says how old every number is.

## If something looks wrong

- **The new dashboard shows "No data":** open the **System** screen. It lists each published file and its age, and why any file could not be read.
- **`manifest.json` or `live.json` are missing on the `data` branch:** the engine has not published since the upgrade. Start the **Trade engine** workflow, or wait for the next session.
- **The journal says "wrong passphrase":** it must match `JOURNAL_PASSPHRASE` exactly.
