# %% [markdown]
# # V34 Parameter Search — which buy/sell prices, windows and markets actually hold up?
#
# Replays the V34-family rules (buy a side whose ask is in a band, sell when its bid reaches
# a target, else hold to settlement; one position per market; repeat after a sale) on every
# market logged from minute 1 in all your tick files.
#
# Guard against fooling ourselves: markets are split by time — the first 60% ("train") and the
# last 40% ("test"). A setting only counts if it makes money in BOTH. Settings that only look good
# in one half are luck.
#
# Real fills: buys at the ask, sells at the bid, Kalshi taker fee on every order (10 contracts).
# Section 1 replays V34b's own rules on V34b's own markets and compares with its live trades file.
# Runtime -> Run all (takes a few minutes).

# %%
from google.colab import drive
drive.mount('/content/drive')

# %%
import csv, os, re, time, math, collections, gc
from array import array
import numpy as np
import pandas as pd

D = "/content/drive/MyDrive/"
# earlier files win when two bots logged the same market
TICK_FILES = [D + f for f in ("commod15min_v34b_ticks.csv", "commod15min_v34c_ticks.csv",
                              "commod15min_v34_ticks.csv", "commod15min_v34a_ticks.csv",
                              "commodmaker_v1_ticks.csv", "commod15min_v33_ticks.csv",
                              "commod15min_v31_ticks.csv", "commod1dollar_v2_ticks.csv",
                              "commod15min_v30_ticks.csv", "commod1dollar_v1_ticks.csv")]
SETTLE_FILES = [D + f for f in ("commod15min_v34b_settlements.csv", "commod15min_v34c_settlements.csv",
                                "commod15min_v34_settlements.csv", "commod15min_v34a_settlements.csv",
                                "commodmaker_v1_settlements.csv", "commod15min_v33_settlements.csv",
                                "commod15min_v31_settlements.csv", "commod1dollar_v2_settlements.csv",
                                "commod15min_v30_settlements.csv", "commod1dollar_v1_settlements.csv")]
V34B_TRADES = D + "commod15min_v34b_trades.csv"
OUT_GRID  = D + "v34_param_grid.csv"
OUT_ROBUST = D + "v34_param_robust.csv"

CRYPTO    = {"BTC", "ETH", "SOL", "XRP", "DOGE"}
COVER_MIN = 840        # market must be logged from minute 1 (840s before close) or earlier
DEADBAND  = 2
MAX_SPREAD = 5
LOTS      = 10
TRAIN_FRAC = 0.6

ENTRIES = [(23, 27), (28, 32), (33, 37), (38, 42), (43, 47), (47, 53), (53, 57), (58, 62)]
TARGET_UP = [15, 20, 25]               # sell target = band middle + this
WINDOWS = {"min 1-10": (840, 300, ()), "min 1-10, skip 2-3": (840, 300, (2, 3)),
           "min 1-5": (840, 600, ()), "min 1-3": (840, 720, ())}

def fee(p):
    return math.ceil(0.07 * LOTS * p * (100 - p) / 100 - 1e-9)

MON = {m: i for i, m in enumerate(["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"], 1)}
def close_key(t):          # KXBTC15M-26OCT011215-15 -> 2026-10-01 12:15 (sortable)
    m = re.search(r"-(\d\d)([A-Z]{3})(\d\d)(\d{4})-", t)
    return f"20{m.group(1)}{MON[m.group(2)]:02d}{m.group(3)}{m.group(4)}" if m else t

# %%
# LOAD — two tick layouts: older bots log yes_bid, yes_ask, yes_mid, sec_to_close; the V34 family
# and the maker bot log yes_bid, yes_ask, sec_to_close. The header tells which.
def num(x):
    try: return float(x)
    except (TypeError, ValueError): return None

