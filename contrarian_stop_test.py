# %% [markdown]
# # Contrarian Stop Test — V33 rules, full 15 minutes, with a stop on the opposite side
#
# V33 CONTRARIAN rules: when the favorite's ask is 51-55c, buy the OPPOSITE side (10 contracts).
# If the favorite's bid then dips to 27c: sell the opposite and buy the favorite (reversal),
# held to settlement — unless the favorite's bid is already below 23c (then hold the opposite).
#
# NEW: a stop on the opposite side. If the opposite's bid falls to X (the favorite is running
# away), sell the opposite and stop. X = none (V33), 20, 25, 30, 35, 40c.
#
# Entry windows compared on the SAME markets (only markets logged for the full 15 minutes):
# - last 7 min (what V33 did) · first 8 min only · full 15 min
#
# Categories (same as the V33 breakdown):
# 1. opposite sold at ~73c when the favorite's bid hit 27c   2. the reversal that follows
# 3. opposite held to settlement (favorite never dipped to 27c)   4. held: favorite already < 23c
# 5. NEW: opposite stopped out at X
#
# Fills at real prices: buys at the ask, sells at the bid. The V33 paper log filled the
# reversal at the favorite's BID (about 1c better per leg); a check at the end shows both.
# Fees as in V33: Kalshi taker fee rounded up per contract. Runtime -> Run all.

# %%
from google.colab import drive
drive.mount('/content/drive')

# %%
import csv, os, time, math, collections, gc
from array import array
import numpy as np
import pandas as pd

D = "/content/drive/MyDrive/"
TICK_FILES = [D + f for f in ("commod15min_v33_ticks.csv", "commod15min_v31_ticks.csv",
                              "commod15min_v30_ticks.csv", "commod1dollar_v2_ticks.csv",
                              "commod1dollar_v1_ticks.csv")]
SETTLE_FILES = [D + f for f in ("commod15min_v33_settlements.csv", "commod15min_v31_settlements.csv",
                                "commod15min_v30_settlements.csv", "commod1dollar_v2_settlements.csv",
                                "commod1dollar_v1_settlements.csv")]
V33_TRADES = D + "commod15min_v33_trades.csv"
OUT_GRID   = D + "contrarian_stop_grid.csv"
OUT_TRADES = D + "contrarian_stop_trades.csv"

CRYPTO      = {"BTC", "ETH", "SOL", "XRP", "DOGE"}
COVER_MIN   = 860          # market must be logged from 860s+ before close (full 15 minutes)
DEADBAND    = 2            # never act in the last 2 seconds
FAV_ASK     = (51, 55)     # entry: favorite's ask in this range -> buy the opposite
REV_TRIGGER = 27           # favorite's bid <= 27c -> reverse into the favorite
REV_FLOOR   = 23           # ...unless it's already below 23c (then hold the opposite)
STOPS       = [None, 20, 25, 30, 35, 40]
WINDOWS     = {"last 7 min (V33)": (420, DEADBAND), "first 8 min only": (900, 420),
               "full 15 min": (900, DEADBAND)}
LOTS        = 10

def fee(p):     # V33's fee: Kalshi taker fee rounded up per contract, x10 contracts
    return math.ceil(7 * (p / 100) * (1 - p / 100)) * LOTS

# %%
# LOAD — rows read by content (files mix column layouts): ticker, asset, then the next four
# numbers are yes_bid, yes_ask, yes_mid, sec_to_close; spot and strike follow.
def num(x):
    try: return float(x)
    except (TypeError, ValueError): return None

