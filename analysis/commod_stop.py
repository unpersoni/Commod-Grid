import pandas as pd, numpy as np, math
def fee(p): return math.ceil(0.07*p*(100-p)/100 - 1e-9)
g = pd.read_pickle("grid_rows2.pkl"); g = g[g.cls=="commod"]
e = g[g.px.between(85,95) & (g.sec > 420)].sort_values("ts").drop_duplicates("ticker")      # V38 commodity early fav
t = pd.read_pickle("v37ticks_all.pkl"); t = t[t.ticker.isin(e.ticker)].copy(); t["ts"] = t.ts.dt.tz_convert(None)
CUT = pd.Timestamp("2026-10-13 12:00")
paths = []
for _, r in e.iterrows():
    m = t[(t.ticker==r.ticker) & (t.ts > r.ts) & (t.sec_to_close > 2)]
    bid = (m.yes_bid if r.side=="yes" else 100 - m.yes_ask).values.astype(int)
    paths.append((r, bid, m.sec_to_close.values))
hold = np.array([100*r.win - r.px - fee(r.px) for r,_,_ in paths]); win = np.array([r.win for r,_,_ in paths]); new = np.array([r.close >= CUT for r,_,_ in paths])
print(f"Commodity early favs (85-95c, first 8 min): {len(paths)} trades, {win.mean()*100:.1f}% won, hold {hold.mean():+.2f}c/contract "
      f"(${hold.sum()*10/100:+.2f} at 10)  | Fri-Mon {hold[~new].mean():+.2f}, Tue-Wed {hold[new].mean():+.2f}")
lows = np.array([b.min() if len(b) else 99 for _, b, _ in paths])
print("lowest bid reached by WINNERS:", np.percentile(lows[win==1], [1,5,10,25,50]).round(0).tolist(), "(1st/5th/10th/25th/50th pct)")
print("lowest bid reached by LOSERS (before they hit 0): count", (win==0).sum())
rows = []
for active, lab in [(900,"whole market"), (300,"last 5 min only"), (180,"last 3 min only"), (120,"last 2 min only")]:
    for S_ in [20,30,40,50,60,70,75,80]:
        net = []; dumped = saved = 0
        for k, (r, b, s) in enumerate(paths):
            hit = np.flatnonzero((b <= S_) & (s <= active))
            if len(hit):
                sp = int(b[hit[0]]); n = sp - r.px - fee(r.px) - (fee(sp) if sp > 0 else 0)
                dumped += r.win; saved += 1 - r.win
            else: n = hold[k]
            net.append(n)
        net = np.array(net)
        rows.append([lab, S_, dumped, saved, round(net.mean(),2), round(net.mean()-hold.mean(),2), round(net.sum()*10/100,2),
                     round(net[~new].mean()-hold[~new].mean(),2), round(net[new].mean()-hold[new].mean(),2)])
R = pd.DataFrame(rows, columns=["stop active","sell if bid <=","winners dumped","losers saved","c/trade","vs hold","$ total @10","vs hold Fri-Mon","vs hold Tue-Wed"])
pd.set_option("display.width", 200); print(R.to_string(index=False))
