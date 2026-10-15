import pandas as pd, numpy as np, sys
g = pd.read_pickle(sys.argv[1])
CUT = pd.Timestamp("2026-10-13 12:00")
g["per"] = np.where(g.close >= CUT, "NEW", "OLD")
oldmed = g[g.per=="OLD"].close.median()
g.loc[(g.per=="OLD") & (g.close <= oldmed), "per"] = "A"; g.loc[(g.per=="OLD"), "per"] = "B"
g["band"] = pd.cut(g.px, [29,39,49,57,67,77,87,95], labels=["30-39","40-49","50-57","58-67","68-77","78-87","88-95"])
g["win_"] = pd.cut(g.sec, [29,240,480,720,900], labels=["min 12-14","min 8-11","min 4-7","min 0-3"])
cut3 = lambda x, a, b, labs: pd.Series(np.where(x.isna(), None, np.where(x <= a, labs[0], np.where(x >= b, labs[2], labs[1]))), index=x.index)
F = {}
F["01 none"] = pd.Series("all", index=g.index)
F["02 spread"] = pd.Series(np.where(g.spread<=1,"1c",np.where(g.spread==2,"2c","3-5c")), index=g.index)
F["03 dir30"] = cut3(g.dir30, -2, 2, ["falling","flat","rising"])
F["04 dir60"] = cut3(g.dir60, -3, 3, ["falling","flat","rising"])
F["05 speed(range60)"] = cut3(g.range60, 9, 17, ["slow","medium","fast"])
F["06 open lean"] = cut3(g.open_lean.where(g.open_seen), -3, 3, ["opened underdog","opened even","opened favourite"])
F["07 spot vs strike"] = cut3(g.z_spot, 0, 0.5, ["spot against","spot slightly with","spot well with"])
F["08 coin spot 60s"] = cut3(g.coin_chg60, -0.01, 0.01, ["coin against","coin flat","coin with"])
F["09 BTC spot 60s"] = cut3(g.btc_chg60, -0.01, 0.01, ["BTC against","BTC flat","BTC with"])
F["10 cycle"] = cut3(g.cyc_agree - g.cyc_disagree, -2, 2, ["others against","others mixed","others with"])
F["11 streak"] = pd.Series(np.where(g.prev_same.isna(), None, np.where(g.prev_same==1,"prev market won this side","prev market won other side")), index=g.index)
F["12 book"] = cut3(g.book_imb, 0.33, 0.67, ["book against","book balanced","book with"])
et_h = (g.ts - pd.Timedelta(hours=4)).dt.hour
F["13 time ET"] = pd.Series(np.where(et_h < 9, "night 0-9", np.where(et_h < 18, "day 9-18", "evening 18-24")), index=g.index)
out = []
base = g[["ticker","side","ts","per","cls","band","win_","px","win","net_hold","net_s90"]]
for fname, lev in F.items():
    d = base.assign(level=lev.values).dropna(subset=["level","band","win_"])
    d = d.sort_values("ts").drop_duplicates(["cls","band","win_","level","ticker","side"])   # first moment per market & side
    a = d.groupby(["cls","band","win_","level","per"], observed=True).agg(n=("win","size"), px=("px","mean"), won=("win","mean"),
                                                                       hold=("net_hold","mean"), s90=("net_s90","mean")).reset_index()
    a["factor"] = fname; out.append(a)
r = pd.concat(out)
w = r.pivot_table(index=["cls","factor","band","win_","level"], columns="per", values=["n","px","won","hold","s90"], observed=True)
w.columns = [f"{a}_{b}" for a, b in w.columns]; w = w.reset_index()
w.to_pickle(sys.argv[2]); print("cells:", len(w))
