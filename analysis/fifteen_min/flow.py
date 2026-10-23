import pandas as pd, numpy as np, glob, os, sys
D = sys.argv[1]
mk = pd.read_csv(f"{D}/markets.csv"); mk = mk[mk.result.isin(["yes", "no"])]
won = mk.set_index("ticker").up_won
rows = []
for f in sorted(glob.glob(f"{D}/prices15s_*.csv.gz")):
    p = pd.read_csv(f); p["series"] = os.path.basename(f)[10:-7]; rows.append(p)
p = pd.concat(rows)
p = p[p.ticker.isin(won.index) & (p.trades > 0)].copy()
p["won"] = p.ticker.map(won)
p = p.sort_values(["ticker", "sec_to_close"], ascending=[True, False])
# price a little later in the same market (for short-horizon markouts), UP side in cents
for k, lag in (("up_1m", 4), ("up_5m", 20)):
    p[k] = p.groupby("ticker").up_last.shift(-lag)
p["fin"] = 100 * p.won
for k in ("up_1m", "up_5m"): p[k] = p[k].fillna(p.fin)       # past the close: the settlement
U, Dn = p.contracts_taker_bought_up, p.contracts_taker_bought_down
# a taker who buys UP at up_last gains (later UP price - up_last); a DOWN taker gains the opposite move
def taker_gain(col):  # cents per contract, contract weighted, over both directions
    g = U * (p[col] - p.up_last) + Dn * (p.up_last - p[col])
    return g
for col in ("up_1m", "up_5m", "fin"): p["g_" + col] = taker_gain(col)
p["n"] = U + Dn
def summ(g):
    n = g.n.sum()
    return pd.Series({"contracts(k)": n / 1000, "after 1 min": g.g_up_1m.sum() / n, "after 5 min": g.g_up_5m.sum() / n,
                      "at settlement": g.g_fin.sum() / n})
print("B) TAKERS' GAIN in cents per contract (negative = takers lose = resting orders earn it), price = last trade of each 15 s")
print(p.groupby("series").apply(summ).round(2).to_string())
print("\nall series:"); print(summ(p).round(2).to_string())
p["left"] = pd.cut(p.sec_to_close, [0, 60, 180, 420, 900], labels=["last 1 min", "1-3 min", "3-7 min", "7-15 min"])
p["fav"] = np.maximum(p.up_last, 100 - p.up_last)
p["zone"] = pd.cut(p.fav, [49, 60, 75, 90, 100], labels=["50-60", "60-75", "75-90", "90-99"])
print("\nby time left (all series):"); print(p.groupby("left", observed=True).apply(summ).round(2).to_string())
print("\nby favourite's price (all series):"); print(p.groupby("zone", observed=True).apply(summ).round(2).to_string())
# who is buying what: takers buying the favourite vs the underdog
fav_up = p.up_last >= 50
p["n_fav"] = np.where(fav_up, U, Dn); p["n_dog"] = np.where(fav_up, Dn, U)
gf = np.where(fav_up, U * (p.fin - p.up_last), Dn * (p.up_last - p.fin))
gd = np.where(fav_up, Dn * (p.up_last - p.fin), U * (p.fin - p.up_last))
print(f"\ntakers buying the favourite: {p.n_fav.sum()/1e6:.1f}M contracts, {gf.sum()/p.n_fav.sum():+.2f}c each at settlement")
print(f"takers buying the underdog:  {p.n_dog.sum()/1e6:.1f}M contracts, {gd.sum()/p.n_dog.sum():+.2f}c each at settlement")
# per-day stability of the settlement number
p["day"] = p.ticker.str.extract(r"-26OCT(\d\d)")[0]
print("\nat settlement, by day (all series):", p.groupby("day").apply(lambda g: round(g.g_fin.sum() / g.n.sum(), 2)).to_dict())
