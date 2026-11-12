# Upgrade to Signal Desk 4.1.1

This is a source hotfix for the 4.1.0 release. It does not intentionally wipe trading history or change your Telegram pairing.

## Before replacing source files

1. Stop or disable any running `engine.supervisor`, `engine.intelligence`, or GitHub Actions Trade engine run while updating.
2. Keep a copy of your current repository and the `data` branch. Do not delete the `data` branch.
3. Keep all existing repository secrets and variables, including Kotak, Fyers, Telegram and GitHub publishing credentials.
4. Extract 4.1.1 and replace the source files on the repository's default branch. Do not upload `.env`, tokens, `secure.bin`, local `_data`, or private credentials to the source branch.

## Fyers MCX access

The workflow log you shared shows that Fyers OAuth login was accepted, but the four MCX quote preflights did not return usable quotes. The current MCX mini-futures symbols were found in the public Fyers contract master for the session date, so first check the Fyers API app's **Market Data** permission and your account's MCX data access.

Fyers' Market Data permission is required for Quotes, Market Depth and Historical Data. If your current app was created with a Basic-only permission template and cannot be upgraded, create a new Fyers API app with the Market Data permission, then update these GitHub repository secrets:

- `FYERS_APP_ID`
- `FYERS_SECRET_KEY`
- `FYERS_PIN`

Run the Trade engine workflow and complete the fresh Fyers login. The engine checks each configured MCX future independently; each market must return both a usable quote and valid recent five-minute candles.

A successful Fyers login does not by itself prove MCX market-data permission. The new logs retain the HTTP/per-symbol provider error so that permission, entitlement, stale token and contract errors can be distinguished.

## Kotak Midcap and rate limits

The default Kotak symbol for Midcap Nifty is now the exact `NIFTY MID SELECT` name. If you have an explicit `broker.kotak.index_tokens` override in `config.yaml`, ensure the override uses `nse_cm|NIFTY MID SELECT`.

Kotak history requests now start more slowly and use escalating cooldowns after 429 responses. This can increase scan duration after rate limiting; do not reduce the configured cooldown to force more requests. If 429 responses continue, reduce historical requests further or contact Kotak about the API rate limit applicable to your account.

## Verify after deployment

1. Run **Kotak check**. Confirm NIFTY, Bank Nifty, Fin Nifty, Midcap Nifty and Sensex checks resolve correctly where enabled.
2. Run **Trade engine**. Confirm Kotak remains connected for NSE/BSE.
3. Confirm the Fyers diagnostic names the exact failed check. If it reports HTTP 403 or a per-symbol permission message, resolve Fyers Market Data/MCX permissions; do not keep re-authenticating the same Basic-only app.
4. Confirm `CRUDE`, `GOLD`, `SILVER` and `NATGAS` are enabled only if their individual quote and candle preflights pass.
5. Inspect the dashboard's broker/data-health status and verify that the `data` branch continues to receive updates.

## What this update does not do

- It does not place broker orders; the engine remains read-only and paper-trading only.
- It does not bypass Kotak or Fyers rate limits or account permissions.
- It does not guarantee that all MCX contracts are available for every Fyers account.
- It does not reset old paper trades or Telegram pairing.
