import pandas as pd, numpy as np
r = pd.read_pickle("v37_phases.pkl"); r = r[r.dir.isin(["WIN","LOSS"])].copy()
g = pd.read_pickle("grid_rows.pkl"); g = g[g.cls=="crypto"]
r["side_l"] = r.side.str.lower()
f = g[["ticker","side","ts","spread","dir30","dir60","range60","open_lean","open_seen","z_spot","coin_chg60","btc_chg60","cyc_agree","cyc_disagree","prev_same","book_imb"]].sort_values("ts")
m = pd.merge_asof(r.sort_values("ts"), f, on="ts", left_by=["window","side_l"], right_by=["ticker","side"], direction="nearest", tolerance=pd.Timedelta("5s"))
print("trades matched to conditions:", m.spread.notna().mean().round(3))
cut3 = lambda x,a,b,l: np.where(x.isna(), None, np.where(x<=a,l[0],np.where(x>=b,l[2],l[1])))
C = {
 "01 minute": np.where(m.minute_in_market<=0,"min 0",np.where(m.minute_in_market==1,"min 1","min 2-3")),
 "02 spread": np.where(m.spread<=1,"1c",np.where(m.spread==2,"2c","3-5c")),
 "03 dir30": cut3(m.dir30,-2,2,["falling","flat","rising"]),
 "04 dir60": cut3(m.dir60,-3,3,["falling","flat","rising"]),
 "05 speed": cut3(m.range60,9,17,["slow","medium","fast"]),
 "06 open lean": cut3(m.open_lean.where(m.open_seen==True),-3,3,["opened underdog","opened even","opened favourite"]),
 "07 spot vs strike": cut3(m.z_spot,0,0.5,["against","slightly with","well with"]),
 "08 coin spot 60s": cut3(m.coin_chg60,-0.01,0.01,["against","flat","with"]),
 "09 BTC spot 60s": cut3(m.btc_chg60,-0.01,0.01,["against","flat","with"]),
 "10 cycle": cut3(m.cyc_agree-m.cyc_disagree,-2,2,["others against","mixed","others with"]),
 "11 streak": np.where(m.prev_same.isna(),None,np.where(m.prev_same==1,"prev won this side","prev won other side")),
 "12 book": cut3(m.book_imb,0.33,0.67,["against","balanced","with"]),
 "13 time ET": np.where(((m.ts-pd.Timedelta(hours=4)).dt.hour)<9,"night 0-9",np.where(((m.ts-pd.Timedelta(hours=4)).dt.hour)<18,"day 9-18","evening 18-24")),
 "14 side": m.side_l.values, "15 coin": m.asset.values,
}
print("\nShare of trades at each level, WIN phases vs LOSS phases (and the trade win rate at that level in each)")
for k, v in C.items():
    d = pd.DataFrame({"lvl": v, "dir": m.dir.values, "won": (m.usd>0).values}).dropna()
    tab = d.groupby(["lvl","dir"]).size().unstack(fill_value=0)
    sh = (tab/tab.sum()*100).round(0)
    wr = d.groupby(["lvl","dir"]).won.mean().unstack().mul(100).round(0)
    out = pd.DataFrame({"WIN share%": sh.get("WIN"), "LOSS share%": sh.get("LOSS"), "diff": (sh.get("WIN")-sh.get("LOSS")),
                        "won% in WIN": wr.get("WIN"), "won% in LOSS": wr.get("LOSS")})
    print(f"\n{k}"); print(out.to_string())
m.to_pickle("v37_phase_trades.pkl")
