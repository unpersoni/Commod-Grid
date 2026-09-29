# %% [markdown]
# # 70-75c Entry + 44c Reversal
#
# Entry: first side whose ask reaches 70-75c in the last 7 minutes (10 contracts, fees in).
# Reversal: if its bid falls to 44c inside the reversal window (120 / 90 / 60 / 50 s left),
# buy 20 of the other side (close 10, open 10), held to settlement. Also shown: stop-only,
# and triggers around 44c to check the result isn't a fluke of one price.
#
# Runtime -> Run all. Writes band7075_results.csv and band7075_trades.csv to Drive.

# %%
from google.colab import drive
drive.mount('/content/drive')

# %%
import csv, math, time, collections
import numpy as np
import pandas as pd

TICK_CSV = "/content/drive/MyDrive/commod15min_ticks.csv"
OUT_TRADES = "/content/drive/MyDrive/band7075_trades.csv"
OUT_RESULT = "/content/drive/MyDrive/band7075_results.csv"

WINDOW_SEC      = 420          # bot is armed for the last 7 minutes
COVER_SLACK     = 20           # market must be logged back to >= 400s before close
DEADBAND_SEC    = 2            # ignore the final 2s (book empties at close)
SETTLE_LOOK_SEC = 15
LOTS            = 10
CAL_BANDS = [(51, 55), (55, 60), (60, 65), (65, 70), (70, 75), (75, 80), (80, 85),
             (85, 88), (88, 92), (92, 95), (95, 99)]
TIME_BUCKETS = [(420, 300), (300, 180), (180, 120), (120, 60), (60, DEADBAND_SEC)]
STRATEGY_BANDS = [(88, 92), (55, 60)]
REV_WINDOWS = [30, 60, 90, 120, 420]
V29 = {"band": (88, 92), "mode": "reverse", "trigger_c": 70, "window_s": 90}
SPOT_BUCKETS = [(120, 90), (90, 60), (60, 30), (30, 10), (10, DEADBAND_SEC)]
FILTER_TRIGGERS = [85, 80, 75, 70, 65, 60, 55, 50, 45, 40, 35]
CRYPTO = {"BTC", "ETH", "SOL", "XRP", "DOGE"}

def fee_c(p, n=LOTS):
    """Kalshi taker fee in cents for an order of n contracts at p cents."""
    p = np.asarray(p, float)
    return np.ceil(0.07 * n * p * (100 - p) / 100 - 1e-9)


BAND        = (70, 75)
REV_WINDOWS_7075 = [120, 90, 60, 50]
TRIGGER_C   = 44
NEARBY      = [50, 48, 46, 44, 42, 40, 38]   # robustness check around 44c

# %%
# LOAD — each row read by content (bot versions wrote different column layouts): find the
# ticker, skip text fields, next four numbers = yes_bid, yes_ask, yes_mid, sec_to_close; in rows
# that have them, the next two = spot price and strike.
t0 = time.time()

def num(x):
    try: return float(x)
    except (TypeError, ValueError): return None

recs, cover = [], collections.defaultdict(float)
n_rows = n_bad = 0
with open(TICK_CSV, newline="") as fh:
    rd = csv.reader(fh)
    next(rd)
    for row in rd:
        n_rows += 1
        ti = next((i for i, f in enumerate(row[:4]) if "15M-" in f), None)
        if ti is None:
            n_bad += 1; continue
        vals, first = [], None
        for j in range(ti + 2, len(row)):
            v = num(row[j]) if row[j] != "" else None
            if v is None:
                if vals: break
                continue
            if first is None: first = j
            vals.append(v)
            if len(vals) == 4: break
        if len(vals) < 4:
            n_bad += 1; continue
        yb, ya, mid, s = vals
        if not (0 <= yb <= ya <= 100 and 0 <= mid <= 100 and abs(mid - (yb + ya) / 2) <= 1
                and -60 <= s <= 900):
            n_bad += 1; continue
        t = row[ti]
        cover[t] = max(cover[t], s)
        if s > WINDOW_SEC + 30:
            continue
        spot = strike = np.nan
        if first + 5 < len(row):
            sp, sk = num(row[first + 4]), num(row[first + 5])
            if sk and sk > 0:
                strike = sk
                if sp and abs(sp / sk - 1) < 0.05:
                    spot = sp
        recs.append((t, row[ti + 1], yb, ya, mid, s, spot, strike))

