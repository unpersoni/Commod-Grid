# %% [markdown]
# # V36 Grid Search — which entry band, sell target, window, filter and exit actually hold up?
#
# Replays the bot rules (buy a side whose ask is in a band, sell when its bid reaches a target,
# else hold to settlement; ONE trade per market) on every market in your tick logs — the V36/V35
# files first, then the older bots' files for extra markets.
#
# Grid: entry bands 23-27c ... 73-77c (5c steps) x sell targets +5 ... +30c x buy windows
# (minute 0-3, 0-6, 0-9, 0-12) x exits x filters.
# Exits: hold   - not sold at the target -> held to settlement ($1 or $0)
#        exit10 / exit12 / exit14 - not sold by minute 10 / 12 / 14 -> sold at the bid then
# Filters:
#   none            - plain rules
#   kalshi_with     - the bought side's price rose 2c+ over the last 60s (moving our way)
#   kalshi_against  - the bought side's price fell 2c+ over the last 60s
#   spot_with       - crypto only: the coin OR BTC spot moved 0.01%+ our way over the last 60s
#
# Guard against fooling ourselves: markets are split by time — first 60% "train", last 40% "test".
# A setting only counts if it makes money in BOTH halves.
# Real fills: buys at the ask, sells at the bid, Kalshi taker fee on every order (10 contracts).
#
# Saves to Drive:  v36_grid.csv (every setting), v36_grid_robust.csv (positive in both halves),
#                  v36_entries.csv.gz (every simulated entry with its factors and outcomes, for
#                  deeper analysis).  Runtime -> Run all.

# %%
try:
    from google.colab import drive
    drive.mount('/content/drive')
    D = "/content/drive/MyDrive/"
except ImportError:                      # local test run
    import os
    D = os.environ["GRID_DATA_DIR"]

# %%
import csv, os, re, time, math, collections, gc, datetime as dt
from array import array
import numpy as np
import pandas as pd
try:
    display
except NameError:
    display = print

# earlier files win when two bots logged the same market (V36/V35 first: they have spot prices)
BOTS = ["commod15min_v36a", "commod15min_v36b2", "commod15min_v36b", "commod15min_v35a", "commod15min_v35b",
        "commod15min_v34b", "commod15min_v34c", "commod15min_v34", "commod15min_v34a", "commodmaker_v1",
        "commod15min_v33", "commod15min_v31", "commod1dollar_v2", "commod15min_v30", "commod1dollar_v1"]
TICK_FILES = [D + b + "_ticks.csv" for b in BOTS]
SETTLE_FILES = [D + b + "_settlements.csv" for b in BOTS]
V36A_TRADES = D + "commod15min_v36a_trades.csv"
OUT_GRID, OUT_ROBUST, OUT_ENTRIES = D + "v36_grid.csv", D + "v36_grid_robust.csv", D + "v36_entries.csv.gz"

CRYPTO     = {"BTC", "ETH", "SOL", "XRP", "DOGE"}
COVER_FROM = 870       # market must be logged from its first 30 seconds
DEADBAND   = 2
MAX_SPREAD = 5
LOTS       = 10
TRAIN_FRAC = 0.6
MIN_N      = 40        # positions needed in EACH half for a setting to count

BANDS   = [(lo, lo + 4) for lo in range(23, 74, 5)]          # 23-27 ... 73-77
UPS     = [5, 10, 15, 20, 25, 30]                             # sell target = band middle + this
WINDOWS = {"min 0-3": (900, 720), "min 0-6": (900, 540), "min 0-9": (900, 360), "min 0-12": (900, 180)}
FILTERS = ["none", "kalshi_with", "kalshi_against", "spot_with"]
EXITS   = {"hold": None, "exit10": 300, "exit12": 180, "exit14": 60}   # sell at the bid once s2c <= this
K_MOVE  = 2            # cents, Kalshi move over 60s for kalshi_with / kalshi_against
S_MOVE  = 0.01         # %, spot move over 60s for spot_with

def fee(p):
    return math.ceil(0.07 * LOTS * p * (100 - p) / 100 - 1e-9)

MON = {m: i for i, m in enumerate(["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"], 1)}
def close_key(t):          # KXBTC15M-26OCT011215-15 -> 202610011215 (sortable)
    m = re.search(r"-(\d\d)([A-Z]{3})(\d\d)(\d{4})-", t)
    return f"20{m.group(1)}{MON[m.group(2)]:02d}{m.group(3)}{m.group(4)}" if m else t