t0 = time.time()
tid_of, tickers, asset_of = {}, [], []
TID, YB, YA, S2C, SPOT, STRIKE = array("i"), array("f"), array("f"), array("f"), array("d"), array("d")
cover = collections.defaultdict(float)
NAN = float("nan")
for path in TICK_FILES:
    if not os.path.exists(path):
        print("not found (skipped):", path); continue
    n0 = len(TID)
    with open(path, newline="") as fh:
        rd = csv.reader(fh); next(rd, None)
        for row in rd:
            ti = next((i for i, f in enumerate(row[:4]) if "15M-" in f), None)
            if ti is None or ti + 1 >= len(row): continue
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
            if not (0 <= yb <= ya <= 100 and abs(mid - (yb + ya) / 2) <= 1 and -60 <= s <= 930):
                continue
            t = row[ti]
            if s > cover[t]: cover[t] = s
            spot = strike = NAN
            if row[ti + 1] in CRYPTO and first + 5 < len(row):
                sp, sk = num(row[first + 4]), num(row[first + 5])
                if sk and sk > 0:
                    strike = sk
                    if sp and abs(sp / sk - 1) < 0.05: spot = sp
            k = tid_of.get(t)
            if k is None:
                k = tid_of[t] = len(tickers); tickers.append(t); asset_of.append(row[ti + 1])
            TID.append(k); YB.append(yb); YA.append(ya); S2C.append(s); SPOT.append(spot); STRIKE.append(strike)
    print(f"{os.path.basename(path)}: {len(TID) - n0:,} rows kept ({time.time() - t0:.0f}s)")

if not len(TID):
    raise SystemExit("No tick rows found — are the bots' tick files on your Drive?")
TID, YB, YA, S2C = (np.frombuffer(a, dtype=d) for a, d in
                    ((TID, np.int32), (YB, np.float32), (YA, np.float32), (S2C, np.float32)))
SPOT, STRIKE = np.frombuffer(SPOT, dtype=np.float64), np.frombuffer(STRIKE, dtype=np.float64)
order = np.lexsort((-S2C, TID))
TID, YB, YA, S2C, SPOT, STRIKE = (a[order] for a in (TID, YB, YA, S2C, SPOT, STRIKE))
del order; gc.collect()
bounds = np.flatnonzero(np.diff(TID)) + 1
starts, ends = np.r_[0, bounds], np.r_[bounds, len(TID)]

official = {}
for path in SETTLE_FILES:
    if os.path.exists(path):
        sf = pd.read_csv(path)
        official.update({t: r == "yes" for t, r in zip(sf["ticker"], sf["result"]) if r in ("yes", "no")})

def price_result(s2c, yb, ya):
    mid = (yb + ya) / 2
    m = s2c <= 15
    iy = np.flatnonzero(m & ((mid >= 99) | (yb >= 99)))
    ino = np.flatnonzero(m & ((mid <= 1) | (ya <= 1)))
    if not len(iy) and not len(ino): return None
    if not len(ino): return True
    if not len(iy): return False
    return bool(iy[-1] > ino[-1])

markets = []
for a, b in zip(starts, ends):
    t = tickers[TID[a]]
    if cover[t] < COVER_MIN: continue
    s2c = S2C[a:b].astype(float)
    keep = np.r_[True, np.diff(s2c) != 0]
    s2c = s2c[keep]
    yb, ya = YB[a:b][keep].astype(float), YA[a:b][keep].astype(float)
    res = official.get(t)
    if res is None: res = price_result(s2c, yb, ya)
    if res is None: continue
    k = (s2c > DEADBAND) & (s2c <= 900)
    sk = STRIKE[a:b][np.isfinite(STRIKE[a:b])]
    markets.append({"t": t, "asset": asset_of[TID[a]], "yes_won": res,
                    "strike": float(sk[0]) if len(sk) else np.nan,
                    "s2c": s2c[k], "yb": yb[k], "ya": ya[k], "spot": SPOT[a:b][keep][k]})
del TID, YB, YA, S2C, SPOT, STRIKE; gc.collect()
markets.sort(key=lambda m: m["t"][-13:])          # roughly by date/time, for the split-half check
print(f"{len(markets):,} markets logged for the full 15 minutes with a known result "
      f"({sum(m['t'] in official for m in markets):,} official) in {time.time() - t0:.0f}s")

