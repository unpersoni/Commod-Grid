import pandas as pd, numpy as np, math
def fee(p): return math.ceil(0.07*p*(100-p)/100 - 1e-9)
g = pd.read_pickle("grid_rows.pkl"); g = g[g.cls=="crypto"]
q = g[(g.px>=88)&(g.px<=95)&(g.sec>720)].sort_values("ts").drop_duplicates("ticker")
t = pd.read_pickle("v37ticks_all.pkl"); t = t[t.ticker.isin(q.ticker)].copy(); t["ts"]=t.ts.dt.tz_convert(None)
CUT = pd.Timestamp("2026-10-13 12:00")
paths = []
for _, e in q.iterrows():
    m = t[(t.ticker==e.ticker)&(t.ts>e.ts)&(t.sec_to_close>2)]
    bid = (m.yes_bid if e.side=="yes" else 100-m.yes_ask).values.astype(int)
    ask = (m.yes_ask if e.side=="yes" else 100-m.yes_bid).values.astype(int)
    paths.append((e, bid, ask, m.sec_to_close.values))
# 1) shape of the fall to the low (winners): count 3c+ bounces before the low
def bounces(b, thr=3):
    low_i = b.argmin(); seg = b[:low_i+1]; n=0; run_low=seg[0] if len(seg) else 0; up=False
    for x in seg:
        if x < run_low: run_low = x; up=False
        elif x >= run_low + thr and not up: n+=1; up=True; run_low = x   # a bounce, then measure the next fall from here
    return n
sh=[]
for e,b,a,s in paths:
    if e.win==1 and len(b): sh.append(dict(dip=e.px-b.min(), bounces=bounces(b)))
sh=pd.DataFrame(sh); sh=sh[sh.dip>=5]
print(f"Winners that dipped 5c+ below the buy price: {len(sh)} of {sum(e.win for e,*_ in paths)}")
print("Bounces of 3c+ on the way down to the low:")
print(pd.cut(sh.bounces,[-1,0,1,2,4,100],labels=["0 (straight down)","1","2","3-4","5+"]).value_counts().sort_index().to_string())
# 2) rebound rule: after the 88-95 signal, wait for the bid to fall D below the signal price,
#    then buy (at the ask) when the bid is R above its lowest point so far. Hold to settlement.
print("\nRebound entry (one buy per market, hold). Compare: buying at the signal = 118 buys, 94.9% won, +5.6c/trade")
res=[]
for D in [5,10,15,20]:
    for R in [2,3,5,8]:
        out=[]
        for e,b,a,s in paths:
            lo=999; armed=False
            for i in range(len(b)):
                lo=min(lo,b[i])
                if not armed and b[i] <= e.px-D: armed=True
                if armed and b[i] >= lo+R and s[i] >= 30:
                    p=a[i]; out.append(dict(win=e.win, px=p, net=100*e.win-p-fee(p), new=e.close>=CUT, false=(b[i+1:].min() if i+1<len(b) else 99) < lo)); break
        o=pd.DataFrame(out)
        if len(o): res.append(dict(dip=D, rebound=R, buys=len(o), won=f"{o.win.mean()*100:.1f}%", lost=int((1-o.win).sum()), avg_px=round(o.px.mean(),1),
            net=round(o.net.mean(),1), total_usd=round(o.net.sum()*10/100,2), fell_lower_after=f"{o.false.mean()*100:.0f}%",
            old=round(o[~o.new].net.mean(),1), new=round(o[o.new].net.mean(),1)))
print(pd.DataFrame(res).to_string(index=False))