df = pd.DataFrame(recs, columns=["ticker", "asset", "yb", "ya", "mid", "s2c", "spot", "strike"])
del recs
print(f"{n_rows:,} rows read ({n_bad:,} unusable) in {time.time() - t0:.0f}s")

def settle_result(s2c, mid, yb, ya):
    m = s2c <= SETTLE_LOOK_SEC
    iy = np.flatnonzero(m & ((mid >= 99) | (yb >= 99)))
    ino = np.flatnonzero(m & ((mid <= 1) | (ya <= 1)))
    if not len(iy) and not len(ino): return None
    if not len(ino): return True
    if not len(iy): return False
    return bool(iy[-1] > ino[-1])

markets, n_unknown = [], 0
for t, g in df.groupby("ticker", sort=False):
    g = g.sort_values("s2c", ascending=False)
    s2c = g["s2c"].to_numpy(float)
    yb, ya, mid = (g[c].to_numpy(float) for c in ("yb", "ya", "mid"))
    yes_won = settle_result(s2c, mid, yb, ya)
    if yes_won is None:
        n_unknown += 1; continue
    k = s2c > DEADBAND_SEC
    sk = g["strike"].dropna()
    markets.append({"t": t, "asset": g["asset"].iloc[0], "yes_won": yes_won,
                    "cover": cover[t], "s2c": s2c[k], "yb": yb[k], "ya": ya[k], "mid": mid[k],
                    "spot": g["spot"].to_numpy(float)[k],
                    "strike": float(sk.iloc[0]) if len(sk) else np.nan})
del df
full = [m for m in markets if m["cover"] >= WINDOW_SEC - COVER_SLACK]
print(f"{len(markets):,} markets with a clear 99-100c settlement ({n_unknown:,} unclear, skipped)")
print(f"{len(full):,} of them logged back to >= {WINDOW_SEC - COVER_SLACK}s before close "
      f"-> used for the 7-minute tests")

# %%
def first_entry(m, lo, hi, window=WINDOW_SEC):
    """First tick (<= window s left) where a side's ask is in [lo, hi]: (index, yes_side, price)."""
    s, yb, ya, mid = m["s2c"], m["yb"], m["ya"], m["mid"]
    ok = s <= window
    in_yes = ok & (ya >= lo) & (ya <= hi)
    in_no = ok & (100 - yb >= lo) & (100 - yb <= hi)
    idx = np.flatnonzero(in_yes | in_no)
    if not len(idx): return None
    i = int(idx[0])
    yes = bool(in_yes[i] and (not in_no[i] or mid[i] >= 50))
    return i, yes, float(ya[i] if yes else 100 - yb[i])

def entries_for(lo, hi, mkts):
    out = []
    for m in mkts:
        e = first_entry(m, lo, hi)
        if not e: continue
        i, yes, p = e
        bid = m["yb"] if yes else 100 - m["ya"]
        out.append({"m": m, "i": i, "yes": yes, "p": p, "won": yes == m["yes_won"], "bid": bid})
    return out

def triggers(E, T, R):
    """For each entry: first tick after entry with <= R s left where the held bid <= T.
    Returns exit bid [n, nT] (nan = never) and the tick index [n, nT] (-1 = never)."""
    X = np.full((len(E), len(T)), np.nan); I = np.full((len(E), len(T)), -1)
    for k, e in enumerate(E):
        s = e["m"]["s2c"]
        sel = np.flatnonzero((np.arange(len(s)) > e["i"]) & (s <= R))
        if not len(sel): continue
        b = e["bid"][sel]
        cm = np.minimum.accumulate(b)
        j = np.searchsorted(-cm, -np.asarray(T, float), side="left")
        ok = j < len(b)
        X[k, ok] = b[j[ok]]; I[k, ok] = sel[j[ok]]
    return X, I

def pnl(E, X, mode):
    """Per-trade P&L in cents for 10 contracts. X = exit bid (nan = no trigger -> hold)."""
    p = np.array([e["p"] for e in E])[:, None]
    won = np.array([e["won"] for e in E])[:, None]
    hold = np.where(won, 100 - p, -p) * LOTS - fee_c(p)
    x = np.nan_to_num(X, nan=50.0)
    if mode == "stop":
        out = (x - p) * LOTS - fee_c(p) - fee_c(x)
    else:
        out = (x - p) * LOTS + np.where(won, -(100 - x), x) * LOTS - fee_c(p) - fee_c(x, 2 * LOTS)
    return np.where(np.isnan(X), hold, out), hold[:, 0]

