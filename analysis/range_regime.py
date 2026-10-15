import pandas as pd, numpy as np, glob, math
def fee(p): return math.ceil(0.07*p*(100-p)/100 - 1e-9)
FEE = np.array([fee(p) for p in range(101)])
CR = ["BTC","ETH","SOL","XRP","DOGE"]
raw = pd.concat([pd.read_csv(f, usecols=["ts_iso","asset","spot_px"]) for f in glob.glob("../v37ticks/ticks_*")])
raw = raw[raw.asset.isin(CR) & raw.spot_px.notna()]
raw["ts"] = pd.to_datetime(raw.ts_iso, format="ISO8601", utc=True).dt.tz_convert(None).dt.floor("1s")
er = {}
for a, g in raw.groupby("asset"):
    s = g.drop_duplicates("ts", keep="last").set_index("ts").spot_px.sort_index().resample("1s").last().ffill(limit=10)
    s30 = s.iloc[::30]                                   # 30-second steps
    path = s30.diff().abs().rolling(20, min_periods=17).sum().reindex(s.index).ffill(limit=29)
    net = (s - s.shift(600)).abs()
    rng = (s.rolling(600, min_periods=500).max() - s.rolling(600, min_periods=500).min()) / s * 100
    er[a] = pd.DataFrame({"er": net/path, "rng10_pct": rng})
g = pd.read_pickle("grid_rows.pkl"); g = g[g.cls=="crypto"].copy()
g["sec1"] = g.ts.dt.floor("1s")
parts = []
for a in CR:
    x = g[g.asset==a]; e = er[a].reindex(x.sec1.values)
    parts.append(x.assign(er=e.er.values, rng10=e.rng10_pct.values))
g = pd.concat(parts)
g["regime"] = pd.qcut(g.er, 3, labels=["range-bound","in between","trending"])
print("efficiency ratio thirds:", g.er.quantile([1/3,2/3]).round(3).tolist())
g["near"] = pd.cut(g.z_spot.abs(), [-0.01, 0.25, 0.75, 99], labels=["spot near strike","medium","spot far"])
CUT = pd.Timestamp("2026-10-13 12:00"); g["per"] = np.where(g.close>=CUT, "Tue-Wed", "Fri-Mon")
g.to_pickle("grid_rows_er.pkl")
print("share of moments: ", g.regime.value_counts(normalize=True).round(2).to_dict())
