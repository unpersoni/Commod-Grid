# Commod15min V38b V2 — open favourite with a dry-run quick-order path (Oct 19 2026)

`commod15min_v38b_v2.py` is the notebook's main cell without the API key (`KEY_ID = "__KEY_ID__"`).
Strategy unchanged from V38b: in the first 60 s of each market, buy 10 of the side whose ask is 56-95c (spread <= 5c),
hold to settlement; crypto and commodities with separate totals and per-price-band lines. No files are written.

## Modes (`MODE`)
| Mode | Buy | Fill |
|---|---|---|
| `paper` | not built (V38b behaviour) | at the ask seen |
| `dry_run` (default) | exact Kalshi V2 fill-or-kill request built + signed + rate-limited + timed, **never sent** | simulated at the ask when the request is ready; killed if that ask is past the limit (then retried while still in the first 60 s) |
| `demo` | sent to Kalshi's demo site (play money; demo account + key) | Kalshi's response (`fill_count`, `average_fill_price`, `average_fee_paid`) |
| `live` | locked | |

Request: `POST /portfolio/events/orders` `{"ticker", "client_order_id", "side": "bid"|"ask", "count": "10.00",
"price": limit, "time_in_force": "fill_or_kill", "self_trade_prevention_type": "taker_at_cross", "exchange_index": -1}`;
buying NO at <= L is a YES ask at 100-L (prices in the request and the fill are YES prices).

## Speed (Commod15min V21-V25 lessons)
The buy fires on the price update itself (and the main loop re-checks every 0.5 s); one sender thread per market; one
kept-alive HTTPS session warmed up by the start-up checks; no balance lookup before the buy; band checked on the ask;
limit = ask seen + `MAX_SLIPPAGE_C` (2c), never 99c (the V23 fix); each order logs decision -> request ready, build + sign,
round trip and slippage; status shows medians / 95th percentiles and the Kalshi round trip measured every minute.

## Safety
One buy per market and one order in flight per market · nothing within 2 s of the close or when the close time is unknown ·
no new buys after a -$20 UTC day (no cap on gains; dry run only reports it) · kill switch: `STOP_V38B.txt` in My Drive ·
at most 10 order actions in any second. Read-only start-up checks: Kalshi round trip, balance per shard, resting orders.

## Still to confirm in demo
The fill fields in Kalshi's response (the first 5 raw responses are printed), the fee field, whether a NO buy's
`average_fill_price` is a YES price (assumed), and the balance shard the 15-minute markets use (shard 2 per the V2x bots).

`test_v38b_v2.py`: dry run (exact bodies, nothing sent, a kill and retry, slippage, rate limit, daily loss, kill switch,
no files), demo path against a fake Kalshi, mode guards.