t0 = time.time()
claimed, tickers, asset_of = {}, [], []
TID, YB, YA, S2C = array("i"), array("f"), array("f"), array("f")
cover = collections.defaultdict(float)
for fi, path in enumerate(TICK_FILES):
    if not os.path.exists(path):
        print("not found (skipped):", path); continue
    n0 = len(TID)
    with open(path, newline="") as fh:
        rd = csv.reader(fh)
        header = next(rd, [])
        k_need = 4 if "yes_mid" in header else 3
        for row in rd:
            ti = next((i for i, f in enumerate(row[:4]) if "15M-" in f), None)
            if ti is None or ti + 1 >= len(row): continue
            t = row[ti]
            own = claimed.get(t)
            if own is not None and own[0] != fi: continue
            vals = []
            for j in range(ti + 2, len(row)):
                v = num(row[j]) if row[j] != "" else None
                if v is None:
                    if vals: break
                    continue
                vals.append(v)
                if len(vals) == k_need: break
            if len(vals) < k_need: continue
            if k_need == 4:
                yb, ya, mid, s = vals
                if abs(mid - (yb + ya) / 2) > 1: continue
            else:
                yb, ya, s = vals
            if not (0 <= yb <= ya <= 100 and -60 <= s <= 930): continue
            if own is None:
                own = claimed[t] = (fi, len(tickers)); tickers.append(t); asset_of.append(row[ti + 1])
            if s > cover[t]: cover[t] = s
            TID.append(own[1]); YB.append(yb); YA.append(ya); S2C.append(s)
    print(f"{os.path.basename(path)}: {len(TID) - n0:,} rows ({time.time() - t0:.0f}s)")
if not len(TID): raise SystemExit("No tick rows found.")

TID, YB, YA, S2C = (np.frombuffer(a, dtype=d) for a, d in
                    ((TID, np.int32), (YB, np.float32), (YA, np.float32), (S2C, np.float32)))
order = np.lexsort((-S2C, TID))
TID, YB, YA, S2C = TID[order], YB[order], YA[order], S2C[order]
del order; gc.collect()
bounds = np.flatnonzero(np.diff(TID)) + 1
starts, ends = np.r_[0, bounds], np.r_[bounds, len(TID)]

official = {}
for path in SETTLE_FILES:
    if os.path.exists(path):
        sf = pd.read_csv(path)
        official.update({t: r == "yes" for t, r in zip(sf["ticker"], sf["result"]) if r in ("yes", "no")})

markets = []
for a, b in zip(starts, ends):
    t = tickers[TID[a]]
    if cover[t] < COVER_MIN or t not in official: continue
    s = S2C[a:b].astype(float)
    keep = np.r_[True, np.diff(s) != 0] & (s > DEADBAND) & (s <= 900)
    markets.append({"t": t, "asset": asset_of[TID[a]], "yes_won": official[t], "key": close_key(t),
                    "s": s[keep], "yb": YB[a:b][keep].astype(float), "ya": YA[a:b][keep].astype(float)})
del TID, YB, YA, S2C; gc.collect()
markets.sort(key=lambda m: m["key"])
cut = int(len(markets) * TRAIN_FRAC)
for i, m in enumerate(markets): m["part"] = "train" if i < cut else "test"
print(f"{len(markets):,} markets logged from minute 1 with an official result "
      f"({cut} train: {markets[0]['key']}..{markets[cut-1]['key']}, "
      f"{len(markets)-cut} test: {markets[cut]['key']}..{markets[-1]['key']}) in {time.time() - t0:.0f}s")