# %%
E = entries_for(*BAND, full)
won = np.array([e["won"] for e in E]); price = np.array([e["p"] for e in E])
assets = np.array([e["m"]["asset"] for e in E])
hold = np.where(won, 100 - price, -price) * LOTS - fee_c(price)
print(f"ENTRY: first side whose ask reaches {BAND[0]}-{BAND[1]}c in the last 7 minutes")
print(f"   trades {len(E):,}   favorite won {won.sum():,} ({won.mean():.1%})   lost {(~won).sum():,}"
      f"   avg entry {price.mean():.1f}c")
print(f"   hold every trade to settlement: ${hold.sum() / 100:+,.2f}  (${hold.mean() / 100:+.3f} per trade)")

rows = []
for R in REV_WINDOWS_7075:
    X, I = triggers(E, NEARBY, R)
    for mode in ("reverse", "stop"):
        P, _ = pnl(E, X, mode)
        for k, T in enumerate(NEARBY):
            tr = ~np.isnan(X[:, k])
            rows.append({"window_s": R, "mode": mode, "trigger_c": T, "trades": len(E),
                         "triggered": int(tr.sum()), "right_(fav_lost)": int((tr & ~won).sum()),
                         "wrong_(fav_won)": int((tr & won).sum()),
                         "avg_exit_c": round(float(X[tr, k].mean()), 1) if tr.any() else None,
                         "hold_$": round(hold.sum() / 100, 2),
                         "strategy_$": round(P[:, k].sum() / 100, 2),
                         "vs_hold_$": round((P[:, k].sum() - hold.sum()) / 100, 2),
                         "per_trade_$": round(P[:, k].mean() / 100, 3)})
RES = pd.DataFrame(rows)
RES.to_csv(OUT_RESULT, index=False)
cols = ["window_s", "triggered", "right_(fav_lost)", "wrong_(fav_won)", "avg_exit_c",
        "strategy_$", "vs_hold_$", "per_trade_$"]
print(f"\nREVERSE AT {TRIGGER_C}c — by reversal window")
display(RES[(RES["mode"] == "reverse") & (RES.trigger_c == TRIGGER_C)][cols].reset_index(drop=True))
print(f"STOP ONLY (sell, no reversal) AT {TRIGGER_C}c — by reversal window")
display(RES[(RES["mode"] == "stop") & (RES.trigger_c == TRIGGER_C)][cols].reset_index(drop=True))
print("ROBUSTNESS — reverse, strategy_$ for triggers around 44c (rows = window)")
display(RES[RES["mode"] == "reverse"].pivot(index="window_s", columns="trigger_c", values="strategy_$")
        .sort_index(ascending=False)[sorted(NEARBY, reverse=True)])

# per-trade log + per market
tl = []
for R in REV_WINDOWS_7075:
    X, _ = triggers(E, [TRIGGER_C], R)
    P, _ = pnl(E, X, "reverse")
    for k, e in enumerate(E):
        tl.append({"window_s": R, "ticker": e["m"]["t"], "asset": e["m"]["asset"],
                   "side": "YES" if e["yes"] else "NO", "entry_c": e["p"],
                   "entry_s2c": e["m"]["s2c"][e["i"]], "fav_won": e["won"],
                   "reversed": not np.isnan(X[k, 0]),
                   "exit_c": None if np.isnan(X[k, 0]) else X[k, 0],
                   "pnl_$": P[k, 0] / 100, "hold_pnl_$": hold[k] / 100})
TL = pd.DataFrame(tl)
TL.to_csv(OUT_TRADES, index=False)
print(f"PER MARKET — reverse at {TRIGGER_C}c")
display(TL.groupby(["asset", "window_s"]).agg(trades=("pnl_$", "size"), won=("fav_won", "sum"),
        reversals=("reversed", "sum"), hold_dollars=("hold_pnl_$", "sum"),
        strategy_dollars=("pnl_$", "sum")).round(2).unstack("window_s"))
