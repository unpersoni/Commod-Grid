import pandas as pd, numpy as np, itertools
e = pd.read_pickle("cross90.pkl")
CUT = pd.Timestamp("2026-10-13 12:00"); oldmed = e.close[e.close < CUT].median()
e["per"] = np.where(e.close >= CUT, "NEW", np.where(e.close <= oldmed, "A", "B"))
cut3 = lambda x, a, b, l: pd.Series(np.where(x.isna(), None, np.where(x <= a, l[0], np.where(x >= b, l[2], l[1]))), index=x.index)
et_h = (e.ts - pd.Timedelta(hours=4)).dt.hour
C = {"time left": pd.Series(np.where(e.sec > 660, "min 0-3", np.where(e.sec > 240, "min 4-10", "min 11-14")), index=e.index),
     "cross no.": pd.Series(np.where(e.cross_no == 1, "1st cross", np.where(e.cross_no == 2, "2nd", "3rd+")), index=e.index),
     "spread": pd.Series(np.where(e.spread <= 1, "1c", "2c+"), index=e.index),
     "dir30": cut3(e.dir30, -2, 2, ["falling", "flat", "rising"]), "dir60": cut3(e.dir60, -3, 3, ["falling", "flat", "rising"]),
     "speed": cut3(e.range60, 9, 17, ["slow", "medium", "fast"]),
     "open lean": cut3(e.open_lean.where(e.open_seen == True), -3, 3, ["opened underdog", "opened even", "opened favourite"]),
     "spot vs strike": cut3(e.z_spot, 0.5, 1.5, ["spot close", "spot medium", "spot far on our side"]),
     "coin spot 60s": cut3(e.coin_chg60, -0.01, 0.01, ["coin against", "coin flat", "coin with"]),
     "BTC spot 60s": cut3(e.btc_chg60, -0.01, 0.01, ["BTC against", "BTC flat", "BTC with"]),
     "cycle": cut3(e.cyc_agree - e.cyc_disagree, -2, 2, ["others against", "mixed", "others with"]),
     "streak": pd.Series(np.where(e.prev_same.isna(), None, np.where(e.prev_same == 1, "prev won this side", "prev won other")), index=e.index),
     "book": cut3(e.book_imb, 0.33, 0.67, ["book against", "book balanced", "book with"]),
     "time ET": pd.Series(np.where(et_h < 9, "night", np.where(et_h < 18, "day", "evening")), index=e.index)}
rows = []
names = list(C)
combos = [(a,) for a in names] + list(itertools.combinations(names, 2))
for cls in ["crypto", "commod"]:
    d0 = e[e.cls == cls]
    for combo in combos:
        key = pd.Series(list(zip(*[C[c].loc[d0.index] for c in combo])), index=d0.index)
        ok = key.map(lambda k: all(isinstance(v, str) for v in k))
        d = d0[ok].assign(k=key[ok].map(lambda k: " & ".join(k)))
        a = d.groupby(["k", "per"]).agg(n=("net", "size"), net=("net", "mean"), final=("final", "mean"), win=("win", "mean"), px=("px", "mean")).unstack()
        for k, r in a.iterrows():
            rows.append(dict(cls=cls, conds=" + ".join(combo), level=k, **{f"{m}_{p}": r.get((m, p)) for m in ["n", "net", "final", "win", "px"] for p in ["A", "B", "NEW"]}))
R = pd.DataFrame(rows).fillna({"n_A": 0, "n_B": 0, "n_NEW": 0})
R.to_pickle("cross90_cond.pkl")
for cls in ["crypto", "commod"]:
    x = R[(R.cls == cls) & (R.n_A >= 30) & (R.n_B >= 30) & (R.n_NEW >= 20)].copy()
    x["final_all"] = (x.final_A*x.n_A + x.final_B*x.n_B + x.final_NEW*x.n_NEW) / (x.n_A + x.n_B + x.n_NEW)
    print(f"\n######## {cls}: {len(x)} condition settings with enough crosses (>=30 per Fri-Mon half, >=20 new)")
    print("Highest 'final cross' rates (all periods):")
    print(x.sort_values("final_all", ascending=False).head(8)[["conds","level","n_A","n_B","n_NEW","final_A","final_B","final_NEW","net_NEW"]].round(2).to_string(index=False))
    passed = x[(x.net_A > 0) & (x.net_B > 0)]
    print(f"\nProfitable in both Fri-Mon halves: {len(passed)} of {len(x)}; also profitable on Tue-Wed: {(passed.net_NEW > 0).sum()} "
          f"(any setting profitable on Tue-Wed: {(x.net_NEW > 0).mean()*100:.0f}%, passers: {(passed.net_NEW > 0).mean()*100 if len(passed) else 0:.0f}%)")
    p = passed.assign(m=passed[["net_A","net_B","net_NEW"]].min(axis=1)).sort_values("m", ascending=False).head(12)
    print(p[["conds","level","n_A","net_A","n_B","net_B","n_NEW","net_NEW","win_NEW","final_NEW"]].round(2).to_string(index=False))
