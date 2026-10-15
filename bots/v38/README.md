# V38 "early favs" — builder sources (no API key in here)
`python build_v38.py` joins the parts into `v38_src.py`; the notebook delivered to the user is that file with the
key inserted (cell 2), plus a Drive-mount cell and a keep-alive cell. `test_v38.py` runs it against simulated
Kalshi data (needs the websocket-client and cryptography packages).
Strategies (each with its own totals; trades file column "strategy"):
- early fav: crypto 88-95c in the first 3 minutes, commodities 85-95c in the first 8 minutes, hold.
- rebound (crypto): after an early-fav buy, the side's bid falls 5c below the buy price, then buy when the bid is
  2c above its low (>= 30 s left), hold.
- spot cross (crypto): a side's ask crosses from < 90c to 90-95c while Coinbase spot is >= 1.5 normal moves past the
  strike on that side (15-min volatility x time left), hold.
10 contracts, one trade per strategy per market, spread <= 5c. See FINDINGS_log.md ("V38 plan").