# %%
# THE RULES (same as the V34 bots)
def simulate(m, lo, hi, target, w_from, w_until, skip=()):
    s, yb, ya = m["s"], m["yb"], m["ya"]
    na = 100 - yb
    ok = (s <= w_from) & (s >= w_until) & ((ya - yb) <= MAX_SPREAD)
    if skip: ok &= ~np.isin(((900 - s) // 60).astype(int), skip)
    yes_in = ok & (ya >= lo) & (ya <= hi)
    no_in = ok & (na >= lo) & (na <= hi)
    cand = (yes_in | no_in) & ~(yes_in & no_in & (ya == na))     # equal prices: wait
    out, i, n = [], 0, len(s)
    while i < n:
        c = np.flatnonzero(cand[i:])
        if not len(c): break
        k = i + int(c[0])
        yes = bool(yes_in[k] and (not no_in[k] or ya[k] < na[k]))
        px = float(ya[k] if yes else na[k])
        bid = yb if yes else 100 - ya
        up = np.flatnonzero(bid[k + 1:] >= target)
        minute = int((900 - s[k]) // 60)
        if len(up):
            j = k + 1 + int(up[0]); sp = float(bid[j])
            out.append((minute, px, "sold", (sp - px) * LOTS - fee(px) - fee(sp)))
            i = j + 1
        else:
            won = m["yes_won"] == yes
            out.append((minute, px, "held", ((100 if won else 0) - px) * LOTS - fee(px)))
            break
    return out

# %%
# 1. CHECK: V34b's rules on V34b's own markets vs its live trades file
if os.path.exists(V34B_TRADES):
    live = pd.read_csv(V34B_TRADES)
    lt = set(live.window)
    mine = [m for m in markets if m["t"] in lt]
    sim = [x for m in mine for x in simulate(m, 47, 53, 72, 840, 300)]
    lv = live[live.window.isin({m["t"] for m in mine})]
    print(f"1. V34b check on {len(mine)} markets: replay {len(sim)} positions, "
          f"{sum(x[2] == 'sold' for x in sim)} sold, ${sum(x[3] for x in sim) / 100:.2f}  |  "
          f"live {len(lv)} positions, {(lv.reason == 'sold').sum()} sold, ${lv.net_c.sum() / 100:.2f}")

# %%
# 2. THE GRID
rows = []
t1 = time.time()
for (lo, hi) in ENTRIES:
    mid = (lo + hi) // 2
    for up in TARGET_UP:
        target = min(mid + up, 95)
        for wname, (wf, wu, skip) in WINDOWS.items():
            for m in markets:
                for minute, px, how, net in simulate(m, lo, hi, target, wf, wu, skip):
                    rows.append((f"{lo}-{hi}", target, wname, m["asset"], m["asset"] in CRYPTO,
                                 m["part"], minute, how, net))
print(f"{len(rows):,} simulated positions in {time.time() - t1:.0f}s")
R = pd.DataFrame(rows, columns=["buy", "sell", "window", "asset", "crypto", "part", "minute", "how", "net"])
del rows; gc.collect()

def agg(df):
    if df.empty: return pd.DataFrame()
    g = df.groupby(["buy", "sell", "window", "part"]).agg(n=("net", "size"), total=("net", "sum"),
                                                          sold=("how", lambda x: (x == "sold").mean()))
    w = g.unstack("part")
    w = w.reindex(columns=pd.MultiIndex.from_product([["n", "total", "sold"], ["train", "test"]]))
    out = pd.DataFrame({"n_train": w[("n", "train")], "per_train_c": (w[("total", "train")] / w[("n", "train")]).round(1),
                        "n_test": w[("n", "test")], "per_test_c": (w[("total", "test")] / w[("n", "test")]).round(1),
                        "total_$": ((w[("total", "train")].fillna(0) + w[("total", "test")].fillna(0)) / 100).round(2),
                        "sold_rate": ((w[("sold", "train")] * w[("n", "train")] + w[("sold", "test")] * w[("n", "test")])
                                      / (w[("n", "train")] + w[("n", "test")])).round(3)})
    out["worst_half_c"] = out[["per_train_c", "per_test_c"]].min(axis=1)
    return out.reset_index()

G = pd.concat([agg(R).assign(markets="all"), agg(R[R.crypto]).assign(markets="crypto"),
               agg(R[~R.crypto]).assign(markets="commodity")], ignore_index=True)
G.to_csv(OUT_GRID, index=False)
for grp in ["all", "crypto", "commodity"]:
    print(f"2. per-position cents, worst of the two halves — {grp} markets, window 'min 1-10'")
    display(G[(G.markets == grp) & (G.window == "min 1-10")]
            .pivot(index="buy", columns="sell", values="worst_half_c").sort_index())

# %%
# 3. SETTINGS THAT MADE MONEY IN BOTH HALVES (at least 40 positions in each half)
ROB = G[(G.per_train_c > 0) & (G.per_test_c > 0) & (G.n_train >= 40) & (G.n_test >= 40)] \
        .sort_values("worst_half_c", ascending=False)
ROB.to_csv(OUT_ROBUST, index=False)
print(f"3. {len(ROB)} of {len(G)} settings were positive in both halves")
display(ROB.head(40))

# %%
# 4. FOR THE TOP SETTINGS: which markets and which entry minutes carried them
for _, r in ROB.head(3).iterrows():
    sub = R[(R.buy == r.buy) & (R.sell == r.sell) & (R.window == r.window)]
    if r.markets == "crypto": sub = sub[sub.crypto]
    if r.markets == "commodity": sub = sub[~sub.crypto]
    print(f"4. {r.markets} · buy {r.buy} · sell {r.sell} · {r.window}")
    display(sub.pivot_table(index="asset", columns="part", values="net", aggfunc=["count", "sum"]).div(1))
    display(sub.pivot_table(index="minute", columns="part", values="net", aggfunc=["count", "mean"]).round(1))
