# Signal Desk always-on deployment (4.1.0)

For a genuinely continuously running installation, deploy **two services** on one always-on Linux VPS or office PC:

1. **Trading supervisor** (`engine.supervisor`) stays alive 24/7, starts the existing Kotak/Fyers trading session during configured NSE/BSE/MCX windows, and sleeps between sessions. It does not poll closed markets or invent prices when an exchange is shut.
2. **News intelligence** (`engine.intelligence`) polls news every five minutes, including evenings, weekends and exchange holidays, and keeps the Impact Radar updated independently of broker sessions.

The GitHub Actions workflows remain useful as recovery/manual triggers and a best-effort news fallback. They are not the primary always-on runtime. On a VPS, do not also run a second persistent trading supervisor or news daemon against the same `_data` checkout.

## Recommended Linux VPS installation

1. Create a dedicated `signaldesk` Linux user and install Python 3.11+ and Git.
2. Clone the Signal Desk source repository to `/opt/signal-desk` as that user.
3. Create a venv and install dependencies:

   ```bash
   cd /opt/signal-desk
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

4. Clone the repository's `data` branch into `_data` (or initialise it if the branch does not exist). Keep its remote URL credential-free:

   ```bash
   git clone --branch data --single-branch https://github.com/OWNER/REPOSITORY.git _data
   ```

   If this is a first install and no `data` branch exists, create `_data`, initialise Git on branch `data`, set the credential-free HTTPS `origin`, and make an initial commit. The first service publish can then push the initial data files.

5. Copy `deploy/signal-desk.env.example` to `/etc/signal-desk.env`. Set `GITHUB_TOKEN` (fine-grained token with Contents read/write for this repository) and `GITHUB_REPOSITORY=OWNER/REPOSITORY`. Add `KOTAK_CONSUMER_KEY`, `FYERS_APP_ID`, `FYERS_SECRET_KEY`, `FYERS_PIN` as applicable, plus the existing `TELEGRAM_BOT_TOKEN` and paired `TELEGRAM_CHAT_ID` (or pairing code). `OPENAI_API_KEY` is optional. Protect the file:

   ```bash
   sudo chown root:root /etc/signal-desk.env
   sudo chmod 600 /etc/signal-desk.env
   ```

6. Ensure the service account owns the checkout and `_data`:

   ```bash
   sudo chown -R signaldesk:signaldesk /opt/signal-desk
   ```

7. Install and enable both services:

   ```bash
   sudo cp deploy/signal-desk-intelligence.service /etc/systemd/system/
   sudo cp deploy/signal-desk-trading.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now signal-desk-intelligence signal-desk-trading
   sudo systemctl status signal-desk-intelligence signal-desk-trading
   ```

   Follow logs:

   ```bash
   journalctl -u signal-desk-intelligence -f
   journalctl -u signal-desk-trading -f
   ```

The services share `_data` but own different public files. `Store.publish` now serialises same-host publishers and protects the other service's locally owned files while refreshing the remote branch. Runtime lock/temporary files are excluded from the public data branch. The GitHub token is injected into process-local Git configuration; it is never written into the origin URL.

## Windows / always-on office PC

1. Copy `deploy/signal-desk.env.example` to `deploy/signal-desk.env` and fill in the same GitHub, Kotak, Fyers, Telegram and optional OpenAI values. Keep `signal-desk.env` private; it is excluded from Git.
2. Install both Task Scheduler tasks under a dedicated Windows user. Set **Run whether user is logged on or not**, **Run with highest privileges** only if actually needed, and configure **Restart on failure**. Do not configure a stop-after duration.
3. Use `powershell.exe` as the program. For the News task, use these arguments (replace the path):

   ```text
   -NoProfile -ExecutionPolicy Bypass -File "C:\SignalDesk\deploy\start-worker.ps1" -Service news
   ```

   For the Trading task:

   ```text
   -NoProfile -ExecutionPolicy Bypass -File "C:\SignalDesk\deploy\start-worker.ps1" -Service trading
   ```

   Set **Start in** to the project root, e.g. `C:\SignalDesk`. The wrapper loads the local env file into the process, sets the shared-data flags and starts the correct daemon. Both tasks use the same project `_data` directory. Do not run duplicate copies against it.

## Local verification

```bash
python -m engine.intelligence --once
python -m engine.intelligence --daemon
python -m engine.supervisor --once
python -m engine.supervisor --daemon
```

`--once` is suitable for local checks. `--daemon` is the persistent service. Process locks prevent duplicate local copies of each worker.

## GitHub Actions fallback

- **Trade engine** continues to provide a scheduled/manual recovery path. The persistent supervisor starts the same `Session` logic locally and suppresses the GitHub workflow hand-over when `SIGNAL_DESK_SUPERVISOR=1`.
- **News intelligence fallback** is a best-effort backup. It skips when the continuous news worker has a fresh heartbeat. GitHub schedules can be delayed or dropped; they are not a daemon.
- If the VPS is the primary runtime, leave the Actions workflows enabled as a fallback, but don't manually launch a second persistent daemon on another machine against the same data branch.

## API, source and risk notes

- Without `OPENAI_API_KEY`, deterministic rules run locally. With a key, only a bounded number of new headlines per scan receive optional structured AI interpretation. API charges are separate from a ChatGPT subscription.
- Google News RSS is a discovery source, not a licensed institutional news feed. Confirm important events against primary sources.
- Impact score measures headline relevance, not a statistical probability of a price move. Mapping confidence is not probability. News events never create trades. Affected equities enter the normal strategy watchlist only after relevance/age checks and must still pass current price, volume, liquidity, strategy and risk gates.
- Kotak remains primary for NSE/BSE. Fyers is the secondary MCX source. Each configured MCX market is enabled independently only after its Fyers futures contract passes quote and recent 5-minute history preflight. A successful Kotak check does not validate Fyers MCX history or options.
- The services are paper-trading/read-only market data. No orders are placed. Keep the host clock synchronised with NTP. Historical option-chain prices are not backfilled; option quote collection is prospective.
