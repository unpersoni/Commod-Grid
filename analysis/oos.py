import pandas as pd, numpy as np, math
CUT = pd.Timestamp("2026-10-13 12:00", tz="UTC")
def fee(p): return math.ceil(0.07*p*(100-p)/100 - 1e-9)
def line(lab, g, edge=True, netcol="net_hold"):
    if not len(g): print(f"  {lab:58} n=0"); return
    w=g.win.mean()*100; p=g.px.mean(); se=np.sqrt(max(w*(100-w),1)/len(g))
    print(f"  {lab:58} n={len(g):4}  px {p:5.1f}  won {w:5.1f}%  edge {w-p:+5.1f} ±{se:4.1f}  net {g[netcol].mean():+5.1f}c")
print("=== 1. Dip after a high (crypto; one buy per market/side/high in each dip group) ===")
e=pd.read_pickle("dip_entries_all.pkl"); e["new"]=e.close>=CUT
cands=[("reached 80, dip 5-8c, 10-15m left",80,(5,8),(600,900)),("reached 80, dip 10-13c, 10-15m left",80,(10,13),(600,900)),
       ("reached 80, dip 15-25c, 10-15m left",80,(15,25),(600,900)),("reached 90, dip 5-8c, 10-15m left",90,(5,8),(600,900)),
       ("reached 95, dip 5-8c, 2-5m left",95,(5,8),(120,300)),("reached 95, dip 10-13c, 2-5m left",95,(10,13),(120,300)),
       ("reached 95, dip 15-25c, 2-5m left",95,(15,25),(120,300))]
for lab,H,(d0,d1),(s0,s1) in cands:
    g=e[(e.cls=="crypto")&(e.H==H)&(e.D>=d0)&(e.D<=d1)&(e.sec>s0)&(e.sec<=s1)].sort_values("D").drop_duplicates(["ticker","side","H"])
    line(lab+"  OLD", g[~g.new]); line(lab+"  NEW", g[g.new])
print("\n=== 2. Early fast riser: crypto, >=11m left, 60-89c, rose 15c+ in 60s (one per market) ===")
b=pd.read_pickle("path_base_all.pkl"); b["new"]=b.close>=CUT; b["net_hold"]=b.net
x=b[(b.cls=="crypto")&(b.sec>=660)&(b.px>=60)&(b.px<=89)]
for lab,c in [("rose 15c+",x.chg60>=15),("rose 5-15c",(x.chg60>=5)&(x.chg60<15)),("not rising",x.chg60<5)]:
    g=x[c].sort_values("sec",ascending=False).drop_duplicates("ticker")
    line(lab+"  OLD", g[~g.new]); line(lab+"  NEW", g[g.new])
print("\n=== 3. Spot model gap (k=1.25 and blend weights fixed from the old data) ===")
t=pd.read_pickle("spot_ticks_all.pkl"); t["new"]=t.close.dt.tz_localize("UTC")>=CUT
erf=np.vectorize(math.erf); t["p_spot"]=np.clip(0.5*(1+erf(1.25*t.z0.values/math.sqrt(2))),.001,.999)
t["mid"]=((t.yes_bid+t.yes_ask)/200).clip(.01,.99); lg=lambda p: np.log(p/(1-p))
t["p_blend"]=1/(1+np.exp(-(-0.2+0.89*lg(t.mid)+0.19*lg(t.p_spot))))
def ll(p,y): p=np.clip(p,.005,.995); return -np.mean(y*np.log(p)+(1-y)*np.log(1-p))
for k,d in t.groupby("new"):
    d=d[(d.sec_to_close>=30)&(d.sec_to_close<=870)]; y=(d.result=="yes").astype(int).values
    print(f"  {'NEW' if k else 'OLD'} log-loss: Kalshi {ll(d.mid.values,y):.4f}  spot {ll(d.p_spot.values,y):.4f}  blend {ll(d.p_blend.values,y):.4f}")
def gap_trades(d, model, thr):
    out=[]
    d=d[(d.sec_to_close>=30)&(d.sec_to_close<=870)]
    for side in ("yes","no"):
        p=d[model] if side=="yes" else 1-d[model]; ask=d.yes_ask if side=="yes" else 100-d.yes_bid
        q=d[(p*100-ask)>=thr].assign(px=ask).sort_values("ts").drop_duplicates("ticker")
        w=(q.result==side).astype(int)
        out.append(pd.DataFrame({"px":q.px,"win":w,"net_hold":100*w-q.px-q.px.map(fee),"new":q.new}))
    return pd.concat(out)
for model,thr in [("p_spot",15),("p_blend",8),("p_blend",5)]:
    r=gap_trades(t,model,thr); line(f"{model} gap>={thr}  OLD", r[~r.new]); line(f"{model} gap>={thr}  NEW", r[r.new])
