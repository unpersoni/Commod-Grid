import pandas as pd, numpy as np, math
def fee10(p): return math.ceil(0.07*10*p*(100-p)/100 - 1e-9)
g = pd.read_pickle("grid_rows2.pkl"); g = g[g.cls=="commod"]
e = g[g.px.between(85,95) & (g.sec > 420)].sort_values("ts").drop_duplicates("ticker").copy()   # V38 commodity early-fav buys
e["usd"] = ((100*e.win - e.px)*10 - e.px.map(fee10)) / 100
CUT = pd.Timestamp("2026-10-13 12:00"); e["per"] = np.where(e.close >= CUT, "Tue-Wed", "Fri-Mon")
print(f"V37 data, V38 commodity rule: {len(e)} trades, {int(e.win.sum())} won, {int((1-e.win).sum())} lost, total ${e.usd.sum():+.2f}")
print("\nBy commodity:")
print(e.groupby("asset").agg(trades=("win","size"), lost=("win", lambda x: int((1-x).sum())), win_rate=("win", lambda x: round(x.mean()*100,1)),
      total=("usd", lambda x: round(x.sum(),2)), fri_mon=("usd", lambda x: round(x[e.loc[x.index,'per']=='Fri-Mon'].sum(),2)),
      tue_wed=("usd", lambda x: round(x[e.loc[x.index,'per']=='Tue-Wed'].sum(),2))).to_string())
cy = e.groupby("close").agg(n=("win","size"), losses=("win", lambda x: int((1-x).sum())), usd=("usd","sum"))
print("\nCycles by number of commodity losses:", cy.losses.value_counts().sort_index().to_dict(), f"(of {len(cy)} cycles)")
# are losses bunched? compare with what independent trades would give
p = 1 - e.win.mean()
exp2 = sum(1 - (1-p)**n - n*p*(1-p)**(n-1) for n in cy.n)
print(f"cycles with 2+ losses: actual {int((cy.losses>=2).sum())}, expected if trades were independent {exp2:.1f}")
# rules that limit correlated exposure
METALS = {"GOLD","SILVER","PLATINUM","PALLADIUM"}
e = e.sort_values("ts")
def apply(rule):
    keep = []; seen = {}
    for i, r in e.iterrows():
        k = seen.setdefault(r.close, {"all":0, "metals":0})
        ok = rule(r, k)
        if ok:
            k["all"] += 1; k["metals"] += r.asset in METALS
        keep.append(ok)
    return e[np.array(keep)]
rules = {
 "V38 now (all commodities)":            lambda r,k: True,
 "skip WTI":                            lambda r,k: r.asset != "WTI",
 "max 1 metal per cycle (first one)":   lambda r,k: not (r.asset in METALS and k["metals"] >= 1),
 "max 2 commodities per cycle":         lambda r,k: k["all"] < 2,
 "max 1 commodity per cycle":           lambda r,k: k["all"] < 1,
 "skip WTI + max 1 metal per cycle":    lambda r,k: r.asset != "WTI" and not (r.asset in METALS and k["metals"] >= 1),
}
print("\nrule                                 trades  lost  total $   worst cycle $  worst 4-cycle stretch $  Fri-Mon $  Tue-Wed $")
for lab, f in rules.items():
    d = apply(f); c = d.groupby("close").usd.sum().sort_index()
    roll = c.rolling(4, min_periods=1).sum().min()
    print(f"{lab:36} {len(d):5} {int((1-d.win).sum()):5}  {d.usd.sum():+8.2f}   {c.min():+8.2f}       {roll:+8.2f}           {d[d.per=='Fri-Mon'].usd.sum():+8.2f}  {d[d.per=='Tue-Wed'].usd.sum():+8.2f}")
