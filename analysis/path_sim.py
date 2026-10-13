import pandas as pd, numpy as np, math, sys
t = pd.read_pickle(sys.argv[1]); t = t[t.result.isin(["yes","no"])]
CRYPTO = {"BTC","ETH","SOL","XRP","DOGE"}
def fee(p): return math.ceil(0.07*p*(100-p)/100 - 1e-9)
rows = []
for tk, g in t.groupby("ticker", sort=False):
    asset = g.asset.iat[0]; cls = "crypto" if asset in CRYPTO else "commod"
    res = g.result.iat[0]; close = g.ts.iat[-1]
    ts = g.ts.dt.tz_convert(None).values.astype("datetime64[ms]").astype("int64")/1000.0
    yb, ya, sec = g.yes_bid.values, g.yes_ask.values, g.sec_to_close.values
    for side in ("yes","no"):
        bid = yb if side=="yes" else 100-ya
        ask = ya if side=="yes" else 100-yb
        mid = (bid+ask)/2
        win = int(res == side)
        runmax = np.maximum.accumulate(bid); runmin = np.minimum.accumulate(ask)
        for L in range(30, 96, 2):   # first time the ask enters [L-1, L]
            idx = np.flatnonzero((ask <= L) & (ask >= L-1) & (sec >= 30) & (sec <= 870))
            if not len(idx): continue
            i = idx[0]
            def ago(s):
                j = np.searchsorted(ts, ts[i]-s)
                return mid[i] - mid[j] if ts[i]-ts[0] >= s else np.nan
            p = int(ask[i])
            rows.append((tk, asset, cls, side, p, win, sec[i], int(runmax[i]), int(runmin[i]),
                         ago(30), ago(60), ago(180), int(ask[i]-bid[i]), close, 100*win-p-fee(p)))
b = pd.DataFrame(rows, columns=["ticker","asset","cls","side","px","win","sec","runmax","runmin",
                                "chg30","chg60","chg180","spread","close","net"])
b.to_pickle(sys.argv[2]); print(len(b), b.ticker.nunique())
