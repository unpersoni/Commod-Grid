# %% [markdown]
# # Spot Distance Test — how far from the strike is "safe"?
#
# Crypto markets only (BTC, ETH, SOL, XRP, DOGE). Uses every tick log: the old
# commod15min_ticks.csv plus the new Commod1Dollar V1 / V30 tick files.
#
# 1. **Safety map** — at fixed moments (420s ... 10s before close), how often the side that
#    Coinbase spot favors (above / below the strike) wins, by how far spot is from the strike (%).
# 2. **"Never lost" thresholds** — per asset and moment: the smallest distance beyond which the
#    spot-favored side has NEVER lost here (at least 30 cases), plus the price Kalshi was
#    asking for that side at the time. Zero losses in n cases still allows a true loss rate
#    up to about 3/n — that column is shown.
# 3. **As an entry filter** — your 88-92c and 70-75c entries: only take the trade if spot is at
#    least X% on the favorite's side of the strike. Win rate and P&L for each X.
#
# 10 contracts, Kalshi taker fees included. Runtime -> Run all.

# %%
from google.colab import drive
drive.mount('/content/drive')

# %%
import csv, math, os, time, collections
import numpy as np
import pandas as pd

D = "/content/drive/MyDrive/"
TICK_FILES = [D + "commod15min_ticks.csv", D + "commod1dollar_v1_ticks.csv",
              D + "commod15min_v30_ticks.csv"]
SETTLE_FILES = [D + "commod1dollar_v1_settlements.csv", D + "commod15min_v30_settlements.csv"]
OUT_MAP    = D + "spot_safety_map.csv"
OUT_THRESH = D + "spot_thresholds.csv"
OUT_FILTER = D + "spot_entry_filter.csv"

CRYPTO      = {"BTC", "ETH", "SOL", "XRP", "DOGE"}
CHECKPOINTS = [420, 360, 300, 240, 180, 120, 90, 60, 45, 30, 20, 10]   # seconds before close
MATCH_SEC   = 3.0              # use the tick within 3s of each checkpoint
DIST_EDGES  = [0, 0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15, 0.25, 100]   # % from strike
THRESH_GRID = np.round(np.arange(0.0, 0.501, 0.005), 3)
MIN_N       = 30               # need at least this many cases beyond a threshold
BANDS       = [(88, 92), (70, 75)]
FILTER_D    = [None, 0.0, 0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15]
WINDOW_SEC, COVER_SLACK, DEADBAND_SEC, LOTS = 420, 20, 2, 10

def fee_c(p, n=LOTS):
    p = np.asarray(p, float)
    return np.ceil(0.07 * n * p * (100 - p) / 100 - 1e-9)

# %%
# LOAD — rows read by content (the old file mixes column layouts): ticker, asset, then the
# next four numbers are yes_bid, yes_ask, yes_mid, sec_to_close; spot and strike follow.
def num(x):
    try: return float(x)
    except (TypeError, ValueError): return None

t0 = time.time()
recs, cover = [], collections.defaultdict(float)
for path in TICK_FILES:
    if not os.path.exists(path):
        print("not found (skipped):", path); continue
    n0 = len(recs)
    with open(path, newline="") as fh:
        rd = csv.reader(fh); next(rd, None)
        for row in rd:
            ti = next((i for i, f in enumerate(row[:4]) if "15M-" in f), None)
            if ti is None or ti + 1 >= len(row) or row[ti + 1] not in CRYPTO: continue
            vals, first = [], None
            for j in range(ti + 2, len(row)):
                v = num(row[j]) if row[j] != "" else None
                if v is None:
                    if vals: break
                    continue
                if first is None: first = j
                vals.append(v)
                if len(vals) == 4: break
            if len(vals) < 4: continue
            yb, ya, mid, s = vals
            if not (0 <= yb <= ya <= 100 and abs(mid - (yb + ya) / 2) <= 1 and -60 <= s <= 900):
                continue
            t = row[ti]
            cover[t] = max(cover[t], s)
            if s > WINDOW_SEC + 30: continue
            spot = strike = np.nan
            if first + 5 < len(row):
                sp, sk = num(row[first + 4]), num(row[first + 5])
                if sk and sk > 0:
                    strike = sk
                    if sp and abs(sp / sk - 1) < 0.05: spot = sp
            recs.append((t, row[ti + 1], yb, ya, mid, s, spot, strike))
    print(f"{os.path.basename(path)}: {len(recs) - n0:,} crypto rows kept")
