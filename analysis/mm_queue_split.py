import pandas as pd, numpy as np, sys
sys.path.insert(0, sys.path[0])
from mm_queue_sim import *
pd.set_option("display.width", 250)
def tab(f, by):
    g = f.groupby(by, observed=True)
    return pd.DataFrame({"contracts": g.ct.sum(), "won%": (g.apply(lambda x: 100*(x.win*x.ct).sum()/x.ct.sum())).round(1),
                         "avg_px": g.apply(lambda x: (x.px*x.ct).sum()/x.ct.sum()).round(1),
                         "c_per_ct": g.apply(lambda x: x.pnl.sum()/x.ct.sum()).round(2), "usd": (g.pnl.sum()/100).round(2)})
tick_time = tk.groupby("ticker").ts.min()
for nm, lo, hi in [("fav 85-98", 85, 98), ("fav 91-98", 91, 98)]:
    f = run(lo, hi, 900, 30, "prop", 5)
    f["half"] = np.where(f.ticker.map(tick_time) >= pd.Timestamp("2026-10-16 12:00", tz="UTC"), "Oct16 day", "Oct15-16 night")
    f["tleft"] = pd.cut(f.s2c, [0, 60, 180, 420, 660, 900], labels=["0-1m", "1-3m", "3-7m", "7-11m", "11-15m"])
    f["pxb"] = pd.cut(f.px, [84, 88, 91, 94, 96, 98], labels=["85-88", "89-91", "92-94", "95-96", "97-98"])
    print(f"\n===== {nm} =====")
    print(tab(f, ["cls", "how"]).to_string())
    print(tab(f, ["cls", "half"]).to_string())
    print(tab(f, ["cls", "tleft"]).to_string())
    print(tab(f, ["cls", "pxb"]).to_string())
