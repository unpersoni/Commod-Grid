import pandas as pd, numpy as np, sys
D = sys.argv[1]
mk = pd.read_csv(f"{D}/markets.csv"); mk = mk[mk.result.isin(["yes", "no"])].copy()
mk["move"] = (mk.expiration_value / mk.strike - 1) * 1e4          # the underlying's move over the 15 min, basis points
w = mk.pivot_table(index="close_time", columns="series", values="up_won")
mv = mk.pivot_table(index="close_time", columns="series", values="move")
short = {c: c.replace("KX", "").replace("15M", "") for c in w.columns}
w, mv = w.rename(columns=short), mv.rename(columns=short)
print("windows:", len(w), "| windows with all 11 series:", int(w.notna().all(axis=1).sum()))
pd.set_option("display.width", 200)
print("\nOUTCOME correlation (UP won 1/0), same 15-min window:")
print(w.corr(min_periods=100).round(2).to_string())
print("\nMOVE correlation (underlying % move over the window):")
print(mv.corr(min_periods=100).round(2).to_string())
groups = {"crypto": ["BTC", "ETH", "SOL", "XRP", "DOGE"], "metals": ["GOLD", "SILVER", "PLATINUM", "PALLADIUM", "COPPER"], "oil": ["WTI"]}
def avg_off(cm):
    a = cm.values[np.triu_indices(len(cm), 1)]; return float(np.nanmean(a))
print("\naverage outcome correlation, and how many independent bets N markets in one window are worth (N / (1 + (N-1)·r)):")
for g, cols in groups.items():
    if len(cols) < 2: continue
    r = avg_off(w[cols].corr(min_periods=100))
    print(f"  {g:7} r = {r:.2f}  -> {len(cols)} markets act like {len(cols) / (1 + (len(cols) - 1) * r):.1f}")
allc = list(w.columns); r_all = avg_off(w[allc].corr(min_periods=100))
cross = np.nanmean([w[a].corr(w[b]) for a in groups["crypto"] for b in groups["metals"]])
print(f"  all 11  r = {r_all:.2f}  -> 11 markets act like {11 / (1 + 10 * r_all):.1f}   (crypto vs metals r = {cross:.2f})")
print("\none series, consecutive windows (does one window's result carry into the next?):")
print("  ", {c: round(w[c].autocorr(1), 2) for c in w.columns})
# a bad window: share of windows where 5 crypto all went the same way
cw = w[groups["crypto"]].dropna()
same = ((cw.sum(axis=1) == 0) | (cw.sum(axis=1) == 5)).mean()
print(f"\nwindows where all 5 crypto went the same way: {same:.0%}   (if independent: {2 * 0.5 ** 5:.0%})")
mw = w[groups["metals"]].dropna()
same_m = ((mw.sum(axis=1) == 0) | (mw.sum(axis=1) == 5)).mean()
print(f"windows where all 5 metals went the same way: {same_m:.0%}")
