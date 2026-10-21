# LIP Maker V1 — a two-sided maker paid by Kalshi's liquidity rewards (Oct 21 2026)

`lip_maker_v1.py` is the notebook's bot-code cell without the API key. `switches_cell.py` is its SWITCHES cell.
The notebook given to the user has the key filled in; it is never committed.

## Why this bot
The taker strategies and the one-sided "market maker" (MM V1-V4) failed. Outside research (FINDINGS_log.md, Oct 21)
found that on Kalshi, makers as a group lose too (about -10% vs -32% for takers), and the professionals profit from
speed, special terms and rewards. The one advantage a small, slow bot can get is Kalshi's **Liquidity Incentive
Program**: a reward pool per market, paid for resting orders near the best price whether or not they fill.
`tools/lip_programs_cell.py` and `tools/lip_finder_cell.py` read Kalshi's live programs and order books: on Oct 21
there were 6,640 programs; the AAA daily gas price markets (KXAAAGASD..., by state) paid about $6.26 an hour each
with little competition (about 23-33% of the pool for 100 contracts a side).

## How it works
- Every 10 minutes: reads Kalshi's active liquidity programs in `SERIES`, the market's close time and order book,
  and picks the `MAX_MARKETS` best by estimated reward within `BUDGET_USD`.
- Every 3 seconds per market: keeps one YES bid and one NO bid (post-only, `CONTRACTS` each) at the **reference
  price** (walking down from the best bid, the first price where the size reaches 1/5 of the program's target size;
  orders there score in full). A YES + NO pair pays $1.00, so fills on both sides lock in the gap between the bids.
- Reward estimate: each side scored against half the pool (score = size x discount^(cents below the reference)).
  Kalshi's real payout is on the Rewards page and in Account -> Activity -> Credits.

## Safety
Post-only · `MAX_NET` contracts net per market (that side stops quoting) · no orders in the last 60 minutes or
outside the reward period · a 4c move within 60 s pulls both sides for 5 minutes · sides quoted only at 5-95c ·
`MAX_LOSS_USD`: once fills are down that much (marked at the best bids) every order is cancelled for the rest of
the run · kill switch `STOP_LIPMM.txt` on Drive · a cancel is only forgotten once Kalshi confirms it (else retried) ·
live: Kalshi-side expiry, an order group (200 contracts / 15 s), leftover orders in `SERIES` cancelled at start
(nothing else is touched), positions checked against Kalshi every 2 minutes, 10-second countdown.

## Not yet confirmed against Kalshi
- The exact payout rules beyond the help center's description (half the pool per side is an assumption).
- `GET /portfolio/orders/{id}` fields for fills (`fill_count` / `remaining_count`, `_fp` forms handled).
- The V2 cancel path; the older `DELETE /portfolio/orders/{id}` is tried when it is not recognised.

`test_lip_maker_v1.py` runs the bot against a fake Kalshi: dry run (picking, reference prices, simulated fills,
net limit, fast-move pull, stop) and the live path (exact requests, fills from order state, moving an order,
an unconfirmed cancel, positions check, loss limit), plus the switch checks.
