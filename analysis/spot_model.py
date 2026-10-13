import pandas as pd, numpy as np, glob, os, sys, math
from math import erf
D = sys.argv[1]
CR = ["BTC","ETH","SOL","XRP","DOGE"]
dfs=[pd.read_csv(f, usecols=["ts_iso","ticker","asset","yes_bid","yes_ask","sec_to_close","spot_px"]) for f in sorted(glob.glob(D+"/ticks_*.csv*"))]
t=pd.concat(dfs); t=t[t.asset.isin(CR)]
t["ts"]=pd.to_datetime(t.ts_iso, format="ISO8601", utc=True).dt.tz_convert(None)
# spot series per asset, 1-second grid, realized variance per second over trailing 15 min
vol = {}
for a,g in t[t.spot_px.notna()].groupby("asset"):
    s = g.set_index("ts").spot_px.sort_index(); s = s[~s.index.duplicated(keep="last")].resample("1s").last()
    gap = s.isna().rolling(5).sum()  # mark gaps
    s = s.ffill(limit=10)
    r2 = np.log(s).diff()**2
    v = r2.rolling(900, min_periods=300).mean()
    vol[a] = pd.DataFrame({"spot1s": s, "var_s": v})
st = pd.read_csv(D+"/commod15min_v37_settlements.csv").drop_duplicates("ticker", keep="last")[["ticker","result","floor_strike"]]
t = t[(t.sec_to_close>=30)&(t.sec_to_close<=870)&(t.yes_bid>0)&(t.yes_ask<100)&(t.yes_bid<=t.yes_ask)&t.spot_px.notna()]
t = t.merge(st, on="ticker"); t = t[t.result.isin(["yes","no"])]
t["sec1"] = t.ts.dt.floor("1s")
parts=[]
for a,g in t.groupby("asset"):
    parts.append(g.merge(vol[a][["var_s"]], left_on="sec1", right_index=True, how="left"))
t = pd.concat(parts).dropna(subset=["var_s"]); t = t[t.var_s>0]
t["z0"] = np.log(t.spot_px/t.floor_strike) / np.sqrt(t.var_s*t.sec_to_close)
t["yes_win"] = (t.result=="yes").astype(int)
t["close"] = t.ticker.map(t.groupby("ticker").ts.max())
t.sort_values(["ticker","ts"]).to_pickle(sys.argv[2])
print(len(t), t.ticker.nunique(), t.asset.value_counts().to_dict())
