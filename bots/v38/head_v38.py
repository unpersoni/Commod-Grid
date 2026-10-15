# ============================================================
# Commod15min V38 "early favs" — PAPER ONLY (no order is ever sent to Kalshi)
#
# STRATEGY: buy the side that is already a strong favourite early in the market, hold it to settlement.
#   - CRYPTO (BTC, ETH, SOL, XRP, DOGE): a side whose ask is 88-95c in the first 3 minutes.
#     V37 data: 118 such markets, 94.9% won, +5.6c per contract after fees.
#   - COMMODITIES (GOLD, SILVER, WTI, COPPER, PLATINUM, PALLADIUM): 85-95c in the first 8 minutes.
#     Chosen for volume (about 45% of markets qualify); about break-even so far — this run tests it.
#   - 10 contracts at the ask, one trade per market, no buy if the spread is wider than 5c.
#   - No sells, no stops: every position is held to $1 or $0.
#
# REVERSAL GUARD (information only — trades are still taken):
#   share of the last hour's markets (same class) whose leader at 3 minutes in ended up losing.
#   35% or more = choppy market. Read from Kalshi's history at start-up, then kept up to date live.
#   Each trade is labelled guard on/off; the status line shows totals for guard-off trades separately.
#
# FILLS (paper): buys at the ASK, Kalshi taker fee: 0.07 x contracts x P x (1-P), rounded up to the cent.
#
# LOGS (Google Drive) — kept small (about 10 MB a day):
#   commod15min_v38_trades.csv        one row per position (+ entry conditions and guard)
#   commod15min_v38_settlements.csv   Kalshi's official result for every market (all 11)
#   commod15min_v38_data/             ticks_YYYY-MM-DD_HH: every price change + top-of-book size
#                                     flow_YYYY-MM-DD_HH:  trades per market per 15 s (buyers of YES vs NO)
#                                     one file per hour (UTC), compressed to .csv.gz when the hour ends
#   A status line every 15 minutes shows results and Drive free space; a loud warning below 200 MB.
# ============================================================