# Does spot above the strike mean YES? (checked, not assumed)
agree = n_dir = 0
for m in markets:
    ok = np.isfinite(m["spot"]) & (m["s2c"] <= 10)
    if ok.any() and np.isfinite(m["strike"]):
        n_dir += 1; agree += (m["spot"][ok][-1] > m["strike"]) == m["yes_won"]
UP_IS_YES = agree >= n_dir / 2

# %%
# SIMULATION
def simulate(m, window, stop, v33_fills=False):
    s, yb, ya = m["s2c"], m["yb"], m["ya"]
    hi, lo = window
    fav_yes = (yb + ya) >= 100
    fav_ask = np.where(fav_yes, ya, 100 - yb)
    ok = (s <= hi) & (s > lo) & (fav_ask >= FAV_ASK[0]) & (fav_ask <= FAV_ASK[1])
    idx = np.flatnonzero(ok)
    if not len(idx): return None
    i = int(idx[0])
    fy = bool(fav_yes[i])                              # the tagged favorite
    fb = yb if fy else 100 - ya                        # favorite's bid / ask over time
    fa = ya if fy else 100 - yb
    entry = float(100 - fb[i])                         # buy the opposite at its ask
    opp_won = m["yes_won"] != fy
    n = len(s)
    rev = np.flatnonzero(fb[i + 1:] <= REV_TRIGGER)
    k_rev = i + 1 + int(rev[0]) if len(rev) else n
    k_stop = n
    if stop is not None:
        st = np.flatnonzero((100 - fa[i + 1:]) <= stop)      # opposite's bid <= stop
        k_stop = i + 1 + int(st[0]) if len(st) else n
    pay_opp = (100 if opp_won else 0)
    d = {"t": m["t"], "asset": m["asset"], "entry_s2c": s[i], "entry_c": entry, "opp_won": opp_won}
    sp, sk = m["spot"][i], m["strike"]
    d["spot_side"] = ("n/a" if not (np.isfinite(sp) and np.isfinite(sk)) else
                      "spot favors opposite" if ((sp > sk) == UP_IS_YES) != fy else "spot favors favorite")
    if k_rev < k_stop:
        if fb[k_rev] < REV_FLOOR:                                     # 4. hold the opposite
            d["cat"] = "4 held (fav < 23c)"
            d["net"] = (pay_opp - entry) * LOTS - fee(entry)
        else:                                                         # 1 + 2
            fav_px = float(fb[k_rev] if v33_fills else fa[k_rev])
            exit_opp = 100 - fav_px
            d["cat"] = "1+2 stop at 27c + reversal"
            d["leg1"] = (exit_opp - entry) * LOTS - fee(entry) - fee(exit_opp)
            d["leg2"] = ((100 if not opp_won else 0) - fav_px) * LOTS - fee(fav_px)
            d["rev_won"] = not opp_won
            d["net"] = d["leg1"] + d["leg2"]
    elif k_stop < n:                                                  # 5. opposite stopped out
        exit_px = float(100 - fa[k_stop])
        d["cat"] = "5 opposite stopped"
        d["net"] = (exit_px - entry) * LOTS - fee(entry) - fee(exit_px)
        d["stopped_but_would_win"] = opp_won
    else:                                                             # 3. held to settlement
        d["cat"] = "3 held to settlement"
        d["net"] = (pay_opp - entry) * LOTS - fee(entry)
    return d

rows = []
for wname, w in WINDOWS.items():
    for stop in STOPS:
        for m in markets:
            d = simulate(m, w, stop)
            if d: rows.append(dict(d, window=wname, stop="none" if stop is None else f"{stop}c"))
R = pd.DataFrame(rows)
R.to_csv(OUT_TRADES, index=False)
print(f"{len(R):,} simulated trades")

