import pandas as pd, numpy as np, math
def fee(p): return math.ceil(0.07*p*(100-p)/100 - 1e-9)
g = pd.read_pickle("grid_rows2.pkl"); t = pd.read_pickle("v37ticks_all.pkl"); t["ts"] = t.ts.dt.tz_convert(None)
CUT = pd.Timestamp("2026-10-13 12:00")
pd.set_option("display.width", 220)
for cls, lab, lo, hi, sec in [("crypto","CRYPTO early favs (88-95c, first 3 min)",88,95,720), ("commod","COMMODITY early favs (85-95c, first 8 min)",85,95,420)]:
    q = g[(g.cls==cls) & g.px.between(lo,hi) & (g.sec > sec)].sort_values("ts").drop_duplicates("ticker")
    tt = t[t.ticker.isin(q.ticker)]
    P = []
    for _, e in q.iterrows():
        m = tt[(tt.ticker==e.ticker) & (tt.ts > e.ts) & (tt.sec_to_close > 2)]
        yb, ya = m.yes_bid.values.astype(int), m.yes_ask.values.astype(int)
        P.append((e, yb if e.side=="yes" else 100-ya, ya if e.side=="yes" else 100-yb, m.sec_to_close.values))
    print(f"\n######## {lab}: {len(q)} trades, {int(q.win.sum())} won, {int((1-q.win).sum())} lost")
    rows = []
    for D in [10,15,20,30,40]:
        for kind, vals in [("c", [5,10,15,20,30]), ("%", [0.25,0.5,0.75])]:
            for R in vals:
                out = []
                for e, b, a, s in P:
                    low = 999; dipped = False
                    for i in range(len(b)):
                        low = min(low, b[i])
                        if not dipped and b[i] <= e.px - D: dipped = True
                        if dipped and s[i] >= 30:
                            need = low + (R if kind == "c" else math.ceil(R * (e.px - low)))
                            if b[i] >= need and b[i] > low:
                                p = int(a[i]); out.append((e.win, int(b[i]), p, 100*e.win - p - fee(p), e.close >= CUT)); break
                if not out: continue
                o = pd.DataFrame(out, columns=["win","bid","ask","net","new"])
                rows.append(dict(dip=f"{D}c", rebound=(f"+{R}c" if kind=="c" else f"{int(R*100)}% of dip"), cases=len(o), won=int(o.win.sum()), lost=int((1-o.win).sum()),
                                 win_rate=round(o.win.mean()*100,1), price_then=round(o.ask.mean(),1), edge=round(o.win.mean()*100-o.ask.mean(),1),
                                 net_c=round(o.net.mean(),1), fri_mon=round(o[~o.new].net.mean(),1), tue_wed=round(o[o.new].net.mean(),1)))
    print(pd.DataFrame(rows).to_string(index=False))
