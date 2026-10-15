import pandas as pd, numpy as np, sys
w = pd.read_pickle(sys.argv[1])
pd.set_option("display.width", 250); pd.set_option("display.max_rows", 200)
for cls in ["crypto","commod"]:
  for ex in ["hold","s90"]:
    c = w[w.cls==cls].copy()
    test = c[c.n_NEW >= 15]
    passed = test[(c.n_A >= 30) & (c.n_B >= 30) & (c[f"{ex}_A"] > 0) & (c[f"{ex}_B"] > 0)]
    base_pos = (test[f"{ex}_NEW"] > 0).mean()*100
    pass_pos = (passed[f"{ex}_NEW"] > 0).mean()*100 if len(passed) else float("nan")
    wavg = lambda d,p: (d[f"{ex}_{p}"]*d[f"n_{p}"]).sum()/d[f"n_{p}"].sum() if len(d) else float("nan")
    print(f"\n######## {cls}, exit = {'hold to settlement' if ex=='hold' else 'sell at 90c, else hold'}")
    print(f"settings with enough data: {len(test)}; positive on NEW: {base_pos:.0f}%")
    print(f"passed both Fri-Mon halves: {len(passed)}; of those positive on NEW: {pass_pos:.0f}%;  avg c/trade on NEW: passed {wavg(passed,'NEW'):+.1f} vs all {wavg(test,'NEW'):+.1f}")
    if len(passed):
        p = passed.assign(minAB=passed[[f"{ex}_A",f"{ex}_B"]].min(axis=1)).sort_values("minAB", ascending=False).head(15)
        p = p[["factor","band","win_","level","n_A",f"{ex}_A","n_B",f"{ex}_B","n_NEW","px_NEW","won_NEW",f"{ex}_NEW"]]
        p["won_NEW"] = (p.won_NEW*100).round(1)
        print(p.round(1).to_string(index=False))
