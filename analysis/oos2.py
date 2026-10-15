import pandas as pd, numpy as np
CUT=pd.Timestamp("2026-10-13 12:00")
d=pd.read_pickle("v37_streak_all.pkl"); d["new"]=d.entry>=CUT
print("=== 4. Session switch (last-60-min V37 P&L < -200c/contract), replay ===")
for k,g in d.groupby("new"):
    c=g.net60<-200
    print(f"  {'NEW' if k else 'OLD'}: {len(g)} trades, fires {c.sum()} | V37 {g.v_net.sum():+.0f} | pause {g.v_net[~c].sum():+.0f} | flip {np.where(c,g.inv_net,g.v_net).sum():+.0f}"
          f"  (flipped trades: V37 {g.v_net[c].mean():+.1f}c, inverse {g.inv_net[c].mean():+.1f}c)  [c per contract]")
r=pd.read_csv("/tmp/claude-0/-home-user-Commod-Grid/1ed5b2d5-3984-5929-851b-bcc569fede2c/scratchpad/v37ticks/commod15min_v37_trades.csv")
r["buy"]=pd.to_datetime(r.buy_iso,format="ISO8601",utc=True); r["new"]=r.buy>=CUT.tz_localize("UTC")
r["et"]=r.buy.dt.tz_convert("America/New_York"); r["won"]=r.net_c>0
r["fav_won"]=(r.result.str.lower().str[:1]==r.side.str.lower().str[:1]) if "result" in r else np.nan
def s(g): return pd.Series(dict(n=len(g), won=round(g.won.mean()*100,1), avg_px=round(g.buy_px.mean(),1), usd=round(g.net_c.sum()/100,2), c_per_trade=round(g.net_c.mean()/10,1)))
print("\n=== 5. V37 real trades ===")
print(r.groupby("new").apply(s).rename(index={False:"OLD Fri-Mon",True:"NEW Tue-Thu"}).to_string())
print("\nby ET day:"); print(r.groupby(r.et.dt.strftime("%a %m-%d")).apply(s).to_string())
print("\nspread at entry (1c vs 2c+):")
r["sp"]=np.where(r.spread_c<=1,"1c","2c+")
print(r.groupby(["new","sp"]).apply(s).to_string())
r["block"]=pd.cut(r.et.dt.hour,[-1,8,17,23],labels=["night 0-9","day 9-18","evening 18-24"])
print("\ntime of day (ET):"); print(r.groupby(["new","block"]).apply(s).to_string())
print("\nby coin:"); print(r.groupby(["asset","new"]).apply(s).unstack(0)["usd"].to_string())
print("\nsold at 90 vs held:"); print(r.groupby(["new","reason"]).apply(s).to_string())
