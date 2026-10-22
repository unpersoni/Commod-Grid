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
- **Orders expire on Kalshi's side 30 minutes after they are placed** and are renewed after 25 minutes while the
  bot runs: Colab can stop without warning (it did in the first dry run), and then no order outlives the bot by
  more than 30 minutes.
- **Fewer swaps**: a market already quoted is kept unless another looks 25% better (the first dry run swapped 4-10
  of 15 markets every 10 minutes on small changes in the estimate).
- Defaults: 15 markets, 3 per state, 10 contracts a side, $150 budget, 20 net per market, $30 loss limit.

Do not run V1 and V2 live at the same time: each cancels resting orders in its series when it starts.

`test_lip_maker_v2.py`: a fake Kalshi plus a fake price stream: dry run (picking with the per-state cap, reference
prices, the queue simulation, net limit, stream-down pull, stop), live path (exact requests, fills from the resting
orders read, an executed order looked up, moving an order, positions check, loss limit, everything cancelled at the
stop), switch checks and the stream parser.

## First dry run (Oct 21-22, 22:34-01:31 UTC, then Colab dropped the runtime)
927 gas reward programs, 84-119 quotable; 15 quoted. Estimated reward $5.82 in about 2h45m (~$2/h); 3 simulated
fills (-$1.50 marked). Fast-move pulls came in bursts across many states within seconds (~23:35, 00:35, 01:20 UTC):
one participant repricing every gas market at once; pulling then is right. Some quotes sit at 1c where a book is
thin (Kalshi's reference price is deep there): cheap, but whether such thin books earn rewards is unverified.
