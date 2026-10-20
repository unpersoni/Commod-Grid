# How often do all 5 crypto 15-minute markets (BTC, ETH, SOL, XRP, DOGE) settle the same way in the same window?
# Uses every settlement file in data/ (Sep 30 - Oct 17 2026); only windows where all 5 results are known count.
import pandas as pd, numpy as np, glob, itertools, sys
R = sys.argv[1] if len(sys.argv) > 1 else "data"
CR = ["BTC", "ETH", "SOL", "XRP", "DOGE"]
s = pd.concat([pd.read_csv(f, usecols=["ticker", "asset", "close_time", "result"])
               for f in sorted(glob.glob(R + "/settlements/*.csv") + glob.glob(R + "/v38/*settlements*.csv"))])
s = s[s.asset.isin(CR) & s.result.isin(["yes", "no"])].drop_duplicates("ticker", keep="last")
s["up"] = (s.result == "yes").astype(int)
w = s.pivot_table(index="close_time", columns="asset", values="up", aggfunc="first")[CR].dropna().astype(int)
w.index = pd.to_datetime(w.index, utc=True)
n = len(w)
ups = w.sum(axis=1)
print(f"windows with all 5 results: {n}  ({w.index.min():%b %d %H:%M} - {w.index.max():%b %d %H:%M} UTC)")
print(f"each coin's 'up' rate: " + ", ".join(f"{a} {100 * w[a].mean():.1f}%" for a in CR))
same = ups.isin([0, 5])
print(f"\nALL 5 THE SAME WAY: {same.sum()} of {n} = {100 * same.mean():.1f}%   (all up {100 * (ups == 5).mean():.1f}%, all down {100 * (ups == 0).mean():.1f}%)")
# if the coins moved independently (each with its own up rate), how often would all 5 agree?
p = w.mean()
indep = p.prod() + (1 - p).prod()
print(f"if the 5 coins were unrelated it would be {100 * indep:.1f}%")
split = ups.map(lambda k: f"{max(k, 5 - k)}-{min(k, 5 - k)}")
print("\nsplit of the 5 results (majority-minority):")
for k in ("5-0", "4-1", "3-2"):
    print(f"   {k}: {100 * (split == k).mean():5.1f}%  ({(split == k).sum()} windows)")
print("\npairs settling the same way:")
for a, b in itertools.combinations(CR, 2):
    print(f"   {a}-{b}: {100 * (w[a] == w[b]).mean():.1f}%")
print("\nhow many coins follow BTC:")
fol = (w[["ETH", "SOL", "XRP", "DOGE"]].values == w[["BTC"]].values).sum(axis=1)
for k in range(5): print(f"   {k} of 4 same as BTC: {100 * (fol == k).mean():5.1f}%")
w["day"] = w.index.strftime("%b %d")
d = w.assign(same=same.values).groupby("day").same.agg(["size", "mean"])
print("\nby day (windows, all-5-same %):")
for day, r in d.iterrows(): print(f"   {day}: {int(r['size']):3}  {100 * r['mean']:5.1f}%")
h = w.assign(same=same.values, hr=w.index.hour).groupby("hr").same.agg(["size", "mean"])
print("\nby hour UTC (windows, all-5-same %):")
print("   " + "  ".join(f"{hr:02d}h {100 * r['mean']:.0f}% ({int(r['size'])})" for hr, r in h.iterrows()))
