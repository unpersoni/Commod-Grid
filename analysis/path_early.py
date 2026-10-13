import pandas as pd, numpy as np, sys
b = pd.read_pickle(sys.argv[1]); cut=b.close.quantile(.5); b["half"]=np.where(b.close<=cut,"H1","H2")
b["day"]=b.close.dt.tz_convert("America/New_York").dt.strftime("%a")
def s(g, lab):
    g = g.sort_values("sec", ascending=False).drop_duplicates(["ticker"])   # first qualifying moment, one side per market
    if not len(g): return
    e = g.win.mean()*100-g.px.mean(); se=np.sqrt(g.win.mean()*(1-g.win.mean())/len(g))*100
    hs = " ".join(f"{k}:{(x.win.mean()*100-x.px.mean()):+.1f}(n={len(x)})" for k,x in g.groupby("half"))
    ds = " ".join(f"{k}:{(x.win.mean()*100-x.px.mean()):+.0f}({len(x)})" for k,x in g.groupby("day"))
    print(f"{lab:48} mkts={len(g):4} px={g.px.mean():.1f} won={g.win.mean()*100:.1f}% edge={e:+.1f}±{se:.1f} net={g.net.mean():+.1f}c  {hs}\n{'':48} {ds}")
for cls in ["crypto","commod"]:
    c = b[(b.cls==cls)&(b.sec>=660)]
    print(f"\n== {cls}, first 4 minutes (>=11m left) ==")
    for lo,hi in [(45,59),(60,74),(75,89),(60,89)]:
        x = c[(c.px>=lo)&(c.px<=hi)]
        s(x[x.chg60>=15], f"{lo}-{hi}c, rose 15c+ in last 60s")
        s(x[(x.chg60>=5)&(x.chg60<15)], f"{lo}-{hi}c, rose 5-15c")
        s(x[x.chg60<5], f"{lo}-{hi}c, not rising (<5c)")
