# Why these three strategies

Eleven strategies ran side by side before. Too many strategies tested together on thin evidence is how a tool finds "winners" that are luck. This note records what the research says, what our own test found, and what was kept. Nothing here is a promise of profit.

## What the research says

**1. Opening range breakout on "stocks in play" (kept).** Zarattini, Barbon and Aziz (2024, Swiss Finance Institute paper 24-98) test a 5-minute opening range breakout on US stocks and find that limiting it to *stocks in play* (a catalyst such as earnings or news, and unusually heavy opening volume) is what makes it work: a net Sharpe ratio of about 2.8 on a top-20 portfolio, 2016 to 2023, after costs. QuantConnect's independent replication got about 2.4. Caveats: it is a backtest, US-only, one period, and QuantConnect itself notes that slippage at the open can be large. This is the only strategy here with academic backing, and it is why the tool is built around a watchlist of shares in play (results, news, volume surge) rather than scanning every share.
Sources: [QuantConnect replication](https://www.quantconnect.com/research/18444/opening-range-breakout-for-stocks-in-play/), [Concretum summary of the paper](https://concretumgroup.com/a-profitable-day-trading-strategy-for-the-u-s-equity-market/), [Swiss Finance Institute listing](https://ideas.repec.org/p/chf/rpseri/rp2498.html), [CXO Advisory summary](https://www.cxoadvisory.com/?p=45379).

**2. Opening drive (kept, as a test).** Entering the first strong thrust of a share already moving on heavy volume. It comes from a missed move (Motilal Oswal, which ran about Rs 50 in 15 minutes on crazy volume) and from the results-day logic: a surprise gaps and often keeps going. The evidence for post-earnings drift in India is mixed. One study of 100 NSE firms (2014 to 2018) finds the market fairly efficient around results; others find drift, mainly after bad news; an NSE-IGIDR paper finds insiders profit from it. So this one is a hypothesis to test, not a proven edge.
Sources: [Post-earnings drift in NSE-listed firms](https://dspace.vut.cz/items/bb999abe-68da-4d2b-a992-5c1d6cd5914c), [NSE-IGIDR working paper](https://nsearchives.nseindia.com/research/content/NSE-IGIDR-WP4-14-15.pdf), [IIMB working paper](https://repository.iimb.ac.in/handle/2074/17935).

**3. EMA trend pullback (kept, weakest).** Buying or selling the first pullback in a strong, established trend. It is a standard trend-following pattern with no strong study behind it for intraday Indian shares. It is kept only because it was the one strategy that was not clearly negative in our own test, on a sample too small to trust.

**Not used: the first half-hour predicts the last half-hour.** Gao, Han, Li and Zhou (Journal of Financial Economics, 2018) find that the first half-hour return of an index ETF predicts the last half-hour return. The effect is modest (R-squared about 0.02), stronger in volatile markets and recessions, and independent checks found it weaker. It is a late-day index idea, so it was left out of this version; the index scanner reads the morning range and trend instead.
Sources: [Alpha Architect summary](https://alphaarchitect.com/2014/08/attention-prop-traders-the-first-half-hour-of-trading-predicts-the-last-half-hour/), [paper listing](https://econpapers.repec.org/RePEc:eee:jfinec:v:129:y:2018:i:2:p:394-414), [CXO Advisory check](https://cxoadvisory.com/calendar-effects/first-and-last-half-hours-of-trading-linked).

**Why fewer trades, and why costs matter most.** SEBI's July 2024 study found that 71% of individual intraday traders in the equity cash segment lost money in FY2022-23, loss-makers spent about 57% of their losses again on trading costs (profit-makers about 19% of their profits), and the share losing money rose to about 80% among those with more than 500 trades a year. That is why the tool grades every signal on cost against risk first, and why it should trade rarely.
Source: [SEBI press release, 24 July 2024](https://www.sebi.gov.in/media-and-notifications/press-releases/jul-2024/sebi-study-finds-that-7-out-of-10-individual-intraday-traders-in-equity-cash-segment-make-losses_84948.html), [Business Standard report](https://www.business-standard.com/amp/markets/news/7-in-10-intraday-traders-in-equity-cash-suffered-losses-in-fy23-sebi-study-124072400975_1.html).

## Where this is not a replication of the research

The paper uses a **5-minute** opening range, ranks stocks by **relative volume of the first five minutes** against previous sessions, and stops at a fraction of ATR on US stocks. The opening range breakout here uses a **15-minute** range (from the Flattrade article), a different stop and target, Nifty 200 shares, and a watchlist chosen by results, news and volume against the time of day. It is an adaptation, not a replication, and the US result says nothing certain about it. A separate 5-minute version should be tested head to head against it.

The tests so far also **do not replay the watchlist**: they run the strategies over every share on every day, without knowing which shares were in play (results, news, volume), and with today's Nifty 200 list rather than the list on each date. They are diagnostics, not evidence about the system as it now runs.

## What our own test found

60 sessions (14 August to 11 November 2026) of real 5-minute prices from Yahoo Finance for the Nifty 200 and the indices, with estimated costs and slippage, next-candle fills:

| Strategy | All signals, per trade | Graded "worth taking": trades | per trade | first half / second half |
|---|---|---|---|---|
| Opening range breakout | -0.15R | 644 | about -0.11R | -0.15R / -0.07R |
| Opening drive | -0.07R | 190 | about -0.06R | -0.03R / -0.10R |
| EMA trend pullback | -0.66R | 77 | about +0.10R | +0.44R / -0.24R |

(R is the amount risked on a trade.) All three lost money overall, and the one small positive is not stable. That is an honest result: the tool is a research and paper-trading aid, and none of these is proven to make money. The test used the whole Nifty 200 without the new results and volume watchlist, which is the thing the research says matters, so it is not yet a test of the ORB-on-stocks-in-play idea. Run the **Strategy backtest** workflow on Kotak's longer history, and let the live paper trades accumulate, before putting money behind any of it.

## What was dropped

Momentum, breakout, reversal, VWAP pullback, gap and go, Bollinger bounce, Bollinger squeeze and EMA crossover. In the earlier 60-session test every one was negative after costs, and none has more than blog-level support. They remain in `engine/strategies/defs.py` (under `ALL_STRATEGIES`) and are not run. To try one again, add its id to `ACTIVE_IDS` in that file.

## The point-in-time replay (version 3.2.0, corrected in 3.3.0)

`python -m engine.replay` (or the **Research replay** workflow) replays the whole decision process on past prices, bar by bar, with only what was known at the time: NSE's results calendar for those dates, each share's volume against its previous ten sessions on the live time-of-day curve, the same promotion thresholds and per-candle limit, then the three strategies with next-candle fills and costs. A trade counts for the full system only if its share was on the watchlist when the signal candle closed. News is not replayed (no timestamped history exists) and today's Nifty 200 list is used for every date.

**A correction.** The first version of this replay said its last 10 sessions were a sealed holdout, but its totals, breakdowns and cost table still included them. That was a bug, and the first figures I reported (full system -0.09R, "+0.05R with no costs") were contaminated by it. It is fixed: with the holdout sealed, those sessions are removed before any number is computed. The holdout of that replay was seen in aggregate, so it counts as used; for a fresh one, fix a future date with `--holdout-from`.

Corrected run, 60 sessions of Yahoo prices (38 shown after warm-up, 10 sessions sealed). Average R per trade after costs, with a 95% range that resamples whole days:

| What is included | Trades | Avg R | 95% range | Development | Validation |
|---|---|---|---|---|---|
| Every signal on every share | 14,330 | -0.40R | -0.48 to -0.33 | -0.43R | -0.33R |
| On the watchlist by volume or move | 2,078 | -0.39R | -0.48 to -0.31 | -0.40R | -0.38R |
| On the watchlist by results | 475 | -0.27R | -0.41 to -0.08 | -0.63R (24 trades) | -0.25R |
| On the watchlist (either) | 2,398 | -0.38R | -0.46 to -0.29 | -0.40R | -0.34R |
| Graded worth taking, any share | 368 | -0.10R | -0.20 to +0.02 | -0.06R | -0.18R |
| The full system | 243 | -0.14R | -0.24 to -0.02 | -0.10R | -0.23R |

What it says:
- **No edge is shown, and the full system is significantly negative.** Every row is below zero; the full system's range excludes zero.
- **Costs are the whole story.** With no costs the full system averages 0.00R per trade; at half costs -0.07R; at full costs -0.14R.
- **The grade helps, the watchlist is not shown to.** Going from every signal to "worth taking" moves -0.40R to -0.10R. Adding the watchlist on top does not improve it. The watchlist's case is load: it scanned about 18% of the shares per candle.
- **Results-driven trades are about break-even where there is data** (just out +0.08R on 25 trades, today +0.12R on 13) with ranges far too wide to say anything, and September has almost no results.
- **Index futures trades lost more** (-0.17R, 385 trades).

## Daily base-and-breakout research (versions 3.3.0 and 3.4.0)

Intraday trades pay about 0.14R in costs and the research above shows no edge after them. A trade that holds for weeks pays the same money in costs but risks far more per trade, so costs matter much less. `python -m engine.daily` (or the **Daily research** workflow) tests a simple, published swing method on about ten years of daily prices: buy a close above the previous 40-day high on 1.5 times normal volume, in an uptrending share, with a small base, an 8 percent stop (that is 1R), and let winners run. It does not copy any website's proprietary rules.

**The rules and the list of experiments were fixed before any result was seen, and nothing was tuned afterwards.** There are four cumulative filters (breakout, plus uptrend, plus tight base, plus Nifty above its 200-day average) and two exits (leave at the next open after a close below the 20-day average, with the stop moved to the entry after a one-R gain; or a 3R target or 60 sessions). Entries are at the next open, a stop is checked before a target, a gap through the stop fills at the open, and costs are 0.40 percent of the position round trip. The last 20 percent of sessions (from 2025-01-28) was cut away before any signal was computed and stays sealed.

### First run: today's Nifty 200 (biased, kept for comparison)

Yahoo Finance only has shares that exist today, so this run used the 192 current members of the Nifty 200 for every past year. Shares that fell out of the index, were delisted or merged are missing, which flatters any method that buys strength: those shares averaged **18.5 percent a year** against **11.7 percent** for Nifty.

### Second run: every share that traded (survivor-free, version 3.4.0)

NSE publishes a price file for every trading day, listing every share that traded that day, including the ones later delisted, merged or renamed. `python -m engine.bhav` downloads them (about 2,500 small files, resumable). From them the research builds split- and bonus-adjusted prices (using the exchange's own previous close, which NSE adjusts on the ex-date), identifies each share by its ISIN so renames keep their history, and ranks every share each day by the traded value of the previous 50 sessions. **The universe on any date is the 200 most traded shares then**, a point-in-time stand-in for the Nifty 200 that needs no index history. A share that stops trading is closed at its last price, never dropped. 1,756 shares ever qualified, and 184 of them stopped trading inside the test period. Those shares now average **8.4 percent a year**, below Nifty, against 18.5 percent for the survivor list: that gap of about ten points a year is the size of the survivorship tilt.

Result (2017-11 to 2025-01, 7,855 breakout days; one run, same rules):

| What is included | Exit: 20-day average | Exit: 3R target or 60 days |
|---|---|---|
| Breakout on volume | 0.00R (3,479 trades, -0.12 to +0.11) | +0.07R (3,094, -0.12 to +0.24) |
| + uptrend | +0.03R (1,967, -0.09 to +0.16) | +0.12R (1,756, -0.04 to +0.27) |
| + tight base | +0.09R (1,529, -0.05 to +0.22) | +0.21R (1,314, +0.04 to +0.39) |
| + Nifty above its 200-day average | +0.09R (1,487, -0.04 to +0.22) | +0.19R (1,279, +0.01 to +0.37) |

Against the biased run, the same rows were +0.14R and +0.28R at the bottom of the table and +0.17R and +0.31R for the uptrend rows: removing survivorship took away between about a third and all of the apparent edge, depending on the row.

What this does and does not show:
- **A small effect survives in the tightest rows.** With the tight base, and with Nifty above its 200-day average, the breakout beats random entry on the same shares (+0.09R against +0.02R for the 20-day exit; +0.19R against +0.13R for the 3R target). The first two rows do not beat random. The 3R-target comparison only just clears the random range (its upper edge is +0.19R).
- **It is marginal and fragile.** Eight tests were pre-registered, and the lower ends of the ranges sit between 0.00 and +0.04R: with eight looks, one or two near-misses like that are what chance alone produces. In the development period (2017 to April 2023) the full system with the 3R target made +0.05R; in the validation period (April 2023 to January 2025, a strong market) +0.43R.
- **It depends on the market.** The full system by year: 2018 -0.20R, 2019 -0.01R, 2020 +0.16R, 2021 +0.26R, 2022 -0.28R, 2023 +0.41R, 2024 -0.15R.
- **A few trades make it.** The 10 best trades made 106R of the 133R total; without them the average is +0.02R. The 5 best shares made 82R.
- **As a portfolio it does not beat the index.** Risking 1.5 percent of equity per trade with at most 5 positions, marked to market daily: the 3R-target version grew 10.8 percent a year with a 37 percent drawdown, against 11.7 percent and 38 percent for Nifty buy and hold; the 20-day-exit version lost 2.6 percent a year with a 34 percent drawdown.
- **Delisted shares barely matter to this method.** Only 2 of its trades were still open when a share stopped trading (averaging -0.03R), because the uptrend and base filters rarely pick shares that are about to be delisted. Most of the survivorship tilt therefore most likely came from shares that fell out of today's list yet kept trading (never candidates in the biased run), not from end-of-life trades; I did not separate the two.

So: with survivorship removed the daily method shows at best a small, marginal edge that is concentrated in a few trades and strong markets, and it does not beat holding the index. That is not a basis for real money. The proxy universe is by traded value, not NSE's membership, prices are not adjusted for dividends, and a merged share is closed at its last price (its holders received shares of the buyer), so some noise remains. The holdout is still sealed.

## Market context: relative strength and breadth (version 3.5.0)

An outside review said the tool lacked what sites like MarketStrength show: relative strength, breadth and sector rotation. They were built from the same survivor-free NSE daily prices and **tested, not just added**. The settings and the list of tests were fixed in `engine/context.py` before any result was seen: relative strength is the percentile, among the 200 most traded shares that day, of the weighted 3/6/9/12-month return (weights 40/20/20/20, the score MarketStrength describes); breadth is the share of those shares above their 50-day average. Six breakout tests (the tight-base breakout plus each filter, with each exit) and four portfolio tests were added to the eight already run: 18 looks in all, so a lone near-miss is what chance produces.

**Relative strength helps the breakout; breadth does not.** Tight-base breakout, 2017-11 to 2025-01, survivor-free:

| Tight-base breakout, plus | 20-day exit | 3R target |
|---|---|---|
| nothing | +0.09R (1,529 trades) | +0.21R (1,314) |
| relative strength 80 or more | +0.18R (668; 0.00 to +0.37), beats random entry | +0.28R (593; +0.06 to +0.49), beats random |
| breadth 50% or more | +0.09R (1,369), does not beat random | +0.21R (1,192), does not beat random |
| both | +0.21R (539; +0.01 to +0.42), does not beat random | +0.29R (494; +0.04 to +0.53), beats random |

The relative-strength rows are positive in both the development and the validation period (+0.08R and +0.48R for the 20-day exit), and they are the only filter that beats a random entry on the same shares in both exits. Breadth adds nothing measurable beyond the Nifty 200-day filter already tested.

**A relative-strength portfolio beats the index only in one stretch.** The 20 strongest shares of the universe, equal weight, rebalanced monthly, costs of 0.40 percent round trip paid on every trade:

| Portfolio | Per year | Worst fall | 2017 to Apr 2023 | Apr 2023 to Jan 2025 |
|---|---|---|---|---|
| Weighted 3/6/9/12-month score | 16.1% | 45% | 8.6% | 41.9% |
| ... holding cash when Nifty is below its 200-day average | 14.7% | 37% | 4.5% | 51.9% |
| 12-month return skipping the last month | 14.5% | 51% | 6.0% | 44.3% |
| ... with the Nifty rule | 12.5% | 39% | 1.9% | 51.5% |
| All members, equal weight | 8.4% | 53% | 3.4% | 24.9% |
| Nifty held only above its 200-day average | 7.2% | 24% | 4.9% | 14.7% |
| Nifty buy and hold | 11.7% | 38% | 10.6% | 15.3% |

Over the whole period the strength portfolios beat Nifty (16.1% against 11.7%), but:
- **All of the gain is in the last stretch** (April 2023 to January 2025, a mid- and small-cap boom). In the 2017 to April 2023 period the best of them made 8.6% a year against 10.6% for Nifty.
- **It lost to Nifty in four of nine years**: 2018 (-13.5% against +3.2%), 2019 (+3% against +12%), 2022 (-3% against +4%) and January 2025 (-18% against -3%), and won big in 2020, 2021, 2023 and 2024.
- **The falls are deeper**: 45 to 51 percent against 38 percent for Nifty. The Nifty rule cuts the fall to about 37 percent but gives up return in the earlier period.
- **The universe as a whole trails the index** (8.4% a year, equal weight), which is the survivorship tilt showing up the other way once the dead shares are included.
- A rule that holds Nifty only above its 200-day average halves the fall (24 percent) but earns less (7.2 percent a year).

So relative strength is the one context ingredient with a consistent signal, but it behaves like a high-risk, regime-dependent factor, not an edge that has been shown to hold in all markets. Sector rotation was **not tested**: it needs each share's sector at each date and no free, point-in-time classification exists (using today's sectors would bring survivorship back). The holdout is still sealed.

## The 5-minute opening range, against the 15-minute one (version 3.5.0)

The published strategy (Zarattini, Barbon and Aziz) uses the first 5-minute candle for direction and range, trades the 20 shares with the heaviest opening volume, and stops at 10 percent of the daily average range. `orb5` in `engine/strategies/defs.py` follows it as far as our simulator can (the fill is at the next candle's open, later than the paper's stop order; the paper's selection uses the first 5 minutes, but Yahoo reports no volume for the 09:15 candle, so the first 10 minutes are used). It is a research candidate and does not run live. Replay of 60 Yahoo sessions (38 shown, holdout sealed), average R after costs:

| | 15-minute ORB (current) | 5-minute ORB (paper's rules) |
|---|---|---|
| Every signal on every share | -0.13R (6,396 trades) | -0.54R (5,262 trades) |
| Top 20 by opening volume (the paper's selection) | -0.08R (430) | -0.50R (535) |
| Graded worth taking | -0.13R (243) | -1.2R (1 trade: its stops are too tight to pass the cost check) |

The paper's volume selection helps both a little. The 5-minute version is far worse after costs, but **before costs it is the better of the two**: over all 60 sessions (this one figure includes the 10 sessions an earlier replay already used) the 5-minute version averaged **+0.23R gross** against +0.05R for the 15-minute one, with the low win rate (18 percent) the paper also reports. The costs are the whole difference: the 5-minute version's median risk is about Rs 97 a trade (its stop is a tenth of the daily range, so the position cap limits the size) against a median cost of about Rs 76, so costs eat roughly 0.78R per trade. The 15-minute version's median risk is about Rs 413. A stop that tight only works if a round trip costs a few hundredths of a percent, as it does for the US institutions in the paper, not at retail Indian brokerage, STT and slippage. Neither version is profitable here.

## The Leader Playbook notes, tested (version 3.6.0)

Study notes of a trader's podcast (Himanshu Sharma on MastersInOne, citing O'Neil, Livermore, Minervini and Zanger) describe how to find leaders and five ways to buy them. They give rules, not evidence. The rules were written down exactly in `engine/playbook.py` before any result was seen and run on the survivor-free universe with costs, each with a random-entry placebo (random days in which the same shares are uptrending relative-strength leaders, with the same kind of stop and exit). Seven more looks, so 25 in all. Sealed holdout untouched.

| Test (relative-strength leaders in an uptrend) | All | 2017 to Apr 2023 | Apr 2023 to Jan 2025 | Random entry |
|---|---|---|---|---|
| Tight pivot breakout (5% base, stop at the base low) | -0.02R (100 trades) | -0.12R (80) | +0.38R (20) | -0.01R: does not beat random |
| ... with the partial-profit exit | -0.03R (100) | -0.13R | +0.40R | does not beat random |
| Pullback to the 21-day average (stop at the pullback low) | -0.05R (854) | -0.13R (649) | +0.22R (205) | -0.01R: does not beat random |
| ... with the partial-profit exit | -0.07R (854) | -0.15R | +0.19R | does not beat random |
| Tight-base breakout with the partial-profit exit (8% stop) | +0.14R (537; -0.01 to +0.29) | +0.08R | +0.32R | earlier trail exit: +0.18R |

- **The new entries do not work, with these costs.** The pivot and pullback entries have stops of 1 to 8 percent, so costs take a large share of each R. Before costs they average only +0.05R and +0.04R, and neither beats entering at random on the same leaders. The validation period (2023 to January 2025) is positive for both, as everything was in that boom, on 20 and 205 trades.
- **The partial-profit exit does not help.** On the one set of trades that was already positive it lowered the average (+0.14R against +0.18R).
- **The bear gauge is useless here.** It fired on 24 of 1,764 days (2018-10 to 2022-06) in the 200 most traded shares, and changed 0 to 1 breakouts. It would matter more across all 2,500 shares, but the tool trades the liquid ones.
- **Position size changes the ride, not the quality.** The relative-strength tight-base breakout with the 3R target, at 5 positions: 0.25% risk per trade made 3.1% a year with a 6% fall; 0.5% made 6.2% with 12%; 1% made 12.1% with 23%; 1.5% made 17.3% with 33%, against 11.7% and 38% for Nifty. Return divided by fall stays near 0.5 at every size (Nifty 0.30). Halving the risk about halves both, as the notes' "risk less in a choppy market" advice implies. At 1.5% the book beat Nifty in both periods (12.8% against 10.6%, then 31.4% against 15.3%), but lost 6.6% in 2018, 14.5% in 2022 and 14.8% in January 2025, and made 83% in 2021. This is the best-looking result so far and also the most looked-at one, so it is a hypothesis for the sealed holdout, not a finding.

What the notes got right is what the earlier tests already supported: relative strength is the filter that matters. The entry styles are not what makes the difference. The notes' "RS of 87 for past winners" is hindsight, and the theme step (five or more names from one industry) was not tested because no point-in-time sector data exists.

### Using it live (version 3.6.0)

The leaders are now computed every weekday evening from NSE's daily file (`python -m engine.leaders`, the **Leaders** workflow): each liquid share's relative strength, the leaders (strength 80 or more, above the 200-day average), and market health (the share of shares above their 50-day and 200-day averages, and advances). It needs one download a day and no broker requests. The Trade engine reads the result and **flags** leaders on the watchlist, on trade cards, in alerts ("RS leader 92") and in the morning brief. It is a flag and not a gate, because the evidence says relative strength helps in some markets and not others.

## The "Market research" document, tested (version 3.7.0)

An AI-written report proposed five setup families (mean reversion, momentum bursts, episodic pivots, exits, pilot sizing) with pass marks (for example, 68 to 75 percent win rates for mean reversion and a profit factor above 1.25 in 75 percent of 12-month windows). It cites no data of its own, so each claim was written as an exact rule in `engine/archetypes.py` before any result was seen, and run on the survivor-free NSE universe (Nov 2017 to Jan 2025, sealed holdout untouched) with 0.40 percent round-trip costs, next-open fills and a random-entry placebo. About 70 looks have now been made, so a lone near-miss means little.

| Test | After costs | Before costs | Beats random? |
|---|---|---|---|
| RSI(2) below 10, uptrend, leave above 5-day average, 12% disaster stop | +0.05% a trade (7,422; win 61%; CI -0.27 to +0.34) | +0.45% | yes (random -0.30%) |
| ... RSI(2) below 5 | +0.14% (win 61%) | | |
| ... with relative strength 80+ | +0.02% | | |
| ... with a tight 3% stop | -0.38% (win 47%) | | |
| Momentum burst (3 variants) | -0.14R to -0.16R (about 4,000 each) | negative too | no |
| Episodic pivot, 200 most traded shares | 14 trades, -0.4R to -0.7R | | too few to say |
| Time stop / squat exit on the relative-strength tight-base breakout | +0.07R / +0.10R against +0.14R baseline | | neither helps |
| Pilot sizing (0.4% until proven, then 1.5%) | 9.7% a year, 19% fall | | same return-to-fall ratio as fixed 1.5% (17.3%, 33%) |
| Risk-adjusted momentum books | 11.8% / 10.3% a year, 45% / 38% fall | | worse than the plain relative-strength book (16.1%, 45%) |
| Profit factor above 1.25 in 12-month windows | best 60% of windows (target 75%) | | none passes |

- **What the report got right:** a tight stop destroys mean reversion (-0.38% against +0.05%), exactly as claimed, and the dip-buy does beat random entry in the same shares. Pilot sizing does lower the fall.
- **What it did not survive:** the dip-buy's edge is 0.05 percent a trade after costs, and the interval around it spans zero. Win rates were 61 to 64 percent, not 68 to 75. Before costs it makes 0.45 percent, below the report's own 0.8 to 1.0 percent bar. Momentum bursts lose even before costs. No setup reached the report's 75-percent-of-windows profit-factor mark.
- **Post-hoc, not pre-registered:** because the pre-registered episodic pivot had only 14 trades, the same rules were run on all 1,756 shares (data cut at the holdout date). 84 gap days gave 66 trades: +0.14R, interval -0.30 to +0.59; the 28 with a results announcement +0.51R (interval -0.20 to +1.19), the 38 without -0.13R. That is the only hint worth watching and it rests on 28 trades chosen after seeing the first result, so treat it as a question for new data. It does fit the tool's results-first design.
- The results history used 73,508 NSE announcements with no missing pieces.

## Reference sites

**MarketStrength** (market-strength scanner): useful as a list of what to measure (relative strength, sector rotation, breadth). Its terms forbid scraping and bulk download and its screens use end-of-day data, so it is not used as a data source; the same measures are to be computed from Kotak's daily candles.

**BananaPatterns** (bananapatterns.com): a paid, end-of-day screener for Indian stocks built on a different method, multi-week base-and-breakout trading (volatility contraction, blue-sky highs, IPO bases) with a wide stop (about 8 percent), 1.5 percent of capital risked per trade, and a trailing exit; it also has breadth, sector rotation, results and announcements, and a backtester it says covers every breakout since April 2016. Not usable as a data source: its terms limit use to personal, non-commercial use and forbid scraping, bulk download, redistribution and use to train models, its robots file disallows `/api/`, and data is updated only after the close. What is worth taking from it is the idea, not the data: a daily-timeframe method holds for days or weeks, so costs of about 0.14R per trade matter far less than they do intraday, and it is the kind of strategy the SEBI figures suggest has a better chance than intraday trading. That is a research candidate to test with our own daily prices (Kotak's daily history or Yahoo's, which go back years), not something to adopt on its marketing claims, which cannot be checked from the public pages. A person may still use the site by hand, within its terms, to see which stocks are setting up.

## Roadmap after review (in order)

1. **Replay the whole decision process** point in time (done in 3.2.0 and corrected in 3.3.0; still to do: the 5-minute opening range variant, historical index membership, and a fresh holdout): the universe on that date, results from NSE's historical calendar, the volume promotion with its limits, then the strategy with next-candle fills and costs. News cannot be replayed (no timestamped history), so that factor is excluded from the replay and said so. Keep the most recent period untouched as a final out-of-sample test, and report results by strategy, month, regime and time of day, with each filter added one at a time.
2. **Market context from authorised data**: relative strength, sector rotation (relative performance against the index and its change) and breadth (share above their 20, 50 and 200 day averages, advances against declines, new highs and lows), computed from Kotak's daily candles. The MarketStrength site is a useful reference for what to measure, but its terms forbid scraping and its screens use end-of-day data, so it is not used as a data source.
3. **A 5-minute opening range candidate**, tested against the 15-minute one.
4. **Option contract quality** from historical premiums: spread, open interest, implied volatility, delta and time decay.
5. **Live paper trading long enough to measure real slippage** against the simulated costs. Not before this should any real money be considered.

6. **Daily research, next steps:** the survivorship problem is addressed (3.4.0). Still to do: the single run of the sealed holdout with the rules unchanged; a test of relative strength on its own; and a comparison against simply holding the index with a trend filter, since that is what the portfolio result is up against.

7. **Context and 5-minute results (3.5.0):** relative strength helped the breakout and a strength portfolio beat the index only in one boom; breadth added nothing; the 5-minute opening range has a gross edge that costs erase. Not done: sector rotation (no point-in-time sectors), the sealed holdout, and a fresh holdout for the intraday replay.

8. **Playbook results (3.6.0):** pivot and pullback entries do not beat random entry on relative-strength leaders; the partial-profit exit does not help; the bear gauge almost never fires among liquid shares; position size scales return and fall together. The relative-strength tight-base breakout at 1.5% risk is the one hypothesis worth the sealed holdout.

9. **Market research document (3.7.0):** mean reversion has a tiny positive edge before the interval crosses zero; bursts and tight stops lose; no exit or sizing tweak adds return per unit of risk; no setup holds a 1.25 profit factor in 75 percent of windows. Possible lead: gap-ups on results days (28 trades, post-hoc).

## Sealed holdout, opened once (28 Jan 2025 onward)

The relative-strength tight-base breakout was the one hypothesis taken to the holdout, on the user's go-ahead. The holdout is now spent; `--final` will refuse a second run.

- **Trades:** with the 3R target, +0.08R over 127 trades (interval -0.26 to +0.44), against +0.24R across all earlier periods. Without the target, +0.11R over 141 trades (interval -0.14 to +0.41). Positive, but indistinguishable from zero and below the development numbers.
- **Book at 1.5% risk:** 2025 -10.4%, 2026 so far about -6%, against Nifty +10.5% and -3.3%. Over the whole span including the holdout it made 13.8% a year with a 33% fall, against 10.5% and 38% for Nifty, down from 17.3% before the holdout. The earlier outperformance came from 2021 (+83%) and 2023 to 2024.
- **Verdict:** the hypothesis did not confirm. It remains a small, fragile, regime-dependent edge at best. No strategy here has shown a reliable after-cost edge.
