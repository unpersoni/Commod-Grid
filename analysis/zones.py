import pandas as pd, numpy as np, math
def fee(p): return math.ceil(0.07*p*(100-p)/100 - 1e-9)
FEE = np.array([fee(p) for p in range(101)])
g = pd.read_pickle("grid_rows2.pkl"); g = g[g.cls=="crypto"]
CUT = pd.Timestamp("2026-10-13 12:00"); oldmed = g.close[g.close<CUT].median()
g["per"] = np.where(g.close>=CUT,"Tue-Wed",np.where(g.close<=oldmed,"Fri-Mon A","Fri-Mon B"))
g["minute"] = ((900-g.sec)//60).astype(int)
Z = {"1) 85-94c, minutes 2-4": (g.px.between(85,94)) & g.minute.between(2,4),
     "1b) 85-94c, minutes 0-4": (g.px.between(85,94)) & g.minute.between(0,4),
     "2) 95-99c, minutes 7-10": (g.px.between(95,99)) & g.minute.between(7,10),
     "3) 70-94c, minutes 13-14": (g.px.between(70,94)) & g.minute.between(13,14),
     "3b) 85-94c, minutes 13-14": (g.px.between(85,94)) & g.minute.between(13,14)}
for lab, k in Z.items():
    d = g[k].sort_values("ts").drop_duplicates("ticker").copy()
    print(f"\n{lab}: {len(d)} buys of {g.ticker.nunique()} markets, avg price {d.px.mean():.1f}c, won {d.win.mean()*100:.1f}%"
          f"  (break-even ~{(d.px.mean()+FEE[int(round(d.px.mean()))]):.0f}%)")
    rows=[]
    for ex in ["hold",10,16,20,30,40,50]:
        if ex=="hold": net=d.net_hold
        else:
            tgt=d.px.values+ex; hit=(tgt<=99)&(d.fmax.values>=tgt)
            net=pd.Series(np.where(hit, ex-FEE[d.px.values]-FEE[np.minimum(tgt,100)], d.net_hold), index=d.index)
        rows.append([ex if ex=="hold" else f"+{ex}c", round(net.mean(),1), round(net.sum()*10/100,2)] + [round(net[d.per==p].mean(),1) for p in ["Fri-Mon A","Fri-Mon B","Tue-Wed"]])
    print(pd.DataFrame(rows, columns=["exit","c/trade","$ total @10","Fri-Mon A","Fri-Mon B","Tue-Wed"]).to_string(index=False))
    print("   per coin (hold): " + "  ".join(f"{a} {x.net_hold.mean():+.1f} ({len(x)})" for a,x in d.groupby("asset")))
    print("   per day (hold):  " + "  ".join(f"{k} {x.net_hold.mean():+.1f} ({len(x)})" for k,x in d.groupby((d.close-pd.Timedelta(hours=4)).dt.strftime('%a'))))
