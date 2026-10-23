# 15-minute markets — first look (data Oct 15–22, 11 series, 6,169 settled markets)

Data: `tools/kalshi_15m_history_cell.py` output (markets.csv, candles_*, prices15s_*). Run: `python calib.py <data dir>`, `python together.py <data dir>`.

## Premise check: price = probability (calib.py)
UP mid price at 14, 10, 5, 2 and 1 minute(s) before the close vs how often UP won, all series pooled (~23,500 snapshots):
within about ±3c in every 10c bin (largest gaps: 40–50c bin won 3.3 points less than priced, 70–80c bin 3.2 more).
Per series the gaps are mostly within ±4c and change sign between series. Median spread 1c (2c on copper, palladium, platinum).
Reading: the working premise holds — a contract's price is its probability of paying out. Everything built from this
data takes that as given; income has to come from outside the outcome (reward programs, contracts that must add up,
settlement mechanics), and the data is used to size risk, not to pick sides.

## How much the series move together (together.py) — for sizing
Same 15-min window, correlation of outcomes (UP won): crypto with crypto 0.51–0.71 (average 0.61), metals with metals
0.26–0.62 (average 0.44), crypto with metals about 0.23; WTI went the opposite way to everything (−0.06 to −0.23 — check
how its UP is defined before relying on it). The underlying moves are tighter still (crypto 0.85–0.91, gold/silver 0.83).
- 5 crypto markets in one window act like 1.5 independent bets; 5 metals like 1.8; all 11 like 3.0.
- All 5 crypto settled the same way in 61% of windows (6% if they were independent); all 5 metals in 42%.
- One window's result says nothing about the next in the same series (autocorrelation −0.13 to 0.04).
Sizing: an unpaired contract near 50c swings about ±$0.50. Ten unpaired contracts in each of the 5 coins in one window
swing about ±$21 together, not the ±$11 they would if independent. Risk limits must count crypto as one position per window
(metals as about two); consecutive windows add up like independent draws.

## Next
- Reward programs on the 15-minute series: `tools/lip_15m_rewards_cell.py` (run in Colab; Kalshi is blocked here).
- Contracts that must add up: the crypto-lead market against the five coin markets (needs one more fetch).