df = pd.DataFrame(recs, columns=["ticker", "asset", "yb", "ya", "mid", "s2c", "spot", "strike"])
del recs

official = {}
for path in SETTLE_FILES:
    if os.path.exists(path):
        s = pd.read_csv(path)
        official.update({t: r == "yes" for t, r in zip(s["ticker"], s["result"]) if r in ("yes", "no")})

def price_result(s2c, mid, yb, ya):
    m = s2c <= 15
    iy = np.flatnonzero(m & ((mid >= 99) | (yb >= 99)))
    ino = np.flatnonzero(m & ((mid <= 1) | (ya <= 1)))
    if not len(iy) and not len(ino): return None
    if not len(ino): return True
    if not len(iy): return False
    return bool(iy[-1] > ino[-1])

markets = []
for t, g in df.groupby("ticker", sort=False):
    g = g.drop_duplicates("s2c").sort_values("s2c", ascending=False)
    s2c = g["s2c"].to_numpy(float)
    yb, ya, mid = (g[c].to_numpy(float) for c in ("yb", "ya", "mid"))
    res = official.get(t)
    if res is None: res = price_result(s2c, mid, yb, ya)
    sk = g["strike"].dropna()
    if res is None or not len(sk): continue
    k = s2c > DEADBAND_SEC
    markets.append({"t": t, "asset": g["asset"].iloc[0], "yes_won": res, "cover": cover[t],
                    "strike": float(sk.iloc[0]), "s2c": s2c[k], "yb": yb[k], "ya": ya[k],
                    "mid": mid[k], "spot": g["spot"].to_numpy(float)[k]})
del df
print(f"{len(markets):,} crypto markets with a known result and strike "
      f"({sum(m['t'] in official for m in markets):,} from official settlement files) "
      f"in {time.time() - t0:.0f}s")

# %%
# Direction check: does spot above the strike mean YES? (checked, not assumed)
agree = n_dir = 0
for m in markets:
    ok = np.isfinite(m["spot"]) & (m["s2c"] <= 10)
    if ok.any():
        n_dir += 1
        agree += (m["spot"][ok][-1] > m["strike"]) == m["yes_won"]
UP_IS_YES = agree >= n_dir / 2
print(f"spot above strike in the final 10s matched YES in {agree / max(n_dir, 1):.1%} of "
      f"{n_dir:,} markets -> YES = {'above' if UP_IS_YES else 'below'} the strike")

# One observation per market per checkpoint (not every tick, so busy markets don't dominate)
obs = []
for m in markets:
    s, sp = m["s2c"], m["spot"]
    for c in CHECKPOINTS:
        cand = np.flatnonzero(np.isfinite(sp) & (np.abs(s - c) <= MATCH_SEC))
        if not len(cand): continue
        i = cand[np.argmin(np.abs(s[cand] - c))]
        pct = (sp[i] - m["strike"]) / m["strike"] * 100
        spot_yes = (pct > 0) == UP_IS_YES
        obs.append({"asset": m["asset"], "checkpoint": c, "dist_pct": abs(pct),
                    "won": spot_yes == m["yes_won"],
                    "side_ask": m["ya"][i] if spot_yes else 100 - m["yb"][i]})
O = pd.DataFrame(obs)
print(f"{len(O):,} market-moments with spot data")

# %%
# 1. SAFETY MAP
O["dist_band"] = pd.cut(O.dist_pct, DIST_EDGES, right=False,
                        labels=[f"{a}-{b}%" if b < 100 else f"{a}%+" for a, b in zip(DIST_EDGES, DIST_EDGES[1:])])
rows = []
for asset in ["ALL"] + sorted(O.asset.unique()):
    sub = O if asset == "ALL" else O[O.asset == asset]
    for (c, b), g in sub.groupby(["checkpoint", "dist_band"], observed=True):
        rows.append({"asset": asset, "sec_left": c, "distance": b, "cases": len(g),
                     "spot_side_won": int(g.won.sum()), "lost": int((~g.won).sum()),
                     "win_rate": round(g.won.mean(), 4), "avg_ask_c": round(g.side_ask.mean(), 1)})
MAP = pd.DataFrame(rows)
MAP.to_csv(OUT_MAP, index=False)
print("1. SPOT-FAVORED SIDE WIN RATE (all crypto) — rows: seconds left, columns: distance from strike")
display(MAP[MAP.asset == "ALL"].pivot(index="sec_left", columns="distance", values="win_rate")
        .sort_index(ascending=False))
