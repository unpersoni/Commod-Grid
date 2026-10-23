# 15-minute markets — first look (data Oct 15–22, 11 series, 6,169 settled markets)

Data: `tools/kalshi_15m_history_cell.py` output (markets.csv, candles_*, prices15s_*). Run: `python calib.py <data dir>`, `python flow.py <data dir>`.

## A) Are prices accurate? (calib.py)
UP mid price at 14, 10, 5, 2 and 1 minute(s) before the close vs how often UP won, all series pooled (~23,500 snapshots):
within about ±3c in every 10c bin (largest gaps: 40–50c bin won 3.3 points less than priced, 70–80c bin 3.2 more).
Per series the gaps are mostly within ±4c and change sign between series. Median spread 1c (2c on copper, palladium, platinum).
Reading: prices are close to fair; any gap is the size of spread + taker fee and not stable enough to trade on. The
snapshots of one market are not independent, and crypto coins move together, so the error bars are wider than they look.

## B) Who wins at settlement: those who cross the spread or the resting orders? (flow.py)
Taker gain per contract, price = last trade of each 15 s bucket (an approximation within about half a cent):
- all series: about 0 after 1 min, −0.16c after 5 min, **−0.26c at settlement** (resting orders earn it, before fees);
  negative on every one of the 8 days (−0.04 to −0.51c).
- larger for the underdog: takers buying the underdog **−0.63c**, buying the favourite +0.15c (a small favourite–longshot bias).
- by the favourite's price: 50–60c +0.08c, 60–75c −0.53c, **75–90c −0.83c**, 90–99c −0.07c.
- by time left: 7–15 min −0.58c, 3–7 min −0.27c, last 3 min about 0.
Reading: being the resting order is worth a fraction of a cent per contract on average, with each contract's
outcome swinging ±40c — so thousands of independent contracts are needed before it shows, and the coins are correlated.
Whether Kalshi charges maker fees on these series (up to 0.0175·P·(1−P), ~0.44c at 50c) decides if it is positive at all.