def num(x):
    try: return float(x)
    except (TypeError, ValueError): return None

def ts_of(x):
    try: return dt.datetime.fromisoformat(x.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError): return float("nan")

# %%
# LOAD — layouts differ by bot; the header tells where things are. Older bots log
# yes_bid, yes_ask, yes_mid, sec_to_close; newer ones yes_bid, yes_ask, sec_to_close (+ spot_px).
t0 = time.time()
claimed, tickers, asset_of = {}, [], []
TID, YB, YA, S2C, SPOT = array("i"), array("f"), array("f"), array("f"), array("f")
TS = array("d")
cover = collections.defaultdict(float)
for fi, path in enumerate(TICK_FILES):
    if not os.path.exists(path):
        print("not found (skipped):", os.path.basename(path)); continue
    n0, first_ts, last_ts = len(TID), None, None
    with open(path, newline="") as fh:
        rd = csv.reader(fh)
        header = next(rd, [])
        k_need = 4 if "yes_mid" in header else 3
        sp_i = header.index("spot_px") if "spot_px" in header else None
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
            sp = num(row[sp_i]) if sp_i is not None and sp_i < len(row) else None
            ts = ts_of(row[0])
            TID.append(own[1]); YB.append(yb); YA.append(ya); S2C.append(s)
            SPOT.append(sp if sp else float("nan")); TS.append(ts)
            if ts == ts:
                if first_ts is None: first_ts = ts
                last_ts = ts
    span = (f"{dt.datetime.fromtimestamp(first_ts, dt.timezone.utc):%b %d %H:%M} -> {dt.datetime.fromtimestamp(last_ts, dt.timezone.utc):%b %d %H:%M} UTC"
            if first_ts else "no timestamps")
    print(f"{os.path.basename(path)}: {len(TID) - n0:,} rows, {span} ({time.time() - t0:.0f}s)")
    if len(TID) == n0:
        with open(path, "rb") as fh: head = fh.read(4096)
        size = os.path.getsize(path)
        print(f"   !! {size:,} bytes but no usable rows. First bytes: {head[:160]!r}  "
              f"(NUL bytes in first 4KB: {head.count(0)})")
if not len(TID): raise SystemExit("No tick rows found.")

TID, YB, YA, S2C, SPOT, TS = (np.frombuffer(a, dtype=d) for a, d in
    ((TID, np.int32), (YB, np.float32), (YA, np.float32), (S2C, np.float32), (SPOT, np.float32), (TS, np.float64)))
AST = np.array(asset_of)[TID]

# spot history per crypto coin, from every row that has one (used for spot momentum)
SPOT_SERIES = {}
for a in CRYPTO:
    m = (AST == a) & np.isfinite(SPOT) & np.isfinite(TS)
    if m.sum() < 10: continue
    o = np.argsort(TS[m], kind="stable")
    SPOT_SERIES[a] = (TS[m][o], SPOT[m][o].astype(float))
del AST
print("spot history:", {a: len(v[0]) for a, v in SPOT_SERIES.items()})

def spot_chg(a, ts, secs=60):
    """% change of coin a's spot from ts-secs to ts, for an array of times (NaN where there is
    no spot reading within 10s of both ends)."""
    ts = np.asarray(ts, dtype=float)
    ser = SPOT_SERIES.get(a)
    out = np.full(ts.shape, np.nan)
    if ser is None: return out
    T, P = ser
    i = np.searchsorted(T, ts, side="right") - 1
    j = np.searchsorted(T, ts - secs, side="right") - 1
    ok = (i >= 0) & (j >= 0) & np.isfinite(ts)
    ii, jj = np.clip(i, 0, None), np.clip(j, 0, None)
    ok &= (ts - T[ii] <= 10) & ((ts - secs) - T[jj] <= 10)
    out[ok] = (P[ii][ok] - P[jj][ok]) / P[jj][ok] * 100
    return out

def spot_at(a, ts):
    ser = SPOT_SERIES.get(a)
    if ser is None or ts != ts: return None
    T, P = ser
    i = np.searchsorted(T, ts, side="right") - 1
    return P[i] if i >= 0 and ts - T[i] <= 10 else None

order = np.lexsort((-S2C, TID))
TID, YB, YA, S2C, TS = TID[order], YB[order], YA[order], S2C[order], TS[order]
del order, SPOT; gc.collect()
bounds = np.flatnonzero(np.diff(TID)) + 1
starts, ends = np.r_[0, bounds], np.r_[bounds, len(TID)]

