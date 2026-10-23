import pandas as pd, numpy as np, glob, os, sys
D = sys.argv[1]
mk = pd.read_csv(f"{D}/markets.csv")
mk = mk[mk.result.isin(["yes", "no"])]
won = mk.set_index("ticker").up_won
print("markets with a result:", len(mk), "| by series:", mk.groupby("series").size().to_dict())
print("UP won share by series:", mk.groupby("series").up_won.mean().round(3).to_dict())
rows = []
for f in sorted(glob.glob(f"{D}/candles_*.csv.gz")):
    c = pd.read_csv(f)
    c["series"] = os.path.basename(f)[8:-7]
    rows.append(c)
c = pd.concat(rows)
c = c[c.ticker.isin(won.index)]
c["won"] = c.ticker.map(won)
ok = (c.up_bid_close > 0) & (c.up_ask_close < 100) & (c.up_ask_close > c.up_bid_close)
c = c[ok].copy()
c["mid"] = (c.up_bid_close + c.up_ask_close) / 2
c["spread"] = c.up_ask_close - c.up_bid_close
print("\nmedian UP spread (c) by series:", c.groupby("series").spread.median().to_dict())
# one observation per market per checkpoint (independent markets), all series pooled
CK = [840, 600, 300, 120, 60]
c = c[c.sec_to_close.isin(CK)]
bins = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
c["bin"] = pd.cut(c.mid, bins)
print("\nA) CALIBRATION, all series pooled: UP mid price vs how often UP won")
t = c.groupby("bin", observed=True).agg(n=("won", "size"), mid=("mid", "mean"), won=("won", "mean"),
                                         ask=("up_ask_close", "mean"), bid=("up_bid_close", "mean"))
t["won%"] = 100 * t.won; t["se"] = 100 * np.sqrt(t.won * (1 - t.won) / t.n)
t["won-mid"] = t["won%"] - t.mid
print(t[["n", "mid", "won%", "se", "won-mid", "bid", "ask"]].round(1).to_string())
print("\nwon% minus mid, by series (positive = UP underpriced), each checkpoint pooled, low (<30c) / middle / high (>70c) mids")
c["zone"] = pd.cut(c.mid, [0, 30, 70, 100], labels=["low", "mid", "high"])
z = c.groupby(["series", "zone"], observed=True).apply(lambda g: pd.Series({"n": len(g), "gap": 100 * g.won.mean() - g.mid.mean()}))
print(z.unstack().round(1).to_string())
print("\nby time left (all series): won% - mid in low/high zones")
z2 = c.groupby(["sec_to_close", "zone"], observed=True).apply(lambda g: pd.Series({"n": len(g), "gap": 100 * g.won.mean() - g.mid.mean()}))
print(z2.unstack().round(1).to_string())
