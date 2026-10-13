import pandas as pd, glob, os, sys, numpy as np
D = sys.argv[1]; OUT = sys.argv[2]
fs = sorted(glob.glob(D + "/ticks_*.csv*"))
dfs = []
for f in fs:
    d = pd.read_csv(f, usecols=["ts_iso","ticker","asset","yes_bid","yes_ask","sec_to_close"])
    d["file"] = os.path.basename(f); dfs.append(d)
t = pd.concat(dfs, ignore_index=True)
print("rows", len(t), "files", len(fs))
t = t[(t.sec_to_close > 0) & (t.yes_bid > 0) & (t.yes_ask < 100) & (t.yes_bid <= t.yes_ask)].copy()
t["ts"] = pd.to_datetime(t.ts_iso, format="ISO8601", utc=True)
t = t.drop(columns=["ts_iso"]).sort_values(["ticker","ts"])
s = pd.read_csv(D + "/commod15min_v37_settlements.csv").drop_duplicates("ticker", keep="last")
t = t.merge(s[["ticker","result"]], on="ticker", how="left")
print("valid rows", len(t), "markets", t.ticker.nunique(), "with settle", t[t.result.isin(["yes","no"])].ticker.nunique())
t.to_parquet(OUT) if False else t.to_pickle(OUT)
