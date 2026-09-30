# Kalshi 15-Minute Markets: What Actually Makes Money (Research, 2026-09-30)

## Bottom line

- **Nobody publicly reports a profitable taker strategy** (one that pays the ask, like your bots do) on Kalshi's 15-minute markets that has held up with real money.
- **The strongest public test matches your own results.** On 6,298 BTC 15-minute windows, buying the leading side made **+0.67c per contract before fees**. The average taker fee was **1.55c**, so it lost money at every entry time tested.
- **The one structural opening:** Kalshi reportedly charges **no maker fee on KXBTC15M**. It also pays retail traders for resting orders through its **Liquidity Incentive Program**. Waiting in the book as a maker instead of taking is the only approach that isn't negative by construction. Nobody has shown it is profitable yet.
- **Latency arbitrage** (acting on Binance/Coinbase moves before the prediction market reprices) did make real money on Polymarket in 2025. It has since been shut down there, and on Kalshi professional firms now move *ahead* of Binance. A Colab bot can't compete there.

A note on sources: this environment blocked direct access to many sites, including Kalshi's help center and the key papers. Most facts below come from search-engine summaries of those pages. The GitHub repositories were read directly. Anything marked **(verify)** should be checked on Kalshi's own site before you rely on it.

---

## 1. How these markets actually settle (your bots watch the wrong prices)

| Markets | Settles on | What your bots watched |
|---|---|---|
| BTC, ETH, SOL, XRP, DOGE | Average of **60 once-per-second prints** of the **CF Benchmarks Real-Time Index (RTI)** in the final minute. The RTI combines Bitstamp, Coinbase, Gemini, itBit, Kraken and LMAX Digital. | Coinbase only |
| Gold, Silver, Copper (and likely WTI, Platinum, Palladium; **verify**) | **Pyth Network 1-minute candle close** at the window's close vs. at its open | Nothing (Kalshi price only) |

- The strike is fixed at the window's open. After that, the fair value is arithmetic (distance to strike, volatility, time left), not a forecast.
- In the final minute, part of the 60-second average is already locked in, so the outcome becomes certain faster than the spot price alone suggests.
- Pyth's public feed (Hermes, `hermes.pyth.network`) was free, but a notice says an API key is required after its **August 18, 2026** upgrade (**verify**).

## 2. Who makes money on Kalshi (research)

- **Bürgi, Deng & Whelan**, *Makers and Takers* (300,000+ Kalshi contracts):
  - The average return is about **−20%** (−22% after fees).
  - Takers lose about **32%** on average; makers lose about **10%**.
  - Contracts priced **above 50c earn a small positive return**. That's the favorite–longshot bias, and it's the same small favorite edge your tests found before fees.
- **Becker**, *Microstructure of Wealth Transfer* (72.1M Kalshi trades):
  - Money flows **from takers to makers**.
  - Finance markets are the most efficient (a 0.17 percentage-point gap between price and outcome).
- **Della Vedova**, *Who Profits from Prediction Markets? Execution, not Information* (222M trades):
  - Profitable traders win through **better entry prices**, not better predictions.
  - Automated traders pay about **2.5c less per contract**, and that gap alone decides who profits.
- **Bartlett & O'Hara**, *Adverse Selection… Evidence from Kalshi* (41.6M trades):
  - Market makers earn about twice as much per contract as spreads suggest.
  - One-sided order flow predicts maker losses in some markets. That's **adverse selection**: your resting order gets filled when you're wrong.
- **Synth** (August 2026):
  - Kalshi's 15-minute BTC prices increasingly **lead** Binance by 0–2 seconds. The correlation rose from 0.036 to 0.173 this year.
  - Professional firms on Kalshi forecast BTC 5–30 seconds ahead.

## 3. What other bot builders tried, and what happened

