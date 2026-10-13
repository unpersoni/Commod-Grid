import pandas as pd, numpy as np, math, sys
t = pd.read_pickle(sys.argv[1])
t = t[t.result.isin(["yes","no"])]
CRYPTO = {"BTC","ETH","SOL","XRP","DOGE"}
def fee(p): return math.ceil(0.07*p*(100-p)/100 - 1e-9)
HS = [75,80,85,90,95]; DS = [5,8,10,13,15,20,25]
LEVELS = range(50, 96)
rows = []      # dip entries
base = []      # first time ask touches each level (any history) per market-side
for tk, g in t.groupby("ticker", sort=False):
    asset = g.asset.iat[0]; cls = "crypto" if asset in CRYPTO else "commod"
    res = g.result.iat[0]; close = g.ts.iat[-1]
    yb, ya, sec = g.yes_bid.values, g.yes_ask.values, g.sec_to_close.values
    for side in ("yes","no"):
        bid = yb if side=="yes" else 100-ya
        ask = ya if side=="yes" else 100-yb
        win = int(res == side)
        runmax = np.maximum.accumulate(bid)
        # baseline: first tick where ask <= L (entered at that ask)
        for L in LEVELS:
            idx = np.flatnonzero((ask <= L) & (ask >= L-2) & (sec >= 30))
            if len(idx):
                i = idx[0]
                base.append((tk, asset, cls, side, L, int(ask[i]), win, sec[i], int(runmax[i]), close))
        for H in HS:
            hit = np.flatnonzero(bid >= H)
            if not len(hit): continue
            h0 = hit[0]
            for Dd in DS:
                lvl = H - Dd
                cand = np.flatnonzero((ask[h0:] <= lvl) & (sec[h0:] >= 30))
                if not len(cand): continue
                i = h0 + cand[0]; p = int(ask[i])
                # sell back at H: first later tick with bid >= H
                later = np.flatnonzero(bid[i+1:] >= H)
                if len(later):
                    j = i+1+later[0]; sp = int(bid[j]); sold = 1
                    net_sell = sp - p - fee(p) - fee(sp)
                else:
                    sold = 0; net_sell = 100*win - p - fee(p)
                rows.append(dict(ticker=tk, asset=asset, cls=cls, side=side, H=H, D=Dd, px=p, win=win,
                                 sec=sec[i], sec_high=sec[h0], minbid_after=int(bid[i:].min()),
                                 net_hold=100*win - p - fee(p), sold=sold, net_sell=net_sell, close=close))
e = pd.DataFrame(rows); b = pd.DataFrame(base, columns=["ticker","asset","cls","side","L","px","win","sec","runmax","close"])
e.to_pickle(sys.argv[2]); b.to_pickle(sys.argv[3])
print(len(e), len(b))
