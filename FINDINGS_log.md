# Findings log — signals, open questions and what the next grid must test

Kept up to date as data comes in. Every number names its source.

## Current bot: V37 (started Fri Oct 9 2026, 18:01 UTC; planned to run up to 7 days)
- Crypto only, paper: buy a side at 58-67c in minutes 0-3, sell at 90c, else hold; one trade per market.
- Commodities recorded only (ticks, book, prints, settlements, Kalshi strike). No commodity spot price:
  Pyth's live-price API now needs a paid key (HTTP 401). The strike (Pyth price at the open) and the
  settlement value (Pyth price at the close) give a 15-minute Pyth price track instead.
- Logs: `commod15min_v37_trades.csv`, `commod15min_v37_settlements.csv`, and
  `commod15min_v37_data/` (ticks_/book_/prints_ per UTC hour, compressed to .csv.gz when the hour ends).

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
8. Carried over: time exits, Kalshi and spot momentum filters, per-coin results (SOL negative before),
   04:00-08:00 UTC weak in V36 crypto, after-a-loss-on-the-same-coin weaker in V36.

## Data notes
- Old tick/book/prints logs (Sep 18 - Oct 8) were deleted on Oct 9 to free Drive space; the next grid
  runs on V37 data (plus any remaining .csv.gz and trades/settlements files).
- Kalshi sent the V34-V36 bots ticks for every Kalshi market; V37 filters to its own 11 markets.
