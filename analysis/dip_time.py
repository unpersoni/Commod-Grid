import pandas as pd, numpy as np, sys
e = pd.read_pickle(sys.argv[1]); cut=e.close.quantile(.5); e["half"]=np.where(e.close<=cut,"H1","H2")
e["left"]=pd.cut(e.sec,[0,120,300,600,900],labels=["last 2m","2-5m left","5-10m left","10-15m left"])
e["Dg"]=pd.cut(e.D,[0,8,13,25],labels=["dip 5-8c","dip 10-13c","dip 15-25c"])
def cell(g):
    g=g.sort_values("D").drop_duplicates(["ticker","side","H"])  # one buy per market/side/high within the dip group
    if len(g)<30: return "-"
    w=g.win.mean()*100; p=g.px.mean(); se=np.sqrt(w*(100-w)/len(g))
    h=[g[g.half==k] for k in ("H1","H2")]
    return f"{p:.0f}c→{w:.0f}% ({w-p:+.1f}{'*' if abs(w-p)>2*se else ''}) n={len(g)} [{'/'.join(f'{x.win.mean()*100-x.px.mean():+.0f}' if len(x)>=15 else '.' for x in h)}]"
pd.set_option("display.width",250); pd.set_option("display.max_colwidth",60)
for cls in ["crypto","commod"]:
    for H in [80,90,95]:
        x=e[(e.cls==cls)&(e.H==H)]
        print(f"\n{cls}, reached {H}c then dipped — avg buy price → win rate (edge, * = beyond 2 SE) n [1st/2nd half edge]")
        print(x.groupby(["left","Dg"]).apply(cell).unstack().to_string())
