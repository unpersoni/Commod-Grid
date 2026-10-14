# Findings log — signals, open questions and what the next grid must test

Kept up to date as data comes in. Every number names its source.

## Current bot: V37 (started Fri Oct 9 2026, 18:01 UTC; planned to run up to 7 days)
- Crypto only, paper: buy a side at 58-67c in minutes 0-3, sell at 90c, else hold; one trade per market.
- Commodities recorded only (ticks, book, prints, settlements, Kalshi strike). No commodity spot price:
  Pyth's live-price API now needs a paid key (HTTP 401). The strike (Pyth price at the open) and the
  settlement value (Pyth price at the close) give a 15-minute Pyth price track instead.
- Logs: `commod15min_v37_trades.csv`, `commod15min_v37_settlements.csv`, and
  `commod15min_v37_data/` (ticks_/book_/prints_ per UTC hour, compressed to .csv.gz when the hour ends).

## V37b (built Oct 14, user's idea: V37 inverted)
- Crypto only, paper: buy a side at 38-42c (the underdog) in minutes 0-3, sell when its bid reaches 70c, else hold
  to settlement; one trade per market, no buy if spread > 5c, no stops. Same window/limits as V37.
- Not the exact mirror of V37 (that would be: buy the underdog, hold, and stop out at ~10c). The 70c sell is a
  profit target 20c below V37's 90c, matching the entry being ~20c lower.