# %%
# 1. THE GRID — total P&L by entry window and stop price
def summary(g):
    c = g.cat
    rv = g[c == "1+2 stop at 27c + reversal"]
    out = {"trades": len(g),
           "cat1_$": rv.leg1.sum() / 100 if len(rv) else 0.0,
           "cat2_reversal_$": rv.leg2.sum() / 100 if len(rv) else 0.0,
           "reversal_won": f"{int(rv.rev_won.sum())}/{len(rv)}" if len(rv) else "0/0",
           "cat3_held_$": g[c == "3 held to settlement"].net.sum() / 100,
           "cat3_n": int((c == "3 held to settlement").sum()),
           "cat4_held_$": g[c == "4 held (fav < 23c)"].net.sum() / 100,
           "cat5_stopped_$": g[c == "5 opposite stopped"].net.sum() / 100,
           "cat5_n": int((c == "5 opposite stopped").sum()),
           "stopped_but_would_have_won": int((g["stopped_but_would_win"] == True).sum())
                                          if "stopped_but_would_win" in g else 0}
    out["total_$"] = round(g.net.sum() / 100, 2)
    out["per_trade_$"] = round(g.net.mean() / 100, 3)
    return pd.Series(out)

G = R.groupby(["window", "stop"], sort=False).apply(summary).reset_index()
G.to_csv(OUT_GRID, index=False)
print("1. TOTAL P&L ($, 10 contracts, fees included) — rows: entry window, columns: stop on the opposite")
display(G.pivot(index="window", columns="stop", values="total_$")[["none"] + [f"{x}c" for x in STOPS[1:]]])
print("   per-trade $")
display(G.pivot(index="window", columns="stop", values="per_trade_$")[["none"] + [f"{x}c" for x in STOPS[1:]]])
print("   all categories")
display(G)

# %%
# 2. IS IT STABLE? First half vs second half of the markets (by time)
half = {t: (0 if i < len(markets) / 2 else 1) for i, t in enumerate(m["t"] for m in markets)}
R["half"] = R.t.map(half).map({0: "first half", 1: "second half"})
H = R.groupby(["window", "stop", "half"], sort=False).net.sum().div(100).unstack("half").round(2)
print("2. SPLIT HALF — a real edge should be positive in BOTH halves")
display(H)

# %%
# 3. FILTERS KNOWN AT ENTRY (full 15 min window): when did it enter, which market, what spot said
F = R[R.window == "full 15 min"].copy()
F["entry_time"] = pd.cut(F.entry_s2c, [0, 60, 180, 300, 420, 600, 900],
                         labels=["<1m", "1-3m", "3-5m", "5-7m", "7-10m", "10-15m"])
F["class"] = np.where(F.asset.isin(CRYPTO), "crypto", "commodity")
for col in ["entry_time", "class", "asset", "spot_side"]:
    print(f"3. full 15 min — total $ by {col} (rows) and stop (columns)")
    display(F.pivot_table(index=col, columns="stop", values="net", aggfunc="sum", observed=True)
            .div(100).round(2)[["none"] + [f"{x}c" for x in STOPS[1:]]])
print("   trades per entry_time"); display(F[F.stop == "none"].entry_time.value_counts().sort_index())

# %%
# 4. CHECK AGAINST YOUR LIVE V33 RUN (last 7 min, no stop, markets from V33's run only)
if os.path.exists(V33_TRADES):
    live = pd.read_csv(V33_TRADES)
    live_t = set(live.window)
    mine = [m for m in markets if m["t"] in live_t]
    for v33 in (True, False):
        out = [d for d in (simulate(m, WINDOWS["last 7 min (V33)"], None, v33_fills=v33) for m in mine) if d]
        X = pd.DataFrame(out)
        print(f"4. replay of V33's markets ({'V33 paper fills' if v33 else 'real fills'}): "
              f"{len(X)} trades, total ${X.net.sum() / 100:.2f}")
        display(X.groupby("cat").net.agg(["count", "sum"]).assign(sum=lambda x: x["sum"] / 100))
    print(f"   live V33: {live.window.nunique()} markets, total ${live.net_c.sum() / 100:.2f}")
