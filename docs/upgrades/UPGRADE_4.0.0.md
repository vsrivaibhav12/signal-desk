# Upgrading Signal Desk to 4.0.0

## What changes

4.0.0 is a full dashboard refresh plus a data-integrity hardening release. The first screen is designed around four trader questions: what is the market doing, can I trust the inputs, what candidates passed, and what risk is already open? Filters, search, sorting, detail sheets, theme controls, compact mode and exportable logs make the deeper information available without crowding the decision screen.

The engine distinguishes broker exchange timestamps from local response timestamps; rejects malformed and materially future-dated timestamps; validates quote/candle numeric integrity; enforces stock and index candle freshness; and requires complete option quote/depth/OI data before an option can receive TAKE. Unknown mandatory data remains WATCH.

The release also begins prospective collection of live option quote observations in `options_tape.jsonl`. It is included in the data-branch persistence and Reset data workflow. Capture failures are reported but must not interrupt the strategy cycle.

## Safe upgrade steps

1. Back up the repository and download a copy of the existing `data` branch if you want an independent archive of old paper trades and research outputs.
2. Replace the files on the repository's default branch with the contents of the 4.0.0 ZIP. Include `.github/workflows` and the dashboard files.
3. Do not delete and recreate the GitHub repository. Doing so can remove Actions secrets, Pages configuration and the existing `data` branch.
4. Preserve repository secrets (`KOTAK_CONSUMER_KEY`, Telegram credentials and any Fyers credentials) and variables. They are not included in this ZIP.
5. Preserve the existing `data` branch. The app will publish `options_tape.jsonl` there when complete live option quotes are observed.
6. Run **Kotak check** from GitHub Actions and review every PASS/FAIL line. The release was tested offline; no authenticated live broker session was available during packaging.
7. Run the engine in paper mode and review Data health before interpreting any signal. Check quote coverage, stale/invalid candle warnings and option quote-capture status.

## What is not upgraded into existence

- There is no historical option-chain archive in the ZIP. The options tape is prospective only; old options tests still use an underlying/premium proxy.
- The intraday replay still applies today's Nifty 200 membership to historical days. Daily bhavcopy research has a separate survivor-free point-in-time liquid-share universe.
- Historical news replay and point-in-time sector rotation are not implemented.
- Portfolio covariance, full sector concentration, options Greeks and dynamic margin modelling remain future enhancements.
- Signal Desk is read-only plus paper-trading. It does not place orders.
