# Commod15min V38b — open favourite, held to settlement (Oct 18 2026, user's rules)

`commod15min_v38b.py` is the notebook's main cell without the API key (`KEY_ID = "__KEY_ID__"`).

- Opening buy: in the first 60 s of each market, 10 contracts of the side whose ask is 56-95c (at the ask, taker fee).
  One per market; no buy when the spread is over 5c. Held to settlement: no sells, no reversals
  (a staggered-reversal version was built first and then dropped at the user's request).
- Crypto and commodities, separate totals plus a line per opening-price band (56-65, 66-70, 71-80, 81-95c).
  No files: Colab log only.
- `test_v38b.py` runs it against a scripted feed (needs `requests`, `cryptography`, `websocket-client`).
- Background (FINDINGS_log.md): crypto sides at 50c+ at the open won 57.8% at 57.3c on average (Oct 9-16).
