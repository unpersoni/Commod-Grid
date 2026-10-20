# V39: tag the opening favourite, wait, and buy only if it moved up? (user's question, Oct 21)
# Tag = V39's buy moment (first tick in the first 60 s with a side's ask at 56-95c and a spread <= 5c). Then look at
# that side D seconds after the open (D = 30/60/90/120): its mid vs the tag, and its ask then (spread <= 5c).
# Rules: buy at the tag (V39 now); wait D and always buy; wait D and buy only if the mid rose >= k c; only if it fell.
# V37 + V38 ticks (Oct 9-16), all 11 markets, 10 contracts at the ask, taker fee, held to settlement.
import pandas as pd, numpy as np, sys
V37, V38T, V38S = sys.argv[1], sys.argv[2], sys.argv[3]
CR = {"BTC", "ETH", "SOL", "XRP", "DOGE"}
a = pd.read_pickle(V37)[["ticker", "asset", "yes_bid", "yes_ask", "sec_to_close", "ts", "result"]]
b = pd.read_pickle(V38T)[["ticker", "asset", "yes_bid", "yes_ask", "sec_to_close", "ts"]]
b = b.merge(pd.read_csv(V38S).drop_duplicates("ticker", keep="last")[["ticker", "result"]], on="ticker")
d = pd.concat([a, b]).drop_duplicates(["ticker", "ts"])
d = d[d.result.isin(["yes", "no"]) & (d.sec_to_close <= 905) & (d.yes_bid > 0) & (d.yes_ask < 100) & (d.yes_bid < d.yes_ask)]
d = d.sort_values(["ticker", "ts"])
fee = lambda p: np.ceil(0.07 * 10 * p * (100 - p) / 100 - 1e-9) / 10          # per contract, 10-lot
DELAYS = (30, 60, 90, 120)
rows = []
for t, g in d.groupby("ticker"):
    yb, ya, s, res = g.yes_bid.values, g.yes_ask.values, g.sec_to_close.values, g.result.values[0]
    tag = None
    for i in range(len(g)):
        if s[i] < 840: break
        if ya[i] - yb[i] > 5: continue
        if 56 <= ya[i] <= 95: tag = (i, True); break
        if 56 <= 100 - yb[i] <= 95: tag = (i, False); break
    if tag is None: continue
    i0, yes = tag
    side_ask = lambda j: ya[j] if yes else 100 - yb[j]
    side_mid = lambda j: ((yb[j] + ya[j]) / 2) if yes else 100 - (yb[j] + ya[j]) / 2
    r = dict(ticker=t, asset=g.asset.values[0], ts=g.ts.values[i0], won=int((res == "yes") == yes),
             tag_px=int(side_ask(i0)), tag_mid=side_mid(i0), tag_sec=900 - s[i0])
    for D in DELAYS:
        # the first tick at or after D seconds into the market (within 20 s), with a spread <= 5c
        js = [j for j in range(i0, len(g)) if 900 - D >= s[j] >= 900 - D - 20 and ya[j] - yb[j] <= 5]
        if js:
            j = js[0]; r[f"px{D}"] = int(side_ask(j)); r[f"mv{D}"] = side_mid(j) - r["tag_mid"]
        else:
            r[f"px{D}"] = np.nan; r[f"mv{D}"] = np.nan
    rows.append(r)
x = pd.DataFrame(rows)
x["cls"] = np.where(x.asset.isin(CR), "crypto", "commodity")
x["half"] = np.where(x.ts <= x.ts.sort_values().iloc[len(x) // 2], "1st", "2nd")
x.to_pickle(V38T.rsplit("/", 1)[0] + "/v39_delay.pkl")

def show(sel, px, label, base_n):
    y = x[sel & x[px].notna()].copy() if px != "tag_px" else x[sel].copy()
    if len(y) < 15: print(f"      {label:50} {len(y):5} buys (too few)"); return
    p = y[px].astype(int)
    y["net"] = 100 * y.won - p - fee(p)
    h = y.groupby("half").net.mean()
    print(f"      {label:50} {len(y):5} buys ({100 * len(y) / base_n:3.0f}%)  won {100 * y.won.mean():5.1f}%  avg buy {p.mean():5.1f}c  "
          f"net {y.net.mean():+6.2f}c  (halves {h.get('1st', np.nan):+6.2f} / {h.get('2nd', np.nan):+6.2f})  ${y.net.sum() * 10 / 100:+8.2f}")

for c in ("crypto", "commodity"):
    sel0 = x.cls == c; n0 = sel0.sum()
    print(f"\n{c.upper()}: {n0} markets tagged (opening favourite at {x[sel0].tag_px.mean():.1f}c on average, "
          f"tagged {x[sel0].tag_sec.median():.0f} s into the market)")
    show(sel0, "tag_px", "V39 now: buy at the tag", n0)
    for D in DELAYS:
        mv = x[f"mv{D}"]
        print(f"   wait until {D} s into the market (the favourite's move since the tag: "
              f"up {100 * (mv[sel0] >= 1).mean():.0f}% · flat {100 * mv[sel0].between(-0.5, 0.5).mean():.0f}% · down {100 * (mv[sel0] <= -1).mean():.0f}%)")
        show(sel0, f"px{D}", "always buy then (the delay alone)", n0)
        for k in (1, 3, 5):
            show(sel0 & (mv >= k), f"px{D}", f"buy only if it ROSE {k}c+", n0)
        show(sel0 & mv.between(-0.5, 0.5), f"px{D}", "buy only if FLAT", n0)
        for k in (1, 3):
            show(sel0 & (mv <= -k), f"px{D}", f"buy only if it FELL {k}c+", n0)
        # the same markets bought at the tag instead: is it the signal or the price?
        show(sel0 & (mv >= 3), "tag_px", "(rose 3c+ markets, bought at the TAG instead)", n0)
        show(sel0 & (mv <= -3), "tag_px", "(fell 3c+ markets, bought at the TAG instead)", n0)
