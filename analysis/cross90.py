import pandas as pd, numpy as np, math
def fee(p): return math.ceil(0.07*p*(100-p)/100 - 1e-9)
FEE = np.array([fee(p) for p in range(101)])
t = pd.read_pickle("v37ticks_all.pkl"); t = t[t.result.isin(["yes","no"])].copy()
t["ts"] = t.ts.dt.tz_convert(None)
CR = {"BTC","ETH","SOL","XRP","DOGE"}
ev = []
for tk, g in t.groupby("ticker", sort=False):
    yb, ya, sec, ts = g.yes_bid.values, g.yes_ask.values, g.sec_to_close.values, g.ts.values
    res = g.result.iat[0]
    for side in ("yes","no"):
        ask = ya if side=="yes" else 100-yb; bid = yb if side=="yes" else 100-ya
        mid = (ask+bid)/2
        # running minimum of the mid AFTER each tick (to the end of the market)
        fut_min = np.minimum.accumulate(mid[::-1])[::-1]
        fut_min_after = np.append(fut_min[1:], 999)
        n = 0
        for i in range(1, len(ask)):
            if ask[i-1] < 90 <= ask[i] and ask[i] <= 95 and sec[i] > 5 and (ask[i]-bid[i]) <= 5:
                n += 1
                ev.append(dict(ticker=tk, asset=g.asset.iat[0], side=side, ts=ts[i], sec=sec[i], px=int(ask[i]), cross_no=n,
                               final=int(fut_min_after[i] >= 90), win=int(res==side)))
e = pd.DataFrame(ev)
e["cls"] = np.where(e.asset.isin(CR), "crypto", "commod")
e["net"] = 100*e.win - e.px - FEE[e.px.values]
# attach the 13 conditions from the grid table (same tick, same side)
g = pd.read_pickle("grid_rows2.pkl")[["ticker","side","ts","spread","dir30","dir60","range60","open_lean","open_seen","z_spot","coin_chg60","btc_chg60","cyc_agree","cyc_disagree","prev_same","book_imb","close"]]
e = pd.merge_asof(e.sort_values("ts"), g.sort_values("ts"), on="ts", by=["ticker","side"], direction="nearest", tolerance=pd.Timedelta("2s"))
e.to_pickle("cross90.pkl")
print(f"{len(e):,} crosses of 90c in {e.ticker.nunique():,} markets; conditions matched {e.dir60.notna().mean()*100:.0f}%")
for c, d in e.groupby("cls"):
    print(f"\n{c}: {len(d):,} crosses, final {d.final.mean()*100:.1f}%, side won {d.win.mean()*100:.1f}% at avg {d.px.mean():.1f}c, net {d.net.mean():+.1f}c/trade")
    print("  by cross number:  " + "  ".join(f"#{k}: final {x.final.mean()*100:.0f}% won {x.win.mean()*100:.0f}% net {x.net.mean():+.1f} (n={len(x)})" for k,x in d.groupby(d.cross_no.clip(upper=4))))
    mins = ((900-d.sec)//60).astype(int)
    print("  by minute of market (final% / won% / net c):")
    print("   " + "  ".join(f"m{k}: {x.final.mean()*100:.0f}/{x.win.mean()*100:.0f}/{x.net.mean():+.0f} ({len(x)})" for k,x in d.groupby(mins)))
