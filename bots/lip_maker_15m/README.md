# LIP Maker 15M

The LIP Maker V2 gas bot, adapted to the 15-minute markets: Kalshi's liquidity-reward programs pay $20–50 per market per
15-minute window (crypto, crypto-lead, Treasury rates, FX, metals, oil/gas). Prices are taken as fair (price =
probability), so fills are fair exchanges; the bot collects the reward and keeps what fills can swing small.

Differences from V2:
- every 15-minute series with a reward program (`SERIES = ["ALL"]`, `SKIP_SERIES` to leave some out); picks again 3 s
  after each quarter hour, as the next window's markets open;
- no orders in a market's last `PULL_BEFORE_CLOSE_SEC` (120 s); each order expires on Kalshi's side at that moment;
- group limit `GROUP_MAX_NET`: markets that move together (crypto, rates, FX, metals, energy, the crypto-lead set) share
  one count of unpaired contracts; at the limit only the side that pairs what is held is quoted;
- faster safety: a 6c move in 30 s pulls a market for 60 s;
- one line per finished window (estimated reward, fills, settled result) and logs on Drive: `lip15_programs.csv` (every
  reward program seen — when each series pays and how much), `lip15_windows.csv`, `lip15_fills.csv`, `lip15_data/`;
- kill switch `STOP_LIP15.txt`. Leftover orders are cancelled only in 15-minute series (the gas bot's are left alone).

Notebook: mount, keep-alive, the bot (with the API key — delivered privately, never committed), SWITCHES (`switches_cell.py`).
Tests: `python test_lip_maker_15m.py` (fake Kalshi, short windows).
