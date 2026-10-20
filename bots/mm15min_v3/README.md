# MM15Min V3 — resting-bid strategies with switches, dry run (Oct 20 2026)

`mm15min_v3.py` is the notebook's bot-code cell without the API key (`KEY_ID = "__KEY_ID__"`).
`switches_cell.py` is the notebook's last cell: the only cell you change. Running it starts the bot.
V3 is built on MM15Min V2. It keeps V2's order path (dry run / demo), safety limits and Drive data collection.

## Strategies
Every strategy rests 10-contract post-only bids at the best bid and holds what fills to settlement. Each one
fills at most 5 orders per side per market, and nothing new is placed in the last 30 s.

| | Crypto | Commodities |
|---|---|---|
| `fav` favourite | 85-98c, whole market | 85-98c, whole market |
| `lean` leader (new) | 75-90c, first 5 minutes only | 75-84c, whole market |

`lean` comes from `analysis/mm_tune.py` and `mm_tune_v37.py` (FINDINGS_log.md, Oct 20).
- **Backtest day:** +7.9c per contract in crypto, +5.2c in commodities.
- **6 unseen days, worst case:** about break-even.

V1/V2's two-sided `mm` (both sides at 3-97c) was removed on Oct 20. It lost about 1c per contract in every
test and in V2's dry run (47,000 contracts, 49.7% won). The tick and trade-flow files still record the markets,
so any new idea can be simulated from them.

## Switches
```python
DRY_RUN = True              # must stay True in V3: live trading for resting orders is not built yet
FAV_CRYPTO        = True    # True = would be live (★ON), False = dry run only
FAV_COMMODITIES   = True
LEAN_CRYPTO       = False
LEAN_COMMODITIES  = False
SKIP_MARKETS = []           # markets never live, e.g. ["WTI", "DOGE"]
CONTRACTS = 10
DAILY_LOSS_LIMIT_USD = 20
main()
```
Every strategy is always followed in dry run. The switches mark which ones would be live (★ON). The daily loss
stop counts only the switched-on strategies. In dry run it is only reported. A typo stops the bot before it
starts, with a message saying what's wrong. That covers:
- `"False"` written in quotes;
- an unknown market name;
- `DRY_RUN = False`.

## Order rate
The switched-on strategies share Kalshi's 10 order actions per second, as they would live. Each switched-off
strategy is timed on its own limit, as if it were live alone, so the dry-run strategies never delay the
switched-on ones. In V2, `mm`'s ~21,000 actions shared one limit with `fav` and made orders wait up to 8 s.

## Log
Every 15 minutes there is one line each for Favorite Crypto, Favorite Commodities, Lean Crypto and Lean Commodities:
- `★ON` or `off`, and the price band and window;
- markets, fills, contracts and % won;
- net, cents per contract, and the net if maker fees applied;
- the current streak (`W3` = 3 markets in a row that made money) and the last 10 markets.

There are no per-market rows. Fills and SETTLED lines carry the strategy and `★ON` when it is switched on. The last
line shows today's net for the switched-on strategies (with its lowest point) and for all strategies.

## Drive files
- `mm15min_v3_fills.csv`, `mm15min_v3_markets.csv` (both with a `switched_on` column).
- `mm15min_v3_settlements.csv`.
- `mm15min_v3_data/orders_`, `ticks_` and `flow_` files (hourly, gzipped).

## Tests
`test_mm15min_v3.py` covers:
- V2's dry run, demo and mode-guard tests;
- `lean`: it only bids crypto in the first 5 minutes and commodities at 75-84c;
- the switches, the `switched_on` column, `SKIP_MARKETS`, and the four status lines with streaks;
- the switch typo checks.
