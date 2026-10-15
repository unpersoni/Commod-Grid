import pandas as pd, numpy as np, sys
g = pd.read_pickle(sys.argv[1]); g = g[g.cls=="crypto"]
g["day"] = (g.close - pd.Timedelta(hours=4)).dt.strftime("%a %m-%d")
def first(d): return d.sort_values("ts").drop_duplicates(["ticker","side"])
def show(lab, d):
    d = first(d)
    print(f"\n{lab}: {len(d)} buys in {d.close.nunique()} cycles, avg {d.px.mean():.1f}c, won {d.win.mean()*100:.1f}%, "
          f"hold {d.net_hold.mean():+.1f}c/trade (${d.net_hold.sum()*10/100:+.2f} at 10 contracts)")
    print("  by day:  " + "  ".join(f"{k} {x.net_hold.mean():+.1f}c ({len(x)})" for k, x in d.groupby("day")))
    print("  by coin: " + "  ".join(f"{k} {x.net_hold.mean():+.1f}c ({len(x)})" for k, x in d.groupby("asset")))
    cyc = d.groupby("close").net_hold.mean()
    print(f"  per cycle: {len(cyc)} cycles, {(cyc>0).mean()*100:.0f}% positive, cycle-level SE {cyc.std()/np.sqrt(len(cyc)):.1f}c vs mean {cyc.mean():+.1f}c")
show("A) side at 88-95c in minutes 0-3", g[(g.px>=88)&(g.px<=95)&(g.sec>720)])
net = g.cyc_agree - g.cyc_disagree
show("B) side at 30-39c in minutes 8-11 while other coins lean the other way (cycle <= -2)",
     g[(g.px>=30)&(g.px<=39)&(g.sec>240)&(g.sec<=480)&(net<=-2)])
show("B') same, without the cycle condition", g[(g.px>=30)&(g.px<=39)&(g.sec>240)&(g.sec<=480)])
