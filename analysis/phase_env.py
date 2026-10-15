import pandas as pd, numpy as np
t = pd.read_pickle("v37ticks_all.pkl"); t = t[t.asset.isin(["BTC","ETH","SOL","XRP","DOGE"]) & t.result.isin(["yes","no"])].copy()
t["ts"] = t.ts.dt.tz_convert(None); t["mid"] = (t.yes_bid+t.yes_ask)/2
st = pd.read_csv("../v37ticks/commod15min_v37_settlements.csv").drop_duplicates("ticker", keep="last")
st["close"] = pd.to_datetime(st.close_time, utc=True).dt.tz_convert(None)
t = t.merge(st[["ticker","close"]], on="ticker")
# per market: leader at minute 3, did it lose (reversal); how far the price swung; avg spread; mid crossings of 50
rows = []
for tk, g in t.groupby("ticker"):
    m3 = g[g.sec_to_close <= 720]
    if not len(m3) or g.sec_to_close.max() < 840: continue
    lead_yes = m3.mid.iat[0] > 50
    res_yes = g.result.iat[0] == "yes"
    cross = (np.sign(g.mid.values - 50.0)[1:] != np.sign(g.mid.values - 50.0)[:-1]).sum()
    rows.append(dict(ticker=tk, asset=g.asset.iat[0], close=g.close.iat[0], reversal=int(lead_yes != res_yes) if m3.mid.iat[0] != 50 else np.nan,
                     lead_strength=abs(m3.mid.iat[0]-50), crosses=cross, spread=(g.yes_ask-g.yes_bid).mean(),
                     swing=g.mid.max()-g.mid.min(), yes_won=int(res_yes)))
mk = pd.DataFrame(rows)
cyc = mk.groupby("close").agg(reversal=("reversal","mean"), crosses=("crosses","mean"), spread=("spread","mean"),
                              swing=("swing","mean"), lead=("lead_strength","mean"),
                              agree=("yes_won", lambda x: max(x.mean(), 1-x.mean())), n=("ticker","size"))
# BTC volatility per cycle from spot
b = t[(t.asset=="BTC") & t.spot_px.notna()] if "spot_px" in t else None
cyc.to_pickle("cycles_env.pkl"); mk.to_pickle("markets_env.pkl")
r = pd.read_pickle("v37_phases.pkl"); r = r[r.dir.isin(["WIN","LOSS"])].copy()
r["close"] = pd.to_datetime(r.window.str.extract(r"-(\d{2}[A-Z]{3}\d{2}\d{4})")[0], format="%y%b%d%H%M") + pd.Timedelta(hours=4)
# 1) environment DURING the phase (cycles whose market the trade was in)
d = r.merge(cyc, left_on="close", right_index=True, how="left")
cols = ["reversal","crosses","swing","spread","lead","agree"]
print("Market-wide, per 15-min cycle, during WIN vs LOSS phases (all 5 coins, not just V37's trades):")
print(d.groupby("dir")[cols].mean().round(3).T.to_string())
# 2) environment BEFORE the trade: average of the previous 4 completed cycles (the last hour)
cyc_s = cyc.sort_index()
roll = cyc_s[cols].rolling(4, min_periods=3).mean()
roll.index = roll.index + pd.Timedelta(minutes=15)   # known at the open of the next market
d2 = r.merge(roll, left_on="close", right_index=True, how="left").dropna(subset=cols)
CUT = pd.Timestamp("2026-10-13 12:00")
d2["per"] = np.where(d2.ts >= CUT, "Tue-Wed", "Fri-Mon")
print("\nKnown BEFORE the trade (last hour of all markets) -> V37 result of the next trades, by tercile:")
for c in cols:
    q = pd.qcut(d2[c], 3, labels=["low","mid","high"], duplicates="drop")
    tab = d2.groupby([q, "per"], observed=True).usd.agg(["mean","size"]).unstack()
    tot = d2.groupby(q, observed=True).usd.agg(["mean","size","sum"])
    print(f"\n{c}:  (bounds {d2[c].quantile(1/3):.3f} / {d2[c].quantile(2/3):.3f})")
    print(pd.DataFrame({"$ per trade": tot["mean"].round(2), "trades": tot["size"], "total $": tot["sum"].round(0),
                        "Fri-Mon $/tr": tab[("mean","Fri-Mon")].round(2), "Tue-Wed $/tr": tab[("mean","Tue-Wed")].round(2)}).to_string())
