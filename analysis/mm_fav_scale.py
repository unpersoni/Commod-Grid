import pandas as pd, numpy as np, sys
sys.path.insert(0, sys.path[0])
import mm_queue_sim as q
rows = []
for size in (10, 100):
    for maxf in (1, 5):
        q.SIZE = size
        f = q.run(85, 98, 900, 30, "prop", maxf, )
        f = f[f.cls == "commodity"]
        nm = f.ticker.nunique()
        rows.append(dict(size=size, max_orders=maxf, markets_filled=nm, contracts=int(f.ct.sum()), c_per_ct=round(f.pnl.sum()/f.ct.sum(), 2),
                         usd=round(f.pnl.sum()/100, 2), usd_per_market=round(f.pnl.sum()/100/400, 2),
                         worst_market_usd=round(f.groupby("ticker").pnl.sum().min()/100, 2)))
print(pd.DataFrame(rows).to_string(index=False))
q.SIZE = 10
f = q.run(85, 98, 900, 30, "prop", 5); f = f[f.cls == "commodity"]
f["asset"] = f.ticker.str.extract(r"KX([A-Z]+)15M")[0]
g = f.groupby("asset")
print(pd.DataFrame({"contracts": g.ct.sum().round(), "won%": g.apply(lambda x: 100*(x.win*x.ct).sum()/x.ct.sum()).round(1),
                    "c_per_ct": g.apply(lambda x: x.pnl.sum()/x.ct.sum()).round(2), "usd": (g.pnl.sum()/100).round(2),
                    "losing_markets": g.apply(lambda x: (x.groupby('ticker').pnl.sum() < 0).sum())}).to_string())