- No files at all (user's choice): results are only printed in the Colab output, so V37b runs can't be analysed
  later unless the user pastes the output. `SAVE_FILES = True` turns on V37-style files.

## Where the 58-67c -> 90c rule came from
- v36_grid_search on 1,048 markets (Sep 29 - Oct 8): 591 crypto positions, +26c/position,
  +25c first half / +28c second half, +$155. Positive 4 of 6 full days; BTC +79c, ETH +40c, DOGE +34c,
  XRP +8c, SOL -30c per position. About 1.6 standard errors from zero — a candidate, not proven.
- Also flagged by v34_param_search (58-62c -> 85c) and grid run 1 (63-67c -> 90c).
- Sell at 90c vs hold to settlement: same average (+279c vs +282c per position that reached 90c;
  90.3% of those won anyway), but selling was positive in both halves and holding was not (-3c 2nd half).
- Time exits (sell at minute 10/12/14) were worse than holding for this rule.
- Commodities: 1 of 3,168 grid settings positive in both halves. No commodity trading for now.

## V37 run 1 (Fri Oct 9 14:00 ET -> Sat Oct 10 00:15 ET, 200 crypto trades, +$31.66)
- Phase 1, Fri 2:00-6:15pm ET: 86 trades, 81% won, +$99.15 (peak).
- Phase 2, Fri 6:15pm-12:15am ET: 114 trades, 62% won, -$67.49.
- Differences between the phases:
  - BTC moved about twice as much per 60s in phase 1 (0.021% vs 0.012%) — but per trade, BTC movement did
    not separate winners (71% vs 71%): it describes the session, it is not a trade filter by itself.
  - Spread wider in phase 2 (1.52c vs 1.16c). Per trade: 1c spread +34c/trade (146), 2c+ -33c/trade (54).
    Same direction in V36 crypto trades (2c spread worse in both halves). Strongest candidate filter.
  - NO (down) bets fell from 84% to 57% won; YES from 79% to 69%.
  - Not different: buy price, minute, open lean, book depth, open positions, cycle clustering, coin.
- Caveat: one evening, split at the peak (exaggerates differences).

## Time of day, all saved data (checked Oct 10)
Sources: real trades V34b, V34c, V35a, V36a, V36b-old, V36b, V37 (4,915 trades, Oct 1-10) and the grid's
simulated V37 rule (591 positions, Sep 29 - Oct 8). Blocks in ET: day 9am-6pm, evening 6pm-12am, night 12am-9am.
- Crypto, real trades (edge = points above/below the price's implied win rate): day +0.5 (1,557),
  evening -0.7 (1,090), night -4.5 (411). V37 rule replayed: day +38c/pos (299), evening +33c (192),
  night -22c (100). Evening worse than the same day's daytime in 8 of 13 day comparisons.
  Both Fridays dropped in the evening (Oct 2: +4.1 -> -7.5; Oct 9: +13.7 -> -5.6) — only two Fridays.
  Night (12am-9am ET) is the consistently weak block (matches the 04-08 UTC weakness in V36).
- Commodities: day -2.2, evening -8.9 (841 trades), night -4.7; evening worse on 4 of 5 days.
  Likely cause: CME metals/oil pause 5-6pm ET, then a thin evening session (wider spreads).

## Dip buying after a high (checked Oct 13, user's idea)
Question: if a side's price reached H (e.g. 90c) and later dips to H-10, does it still win like a 90c side?
Source: V37 hourly tick files on Drive (54 files, Oct 9 18:00 - Oct 13 00:00 UTC, 1.14M valid rows),
1,521 markets with an official result from `commod15min_v37_settlements.csv`. Rule simulated: the side's
bid reached H, then its ask fell to <= H-D with >= 30s left -> buy at the ask (one buy per market/side/setting).
Hold to settlement, or sell back when the bid returns to H. Edge = win% minus average buy price.
- User's example, crypto H=90 D=10: 395 buys at 75.5c average, won 76.2% (edge +0.7 +/- 2.1).
  It wins like a 76c position, not a 90c one. Net -1.3c/trade holding, -4.5c selling back at 90.
- All 35 crypto settings: edge between -3.2 and +4.6. Pooled crypto -0.7 (17,173 buys), commodities -1.8 (5,731).
- Selling back at the old high was worse than holding in every crypto setting (fees on both legs,
  and the dips that never recover are mostly losers).
- Only weak candidate: crypto H=95, D=10-25 (edge +2.9 to +4.6, 122-228 buys, about 1.2-1.5 standard errors).
  Positive in both halves, but all of it came from Fri Oct 9 (+10.1) and Mon Oct 12 (+13.0);
  Sat/Sun were -0.7/-0.8 (H=95 D=15). BTC -2.9. The best of 35 settings landing at +1.5 SE is expected by chance.
- Same price, different history (first time the ask reached a price, split by how high the bid had been
  before): no consistent pattern in crypto or commodities.
- By time left at the dip (one buy per market/side/high; edge = win% - price; [1st/2nd half]):
  - Crypto, reached 80c, dip bought with 10-15 min left: dip 5-8c +3.9 (183) [+6/+1], 10-13c +8.5 (125) [+12/+3],
    15-25c +13.1 (74) [+18/+5]. Reached 90c, dip 5-8c, 10-15 min left: +11.6 (40). Same idea as the early-mover
    finding: a side that hits 80-90c within the first 5 minutes is strong, and an early dip is worth buying.
  - Crypto, reached 95c, dip bought with 2-5 min left: +3.6 (146) [+4/+3], +5.8 (101) [+6/+5], +7.6 (75) [+8/+8].
  - Last 2 minutes: no edge in crypto (80c dips -6 to +0; 90c +1 to +3; 95c -4 to +1). Dips near the close are
    NOT better than their price. Crypto 80c dips with 5-10 min left: -4 to -6. Commodities: mostly negative.
  - About 60 cells checked over 4 days, so single cells can be luck; the two pockets above hold across all
    dip sizes and both halves. Re-test on the full V37 week.
- Conclusion: no mean reversion after dips in this data; a dip's price is a fair price. Re-test H=95 dips
  (weekday vs weekend) on the full V37 week.

## Price path vs win rate (checked Oct 13, user's point: same price, different 15-minute path)
Source: same V37 ticks/settlements as above. Every first time a side's ask reached each 2c price level
(30-95c, 30s-14.5m left), with the path known at that moment: lowest ask so far, highest bid so far,
mid change over the last 30/60/180s. 66,065 moments in 1,521 markets.
- Prior low ("it was at 45c earlier, now 56c"): no edge. Crypto by how far below it had been:
  never 5c+ lower -0.2, 5-14c -0.3, 15-29c +0.3, 30c+ -2.3 (14,285, beyond 2 SE). Big earlier swings were if anything worse.
- Prior high: no edge (see dip section).
- Rising fast, overall: no edge (crypto rising 15c+ in 60s: -1.6). Late in the market (3-11 min left)
  a fast rise was negative (-4 to -7 at 60-89c): late spikes overshoot.
- The one path signal: crypto, first 4 minutes (11+ min left), price 60-89c, risen 15c+ in the last 60s.
  One buy per market: 604 markets, 69.0c average, won 72.7%, edge +3.7 +/- 1.8 (2 SE), +1.7c/trade net
  after fee (held to settlement). Halves +3.5 / +4.1. Days: Fri +10, Sat -3, Sun +6, Mon +4.
  Same window risen only 5-15c: -0.5; not rising: +1.3. Commodities same window: +0.7 (228), H1 -12.5 / H2 +4.2.
  This matches V37's own rule (58-67c in minutes 0-3): early strong movers are the edge we have.
- Caveat: many cells were checked (5 price bands x 5 path buckets x 4 path types x 2 classes), so
  any single +2 SE cell can be luck. This one is supported by 3 neighbouring price bands and both halves.

## Outside information: spot-vs-strike model vs Kalshi price (checked Oct 13)
User's point: search for signals from outside the Kalshi price. Earlier tests mostly used Kalshi's own
price (bands, dips, momentum), which every trader sees. This is the first test that uses outside data.
Source: V37 ticks (Coinbase spot ~1/s in `spot_px`), strikes (`floor_strike`) and results from
`commod15min_v37_settlements.csv`; 1,017 crypto markets, 632,835 ticks with 30s-14.5m left.
Model: chance YES wins = Phi(k * ln(spot/strike) / sqrt(15-min realized variance per second * seconds left)).
k fitted on the 1st half (k=1.25); everything judged on the 2nd half (497 markets, from Oct 11 05:14 UTC).
- Accuracy (log-loss, lower is better), 2nd half: Kalshi mid 0.4911, spot model alone 0.4973,
  Kalshi + spot blend 0.4857. Spot adds real information beyond Kalshi's price (weights: Kalshi 0.89, spot 0.19).
  Gains were at 3-15 min left; in the last 3 minutes Kalshi was already as good.
- Trading on the gap (buy the side when model% - ask >= threshold, first time per market/side, hold):
  - Spot model alone, gap >= 15: H1 455 buys edge +3.4, net +1.6c; H2 298 buys edge +3.4, net +1.6c.
    Same in both halves; mostly buying 38-41c sides that spot says are ~57%.
  - Blend, gap >= 8: H1 +7.2 (155), H2 +4.7 (101), net +5.2c / +2.7c. Includes a constant fitted on H1
    (tilt toward NO) that may be that half's market direction.
  - Blend without the constant: H2 fell apart at gaps >= 8 (49 buys, -7.0).
- Conclusion: outside data carries a small, real signal; after fees it's worth about +1.5 to +3c per trade
  on these 4 days. Not yet a strategy. Caveat: Kalshi settles on CF Benchmarks, not Coinbase.

## Signals to (re)test on the full V37 week
1. Spread: only buy when the spread is 1c.
2. Time of day x day of week (user's note: Friday payday, after-work activity from ~6pm ET).
   Track each run's hourly P&L, BTC activity and spreads by weekday. The week covers each weekday once,
   so day-of-week effects will be suggestive only.
3. Market activity / session regime: BTC 60s move size, coin spot moves, hourly spread — as conditions
   for switching the bot on/off, not per-trade filters.
4. Stop-losses: sell if the bid falls to 50/45/40/35/30c vs hold (crypto and commodities separately).
5. Sell at 90c vs hold everything.
6. Commodity trend ideas: late-start windows (buy the side still leading after minute 4-6),
   favourite bands up to 97c, the 15-minute Pyth track from strikes + settlements.
7. One direction per cycle (match the cycle majority or BTC's direction).
8. Dip buying after a high (user is tracking these; see section above). Crypto, dip bought with 10-15 min left:
   reached 80c, dip 10-13c (+8.5, 125 buys); reached 80c, dip 15-25c (+13.1, 74); reached 90c, dip 5-8c (+11.6, 40;
   too few to split into halves). Also reached 95c, dip 10-25c with 2-5 min left. Re-test each on the full V37 week:
   win% vs price, net after fees, both halves, by coin, weekday vs weekend.
9. Early fast risers: crypto, first 4 minutes, 60-89c, risen 15c+ in the last 60s (alone and added to
   the V37 rule); also sell at 90c vs hold for these.
10. Outside-information signals (priority): spot-vs-strike model gap (alone and with the V37 rule);
    order-book imbalance (book_ files); trade flow from prints (who is buying, size bursts); BTC spot leading
    alt-coin Kalshi prices; cross-coin agreement within a cycle. Fit on the first part, judge on the rest.
11. Carried over: time exits, Kalshi and spot momentum filters, per-coin results (SOL negative before),
   04:00-08:00 UTC weak in V36 crypto, after-a-loss-on-the-same-coin weaker in V36.

## Data notes
- Old tick/book/prints logs (Sep 18 - Oct 8) were deleted on Oct 9 to free Drive space; the next grid
  runs on V37 data (plus any remaining .csv.gz and trades/settlements files).
- Kalshi sent the V34-V36 bots ticks for every Kalshi market; V37 filters to its own 11 markets.