| Who | Strategy | Result |
|---|---|---|
| botforkalshi analysis | Buy the leading side, hold (your strategy) | +0.67c gross vs. 1.55c taker fee → **negative at every entry time**, 6,298 windows. Stopped taker trading; maker-only now. |
| hudsonjoshuaclark/kalshi-bot | Fair-value model (RTI + Pyth), 15-min crypto and commodities | **No edge.** 3,200 windows, 60 rule combos, 0 survived correction for testing many at once; first-half/second-half results flipped (+$26 / −$17) |
| aurascoper/brti-edge | Rebuilt RTI (Coinbase+Kraken+Bitstamp) + fair value, **live** | 285 live trades, **−$32.99**, 51% win rate |
| quantfirm PR #60 | Commodities 15-min, Pyth + Swissquote fair value | Taker: +104% backtest was an artifact → **−14.6%** with realistic delay. Maker live: **−$19 on 151 fills** (about flat). "Neither leg has demonstrated edge." |
| simonziervogel/trading-bot | Fair value, NO side, taker | Backtest +13.6c/contract, significant 6 months. **Live sample ≈ 0.** Author warns it may just be Kalshi lagging Binance in 1-minute data, the same artifact that sank quantfirm's result. Treat as unproven. |
| TurbineFi | 4,904 simulated strategies on KXBTC15M | Only **2.1%** profitable in 30 days, about what luck alone produces. "Panic-fade" (buy after a sharp drop) filled 93 of the top 100. Backtest only. |
| seanrobenalt/kalshi-bot | Buy YES+NO when combined < $1 | README: "it lost me $100" |

**Pattern:** every public attempt that looked good in a backtest either lost live or has no live record. That's the same pattern as your 100–300 versions.

## 4. Latency and cross-market arbitrage

- **Polymarket 2025:** bots using Binance feeds made millions. One reportedly turned $313 into $414k in a month on 15-minute BTC/ETH/SOL markets.
- **Polymarket then closed the gap** with taker fees on 15-minute crypto markets (paid out as maker rebates), and on **August 7, 2026** it switched to settling on a 60-second Chainlink average.
- **On Kalshi,** taker fees always existed, and professional firms now lead spot (Synth). A Colab bot on a 0.5-second loop is too slow.
- **Kalshi vs. Polymarket "arbitrage"** isn't risk-free for these markets:
  - They settle on different prices (CF RTI vs. Chainlink), so one side can lose on both venues.
  - The two orders aren't placed at the same instant.

## 5. What's realistically left for you (ranked)

1. **Trade as a maker, not a taker (the only structurally viable path).**
   - Post limit orders slightly better than the current best and let others hit you.
   - Reported **no maker fee on KXBTC15M** (**verify** for ETH/SOL/XRP/DOGE; commodity series may charge makers 0.0175 × P × (1−P)).
   - The **Liquidity Incentive Program** pays retail US members for resting orders near the reference price, even unfilled: $1–$1,000 per market per day, split among providers (**verify** that 15-minute markets are included).
   - **The risk** is adverse selection: fast traders fill you right before the price moves against you. The one public live maker test was about flat (−$19 on 151 fills).
2. **Use the settlement data, not Kalshi's price or Coinbase alone.**
   - Rebuild the RTI from its six exchanges (crypto).
   - Pull Pyth (commodities).
   - You need this to set maker quotes near fair value.
3. **Stop:** taker strategies based on Kalshi price patterns (entry bands, dips, reversals, swings), and racing Binance.

## 6. How to test any new idea before it costs money

1. Pick the rules on one stretch of data. Test them **once** on a stretch nobody has looked at.
2. Write down the expected result **and its normal range** before going live. Judge live results against that range.
3. Replay the backtest on the bot's own live data and compare trade by trade.
4. Count how many variants you tried. With hundreds, the best backtest is mostly luck.
5. For a maker bot, don't trust a backtest that assumes every order at the best price gets filled. quantfirm showed a "do-nothing" quoter earning +1,268% under that assumption. Only live fills count.

---

## Sources

