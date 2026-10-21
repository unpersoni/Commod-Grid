# LIP Maker V2 — the reward maker on many markets (Oct 21 2026)

`lip_maker_v2.py` is the notebook's bot-code cell without the API key; `switches_cell.py` is its SWITCHES cell.
Same idea and safety limits as `bots/lip_maker_v1` (read that README first); built to run like the live bot on
15+ markets:

- **Prices stream over Kalshi's websocket** (order books + trade prints, the feed MM15Min V3/V4 ran on for days):
  no per-market polling, so 15-40 markets stay far inside Kalshi's limits. If the stream is down for 20 s, every
  order is pulled until it is back.
- **The dry run waits its turn in line**: a new order sits behind the contracts already at its price; trades at
  that price use up the line first; people cancelling ahead of us move us up; a trade below our price fills us.
  (V1's dry run assumed we were always first: the worst case.)
- **Live fills** from one read of all our resting orders every 4 s; an order no longer resting is looked up once;
  positions checked against Kalshi every 2 minutes.
- **At most `MAX_PER_STATE` markets per series** (per state for gas): neighbouring strikes move together.
- Defaults: 15 markets, 3 per state, 10 contracts a side, $150 budget, 20 net per market, $30 loss limit.

Do not run V1 and V2 live at the same time: each cancels resting orders in its series when it starts.

`test_lip_maker_v2.py`: a fake Kalshi plus a fake price stream: dry run (picking with the per-state cap, reference
prices, the queue simulation, net limit, stream-down pull, stop), live path (exact requests, fills from the resting
orders read, an executed order looked up, moving an order, positions check, loss limit, everything cancelled at the
stop), switch checks and the stream parser.
