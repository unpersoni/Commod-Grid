# MM15Min V4 — resting-bid strategies, dry run by default, live path built in (Oct 20 2026)

`mm15min_v4.py` is the notebook's bot-code cell without the API key (`KEY_ID = "__KEY_ID__"`).
`history_cell.py` and `switches_cell.py` are the notebook's HISTORY and SWITCHES cells.
V4 is built on V3: its order path, safety limits, data collection, switches and 4-line status.

## Strategies (entry points checked in analysis/mm_fav_entry.py, Oct 20)
| | Crypto | Commodities |
|---|---|---|
| `fav` favourite | 85-98c, **first 5 minutes only** (V3: whole market) | 85-98c, whole market |
| `lean` leader | **75-84c** (V3: 75-90c), first 5 minutes only | 75-84c, whole market |

- **Crypto fav:** the profit is in the first 5 minutes, on both datasets:
  - first 5 minutes: queue model +12.3c per contract (both halves); unseen V37 days, worst case, +2.5c (4 of 6 days up);
  - whole market: +1.3c and -2.0c;
  - after minute 5: negative on both.
- **Crypto lean:** at 75-84c it no longer overlaps fav, and does the same as 75-90c (+7.7c vs +7.9c).
- **Commodities:** unchanged; positive everywhere.

Up to 5 filled orders per side per market per strategy, nothing new in the last 30 s, held to settlement.

## Switches
`DRY_RUN` (True by default), `FAV_CRYPTO`, `FAV_COMMODITIES`, `LEAN_CRYPTO`, `LEAN_COMMODITIES`, `SKIP_MARKETS`,
`CONTRACTS` (10). There is no daily loss stop: contracts and limits will be set before live trading.

## Live path (DRY_RUN = False)
Only the switched-on trade types go live. The others keep running in dry run, on their own rate limit.

At start:
- a LIVE banner and a 10-second countdown;
- the balance per shard, with shard 0's money moved to shard 2 if it holds under $25 (re-checked every 30 s);
- leftover resting orders cancelled, **in the 15-minute markets only** (other markets are never touched);
- an order group: Kalshi cancels everything if 300+ contracts fill within 15 s.

Orders:
- Post-only resting bids, each expiring 25 s before the close on Kalshi's side.
- Fills come from Kalshi's private fill feed. Market trades never fake-fill a live order.
- Cancelled when outbid, at the time stop, at the close, on the kill switch and at stop.
- An order with no clear answer from Kalshi is looked up by its client id, never sent twice.
- The first 5 raw answers to creates and the first fills are printed, to check the field names.
- Live orders are logged to `mm15min_v4_data/orders_live_*`, dry-run ones to `orders_*`.

Log:
- `🟢` marks live trade types, and `🟢 LIVE` the FILL and SETTLED lines.
- An error in one pass of the main loop is printed, and the bot keeps running.

## HISTORY cell
Per trade type, over the last `HISTORY_CYCLES` cycles found in `mm15min_v4_markets.csv` and V3's file:
- markets, contracts, % won, net, cents per contract and net with maker fees;
- the streak, and the results market by market.

Cycles when the bot wasn't running are missing.

## Drive files
- `mm15min_v4_fills.csv` and `mm15min_v4_markets.csv` (with `switched_on` and `live` columns), and `mm15min_v4_settlements.csv`.
- `mm15min_v4_data/`: `orders_`, `orders_live_`, `ticks_` and `flow_`, hourly, gzipped.

## Tests
`test_mm15min_v4.py` covers:
- V3's dry run, demo, mode, lean and switch tests, updated for the new windows and the removed daily stop.
- The live path against a fake Kalshi at the production address:
  - only switched-on trade types are sent, with the order group and post-only;
  - Kalshi's fills are counted, and market trades don't fill live orders;
  - a lost answer is found by client id and not re-sent;
  - leftovers are cancelled only in the 15-minute markets;
  - the funds transfer;
  - cancels at the close;
  - the separate live and dry order logs.
- The HISTORY cell, reading V4 and V3 files together.
