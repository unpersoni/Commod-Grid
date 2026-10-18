# Commod15min V38b — open favourite + staggered reversal (Oct 18 2026, user's rules)

`commod15min_v38b.py` is the notebook's main cell without the API key (`KEY_ID = "__KEY_ID__"`).

- Opening buy: in the first 60 s of each market, 10 contracts of the side whose ask is 56-95c (at the ask, taker fee).
- Reversal: if the bought side's bid falls to its trigger, buy 20 of the other side at its ask (Kalshi nets: the 10 held
  are closed and 10 of the other side are held). Trigger by opening price: 56-65c -> 20c, 66-70c -> 30c,
  71-80c -> 40c, 81-95c -> 47c. Once per market, not in the last 30 s. No buy when the spread is over 5c.
- Hold to settlement. Crypto and commodities, separate totals plus a line per price tier. No files: Colab log only.
- `test_v38b.py` runs it against a scripted feed (needs `requests`, `cryptography`, `websocket-client`).
- Background (FINDINGS_log.md): crypto sides at 50c+ at the open won 57.8% at 57.3c on average (Oct 9-16);
  the user is testing the tiered reversal live without a backtest.