official, strike = {}, {}
for path in SETTLE_FILES:
    if os.path.exists(path):
        sf = pd.read_csv(path)
        for t, r, k in zip(sf["ticker"], sf["result"], sf.get("floor_strike", pd.Series([None] * len(sf)))):
            if r in ("yes", "no"):
                official[t] = r == "yes"
                if num(k): strike[t] = num(k)

markets = []
for a, b in zip(starts, ends):
    t = tickers[TID[a]]
    if cover[t] < COVER_FROM or t not in official: continue
    s = S2C[a:b].astype(float)
    keep = np.r_[True, np.diff(s) != 0] & (s > DEADBAND) & (s <= 900)
    if keep.sum() < 5: continue
    markets.append({"t": t, "asset": asset_of[TID[a]], "yes_won": official[t], "key": close_key(t),
                    "strike": strike.get(t), "s": s[keep], "yb": YB[a:b][keep].astype(float),
                    "ya": YA[a:b][keep].astype(float), "ts": TS[a:b][keep]})
del TID, YB, YA, S2C, TS; gc.collect()
markets.sort(key=lambda m: m["key"])
cut = int(len(markets) * TRAIN_FRAC)
for i, m in enumerate(markets): m["part"] = "train" if i < cut else "test"
ncr = sum(m["asset"] in CRYPTO for m in markets)
print(f"{len(markets):,} markets logged from their first 30s with an official result "
      f"({ncr} crypto, {len(markets) - ncr} commodity) | train {markets[0]['key']}..{markets[cut-1]['key']}, "
      f"test {markets[cut]['key']}..{markets[-1]['key']} | {time.time() - t0:.0f}s")

# %%
# PER-MARKET PREP: Kalshi 60s move of the YES mid, and spot moves (crypto) at every tick
def prep(m):
    s, yb, ya, ts = m["s"], m["yb"], m["ya"], m["ts"]
    mid = (yb + ya) / 2
    j60 = np.maximum(np.searchsorted(-s, -(s + 60), side="right") - 1, 0)   # tick ~60s earlier (or the first)
    m["kmove"] = mid - mid[j60]
    m["j60"] = j60
    if m["asset"] in CRYPTO and m["asset"] in SPOT_SERIES:
        cc, bc = spot_chg(m["asset"], ts), spot_chg("BTC", ts)
    else:
        cc = bc = np.full(len(s), np.nan)
    m["cchg"], m["bchg"] = cc, bc

t1 = time.time()
for m in markets: prep(m)
print(f"prep done in {time.time() - t1:.0f}s")

# %%
# THE RULES: first qualifying moment in the window -> one position; sell at target or hold
def entry(m, lo, hi, wf, wu, filt):
    s, yb, ya = m["s"], m["yb"], m["ya"]
    na = 100 - yb
    ok = (s <= wf) & (s >= wu) & ((ya - yb) <= MAX_SPREAD)
    yes_in = ok & (ya >= lo) & (ya <= hi)
    no_in = ok & (na >= lo) & (na <= hi)
    yes_p = yes_in & (~no_in | (ya < na))
    no_p = no_in & (~yes_in | (na < ya))
    if filt == "kalshi_with":
        yes_p &= m["kmove"] >= K_MOVE; no_p &= -m["kmove"] >= K_MOVE
    elif filt == "kalshi_against":
        yes_p &= m["kmove"] <= -K_MOVE; no_p &= -m["kmove"] <= -K_MOVE
    elif filt == "spot_with":
        cc, bc = m["cchg"], m["bchg"]
        yes_p &= (cc >= S_MOVE) | (bc >= S_MOVE)
        no_p &= (cc <= -S_MOVE) | (bc <= -S_MOVE)
    c = np.flatnonzero(yes_p | no_p)
    if not len(c): return None
    k = int(c[0])
    return k, bool(yes_p[k])

def outcome(m, k, yes, target, exit_s=None):
    """(buy price, sold at target?, net cents). exit_s: if not sold at the target before
    s2c <= exit_s, sell at the bid at the first tick from then on (only if bought before it)."""
    s = m["s"]
    px = float(m["ya"][k] if yes else 100 - m["yb"][k])
    bid = m["yb"] if yes else 100 - m["ya"]
    end = len(s)
    if exit_s is not None and s[k] > exit_s:
        e = np.flatnonzero(s[k + 1:] <= exit_s)
        end = k + 1 + int(e[0]) if len(e) else len(s)
    up = np.flatnonzero(bid[k + 1:end] >= target)
    if len(up):
        sp = float(bid[k + 1 + int(up[0])])
        return px, True, (sp - px) * LOTS - fee(px) - fee(sp)
    if end < len(s):                                   # time exit: sell at the bid
        sp = float(bid[end])
        return px, False, (sp - px) * LOTS - fee(px) - (fee(sp) if sp > 0 else 0)
    won = m["yes_won"] == yes
    return px, False, ((100 if won else 0) - px) * LOTS - fee(px)

