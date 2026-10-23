# 15-minute markets — first look (data Oct 15–22, 11 series, 6,169 settled markets)

Data: `tools/kalshi_15m_history_cell.py` output (markets.csv, candles_*, prices15s_*). Run: `python calib.py <data dir>`.

## Premise check: price = probability (calib.py)
UP mid price at 14, 10, 5, 2 and 1 minute(s) before the close vs how often UP won, all series pooled (~23,500 snapshots):
within about ±3c in every 10c bin (largest gaps: 40–50c bin won 3.3 points less than priced, 70–80c bin 3.2 more).
Per series the gaps are mostly within ±4c and change sign between series. Median spread 1c (2c on copper, palladium, platinum).
Reading: the working premise holds — a contract's price is its probability of paying out. Everything built from this
data takes that as given; income has to come from outside the outcome (reward programs, contracts that must add up,
settlement mechanics), and the data is used to size risk, not to pick sides.

## Next
- How much the series move together (for sizing many markets at once).
- Reward programs on the 15-minute series: dollars per hour against volume, as on the gas markets.
- Contracts that must add up: the crypto-lead market against the five coin markets (needs one more fetch).
