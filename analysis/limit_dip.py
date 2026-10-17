import pandas as pd, numpy as np, math
def fee(p): return math.ceil(0.07*p*(100-p)/100 - 1e-9) if 0 < p < 100 else 0
t = pd.read_pickle("v37ticks_all.pkl"); t = t[t.result.isin(["yes","no"])].copy()
CR = {"BTC","ETH","SOL","XRP","DOGE"}; CUT = pd.Timestamp("2026-10-13 12:00", tz="UTC")
rows = []
for tk, g in t.groupby("ticker", sort=False):
    yb, ya, sec = g.yes_bid.values, g.yes_ask.values, g.sec_to_close.values
    cls = "crypto" if g.asset.iat[0] in CR else "commodity"; res = g.result.iat[0]; new = g.ts.iat[-1] >= CUT
    for side in ("yes","no"):
        bid = yb if side=="yes" else 100-ya; ask = ya if side=="yes" else 100-yb
        win = int(res == side)
        for H in range(55, 100, 5):
            hit = np.flatnonzero((bid >= H) & (sec > 30))
            if not len(hit): continue
            h0 = hit[0]
            for D in (5, 10, 15, 20):
                L = H - D
                f = np.flatnonzero((ask[h0:] <= L) & (sec[h0:] > 30))
                if not len(f): continue
                i = h0 + f[0]
                back = int(((bid[i+1:] >= H) & (sec[i+1:] > 0)).any())
                rows.append((cls, tk, side, H, D, L, win, back, sec[i], new))
d = pd.DataFrame(rows, columns=["cls","ticker","side","H","D","fill","win","back","sec","new"])
d["net_maker"] = 100*d.win - d.fill                                  # no fee on a resting order
d["net_taker"] = d.net_maker - d.fill.map(fee)                       # if charged like a taker
d.to_pickle("limit_dip.pkl")
pd.set_option("display.width", 220)
for cls in ["crypto","commodity"]:
    x = d[d.cls==cls]
    print(f"\n################ {cls.upper()}: limit buy resting D cents below a high H the bid reached (fill when the ask comes down to it)")
    for D in (5,10,15,20):
        y = x[x.D==D]
        a = y.groupby("H").agg(fills=("win","size"), back_to_H=("back", lambda v: round(v.mean()*100,1)), won=("win", lambda v: round(v.mean()*100,1)),
                               fill_px=("fill","first"), net_c=("net_maker", lambda v: round(v.mean(),1)), net_with_fee=("net_taker", lambda v: round(v.mean(),1)))
        a["won_minus_price"] = (a.won - a.fill_px).round(1)
        a["fri_mon"] = y[~y.new].groupby("H").net_maker.mean().round(1); a["tue_wed"] = y[y.new].groupby("H").net_maker.mean().round(1)
        tot = y; print(f"\n  dip {D}c  (all highs together: {len(tot)} fills, back to the high {tot.back.mean()*100:.1f}%, won {tot.win.mean()*100:.1f}%, "
                       f"avg fill {tot.fill.mean():.1f}c, net {tot.net_maker.mean():+.1f}c no fee / {tot.net_taker.mean():+.1f}c with fee; "
                       f"Fri-Mon {tot[~tot.new].net_maker.mean():+.1f} Tue-Wed {tot[tot.new].net_maker.mean():+.1f})")
        print(a[["fill_px","fills","back_to_H","won","won_minus_price","net_c","net_with_fee","fri_mon","tue_wed"]].to_string())
