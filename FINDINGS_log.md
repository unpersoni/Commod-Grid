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

## V38 plan ("early favs", agreed Oct 15)
- Crypto: buy 10 contracts of a side whose ask is 88-95c in the first 3 minutes (900-720 s before close), hold to
  settlement; one trade per market; no buy if spread > 5c. (All V37 data: 118 trades, 94.9% won, +5.6c.)
- Commodities: 85-95c in the first 8 minutes, hold. Chosen for volume at about break-even: 433 trades (45% of markets),
  86.4% won, -0.4c/trade, Fri-Mon +1.8 / Tue-Wed -2.9. Narrower 88-95c first 4 min: 86 trades, 0.0c (+5.8 / -5.7);
  85-95c first 6 min: 262, -2.4c; 80-95c first 6 min: 405, -2.7c; 80-95c first 8 min: 591, -2.4c.
  (Note: "minutes 0-3" in the crypto rule = first 3 minutes, sec 900-720.)
- Reversal guard (share of the last hour's markets whose minute-3 leader lost): computed at start-up from Kalshi
  history if available, then live; every trade is still taken and labelled guard on/off.
- Updated Oct 15 (user's request): rebound and spot-confirmed cross added as separate strategies, each with its own
  totals. Rebound (crypto): after the early-fav buy, bid falls 5c below the buy price, buy when the bid is 2c above
  its low (best total in the rebound test: 89 buys, 93.3%, +8.5c). Spot cross (crypto): ask crosses 90c (to 90-95c)
  with spot >= 1.5 normal moves past the strike (173 buys, 96.5%, +5.0c). Spot volatility = 1-second Coinbase samples
  over 15 min (needs 5 min after start). Older V38 trades file with other columns is set aside automatically.
- V38 live start (Oct 15): guard start-up lookup confirmed working against Kalshi -- 44 settled markets in the last
  hour (11 x 4), all with a minute-3 leader; crypto 6/20 reversed (30%, guard off), commodity 10/24 (42%, guard on).
- Logging kept small: trades, settlements, hourly ticks always gzipped with top-of-book size added, trade flow summarised
  per market per 15 s (no full book, no raw prints); warn when Drive < 200 MB free. Target ~10 MB/day.
- Drive cleanup Oct 15: V37 data folder (ticks, book, prints, ~470 MB) moved to Drive Trash. Ticks and book saved in
  data/; prints (~350 MB, never analysed) not saved.

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

## V37 so far + flipping during losing streaks (checked Oct 14)
Source: V37's rule replayed on V37 ticks (Oct 9 18:00 - Oct 13 00:45 UTC). The replay matches the real V37 trades
(Oct 9-10 file) on win/loss for 97% of trades; it's slightly pessimistic on fees (~$37 over the period).
- V37 replay: 954 trades, -5.1c per contract, about -$450 at 10 contracts. Lost every ET day:
  Fri -105c/contract (185 trades; the real Fri run was +$31.66 because it ran to 00:15 Sat), Sat -845 (282),
  Sun -1,715 (264), Mon -2,172 (223). The grid's +26c/position for this rule has not held up live so far.
- Inverse (buy the other side, 38-42c, sell 70c) on the same markets: -2.5c per contract.
- Counting recent losses does NOT predict the next trade: after 4 of the last 5 lost, V37 did better (+5.3c, 92);
  after 3 in a row it did worse (-13.5c, 106), after 4+ in a row better (+1.9c, 167). No usable pattern.
- Rolling last-hour P&L is the best flip signal found: when V37's trades closed in the last 60 min total
  < -200c per contract (~ -$20 at 10 contracts), the next 317 V37 trades lost -11.4c; the inverse made +1.8c
  (sell 70) / +3.8c (hold). Flipping on that signal: -670 vs V37 -4,837 (c/contract), better in both halves and
  on all 4 days. Pausing instead: -1,227. So most of the gain is from not trading V37 in bad hours; the inverse
  itself is close to break-even. Thresholds -100 and -300 were weaker, and V37 after a +50..+150 hour was the
  worst bucket (-16.4c, 91), so the pattern is noisy. 4 days only.
- User's live test: V37 and V37b (no files) running together; watch whether V37b's wins cluster in V37's bad hours.

## Out-of-sample check on new data (checked Oct 15)
New data never used to find any candidate: V37 ticks Tue Oct 13 8pm - Wed Oct 14 9pm ET (405 crypto markets),
real V37 trades Tue-Thu (448). All thresholds fixed in advance from Fri-Mon. V37 recorded 75 hours in total
since Fri Oct 9 (gaps: three overnights Sat/Sun/Mon 1-9am, Mon 9pm-Tue 8pm, Wed 8am-12pm, and Wed 9pm-Thu 12am
when the trades logged but the tick files never reached the full Drive).
- HELD: session switch (last-60-min V37 P&L < -200c/contract). New: V37 -1,066 / pause +276 / flip +449
  (c per contract, 387 replayed trades, 154 fired; flipped trades V37 -8.7c, inverse +1.1c). Second confirmation.
- FAILED: early fast riser (new -1.7 edge, 282); spot gap >= 15 (new -1.7, 212); blend gap >= 8 (new -7.9, 48);
  the blend was worse than Kalshi's price on new data (log-loss 0.4916 vs 0.4876).
- FADED: dips after a high. 80c-then-dip 10-15m left: +2.7 / +0.4 / +3.0 (was +3.9 / +8.5 / +13.1);
  95c-then-dip 2-5m left: +1.2 / +3.6 / -0.1. Only 90c-then-dip 5-8c, 10-15m left stayed up: 18 of 18 won (+17.2),
  too few to count.
- Spread 1c vs 2c+ (real trades): new 1c +0.3c/trade (419) vs 2c+ -9.0 (29); but on all Fri-Mon trades no
  difference (-4.3 vs -4.0). Not reliable.
- Time of day (real trades, ET) flipped between periods: night +5.9 -> -6.4, day -4.3 -> +8.8. No pattern.
- V37 real total: 1,425 trades, -$428.78 (Fri-Mon -$414.34, Tue-Thu -$14.44). By ET day: Fri +7.58, Sat -84.67,
  Sun -161.53, Mon -175.72, Tue +0.93, Wed -15.37.

## Grid with the 12 conditions (run Oct 15, all V37 data Fri Oct 9 - Wed Oct 14)
Source: V37 ticks (75 h) + book files (70 h, 90% of ticks matched) + settlements; 1,430 crypto and 957 commodity
markets. Entry = first moment a side's ask is in the band and window (spread <= 5c, >= 30s left), one per
market/side/setting. Bands 30-39 ... 88-95c x windows min 0-3/4-7/8-11/12-14 x 13 conditions (minute is the
window; spread; 30s and 60s direction; speed = 60s range; open lean; spot vs strike; coin and BTC 60s spot move;
cycle = other markets' lean; streak = previous market's winner; book imbalance at the top level; time of day ET),
each in 3 levels. Exits: hold, or sell at 90c. 1,842 settings. Rule: positive in both halves of Fri-Mon,
then judged on Tue 8pm - Wed 9pm (never used to choose).
- Crypto, hold: 55 of 946 settings passed Fri-Mon; 33% of them were positive on the new data vs 16% of all
  settings (avg -1.7c vs -2.9c). Selection helps a little, but most "winning circumstances" failed on new data.
  Sell-at-90: 33 passed, 39% positive (vs 11%). Commodities: 22 passed, 36% positive (vs 23%); sell-90: 3 passed.
- Only consistent setting: crypto side at 88-95c in minutes 0-3, hold. 118 buys in 49 cycles, avg 88.3c, won
  94.9%, +5.6c/trade (+$65.80 at 10 contracts over the 75 h). Fri-Mon halves +3.2 / +5.0, new +8.4 (41, 1 loss).
  By day: Fri +10.9, Sat -9.7, Sun +10.6, Mon +1.0, Tue +11.0, Wed +8.1. Every coin positive. Per cycle: 94% of
  cycles positive, +5.6c +/- 3.0 (about 1.9 SE). Adding a condition (rising, coin/BTC spot with, book with) gave
  100% won on new data but on ~36 buys. A loss costs ~88c, so one extra loss per ~16 trades erases it.
- Per coin (all data, one buy per market; 286 markets checked per coin): BTC 13 qualified, 92.3% won; ETH 25, 96.0%;
  SOL 28, 96.4%; XRP 22, 90.9%; DOGE 30, 96.7%; all 118, 94.9%, +5.6c. Adding "rose 3c+ in 60s" (chosen after
  looking): 112 qualified, 110 won (98.2%), +8.8c. Coin/BTC spot moving our way: same markets, 98.2% / 98.1%.
  Book on our side: 95.3% (no help).
- Dips after the buy (same 118): no winner stayed at 90c+; median low 80c (9c below the buy); 14 winners dipped
  under 50c and still won; most lows came in minutes 0-3 (58) and 4-6 (26). All 6 losers held up and then fell to
  1c in minutes 13-14. A stop-loss would have sold winners and rarely saved losers.
- Dip shape (83 winners that dipped 5c+): straight down 16, 1 bounce 10, 2 bounces 16, 3-4 bounces 7, 5+ bounces 34.
  After a "turned up" buy the price went below the earlier low 44-79% of the time.
- Buy on the rebound (after the 88-95c signal, wait for the bid to fall D below it, buy at the ask when the bid is
  R above its low, hold): dip 5/turn 2: 89 buys, 93.3%, 83.5c, +8.5c (+$75.50 at 10); dip 10/turn 3: 56, 89.3%,
  +10.3c (+$57.50); dip 15/turn 8: 41, 85.4%, +13.0c (+$53.50); dip 20/turn 3: 34, 82.4%, +14.2c (+$48.30).
  Positive in Fri-Mon and Tue-Wed for all 16 combinations. All 6 losers are bought in every version.
  Better per trade, not more in total (only dip 5/turn 2 beat buying at the signal). Sizes chosen after looking.
  Script: analysis/rebound.py.
- Looked good but is not: crypto 30-39c in minutes 8-11 when other coins lean the other way. Per trade
  +6.1 / +2.5 / +2.0, but per cycle it averages 0.0c: it comes from a few cycles where several underdogs won
  together. Cluster effect, not an edge.
- No setting built on the 12 conditions beat the market on its own outside the 88-95c / minutes 0-3 pocket.

## V37 winning vs losing phases (checked Oct 15)
Source: V37 real trades (1,425, Fri Oct 9 - Thu Oct 15; rule 58-67c in minutes 0-3, sell 90c). Phases: running $
total reverses by $20+ (10 contracts) -> new phase; >60 min gap -> new session. 28 WIN phases (+$1,298, 766
trades, 37 h) and 25 LOSS phases (-$1,722, 651 trades, 30 h), alternating through every day, 0.5-5 h long.
Biggest LOSS: Sun 3:15-8:30pm ET, -$253.6 (101 trades). Conditions matched to the grid definitions (95% of trades).
- Entry conditions barely differ (most within 5 points). Largest: rising over 30s 95% of LOSS-phase buys vs 85% of WIN;
  BTC spot moving our way 67% vs 57% (BTC flat 25% vs 36%); spread 1c 88% vs 77% (opposite of the Oct 9 hint).
  Losing phases look like chasing moves that then reverse. No single condition separates the phases.
- Market-wide (all 5 coins per cycle): share of markets whose minute-3 leader lost = 37.6% in LOSS phases vs 23.8% in
  WIN; price crossed 50c 11.1 vs 9.0 times per market; minute-3 leader 15.2c vs 18.0c from 50.
- Known before the trade (previous hour's markets): reversal share low/mid/high (<25% / 25-35% / >35%) -> V37
  -$0.13 / -$0.39 / -$0.81 per trade (10 contracts); Fri-Mon -0.16/-0.37/-0.81, Tue-Wed -0.10/-0.44/-0.79.
  Same order in both periods. Other measures (crossings, swing, spread, lead, coin agreement) not consistent.
- As a switch (replay, c per contract): pause V37 when last hour's reversals >= 30%: -1,458 vs V37 -5,831
  (Fri-Mon -1,367 vs -4,602; Tue-Wed -91 vs -1,229). Flipping instead was worse than pausing (-2,695): the
  inverse side doesn't make money in those hours. Cuts losses, doesn't make V37 profitable. Fine buckets are noisy.
- Compare with V37's own last-60-min P&L switch (Oct 14-15 note): there flip beat pause on new data (+449).

## Reversal signal: start-up and look-back length (checked Oct 15)
- Start-up: the market-wide reversal signal needs no trades -- only the last markets' results and the leader at
  minute 3, which a bot can fetch from Kalshi's market history at start (to verify when building: trade history /
  per-minute candles). V37's own-P&L signal can also be computed from shadow trades instead of real ones.
- Look-back length (V37 replay, pause when the share of reversed minute-3 leaders >= 30% / 40%; every market counts
  equally and a full window is required, so totals differ a little from the earlier 4-cycle average version):
  15 min: paused trades -4.5c vs kept -4.3c (no separation); 30 min: -4.1 vs -4.7; 45 min: -3.7 vs -5.0 / -6.4 vs -3.5;
  60 min: -5.6 vs -3.2 (30%), -8.5 vs -3.0 (40%); 90 min: -5.2 vs -3.7; 2 h: -7.8 vs -2.2 (30%), -8.5 vs -3.6 (40%).
  Shorter look-backs are noise; it takes ~1 h or more of markets to tell a reversing market from bad luck.
  Kept trades are still negative (-2.2 to -3.6c/contract): the switch cuts losses, doesn't make V37 profitable.

## Spot distance / direction cutoffs for V37 (user's idea, checked Oct 15)
Source: V37 real trades in WIN/LOSS phases (1,417), spot vs strike logged by the bot at each buy (own coin, oriented:
+ = spot already on our side), own coin 60s move, BTC's own spot vs its strike and 60s move (both oriented to our side).
- Scale: in minutes 0-3 spot is very close to the strike. Own coin distance median 0.03%, 90% within ~0.1%; BTC median
  0.016%. Cutoffs of 0.5% / 0.65% remove almost nothing (86% of trades kept by both; the rest have no spot value).
- WIN vs LOSS phases have nearly the same distances (own coin median 0.034% vs 0.030%; BTC 0.015% vs 0.018%).
- "Within X% of the strike": never separates the phases (keeps the same share of WIN and LOSS trades) and is worse the
  tighter it is (|d| <= 0.02%: 61.7% won, -$0.54/trade). Close to the strike = coin flip.
- Opposite direction works a little: spot already on our side by >= 0.05%: 361 trades, 72.0% won, +$0.09/trade
  (keeps 29% of WIN-phase vs 22% of LOSS-phase trades); >= 0.1%: 89 trades, 76.4%, +$0.45. But Fri-Mon still
  negative (-0.27 / -0.07), only Tue-Wed positive (+0.58 / +1.18). Not consistent yet.
- Direction: own coin moving our way changes little (-$0.26 vs -$0.39). BTC on our side AND moving our way is the
  worst group (-$0.60/trade, keeps 47% of LOSS vs 39% of WIN trades): chasing BTC's move hurts. BTC against us: $0.00.
- No spot cutoff made V37 profitable in both periods (except 41 trades: on our side >= 0.03% and not moving our way).

## Range-bound vs trending spot (user's idea, checked Oct 15)
Measure: the coin's spot over the 10 min before the buy, efficiency ratio = |net move| / path travelled in 30s steps
(near 0 = up-and-down without getting anywhere, near 1 = steady one-way move); split into equal thirds
(bounds 0.102 / 0.243). Source: V37 ticks (Coinbase spot) + V37 real trades + all crypto markets.
- V37 real trades: range-bound -$0.62/trade (263), in between -$0.66 (278), trending -$0.30 (318). Range-bound is
  not better; trending inconsistent between periods (Fri-Mon -0.03, Tue-Wed -1.00). Range-bound kept 48% WIN-phase trades.
- Buy cheap, sell higher (side at 35-45c in minutes 0-10, sell when the bid is +T, else hold; one per market/side,
  1,746 buys): +10c target reached 77% / 77% / 78% (range-bound / in between / trending) -- about the fair odds
  (40c -> 50c should happen ~80% of the time). Net per trade, +15c target: -5.6 / -5.5 / -3.9 c; +10c: -5.6 / -5.7 / -4.8;
  +20c: -5.3 / -4.5 / -3.8; hold: -4.0 / -2.4 / -2.0. Range-bound and spot near the strike: -6.3c in both periods (worst).
- Conclusion: when spot hovers near the strike, Kalshi prices swing more but the swings are priced; buying the swings
  doesn't pay, and range-bound conditions were if anything worse for both V37 and swing trading.

## Commodities: do they trend more / are they more reliable? (checked Oct 15)
The Oct 7 remark ("commodities trend more ... probably need their own rules") was a hypothesis, not a finding.
Source: V37 ticks + settlements, 932 commodity and 1,395 crypto markets (Fri Oct 9 - Wed Oct 14).
- Inside a market, commodities are choppier, not calmer: price crosses 50c 11.7 times vs 9.9 for crypto; same swing
  (63c vs 65c); spread twice as wide (1.96c vs 1.04c). Spot moves less in % (avg 15-min move 0.114% vs 0.150%), but
  the Kalshi price scales to each asset's volatility, so in cents the markets swing just as much.
- Leader reliability (side leading at minute M, won% minus its mid price): commodity +0.9 / +2.9 / +0.9 / +2.2 at
  minutes 3 / 5 / 8 / 11 (crypto +2.7 / +1.2 / +0.1 / -0.9). Buying at the ask (+1c over mid) plus the fee (~1-2c)
  takes all of it: no profit.
- Trend across markets (same direction as the previous 15-min market, 50% = none): commodities 49.0% (969 pairs),
  crypto 49.2%. Copper 55.1%, WTI 53.6%, gold 46.8%, silver 44.9%, platinum 45.6% (n 156-181, none significant).
- The crypto winner (88-95c in minutes 0-3) on commodities: 47 buys, 91.5% won, +2.2c; Fri-Mon +10.7 (26) but
  Tue-Wed -8.4 (21). Not confirmed.
- Grid (earlier): 22 commodity settings passed Fri-Mon, 36% positive on new data vs 23% for all. Commodities still
  have no edge in our data.

## Full grid: 5c bands x 30-second windows x 13 conditions x 22 exits (run Oct 15, user's spec)
Bands 10-14 ... 95-99c (18), windows 0:00-0:30 ... 14:30-15:00 (30), conditions as before (3 levels each + none),
exits hold + sell targets +10 ... +50c in 2c steps (a target above 99c falls back to hold). First moment per
market/side, buy at the ask, spread <= 5c, taker fees. All V37 data (1,430 crypto + 957 commodity markets).
751,652 settings. Rule: positive in both Fri-Mon halves, then judged on Tue 8pm - Wed 9pm.
Saved: data/grid/v37_biggrid_sums.csv.gz (summed buys / wins / net per exit for every cell and period).
- Crypto (>= 20 buys per half, >= 10 new): 22,667 passed Fri-Mon; 44% positive on new data vs 25% for any setting;
  9,994 positive in all three vs 3,592 expected by chance (neighbouring settings share markets, so this overstates).
  Passers still averaged -1.6c/trade on new data (all settings -3.4c). Stricter (>= 40 / 20): 40% vs 24%.
- Commodities: >= 20/10: 30% vs 13%, passers -2.65c; >= 40/20: 27 passed, 0 positive on new data.
- Where crypto survivors cluster (share profitable in all three periods): by band 0.3-1.1% for 10-54c (at or below
  chance), 2-4% for 55-84c, 10.6% 85-89c, 15.2% 90-94c, 34% 95-99c; by minute highest at 13-14 (10-12%) and 2-3 / 7-8;
  hold 5.6% vs targets 2.1% (+10c) rising to 4.6% (+50c). Buying cheap sides never held up.
- The zones as simple rules (first entry per market, crypto), hold, c/trade [Fri-Mon A / B / Tue-Wed]:
  85-94c min 2-4: 441 buys, 87.5% won, +0.8 [+1.7 / -1.6 / +2.5]; 95-99c min 7-10: 637, 97.2%, +0.8 [+1.5 / +0.2 / +0.6];
  70-94c min 13-14: 660, 85.0%, +0.7 [+1.9 / -1.5 / +2.0]; 85-94c min 13-14: 655, 90.8%, +0.9 [-0.8 / +3.3 / +0.3].
  All about break-even (+$30-62 per 75 h at 10 contracts). Sell targets never beat holding; +10c was always worst.
- The narrower 88-95c min 0-3 setting (+5.6c, 118 buys) is still the best single result, but widened to 85-94c min 0-4
  it drops to +0.7c (450 buys): part of its +5.6c is likely luck of the exact cut.

## The final cross of 90c (user's idea, checked Oct 15)
Source: all V37 ticks (Oct 9-14). Cross = the side's ask rises from < 90c to 90-95c (spread <= 5c, > 5 s left);
"final" = the side's mid never goes back below 90c before the close. 13 conditions + time left + cross number
attached; singles and pairs tested (Fri-Mon halves, then Tue-Wed).
- 5,354 crypto / 2,768 commodity crosses. Only 13% are final in both classes, yet 90.1% / 90.2% of crossing sides win
  (bought at 90.4c: -1.2c / -1.3c per contract). Final crosses are mostly at the very end (crypto minute 14: 43%).
- No condition makes a cross "definitely final": the highest final rates were ~40% (crypto: spot close to the strike
  & fast market / & mixed cycle). Not needed anyway -- what pays is the side winning, final or not.
- What does pay (crypto, first qualifying cross per market): spot already past the strike by >= 1.5 "normal moves"
  (z = distance / (15-min volatility x sqrt(time left))): 173 buys, 96.5% won at 90.5c, +5.0c/contract;
  Fri-Mon A +5.4 (63), B +5.8 (76), Tue-Wed +2.6 (34); every day positive (Sat +4.0, Sun +4.2, Wed +2.3 lowest);
  every coin (BTC +2.4 ... XRP +8.5); minutes 0-3 +8.8 (40), 4-10 +4.0 (85), 11-14 +3.9 (48).
  >= 1.0: 769 buys, -0.2c (Tue-Wed -3.4); >= 2.0: 40 buys, +3.2c. About 2.3 buys/hour.
- Same idea as the spot-model gap: Coinbase says the side is ~97% likely, Kalshi charges 90c.
- Commodities: no spot price, so this can't be used there; 6 of 264 commodity settings passed Fri-Mon.

## Stop-loss for commodity early favs (user's question, checked Oct 16)
Source: V37 ticks; V38 commodity rule (85-95c in the first 8 minutes, first per market): 433 trades, 86.4% won, hold -0.41c.
Stops sell at the actual bid when it first reaches the level (gaps included) + fee.
- Winners dip deep: lowest bid of winners -- median 77c, 25th pct 63c, 10th pct 40c, 5th pct 26c, 1st pct 10c.
- Every stop was worse than holding overall: 80c any time -3.2c (249 winners dumped / 59 losers saved), 50c -1.1c (55/59),
  20c -0.3c (12/55). Last-3-min-only: 50c +0.07 (32/59, but Fri-Mon -0.2), 70c -0.03; last-2-min-only all -0.5 to -0.7.
  None better than holding in both periods. A dumped winner costs ~80c, a saved loser recovers <= the stop price.
- Conclusion: no stop for commodity early favs; fix the entry (or drop commodities) if they keep losing in V38.

## Rebound on commodity early favs (checked Oct 16, added to V38 at the user's request)
Source: V37 ticks; after the V38 commodity early-fav buy (85-95c, first 8 min; 433 markets, -0.41c, Fri-Mon +1.83 / Tue-Wed -2.94).
- dip 5 / turn 2: 326 buys, 82.2% won at 81.4c, -0.70c (Fri-Mon +1.80, Tue-Wed -3.75); 5/8: -0.25c; 8/8: -0.07c;
  20/3: 162 buys, +0.65c (+5.93 / -5.04). No setting positive in both periods -- every one up Fri-Mon, down Tue-Wed,
  like the first buy. Unlike crypto (+8.5c), the commodity rebound adds no edge; it is a second break-even buy.
- Added to V38 anyway for data collection, same settings as crypto (5c dip, 2c turn), tracked as its own strategy.

## Dip-then-rebound as a "will win" signal (user's question, checked Oct 16)
Source: V37 ticks, early-fav trades (crypto 118: 112 won / 6 lost; commodity 433: 374 / 59). Signal = side's bid dips D below
the buy price, then rises R off its low (cents, or a share of the dip recovered); first such moment per trade, >= 30 s left;
compare the win rate then with the ask then.
- Crypto: every combination won more than its price, in both periods. E.g. dip 10 & +10c: 56 cases, 89.3% won at 77.7c,
  +10.1c (Fri-Mon +6.5 / Tue-Wed +15.2); dip 15 & 50% of the dip back: 38 cases, 3 lost, 92.1% at 75.1c, +15.1c
  (+13.8 / +16.7); dip 20 & 75% back: 29 cases, 1 lost, 96.6% at 80.6c, +14.4c. Bigger rebounds filter out losers
  (6 losers at +5c -> 2 at +30c; 1 at 75% of the dip). BUT all rows are the same ~50 dipping winners and the same 6 losers:
  tiny sample. Total profit is lower than V38's 5c/2c rebound (89 buys, +8.5c, +$75 at 10) because fewer trades qualify.
- Commodities: win rate = price at every combination (edge -2.6 to +5.7, mostly 0 to +2); every row up Fri-Mon,
  down Tue-Wed. Bigger rebounds also filter losers (57 -> 27) but the price is higher by the same amount. No signal.
- Candidate for crypto once V38 has more data: require a bigger rebound (e.g. 50% of the dip back) for the second buy.

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
11. Session switch: flip to the inverse (or pause) when V37's last-60-min P&L < -200c/contract; re-test on the
    full week with thresholds fixed in advance.
12. Early near-certain favourites: crypto side at 88-95c in minutes 0-3, hold (grid Oct 15). Needs more data:
    118 buys so far; check weekend vs weekday (Sat -9.7) and whether adding 60s rising / spot-with helps.
13. Spot-confirmed 90c crosses: crypto side crosses 90c while spot is >= 1.5 normal moves past the strike
    (173 buys, 96.5%, +5.0c). Re-test on V38 data (ticks include spot); candidate for V39.
14. Carried over: time exits, Kalshi and spot momentum filters, per-coin results (SOL negative before),
   04:00-08:00 UTC weak in V36 crypto, after-a-loss-on-the-same-coin weaker in V36.

## Data notes
- Old tick/book/prints logs (Sep 18 - Oct 8) were deleted on Oct 9 to free Drive space; the next grid
  runs on V37 data (plus any remaining .csv.gz and trades/settlements files).
- Kalshi sent the V34-V36 bots ticks for every Kalshi market; V37 filters to its own 11 markets.
