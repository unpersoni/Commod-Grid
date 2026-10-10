# Saved bot data (copied Oct 10 2026, after the Drive logs were deleted)

All paper trading on Kalshi 15-minute markets. Times in the files are UTC.

## trades/ — one row per position
| File | Bot | Rules | Period |
|---|---|---|---|
| commod15min_v34b_trades.csv | V34b | buy 47-53c, sell 72c, minute 1-10, repeat buys | Oct 1-2 |
| commod15min_v34c_trades.csv | V34c | buy 40-65c band, sell 65c | Oct 1-2 |
| commod15min_v35a_trades.csv | V35a | crypto only, buy 48-52c, sell 72c, one trade per market; 7 rows from an earlier run in the old 17-column format removed | Oct 2-3 |
| commod15min_v36a_trades.csv | V36a | all 11 markets, buy 48-52c, sell 72c, minute 0-9 skipping 2-3 | Oct 3-9 |
| commod15min_v36b_trades.csv | V36b (first version) | buy 37-42c, sell 62c | Oct 3-4 |
| commod15min_v36b2_trades.csv | V36b (second version) | buy 38-42c, sell 55c, minute 0-12 | Oct 4-9 |
| commod15min_v37_trades_2026-10-10.csv | V37 | crypto only, buy 58-67c in minute 0-3, sell 90c | Oct 9-10 (snapshot) |

V35 onwards include the entry factor columns (minute, spread, 30/60s price direction, speed, how the
market opened, spot vs strike, spot and BTC moves, open positions, cycle, streak; V36+ also book depth).

## settlements/ — Kalshi's official result per market
- settlements_v33_v34b_v34c.csv — combined, de-duplicated (889 markets, Sep 30 - Oct 2), with a `cycle` column.
- commod15min_v36a_settlements.csv, commod15min_v36b2_settlements.csv — Oct 3-9.

## grid/ — results of v36_grid_search on 1,048 markets (Sep 29 - Oct 8)
- v36_grid.csv — every setting (band x target x window x filter x exit), per half and total.
- v36_entries.csv.gz — every simulated entry (window 0-12 and 6-13, no filter) with its factors and its
  outcome at every sell target and time exit. This is the old tick data in processed form.

## ticks/
- commod15min_v35b_ticks.csv.gz — one day of prices (Oct 2-3); the only raw tick file left.
