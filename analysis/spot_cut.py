import pandas as pd, numpy as np
r = pd.read_pickle("v37_phases.pkl")
r["sg"] = np.where(r.side.str.upper()=="YES", 1, -1)
r["dist"] = pd.to_numeric(r.spot_dist_pct, errors="coerce")          # own coin, oriented, %
r["cdir"] = pd.to_numeric(r.spot_chg60_pct, errors="coerce")          # own coin 60s move, oriented, %
r["bdir"] = pd.to_numeric(r.btc_chg60_pct, errors="coerce") * r.sg    # BTC 60s move, oriented, %
# BTC's own distance from ITS strike at the same moment, oriented by our side
t = pd.read_pickle("v37ticks_all.pkl"); b = t[(t.asset=="BTC")].copy(); b["ts"]=b.ts.dt.tz_convert(None)
st = pd.read_csv("../v37ticks/commod15min_v37_settlements.csv").drop_duplicates("ticker", keep="last")
raw = pd.concat([pd.read_csv(f, usecols=["ts_iso","ticker","spot_px"]) for f in __import__("glob").glob("../v37ticks/ticks_*")])
raw = raw[raw.ticker.str.startswith("KXBTC15M") & raw.spot_px.notna()]
raw["ts"] = pd.to_datetime(raw.ts_iso, format="ISO8601", utc=True).dt.tz_convert(None)
raw = raw.merge(st[["ticker","floor_strike"]], on="ticker")
raw["btc_dist_raw"] = (raw.spot_px - raw.floor_strike)/raw.floor_strike*100
raw["close"] = raw.ticker.str.extract(r"-(\d{2}[A-Z]{3}\d{2}\d{4})")[0]
r["close"] = r.window.str.extract(r"-(\d{2}[A-Z]{3}\d{2}\d{4})")[0]
m = pd.merge_asof(r.sort_values("ts"), raw[["ts","close","btc_dist_raw"]].sort_values("ts"), on="ts", by="close", direction="nearest", tolerance=pd.Timedelta("5s"))
m["bdist"] = m.btc_dist_raw * m.sg
m = m[m.dir.isin(["WIN","LOSS"])]
CUT = pd.Timestamp("2026-10-13 12:00"); m["per"] = np.where(m.ts>=CUT,"Tue-Wed","Fri-Mon")
print("coverage: own dist", m.dist.notna().mean().round(3), " BTC dist", m.bdist.notna().mean().round(3))
print("\nDistribution at entry (percent of price), WIN vs LOSS phases:")
for c,lab in [("dist","own coin: spot vs strike (+ = already on our side)"),("cdir","own coin 60s move (+ = our way)"),
              ("bdist","BTC: spot vs its strike (+ = our side)"),("bdir","BTC 60s move (+ = our way)")]:
    q = m.groupby("dir")[c].describe(percentiles=[.1,.25,.5,.75,.9])[["count","10%","25%","50%","75%","90%"]]
    q.insert(1,"mean abs",m.groupby("dir")[c].apply(lambda x: x.abs().mean()))
    print(f"\n{lab}"); print(q.round(4).to_string())
m.to_pickle("v37_spotcut.pkl")
