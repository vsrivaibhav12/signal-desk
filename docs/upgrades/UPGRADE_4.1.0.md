# Upgrading Signal Desk to 4.1.0

## What changes

4.1.0 adds two persistent 24/7 services, dual-broker market-data routing and the Impact Radar dashboard. Kotak stays primary for NSE/BSE; Fyers is an optional secondary feed for MCX Crude Oil, Gold, Silver and Natural Gas. The news worker scans continuously; the trading supervisor remains alive continuously but only invokes market scans during configured exchange sessions. Macro and company headlines can map to conditional asset pathways. Fresh high-relevance equity impacts can promote a Nifty 200 share into the normal strategy watchlist, with an explanation. No news event becomes a trade by itself.

## Safe upgrade from 4.0.0

1. Back up the repository and, separately, download a copy of the `data` branch. This is your recovery point for trade logs, chart records, Telegram pairing and options quote tape.
2. Replace files on the existing repository's default branch with the 4.1.0 ZIP contents, including `.github`, `deploy`, `docs`, `engine`, `tests`, `config.yaml`, `index.html` and `index.template.html`. Do not delete/recreate the repository.
3. Preserve the existing `data` branch and GitHub Pages settings.
4. Preserve existing GitHub Actions secrets. Add `FYERS_APP_ID`, `FYERS_SECRET_KEY` and optionally `FYERS_PIN` to enable Fyers as the MCX secondary feed. Keep `KOTAK_CONSUMER_KEY` as the primary NSE/BSE credential. Keep `FYERS_SECRET_KEY` once added: the encrypted Telegram session file is re-keyed using the active broker secret and bot token.
5. Optionally add `OPENAI_API_KEY` to enable bounded AI-assisted explanations. Without it, rules-based intelligence still runs. API use is billed separately from a ChatGPT subscription.
6. Run **Kotak check**. Then start **Trade engine** during an MCX session. The engine should prompt for Fyers login in Telegram, unless its saved refresh token works. Complete the Fyers login and paste the copied code into Telegram. Each commodity is enabled independently only after its current Fyers futures contract returns a usable quote and recent valid five-minute history; one unavailable contract does not block the others. Kotak NSE/BSE keeps working independently.
7. Run **News intelligence fallback** once from Actions. Confirm that `intelligence.json` is published on the `data` branch and the dashboard's Impact Radar reports a recent scan.
8. For an always-on installation, deploy **both** `signal-desk-intelligence.service` and `signal-desk-trading.service` using `deploy/README.md`. The news daemon polls continuously; the trading supervisor starts market scans during configured sessions and remains alive between them. The GitHub workflows are recovery/fallback paths, not daemons.

## News and trading behaviour

- News polling continues every five minutes through weekends/holidays only when the persistent news daemon is deployed. Without it, GitHub Actions is a best-effort fallback.
- The trading supervisor process stays alive between market sessions when deployed. Price/strategy scans are intentionally limited to configured NSE/BSE/MCX trading sessions.
- The two services share one `_data` checkout; 4.1.0 serialises Git publishers and protects the other service's local state files. Do not run duplicate persistent services against the same data directory.
- The macro/corporate mapping is conditional scenario analysis, not a forecast with calibrated odds. Impact score ranks relevance; confidence refers to mapping confidence.
- Event-linked equities enter the strategy desk only when their mapping score/age passes configured thresholds and the symbol is in the current Nifty 200 universe. Normal market data, volume, liquidity, strategy and portfolio gates still apply.
- Trading scans run only during configured exchange sessions. Closed-market periods do not produce price signals.

## Rollback

Restore the backed-up 4.0.0 source files on the default branch and keep the same `data` branch. To pause the always-on deployment, stop `signal-desk-intelligence` and `signal-desk-trading` on the VPS (or their Windows scheduled tasks) and disable the fallback workflows. The 4.1.0 intelligence/supervisor files may remain on the data branch but are ignored by older dashboards.

## Verification limits

No live Kotak/Fyers login or 24/7 server deployment was available during this build. Run the broker preflight and confirm MCX candles, quotes, bid/ask depth and contract expiries in your own account before enabling commodity paper scans. The historical option chain still is not backfilled; new option quote captures are prospective only.
