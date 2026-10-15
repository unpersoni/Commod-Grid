import pandas as pd, numpy as np, sys
r = pd.read_pickle(sys.argv[1])
EX = ["hold"] + [f"t{T}" for T in range(10, 51, 2)]
K = ["cls","band","win_","factor","level"]
w = r.pivot_table(index=K, columns="per", values=["n"] + EX, aggfunc="sum", fill_value=0)
long = []
for ex in EX:
    d = pd.DataFrame({p: w[(ex, p)] / w[("n", p)].replace(0, np.nan) for p in ["A","B","NEW"]})
    for p in ["A","B","NEW"]: d[f"n_{p}"] = w[("n", p)]
    d["exit"] = ex; long.append(d.reset_index())
L = pd.concat(long, ignore_index=True)
L.to_pickle("biggrid_long.pkl")
print(f"settings: {len(L):,}")
for cls in ["crypto","commod"]:
    for minA, minN in [(20,10),(40,20)]:
        e = L[(L.cls==cls) & (L.n_A>=minA) & (L.n_B>=minA) & (L.n_NEW>=minN)]
        pa, pb, pn = (e.A>0).mean(), (e.B>0).mean(), (e.NEW>0).mean()
        ps = e[(e.A>0)&(e.B>0)]; all3 = ps[ps.NEW>0]
        print(f"\n{cls}, min {minA} buys in each Fri-Mon half and {minN} on Tue-Wed: {len(e):,} settings with enough data")
        print(f"  positive in half A {pa*100:.0f}%, half B {pb*100:.0f}%, Tue-Wed {pn*100:.0f}%")
        print(f"  passed both Fri-Mon halves: {len(ps):,}  -> positive on Tue-Wed: {len(all3):,} ({len(all3)/max(len(ps),1)*100:.0f}% vs {pn*100:.0f}% for any setting)")
        print(f"  positive in all three: {len(all3):,}; expected by pure chance (if periods were unrelated): {len(e)*pa*pb*pn:,.0f}")
        print(f"  avg c/trade on Tue-Wed: passers {np.average(ps.NEW, weights=ps.n_NEW) if len(ps) else float('nan'):+.2f} vs all {np.average(e.NEW, weights=e.n_NEW):+.2f}")