- Kalshi settlement and markets: [Kalshi Crypto Markets (help)](https://help.kalshi.com/en/articles/13823838-crypto-markets) · [CF Benchmarks on Kalshi](https://www.cfbenchmarks.com/blog/kalshi-leads-surging-crypto-event-contract-market-powered-by-cf-benchmarks) · [How Kalshi settles Bitcoin](https://predictionmarketspicks.com/articles/how-kalshi-settles-bitcoin) · [KXBTC15M rules](https://predictionmarketspicks.com/articles/kalshi-bitcoin-15-minute-markets) · [CF Benchmarks BRTI](https://cfbenchmarks.com/brti) · [Kalshi 15-min gold/silver launch](https://cryptobriefing.com/kalshi-15-minute-gold-silver-prediction-markets/) · [Gold Edge 15-min (Pyth settlement)](https://predictionmarketspicks.com/tools/gold-edge-15m) · [Pyth Hermes docs](https://docs.pyth.network/price-feeds/api-instances-and-providers/hermes)
- Fees and incentives: [Kalshi fee schedule PDF](https://kalshi.com/docs/kalshi-fee-schedule.pdf) · [Maker/Taker Math on Kalshi](https://whirligigbear.substack.com/p/makertaker-math-on-kalshi) · [Kalshi fee exceptions (oddpool)](https://www.oddpool.com/research/prediction-market-fees-explained) · [Liquidity Incentive Program](https://help.kalshi.com/en/articles/13823851-liquidity-incentive-program) · [LIP CFTC filing, July 2026](https://www.cftc.gov/filings/orgrules/rules07152610358.pdf) · [Kalshi liquidity subsidies analysis](https://medium.com/@navnoorbawa/kalshi-publishes-one-liquidity-subsidy-and-seals-the-other-polymarket-too-b59e8c9dffe8) · [Volume Incentive Program](https://help.kalshi.com/en/articles/13823850-what-is-the-kalshi-volume-incentive-program) · [Kalshi API rate limits](https://docs.kalshi.com/getting_started/rate_limits)
- Research: [Bürgi, Deng & Whelan, Makers and Takers](https://www.karlwhelan.com/Papers/Kalshi.pdf) · [VoxEU summary](https://cepr.org/voxeu/columns/economics-kalshi-prediction-market) · [Becker, Microstructure of Wealth Transfer](https://www.jbecker.dev/) · [prediction-market-analysis repo](https://github.com/jon-becker/prediction-market-analysis) · [Who Profits from Prediction Markets? (Quantpedia)](https://quantpedia.com/who-profits-from-prediction-markets/) · [Bartlett & O'Hara, Adverse Selection on Kalshi](https://law.stanford.edu/2026/04/21/adverse-selection-in-prediction-markets-evidence-from-kalshi/) · [Favorite–Longshot Bias on Polymarket (arXiv)](https://arxiv.org/abs/2609.12878) · [Synth: Kalshi leads Binance](https://reportify.cn/news/1287614443735486464)
- Bot builders: [botforkalshi: Kalshi Bitcoin Bot](https://www.botforkalshi.com/blog/kalshi-bitcoin-bot) · [hudsonjoshuaclark/kalshi-bot](https://github.com/hudsonjoshuaclark/kalshi-bot) · [aurascoper/brti-edge](https://github.com/aurascoper/brti-edge) · [quantfirm PR #60](https://github.com/vinilpolepalli/quantfirm/pull/60) · [simonziervogel/trading-bot](https://github.com/simonziervogel/trading-bot) · [TurbineFi 4,904 backtests](https://www.turbinefi.com/blog/5000-strategy-backtest-kalshi-btc-15m) · [seanrobenalt/kalshi-bot](https://github.com/seanrobenalt/kalshi-bot) · [brandononchain/kalshibot](https://github.com/brandononchain/kalshibot) · [reedjacobp/kalshi-trading-bot](https://github.com/reedjacobp/kalshi-trading-bot)
- Latency and cross-market: [Polymarket bots and $40M arbitrage (Yahoo)](https://finance.yahoo.com/news/arbitrage-bots-dominate-polymarket-millions-100000888.html) · [Polymarket taker fees on 15-min markets](https://www.financemagnates.com/cryptocurrency/polymarket-introduces-dynamic-fees-to-curb-latency-arbitrage-in-short-term-crypto-markets/) · [Polymarket TWAP settlement](https://casatrick.substack.com/p/polymarket-twap-latency-trading-bots) · [Chainlink vs Binance oracle gap](https://dev.to/lkto1m/why-my-polymarket-bot-watches-chainlink-not-binance-the-oracle-gap-that-changes-every-close-1e96) · [Most prediction-market traders lose while bots gain (Bloomberg)](https://bloomberg.com/news/articles/2026-04-28/most-prediction-market-traders-are-losing-money-while-bots-rack-up-gains)