print("   ...and how many cases each cell is based on")
display(MAP[MAP.asset == "ALL"].pivot(index="sec_left", columns="distance", values="cases")
        .sort_index(ascending=False))

# %%
# 2. "NEVER LOST" THRESHOLDS
th = []
for asset in ["ALL"] + sorted(O.asset.unique()):
    sub = O if asset == "ALL" else O[O.asset == asset]
    for c in CHECKPOINTS:
        g = sub[sub.checkpoint == c]
        if not len(g): continue
        for label, max_loss_rate in (("never lost", 0.0), ("99%+ won", 0.01)):
            hit = None
            for d in THRESH_GRID:
                beyond = g[g.dist_pct >= d]
                if len(beyond) < MIN_N: break
                if (~beyond.won).mean() <= max_loss_rate:
                    hit = (d, beyond); break
            if hit is None:
                th.append({"asset": asset, "sec_left": c, "rule": label, "min_distance_pct": None})
                continue
            d, b = hit
            th.append({"asset": asset, "sec_left": c, "rule": label, "min_distance_pct": d,
                       "cases_beyond": len(b), "lost": int((~b.won).sum()),
                       "true_loss_rate_could_be_up_to": f"{min(1, 3 / len(b)):.1%}",
                       "share_of_all_moments": f"{len(b) / len(g):.0%}",
                       "avg_ask_c": round(b.side_ask.mean(), 1),
                       "avg_profit_per_contract_c": round(b.won.mean() * 100 - b.side_ask.mean(), 2)})
TH = pd.DataFrame(th)
TH.to_csv(OUT_THRESH, index=False)
print("2. SMALLEST DISTANCE WHERE THE SPOT-FAVORED SIDE NEVER LOST (all crypto)")
print("   avg_ask_c = what Kalshi charged for that side at those moments")
display(TH[(TH.asset == "ALL") & (TH.rule == "never lost")].drop(columns=["asset", "rule"])
        .reset_index(drop=True))
print("   per asset (never lost)")
display(TH[(TH.asset != "ALL") & (TH.rule == "never lost")]
        .pivot(index="sec_left", columns="asset", values="min_distance_pct").sort_index(ascending=False))

# %%
# 3. AS AN ENTRY FILTER for the 88-92c and 70-75c strategies (hold to settlement)
full = [m for m in markets if m["cover"] >= WINDOW_SEC - COVER_SLACK]
frows = []
for lo, hi in BANDS:
    E = []
    for m in full:
        s, yb, ya, mid, sp = m["s2c"], m["yb"], m["ya"], m["mid"], m["spot"]
        ok = s <= WINDOW_SEC
        in_yes = ok & (ya >= lo) & (ya <= hi)
        in_no = ok & (100 - yb >= lo) & (100 - yb <= hi)
        idx = np.flatnonzero(in_yes | in_no)
        if not len(idx): continue
        i = int(idx[0])
        yes = bool(in_yes[i] and (not in_no[i] or mid[i] >= 50))
        price = float(ya[i] if yes else 100 - yb[i])
        near = np.flatnonzero(np.isfinite(sp) & (np.abs(s - s[i]) <= MATCH_SEC))
        if not len(near): continue
        j = near[np.argmin(np.abs(s[near] - s[i]))]
        pct = (sp[j] - m["strike"]) / m["strike"] * 100
        support = pct if (yes == UP_IS_YES) else -pct     # + = spot on the favorite's side
        E.append((price, yes == m["yes_won"], support))
    if not E: continue
    p = np.array([e[0] for e in E]); w = np.array([e[1] for e in E]); sup = np.array([e[2] for e in E])
    for dmin in FILTER_D:
        keep = np.ones(len(E), bool) if dmin is None else sup >= dmin
        if not keep.any(): continue
        pk, wk = p[keep], w[keep]
        hold = (np.where(wk, 100 - pk, -pk) * LOTS - fee_c(pk)).sum() / 100
        frows.append({"band": f"{lo}-{hi}c",
                      "filter": "no filter" if dmin is None else f"spot >= {dmin}% on favorite's side",
                      "trades": int(keep.sum()), "skipped": int((~keep).sum()),
                      "won": int(wk.sum()), "lost": int((~wk).sum()),
                      "win_rate": round(wk.mean(), 4), "hold_$": round(hold, 2),
                      "per_trade_$": round(hold / keep.sum(), 3)})
FIL = pd.DataFrame(frows)
FIL.to_csv(OUT_FILTER, index=False)
print("3. ENTRY FILTER — only buy the favorite if spot is at least X% on its side of the strike")
display(FIL)
