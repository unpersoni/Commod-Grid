# MM15Min V1 — paper market maker (Oct 17 2026)

`mm15min_v1.py` is the notebook's main cell without the API key (`KEY_ID = "__KEY_ID__"`).
The notebook given to the user has the key filled in; it is never committed.

- Two strategies on all 11 markets, totals kept separately for crypto and commodities:
  `fav` rests 10-contract bids on any side whose best bid is 85-98c; `mm` rests bids on both sides at 3-97c,
  stopping a side once it is 30 contracts heavier than the other. From the market's start to 30 s before the close,
  up to 5 filled orders per side per market, held to settlement.
- Paper fills: the order waits behind the contracts already resting at its price (live order book); live trades
  selling into that price use up the line, then fill us; a sale below our price or the other side's ask reaching our
  bid fills us at once. No taker fee; Kalshi's maker fee is logged per fill (`maker_fee_c`) but not deducted
  unless `MAKER_FEE_ON = True`.
- Drive logs: `mm15min_v1_fills.csv` (per fill), `mm15min_v1_markets.csv` (per market and strategy),
  `mm15min_v1_settlements.csv`. No tick files.
- `test_mm15min_v1.py` runs the bot against a scripted feed/book (needs `requests`, `cryptography`, `websocket-client`).
- Background: FINDINGS_log.md, "Market making" section; backtests in analysis/mm_*.py.
