import pandas as pd, numpy as np, math
def fee(p): return math.ceil(0.07*p*(100-p)/100 - 1e-9)
FEE = np.array([fee(p) for p in range(101)])
g = pd.read_pickle("grid_rows_er.pkl")
pd.set_option("display.width", 220)
def summ(d, col):
    a = d.groupby(["regime","per"], observed=True)[col].agg(["mean","size"]).unstack()
    t = d.groupby("regime", observed=True)[col].agg(["mean","size"])
    return pd.DataFrame({"trades": t["size"], "c/trade": t["mean"].round(1), "Fri-Mon": a[("mean","Fri-Mon")].round(1), "Tue-Wed": a[("mean","Tue-Wed")].round(1)})
# ---- 1) V37 real trades by regime ----
m = pd.read_pickle("v37_spotcut.pkl"); m["sec1"] = m.ts.dt.floor("1s")
e = g.drop_duplicates(["asset","sec1"])[["asset","sec1","er","regime"]]
m = m.merge(e, on=["asset","sec1"], how="left")
print("1) V37 real trades (58-67c, minutes 0-3, sell 90) by the coin's last-10-minute pattern")
x = m.dropna(subset=["regime"])
a = x.groupby("regime", observed=True).agg(trades=("usd","size"), won=("usd", lambda v: round((v>0).mean()*100,1)), usd_per_trade=("usd", lambda v: round(v.mean(),2)),
        total=("usd", lambda v: round(v.sum(),0)), win_phase_share=("dir", lambda v: round((v=="WIN").mean()*100)))
a["Fri-Mon $/tr"] = x[x.per=="Fri-Mon"].groupby("regime", observed=True).usd.mean().round(2)
a["Tue-Wed $/tr"] = x[x.per=="Tue-Wed"].groupby("regime", observed=True).usd.mean().round(2)
print(a.to_string())
# ---- 2) buy cheap, sell higher ----
print("\n2) Buy a side at 35-45c in minutes 0-10, sell when its bid rises +T, else hold to settlement (one buy per market & side). c per contract")
s = g[(g.px>=35)&(g.px<=45)&(g.sec>=300)].sort_values("ts").drop_duplicates(["ticker","side"]).copy()
for T in [10,15,20]:
    hit = s.fmax >= s.px + T
    s[f"t{T}"] = np.where(hit, T - FEE[s.px.values] - FEE[np.minimum(s.px.values+T,99)], s.net_hold)
    s[f"hit{T}"] = hit
for T in [10,15,20]:
    print(f"\n  sell at +{T}c   (reached the target: " + ", ".join(f"{k} {v*100:.0f}%" for k,v in s.groupby('regime',observed=True)[f'hit{T}'].mean().items()) + ")")
    print(summ(s, f"t{T}").to_string())
print("\n  hold to settlement (no target)"); print(summ(s, "net_hold").to_string())
print("\n3) Range-bound AND spot near the strike vs the rest (sell at +15c):")
for lab, k in [("range-bound & spot near strike", (s.regime=="range-bound")&(s.near=="spot near strike")),
               ("range-bound & not near", (s.regime=="range-bound")&(s.near!="spot near strike")),
               ("trending (any distance)", s.regime=="trending"), ("all", s.regime.notna())]:
    d = s[k]; f = lambda p: d[d.per==p].t15.mean()
    print(f"   {lab:32} trades {len(d):4}  c/trade {d.t15.mean():+5.1f}  Fri-Mon {f('Fri-Mon'):+5.1f}  Tue-Wed {f('Tue-Wed'):+5.1f}  reached +15: {d.hit15.mean()*100:.0f}%")
