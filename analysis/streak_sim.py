import pandas as pd, numpy as np, math, sys
t = pd.read_pickle(sys.argv[1]); t = t[t.result.isin(["yes","no"]) & t.asset.isin(["BTC","ETH","SOL","XRP","DOGE"])]
def fee(p): return math.ceil(0.07*p*(100-p)/100 - 1e-9)
rows=[]
for tk,g in t.groupby("ticker", sort=False):
    yb,ya,sec,ts = g.yes_bid.values, g.yes_ask.values, g.sec_to_close.values, g.ts.values
    res=g.result.iat[0]
    pick=None
    for i in np.flatnonzero((sec>=720)&(sec<=900)&(ya-yb<=5)):
        c=[(a,y) for y,a in ((True,ya[i]),(False,100-yb[i])) if 58<=a<=67]
        if not c or (len(c)==2 and c[0][0]==c[1][0]): continue
        a,y=min(c); pick=(i,y,a); break
    if pick is None: continue
    i,y,a=pick
    close_ts = ts[-1] + np.timedelta64(int(sec[-1]*1000),'ms')
    def run(side_yes, px, sell_at):
        bid = yb if side_yes else 100-ya
        win = int(res==("yes" if side_yes else "no"))
        for j in range(i+1,len(sec)):
            if sec[j] <= 2: break
            if bid[j] >= sell_at: return bid[j]-px-fee(px)-fee(bid[j]), ts[j]
        return 100*win-px-fee(px), close_ts
    v_net, v_end = run(y, a, 90)
    ip = 100-(yb[i] if y else 100-ya[i])
    i_net, i_end = run(not y, ip, 70)
    i_hold = 100*int(res==("no" if y else "yes")) - ip - fee(ip)
    rows.append(dict(ticker=tk, asset=g.asset.iat[0], entry=ts[i], v_end=v_end, px=a, v_net=v_net, v_win=int(v_net>0),
                     fav_won=int(res==("yes" if y else "no")), inv_px=ip, inv_net=i_net, inv_hold=i_hold))
d=pd.DataFrame(rows).sort_values("entry").reset_index(drop=True)
d.to_pickle(sys.argv[2]); print(len(d), "V37 trades replayed;", d.entry.min(), "->", d.entry.max())
