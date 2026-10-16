import pandas as pd, numpy as np, math
def fee(p): return math.ceil(0.07*p*(100-p)/100 - 1e-9)
g = pd.read_pickle("grid_rows2.pkl"); g = g[g.cls=="commod"]
q = g[g.px.between(85,95) & (g.sec > 420)].sort_values("ts").drop_duplicates("ticker")     # V38 commodity early-fav buys
t = pd.read_pickle("v37ticks_all.pkl"); t = t[t.ticker.isin(q.ticker)].copy(); t["ts"] = t.ts.dt.tz_convert(None)
CUT = pd.Timestamp("2026-10-13 12:00")
paths = []
for _, e in q.iterrows():
    m = t[(t.ticker==e.ticker) & (t.ts > e.ts) & (t.sec_to_close > 2)]
    yb, ya = m.yes_bid.values.astype(int), m.yes_ask.values.astype(int)
    bid = yb if e.side=="yes" else 100-ya; ask = ya if e.side=="yes" else 100-yb
    paths.append((e, bid, ask, m.sec_to_close.values, ya - yb))
base = np.array([100*e.win - e.px - fee(e.px) for e,*_ in paths]); newb = np.array([e.close >= CUT for e,*_ in paths])
print(f"Commodity early-fav buys: {len(paths)}, won {np.mean([e.win for e,*_ in paths])*100:.1f}%, {base.mean():+.2f}c/trade "
      f"(${base.sum()*10/100:+.2f} at 10) | Fri-Mon {base[~newb].mean():+.2f}  Tue-Wed {base[newb].mean():+.2f}")
res = []
for D in [5,8,10,15,20]:
    for R in [2,3,5,8]:
        out = []
        for e, b, a, s, sp in paths:
            lo = 999; armed = False
            for i in range(len(b)):
                lo = min(lo, b[i])
                if not armed and b[i] <= e.px - D: armed = True
                if armed and b[i] >= lo + R and s[i] >= 30 and sp[i] <= 5:
                    p = int(a[i]); out.append((e.win, p, 100*e.win - p - fee(p), e.close >= CUT)); break
        o = pd.DataFrame(out, columns=["win","px","net","new"])
        if len(o):
            res.append(dict(dip=D, turn=R, buys=len(o), won=round(o.win.mean()*100,1), lost=int((1-o.win).sum()), avg_px=round(o.px.mean(),1),
                            c_per_trade=round(o.net.mean(),2), usd_at_10=round(o.net.sum()*10/100,2),
                            fri_mon=round(o[~o.new].net.mean(),2), tue_wed=round(o[o.new].net.mean(),2), n_new=int(o.new.sum())))
pd.set_option("display.width", 200); print(pd.DataFrame(res).to_string(index=False))
