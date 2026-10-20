# Commod15min V38b V3 — open favourite, dry run or LIVE (Oct 19 2026)

`commod15min_v38b_v3.py` is the notebook's bot-code cell without the API key (`KEY_ID = "__KEY_ID__"`).
`switches_cell.py` is the notebook's last cell: the only cell you change. Running it starts the bot.

Strategy (V38b): in the first 60 s of each market, buy 10 contracts of the side whose ask is 56-95c (spread <= 5c),
hold to settlement. Crypto and commodity totals are kept separate, with a line per opening-price band. No files are written.

## The SWITCHES cell
```python
DRY_RUN = True              # True = nothing is sent to Kalshi. False = LIVE, REAL MONEY (10-second countdown first)
TRADE_CRYPTO      = True    # BTC, ETH, SOL, XRP, DOGE
TRADE_COMMODITIES = True    # GOLD, SILVER, WTI, COPPER, PLATINUM, PALLADIUM
SKIP_MARKETS = []           # single markets off, by name
CONTRACTS = 10
DAILY_LOSS_LIMIT_USD = 50
main()
```
To change a switch while the bot runs, stop the cell (■), edit it, and run the same cell again. The bot-code cell does
not need re-running. If the switches cell is never run, the code defaults to dry run.

Typos stop the bot before it starts, with a message. Examples:
- `DRY_RUN = "False"`: written in quotes.
- `SKIP_MARKETS = ["GLD"]`: an unknown name.
- `SKIP_MARKETS = "WTI"`: not a list.
- Every market switched off.
- Live without a key.

### Live and dry-run markets in one run
A market that is switched off is still followed in **dry run**: the same opening-buy rule, simulated fills, nothing sent.
So the log always shows all 11 markets. With `DRY_RUN = False`, the switched-on markets trade live.
Every 15 minutes the status shows crypto and commodities, each split into two sections:
- `🟢 LIVE`: real orders, printed in bold green.
- `⚪ DRY RUN`: followed, nothing sent, printed in the normal colour.

Each section shows the totals and the price bands. For each market it shows:
- the win rate and net;
- the current streak (`W3` = 3 wins in a row, `L2` = 2 losses);
- the last 10 results (e.g. `WWLWWWWLWW`).

BUY and SETTLED lines carry the same tag. The daily loss stop counts **live** trades only. When it hits, or the
kill switch is on, the live markets carry on in dry run, so their results keep showing. `COLORS = False` in the code
cell turns the green off, if Colab shows codes like `[1;32m`. Dry-run fills assume all 10 contracts are there at the
ask. The first live run showed thin books at the open: an ETH ask seen at 66c was filled at 74c. Live fills can be a
few cents worse than the dry-run numbers.

### Turning off individual markets
Put the names in `SKIP_MARKETS`. Capitals don't matter. The names are `BTC ETH SOL XRP DOGE GOLD SILVER WTI COPPER PLATINUM PALLADIUM`.

| You want | Set |
|---|---|
| everything live | `TRADE_CRYPTO = True`, `TRADE_COMMODITIES = True`, `SKIP_MARKETS = []` |
| crypto live, commodities dry run | `TRADE_COMMODITIES = False` |
| commodities live, crypto dry run | `TRADE_CRYPTO = False` |
| crypto without DOGE | `TRADE_COMMODITIES = False`, `SKIP_MARKETS = ["DOGE"]` |
| everything but WTI and PALLADIUM | `SKIP_MARKETS = ["WTI", "PALLADIUM"]` |
| BTC and ETH only | `TRADE_COMMODITIES = False`, `SKIP_MARKETS = ["SOL", "XRP", "DOGE"]` |

The first lines of the log confirm the setting, e.g. `trading: BTC, ETH, SOL, XRP, DOGE`.

## Orders (live and dry run use the same code path)
Kalshi V2 `POST /portfolio/events/orders`:

`{"ticker", "client_order_id", "side": "bid"|"ask", "count": "10.00", "price", "time_in_force": "fill_or_kill", "self_trade_prevention_type": "taker_at_cross", "exchange_index": -1}`

- The limit is the ask seen + 2c, never above 99c. Buying NO at <= L is sent as a YES ask at 100-L.
- A killed order is retried on the next price update while still in the first 60 s, at most 10 tries per market.
- Dry run builds, signs, rate-limits and times the exact request, and never sends it. The fill is simulated at the ask when the request is ready.

## Live safety
- **Countdown:** live trading shows a banner and a 10-second countdown first.
- **Start-up checks:**
  - Kalshi round trip.
  - Balance per shard.
  - Resting orders.
  - Markets already held on the account. Those are never bought again, e.g. after a restart.
- **Funds:** if the trading shard (2) holds under $25 and shard 0 has money, everything on shard 0 is moved there.
  - This happens at start and every 30 s, never in an order's path.
  - The amount is in centicents, the tested fix from the V2x bots.
  - Arrival is checked by re-reading the balance.
  - Dry run only reports it.
- **Fill reading:**
  - Fields read: `fill_count` / `fill_count_fp`, `average_fill_price` / `_dollars`, or the fill cost.
  - The price is read as YES or NO, whichever fits under the limit and is nearest the ask seen. Ties go to the expected reading.
  - A partial fill is kept and that market is done.
  - The first 5 raw answers from Kalshi are printed.
- **No clear answer** (timeout or 5xx):
  - The order is looked up by its `client_order_id` and never re-sent.
  - If it isn't found, the market is marked UNKNOWN and skipped.
- **Rejections:** a rejection (e.g. insufficient balance) stops that market. A 429 or a kill-type error doesn't.
- **Close time:** no action within 2 s of the close or when the close time is unknown.
- **Daily loss stop:**
  - Checked when every trade of a 15-minute round has settled.
  - If today's (UTC) net is below -$50, no new buys until midnight UTC. Gains have no cap.
  - The status shows the day's net and its lowest point.
  - Dry run only reports it.
- **Kill switch:** create `STOP_V38B.txt` in My Drive. Positions already bought ride to settlement.
- **Rate limit:** at most 10 order actions in any second.
- **Bought positions:** stopping the cell never touches positions already bought. They settle on Kalshi.

## Tests
`test_v38b3.py` (run with a Python that has `cryptography` and `requests`) covers five areas:
1. **Dry run:** nothing sent, the skipped market, a kill and retry, the kill switch, no files.
2. **Live path against a fake Kalshi at the production address:**
   - Signed headers.
   - A YES fill, and a NO fill read from a YES price.
   - A timeout that is found by lookup and not re-sent.
   - A timeout that is not found: UNKNOWN.
   - A 400 rejection that is not retried.
   - A kill, then a fill.
   - A held market that is skipped.
   - The funds transfer body.
   - The round-based stop, which blocks the next round.
3. **Round stop:** a dip inside a round that ends above the limit does not stop the bot.
4. **Switch checks.**
5. **Fill parsing.**
