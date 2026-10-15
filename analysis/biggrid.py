import pandas as pd, numpy as np, math, sys, time
def fee(p): return math.ceil(0.07*p*(100-p)/100 - 1e-9)
FEE = np.array([fee(p) for p in range(101)])
g = pd.read_pickle(sys.argv[1])
CUT = pd.Timestamp("2026-10-13 12:00")
per = np.where(g.close >= CUT, "NEW", "OLD"); oldmed = g.close[per=="OLD"].median()
per = np.where(per=="NEW", "NEW", np.where(g.close <= oldmed, "A", "B")); g["per"] = per
g["band"] = ((g.px - 10)//5).clip(0, 17).astype(int)            # 0 = 10-14c ... 17 = 95-99c
g["win_"] = ((900 - g.sec)//30).clip(0, 29).astype(int)          # 0 = 0:00-0:30 ... 29 = 14:30-15:00
TG = list(range(10, 51, 2))
cut3 = lambda x, a, b, labs: pd.Series(np.where(x.isna(), None, np.where(x <= a, labs[0], np.where(x >= b, labs[2], labs[1]))), index=x.index)
et_h = (g.ts - pd.Timedelta(hours=4)).dt.hour
F = {
 "00 none": pd.Series("all", index=g.index),
 "02 spread": pd.Series(np.where(g.spread<=1,"1c",np.where(g.spread==2,"2c","3-5c")), index=g.index),
 "03 dir30": cut3(g.dir30,-2,2,["falling","flat","rising"]),
 "04 dir60": cut3(g.dir60,-3,3,["falling","flat","rising"]),
 "05 speed": cut3(g.range60,9,17,["slow","medium","fast"]),
 "06 open lean": cut3(g.open_lean.where(g.open_seen),-3,3,["opened underdog","opened even","opened favourite"]),
 "07 spot vs strike": cut3(g.z_spot,0,0.5,["against","slightly with","well with"]),
 "08 coin spot 60s": cut3(g.coin_chg60,-0.01,0.01,["against","flat","with"]),
 "09 BTC spot 60s": cut3(g.btc_chg60,-0.01,0.01,["against","flat","with"]),
 "10 cycle": cut3(g.cyc_agree-g.cyc_disagree,-2,2,["others against","mixed","others with"]),
 "11 streak": pd.Series(np.where(g.prev_same.isna(),None,np.where(g.prev_same==1,"prev won this side","prev won other side")), index=g.index),
 "12 book": cut3(g.book_imb,0.33,0.67,["against","balanced","with"]),
 "13 time ET": pd.Series(np.where(et_h<9,"night 0-9",np.where(et_h<18,"day 9-18","evening 18-24")), index=g.index),
}
base = g[["cls","ticker","side","ts","per","band","win_","px","win","net_hold","fmax"]].sort_values("ts")
out = []; t0 = time.time()
for fname, lev in F.items():
    d = base.assign(level=lev.reindex(base.index).values).dropna(subset=["level"])
    d = d.drop_duplicates(["cls","band","win_","level","ticker","side"])      # first moment per market & side
    px = d.px.values; nets = {"hold": d.net_hold.values}
    for T in TG:
        tgt = px + T
        hit = (tgt <= 99) & (d.fmax.values >= tgt)
        nets[f"t{T}"] = np.where(hit, T - FEE[px] - FEE[np.minimum(tgt, 100)], d.net_hold.values)
    m = pd.DataFrame(nets, index=d.index)
    m[["cls","band","win_","level","per"]] = d[["cls","band","win_","level","per"]]
    m["n"] = 1; m["won"] = d.win.values; m["px"] = px
    a = m.groupby(["cls","band","win_","level","per"]).sum(numeric_only=True).reset_index()
    a["factor"] = fname; out.append(a)
    print(f"{fname}: {len(d):,} buys, {len(a):,} cells  ({time.time()-t0:.0f}s)", flush=True)
r = pd.concat(out, ignore_index=True)
r.to_pickle(sys.argv[2]); print("rows", len(r))