# %%
# 1. CHECK: V36a's live rules (48-52c, sell 72c, minute 0-9 skipping 2-3) vs its trades file
if os.path.exists(V36A_TRADES):
    live = pd.read_csv(V36A_TRADES)
    lt = set(live.window)
    mine = [m for m in markets if m["t"] in lt]
    sim = []
    for m in mine:
        mm = dict(m); keep = ~np.isin(((900 - m["s"]) // 60).astype(int), (2, 3))
        for f in ("s", "yb", "ya", "ts", "kmove", "cchg", "bchg"): mm[f] = m[f][keep]
        e = entry(mm, 48, 52, 900, 360, "none")
        if e: sim.append(outcome(mm, e[0], e[1], 72))
    lv = live[live.window.isin({m["t"] for m in mine})]
    print(f"1. V36a check on {len(mine)} markets: replay {len(sim)} positions, {sum(x[1] for x in sim)} sold, "
          f"${sum(x[2] for x in sim) / 100:.2f}  |  live {len(lv)} positions, {(lv.reason == 'sold').sum()} sold, "
          f"${lv.net_c.sum() / 100:.2f}")

# %%
# 2. THE GRID — and the entries file (window 0-12, no filter) with the factors at each entry
acc = collections.defaultdict(lambda: [0, 0.0, 0])        # setting -> [positions, total cents, sold]
ent = []
t1 = time.time()
for mi, m in enumerate(markets):
    cr = m["asset"] in CRYPTO
    for (lo, hi) in BANDS:
        targets = [min((lo + hi) // 2 + up, 97) for up in UPS]
        for filt in FILTERS:
            if filt == "spot_with" and not cr: continue
            for wname, (wf, wu) in WINDOWS.items():
                e = entry(m, lo, hi, wf, wu, filt)
                if e is None: continue
                k, yes = e
                res_x = {xn: [outcome(m, k, yes, tg, xs) for tg in targets] for xn, xs in EXITS.items()}
                res = res_x["hold"]
                for xn, rr in res_x.items():
                    for up, (px, sold, net) in zip(UPS, rr):
                        a_ = acc[(f"{lo}-{hi}", up, wname, filt, xn, cr, m["part"])]
                        a_[0] += 1; a_[1] += net; a_[2] += sold
                if wname == "min 0-12" and filt == "none":
                    s, yb, ya, ts = m["s"], m["yb"], m["ya"], m["ts"]
                    sg = 1 if yes else -1
                    side_mid = (yb + ya) / 2 if yes else 100 - (yb + ya) / 2
                    j = m["j60"][k]
                    w = side_mid[j:k + 1]
                    sp = spot_at(m["asset"], ts[k]) if cr else None
                    j30 = max(int(np.searchsorted(-s, -(s[k] + 30), side="right")) - 1, 0)
                    ent.append({
                        "ticker": m["t"], "asset": m["asset"], "crypto": cr, "key": m["key"], "part": m["part"],
                        "band": f"{lo}-{hi}", "side": "YES" if yes else "NO", "px": res[0][0],
                        "s2c": round(s[k], 1), "minute": int((900 - s[k]) // 60),
                        "hour_utc": (dt.datetime.fromtimestamp(ts[k], dt.timezone.utc).hour if ts[k] == ts[k] else None),
                        "spread": ya[k] - yb[k], "kmove60": side_mid[k] - side_mid[j],
                        "kmove30": side_mid[k] - side_mid[j30], "range60": float(w.max() - w.min()),
                        "ticks60": int(k - j), "open_side_mid": side_mid[0], "open_s2c": round(s[0], 1),
                        "open_lean": abs((yb[0] + ya[0]) / 2 - 50),
                        "spot_dist_pct": (sg * (sp - m["strike"]) / m["strike"] * 100) if sp and m["strike"] else None,
                        "coin_chg60": sg * m["cchg"][k] if m["cchg"][k] == m["cchg"][k] else None,
                        "btc_chg60": sg * m["bchg"][k] if m["bchg"][k] == m["bchg"][k] else None,
                        "won_settle": m["yes_won"] == yes,
                        **{f"net_up{up}": net for up, (_, _, net) in zip(UPS, res)},
                        **{f"net_up{up}_{xn}": net for xn, rr in res_x.items() if xn != "hold"
                           for up, (_, _, net) in zip(UPS, rr)},
                        **{f"sold_up{up}": sold for up, (_, sold, _) in zip(UPS, res)}})
    if mi % 1000 == 999: print(f"  {mi + 1:,} markets ({time.time() - t1:.0f}s)")
R = pd.DataFrame([(*k, n, tot, sold) for k, (n, tot, sold) in acc.items()],
                 columns=["buy", "up", "window", "filter", "exit", "crypto", "part", "n", "total", "sold"])
print(f"{int(R.n.sum()):,} simulated positions in {time.time() - t1:.0f}s")
E = pd.DataFrame(ent); del ent
E.to_csv(OUT_ENTRIES, index=False, compression="gzip")
print(f"saved {len(E):,} entries with factors -> {os.path.basename(OUT_ENTRIES)}")

def agg(df):
    if df.empty: return pd.DataFrame()
    g = df.groupby(["buy", "up", "window", "filter", "exit", "part"])[["n", "total", "sold"]].sum()
    g["sold"] = g["sold"] / g["n"]
    w = g.unstack("part")
    w = w.reindex(columns=pd.MultiIndex.from_product([["n", "total", "sold"], ["train", "test"]]))
    out = pd.DataFrame({"n_train": w[("n", "train")], "per_train_c": (w[("total", "train")] / w[("n", "train")]).round(1),
                        "n_test": w[("n", "test")], "per_test_c": (w[("total", "test")] / w[("n", "test")]).round(1),
                        "total_$": ((w[("total", "train")].fillna(0) + w[("total", "test")].fillna(0)) / 100).round(2),
                        "sold_rate": ((w[("sold", "train")] * w[("n", "train")] + w[("sold", "test")] * w[("n", "test")])
                                      / (w[("n", "train")] + w[("n", "test")])).round(3)})
    out["worst_half_c"] = out[["per_train_c", "per_test_c"]].min(axis=1)
    return out.reset_index()

G = pd.concat([agg(R[R.crypto]).assign(markets="crypto"), agg(R[~R.crypto]).assign(markets="commodity")],
              ignore_index=True)
G.to_csv(OUT_GRID, index=False)
for grp in ["crypto", "commodity"]:
    for filt in ["none", "kalshi_with"] + (["spot_with"] if grp == "crypto" else []):
        sub = G[(G.markets == grp) & (G.window == "min 0-12") & (G["filter"] == filt) & (G.exit == "hold")]
        if sub.empty: continue
        print(f"2. {grp} · window 0-12 · filter {filt}: cents per position, WORST of the two halves "
              f"(rows = entry band, columns = sell target above the band middle)")
        display(sub.pivot(index="buy", columns="up", values="worst_half_c").sort_index())

# %%
# 2b. DOES DUMPING UNSOLD POSITIONS BEFORE SETTLEMENT HELP? (crypto, window 0-12, no filter)
for grp in ["crypto", "commodity"]:
    sub = G[(G.markets == grp) & (G.window == "min 0-12") & (G["filter"] == "none")].copy()
    if sub.empty: continue
    sub["per_pos_c"] = (sub["total_$"] * 100 / (sub.n_train.fillna(0) + sub.n_test.fillna(0))).round(1)
    print(f"2b. {grp}: cents per position (both halves together) — hold vs time exits, by band and target")
    display(sub.pivot_table(index=["buy", "up"], columns="exit", values="per_pos_c").reindex(columns=list(EXITS)))

# %%
# 3. SETTINGS THAT MADE MONEY IN BOTH HALVES (at least MIN_N positions in each half)
ROB = G[(G.per_train_c > 0) & (G.per_test_c > 0) & (G.n_train >= MIN_N) & (G.n_test >= MIN_N)] \
        .sort_values("worst_half_c", ascending=False)
ROB.to_csv(OUT_ROBUST, index=False)
print(f"3. {len(ROB)} of {len(G)} settings were positive in both halves")
display(ROB.head(40))
print("Done. Files saved to Drive:", *(os.path.basename(p) for p in (OUT_GRID, OUT_ROBUST, OUT_ENTRIES)))
