# News Impact Intelligence

Signal Desk's news service is separate from the exchange-session trading scanner. It polls configured RSS search groups every five minutes, 24 hours a day, and publishes the event ledger to `_data/intelligence.json` for the dashboard.

## What an event means

The service deduplicates headlines, records publication time and source evidence, assigns a relevance score, classifies the catalyst, and maps possible transmission paths to an allow-listed catalogue of Indian instruments. Examples include:

- Conflict near major oil routes → conditional crude, gold and natural-gas scenarios; possible second-order effects for upstream producers, refiners, airlines and shipping.
- Central-bank/rate changes → conditional gold, index, bank and exporter paths.
- China demand/stimulus → conditional metals and related Indian producers.
- Named-company order wins, approvals, earnings or regulatory probes → a company-specific watchlist reason.

`impact_score` is a relevance-ranking score, **not** the probability that price will move. `confidence` is the confidence in the event-to-instrument mapping, not a calibrated forecast probability. Direction can be `mixed` because higher commodity prices, for example, can benefit one company and hurt another. Every event remains `CONTEXT_ONLY` until price and volume evidence is checked.

High-relevance, recent, named-company equity impacts can promote a share into the ordinary Nifty 200 strategy watchlist. They cannot generate a trade. The stock still must pass the existing candle freshness, relative-volume, market context, liquidity, strategy, cost and risk checks. Commodity/index impacts appear in the Impact Radar and wait for their own instrument scanners.

## Always-on installation

For the continuously running deployment install both services described in [`../deploy/README.md`](../deploy/README.md):

- `signal-desk-intelligence.service`: `python -m engine.intelligence --daemon`
- `signal-desk-trading.service`: `python -m engine.supervisor --daemon`

The trading supervisor stays alive around the clock but invokes the existing trading session only during configured exchange sessions. The news worker keeps scanning outside those sessions. This avoids the false idea that an exchange has live tradable candles at midnight or on an exchange holiday.

The GitHub Action `News intelligence fallback` is a best-effort backup only. GitHub scheduled workflows have a five-minute minimum schedule interval and may be delayed or dropped under load. They are not a 24/7 persistent daemon.

## Configuration

`config.yaml` → `news_intelligence` controls:

- `enabled`: turn the Impact Radar on/off.
- `poll_seconds`: default 300 seconds (five minutes).
- `queries`: RSS topic searches.
- `retention_days` / `max_events`: event history limits.
- `watchlist_min_impact_score` / `watchlist_max_age_hours`: equity-catalyst promotion limits.
- `telegram_min_impact_score`: Telegram alert threshold.
- `ai.enabled`, `ai.model`, `ai.timeout_seconds`, `ai.max_new_per_scan`: bounded optional AI explanations.

Without `OPENAI_API_KEY`, deterministic rules continue. AI output is restricted to configured asset IDs and is treated as conditional interpretation, not a trading instruction. API usage is billed separately.

## Operational checks

Check the service logs with `journalctl -u signal-desk-intelligence -f` and `journalctl -u signal-desk-trading -f`. On the dashboard, the header shows whether the news worker and trading supervisor have recent continuous heartbeats; the Impact Radar shows source coverage, last scan, mapping confidence and confirmation/invalidation conditions.

A stale or failed RSS source is not equivalent to no news. Check source health. Google News RSS is a discovery feed, not a licensed institutional feed; cross-check market-moving developments against primary sources. The service does not browse every article body and cannot guarantee complete news coverage.
