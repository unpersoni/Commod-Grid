import pandas as pd, numpy as np, sys
e = pd.read_pickle(sys.argv[1]); b = pd.read_pickle(sys.argv[2])
cut = e.close.quantile(0.5); print("half split at", cut)
e["half"] = np.where(e.close <= cut, "H1", "H2")
def summ(g):
    n=len(g); w=g.win.mean()*100; p=g.px.mean()
    se = np.sqrt(max(w*(100-w),1)/n) if n else np.nan
    h1=g[g.half=="H1"]; h2=g[g.half=="H2"]
    return pd.Series(dict(n=n, mkts=g.ticker.nunique(), avg_px=round(p,1), win=round(w,1), edge=round(w-p,1), se=round(se,1),
        hold_c=round(g.net_hold.mean(),1), sellH_c=round(g.net_sell.mean(),1), sold=round(g.sold.mean()*100),
        edge_H1=round(h1.win.mean()*100-h1.px.mean(),1) if len(h1) else np.nan,
        edge_H2=round(h2.win.mean()*100-h2.px.mean(),1) if len(h2) else np.nan))
pd.set_option("display.width",200); pd.set_option("display.max_rows",200)
for cls in ["crypto","commod"]:
    print(f"\n=== {cls}: reached high H (bid>=H), then ask dipped to <= H-D; buy at ask; >=30s left ===")
    print(e[e.cls==cls].groupby(["H","D"]).apply(summ).to_string())
# pooled
print("\n=== pooled by class ===")
print(e.groupby("cls").apply(summ).to_string())
# the user's example: reached 90, buy at <=77-80
print("\n=== crypto, H=90, by coin (D=10) ===")
print(e[(e.cls=="crypto")&(e.H==90)&(e.D==10)].groupby("asset").apply(summ).to_string())
print("\n=== crypto H=90 D=10 by time left at entry ===")
x=e[(e.cls=="crypto")&(e.H==90)&(e.D==10)].copy(); x["left"]=pd.cut(x.sec,[0,120,300,600,900],labels=["<2m","2-5m","5-10m","10-15m"])
print(x.groupby("left").apply(summ).to_string())
# key test: same price, does a prior high matter?
b["gap"] = b.runmax - b.px
b["prior_high"] = pd.cut(b.gap, [-100,4,9,14,19,200], labels=["<5 above","5-9","10-14","15-19","20+ above"])
b["pxb"] = pd.cut(b.px, [49,59,69,79,89,95], labels=["50-59","60-69","70-79","80-89","90-95"])
print("\n=== Same entry price, split by how far above it the bid had been earlier (edge = win% - price) ===")
for cls in ["crypto","commod"]:
    bb=b[b.cls==cls]
    tab = bb.groupby(["pxb","prior_high"]).apply(lambda g: f"{g.win.mean()*100-g.px.mean():+.1f} (n={len(g)})" if len(g)>=20 else "-").unstack()
    print(f"\n{cls}:"); print(tab.to_string())
