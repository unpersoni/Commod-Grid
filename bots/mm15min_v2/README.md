# MM15Min V2 — market maker with a dry-run order path (Oct 19 2026)

`mm15min_v2.py` is the notebook's main cell without the API key (`KEY_ID = "__KEY_ID__"`).
Strategies are unchanged from V1 (`fav` 85-98c bids, `mm` two-sided 3-97c with a 30-contract imbalance cap).

## Modes (`MODE` in the settings)
| Mode | Orders | Fills | Purpose |
|---|---|---|---|
| `paper` | simulated, order code not used | simulated | V1 behaviour |
| `dry_run` (default) | exact Kalshi V2 requests built, signed, rate-limited, safety-checked and logged — **never sent** | simulated (an order joins the line once its request is ready) | prove the order code and measure speed |
| `demo` | sent to Kalshi's demo site (play money; needs a demo account + key) | Kalshi's private `fill` channel | check the request format against Kalshi |
| `live` | locked in V2 | | |

Dry run also makes read-only calls with the real key: Kalshi round trip (`/exchange/status`, every minute),
balance per shard (`/portfolio/balance`), resting orders (`/portfolio/orders`).

## Speed (carried over from Commod15min V21-V25)
Decisions on every price/trade event; orders sent on 16 worker threads; one kept-alive HTTPS session (pool sized for
the workers); no balance lookups before orders; per-order timing logged (decision -> request ready, build + sign,
Kalshi round trip). A strict rate limiter keeps every 1-second span at or under `MAX_WRITES_PER_SEC` (10 = Basic tier).

## Safety
Post-only orders · orders expire on Kalshi 25 s before the close · max 60 resting orders · max 220 contracts per market ·
no new orders after a -$100 UTC day · kill switch: create `STOP_MM15MIN.txt` in My Drive (cancels everything) ·
cancel-all at start (demo/live) and at stop · demo/live: Kalshi order group (cancels everything if 300+ contracts fill
within 15 s) · no action when a market's close time is unknown.

## Request format (V2, from the Commod15min V2x bots that traded live)
`POST /portfolio/events/orders` `{"ticker", "client_order_id", "side": "bid"|"ask", "count": "10.00", "price": "0.8800",
"time_in_force": "good_till_canceled", "post_only": true, "self_trade_prevention_type": "taker_at_cross",
"exchange_index": -1, "expiration_time": "...Z", "order_group_id"}` — buying YES at p = bid at p; buying NO at p = ask at 100-p.
Cancel: `DELETE /portfolio/events/orders/{order_id}`.

## Not yet confirmed against Kalshi (the demo run checks these; the bot logs and falls back)
- the `expiration_time` format (on a rejection the bot drops the field and keeps going),
- the order-group create path and field (`/portfolio/order_groups/create`, `contracts_limit`),
- the private `fill` message fields (the first 20 fills are printed raw),
- whether 15-minute markets carry a maker fee, and which balance shard they use (shard 2 per the V2x bots).

## Logs
`mm15min_v2_fills.csv`, `mm15min_v2_markets.csv`, `mm15min_v2_settlements.csv`,
`mm15min_v2_data/orders_YYYY-MM-DD_HH.csv.gz` (every order action, the exact request, timing).

`test_mm15min_v2.py`: dry run end to end (nothing sent, exact bodies, rate limit, caps, kill switch, daily loss),
demo path against a fake Kalshi, and the mode guards.
