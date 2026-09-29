# %% [markdown]
# # Signal Test — entry price, dips, reversals, and the spot-vs-strike signal
#
# 1. **Price vs. real win rate** — for each entry band, how often the FIRST side to reach it
#    (in the last 7 min) wins, vs. the price paid. Where is the market wrong, if anywhere?
# 2. **Your V29 bot exactly** — 88-92c entry in the last 7 min: results, and how far winners
#    dip in the final 2 minutes and still win.
# 3. **Stop / reversal grid** for 88-92c and 55-60c entries — every trigger price x reversal
#    window, for hold / stop-only / reverse. Exits use the real bid at the trigger (gaps count).
# 4. **Spot signal (crypto)** — does Coinbase spot vs. the strike call the winner better than the
#    Kalshi price? Does filtering reversals with it make money?
#
# All P&L: 10 contracts, Kalshi taker fees included. Only markets logged at least ~7 minutes
# before close are used. Settlement: the side priced 99-100c in the final seconds.
#
# Runtime -> Run all.

# %%
from google.colab import drive
drive.mount('/content/drive')

# %%
import csv, math, time, collections
import numpy as np
import pandas as pd

TICK_CSV = "/content/drive/MyDrive/commod15min_ticks.csv"
OUT_CAL  = "/content/drive/MyDrive/sig_calibration.csv"
OUT_GRID = "/content/drive/MyDrive/sig_reversal_grid.csv"
OUT_SPOT = "/content/drive/MyDrive/sig_spot_accuracy.csv"
OUT_FILT = "/content/drive/MyDrive/sig_spot_filter.csv"

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

# 1. PRICE vs REAL WIN RATE
cal_rows = []
for lo, hi in CAL_BANDS:
    E = []
    for m in full:
        e = first_entry(m, lo, hi)
        if e:
            i, yes, p = e
            E.append((p, yes == m["yes_won"], m["s2c"][i]))
    if not E: continue
    p = np.array([x[0] for x in E]); w = np.array([x[1] for x in E]); s = np.array([x[2] for x in E])
    def summ(mask, label):
        if not mask.any(): return
        pp, ww = p[mask], w[mask]
        hold = (np.where(ww, 100 - pp, -pp) * LOTS - fee_c(pp)).sum() / 100
        cal_rows.append({"band": f"{lo}-{hi}c", "entry_time": label, "entries": int(mask.sum()),
                         "win_rate": round(ww.mean(), 4), "avg_price_c": round(pp.mean(), 1),
                         "edge_c_per_contract": round(ww.mean() * 100 - pp.mean(), 2),
                         "hold_$": round(hold, 2), "hold_$_per_trade": round(hold / mask.sum(), 3)})
    summ(np.ones(len(p), bool), "all")
    for a, b in TIME_BUCKETS:
        summ((s <= a) & (s > b), f"{a}-{b}s left")
CAL = pd.DataFrame(cal_rows)
CAL.to_csv(OUT_CAL, index=False)
print("1. FIRST SIDE TO REACH EACH PRICE BAND (last 7 min): how often it wins vs. the price paid")
print("   edge_c_per_contract > 0 means that band wins MORE often than its price says")
display(CAL[CAL.entry_time == "all"].reset_index(drop=True))

# %%
print("1b. SAME, SPLIT BY WHEN THE ENTRY HAPPENED (seconds left)")
display(CAL.pivot(index="band", columns="entry_time", values="edge_c_per_contract")
        .reindex(columns=["all"] + [f"{a}-{b}s left" for a, b in TIME_BUCKETS]))

# %%
# 3. STOP / REVERSAL GRID (built first; section 2 reads the 88-92c results from it)
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

grid_rows, ENTRIES = [], {}
for lo, hi in STRATEGY_BANDS:
    E = entries_for(lo, hi, full)
    ENTRIES[(lo, hi)] = E
    if not E: continue
    T = np.arange(5, lo - 1)
    won = np.array([e["won"] for e in E])
    for R in REV_WINDOWS:
        X, _ = triggers(E, T, R)
        for mode in ("stop", "reverse"):
            P, hold = pnl(E, X, mode)
            for k, t in enumerate(T):
                tr = ~np.isnan(X[:, k])
                grid_rows.append({
                    "band": f"{lo}-{hi}c", "mode": mode, "window_s": R, "trigger_c": int(t),
                    "entries": len(E), "triggered": int(tr.sum()),
                    "fav_lost_(right)": int((tr & ~won).sum()),
                    "fav_won_(wrong)": int((tr & won).sum()),
                    "avg_exit_c": round(float(X[tr, k].mean()), 1) if tr.any() else None,
                    "hold_all_$": round(hold.sum() / 100, 2),
                    "strategy_$": round(P[:, k].sum() / 100, 2),
                    "vs_hold_$": round((P[:, k].sum() - hold.sum()) / 100, 2)})
G = pd.DataFrame(grid_rows)
G.to_csv(OUT_GRID, index=False)

# %%
lo, hi = V29["band"]
E = ENTRIES[(lo, hi)]
won = np.array([e["won"] for e in E]); price = np.array([e["p"] for e in E])
hold_total = (np.where(won, 100 - price, -price) * LOTS - fee_c(price)).sum() / 100
v29 = G[(G.band == f"{lo}-{hi}c") & (G["mode"] == V29["mode"]) & (G.window_s == V29["window_s"])
        & (G.trigger_c == V29["trigger_c"])].iloc[0]
print("=" * 90)
print(f"2. YOUR V29 BOT: favorite's ask {lo}-{hi}c in the last 7 min, 10 contracts, fees in")
print(f"   trades {len(E):,}   favorite won {won.sum():,} ({won.mean():.1%})   "
      f"lost {(~won).sum():,}   avg entry {price.mean():.1f}c")
print(f"   hold every trade to settlement ...................... ${hold_total:+,.2f}"
      f"  (${hold_total / len(E):+.3f} per trade)")
print(f"   V29 as set (reverse at {V29['trigger_c']}c, last {V29['window_s']}s) ........... "
      f"${v29['strategy_$']:+,.2f}  ({v29['triggered']} reversals: "
      f"{v29['fav_lost_(right)']} right, {v29['fav_won_(wrong)']} wrong)")
print("=" * 90)

rows = []
for e in E:
    s = e["m"]["s2c"]
    sel = (np.arange(len(s)) > e["i"])
    f2 = sel & (s <= 120)
    rows.append({"won": e["won"], "entry_s2c": s[e["i"]],
                 "low_final_2min": e["bid"][f2].min() if f2.any() else np.nan,
                 "low_whole_hold": e["bid"][sel].min() if sel.any() else np.nan})
D = pd.DataFrame(rows)
w = D[D.won]
print("HOW LOW DID WINNING 88-92c FAVORITES DIP IN THE FINAL 2 MINUTES (and still win)?")
for q in [0.5, 0.25, 0.10, 0.05, 0.02, 0.01]:
    print(f"   {1 - q:.0%} of winners stayed above {w.low_final_2min.quantile(q):.0f}c "
          f"(whole hold: {w.low_whole_hold.quantile(q):.0f}c)")
print("\nONCE THE BID TOUCHES T IN THE FINAL 2 MINUTES, HOW OFTEN DOES IT STILL WIN?")
out = []
for T in [85, 80, 75, 72, 70, 68, 65, 60, 55, 50, 45, 40, 35, 30]:
    s_ = D[D.low_final_2min <= T]
    out.append({"bid dipped to": f"{T}c", "trades": len(s_), "still won": int(s_.won.sum()),
                "lost": int((~s_.won).sum()),
                "bounce-back rate": f"{s_.won.mean():.1%}" if len(s_) else "—"})
display(pd.DataFrame(out))

# %%
print("3. STOP / REVERSAL — BEST SETTING FOR EACH ENTRY BAND, MODE AND WINDOW")
print("   vs_hold_$ > 0 = beats simply holding every trade to settlement")
best = G.loc[G.groupby(["band", "mode", "window_s"])["vs_hold_$"].idxmax()]
display(best[["band", "mode", "window_s", "trigger_c", "triggered", "fav_lost_(right)",
              "fav_won_(wrong)", "avg_exit_c", "hold_all_$", "strategy_$", "vs_hold_$"]]
        .reset_index(drop=True))

# %%
cols = ["trigger_c", "triggered", "fav_lost_(right)", "fav_won_(wrong)", "avg_exit_c",
        "strategy_$", "vs_hold_$"]
for (lo, hi) in STRATEGY_BANDS:
    for mode in ("reverse", "stop"):
        sub = G[(G.band == f"{lo}-{hi}c") & (G["mode"] == mode) & (G.window_s == 90)
                & (G.trigger_c % 5 == 0)]
        print(f"{lo}-{hi}c ENTRY — {mode.upper()} at trigger, armed last 90s")
        display(sub[cols].sort_values("trigger_c", ascending=False).reset_index(drop=True))

# %%
# 4. SPOT SIGNAL (crypto). YES normally = settles at/above the strike; the direction is checked
# against real outcomes below rather than assumed.
spot_m = [m for m in markets if m["asset"] in CRYPTO and not np.isnan(m["strike"])
          and np.isfinite(m["spot"]).any()]
agree = n_dir = 0
for m in spot_m:
    ok = np.isfinite(m["spot"]) & (m["s2c"] <= 10)
    if ok.any():
        n_dir += 1
        agree += (m["spot"][ok][-1] > m["strike"]) == m["yes_won"]
UP_IS_YES = agree >= n_dir / 2
print(f"4. SPOT SIGNAL — {len(spot_m):,} crypto markets with spot + strike logged")
if n_dir:
    print(f"   direction check: spot above strike in the final 10s matched YES winning in "
          f"{agree / n_dir:.1%} of {n_dir:,} markets -> YES = {'above' if UP_IS_YES else 'below'} strike")

def spot_says_yes(m):
    """Per tick: +1 spot says YES wins, -1 says NO, 0 unknown."""
    sp = m["spot"]
    out = np.where(np.isfinite(sp), np.where((sp > m["strike"]) == UP_IS_YES, 1, -1), 0)
    return out.astype(int)

def proj_says_yes(m):
    """Last 60s: projected settlement average = average spot so far in the final minute plus
    the current spot for the seconds still to come. +1/-1/0 per tick."""
    s, sp = m["s2c"], m["spot"]
    out = np.zeros(len(s), int)
    in60 = np.flatnonzero((s <= 60) & np.isfinite(sp))
    if not len(in60): return out
    v = sp[in60]
    run = np.cumsum(v) / np.arange(1, len(v) + 1)
    rem = s[in60]
    proj = (run * (60 - rem) + v * rem) / 60
    out[in60] = np.where((proj > m["strike"]) == UP_IS_YES, 1, -1)
    return out

acc = []
for m in spot_m:
    s = m["s2c"]; truth = 1 if m["yes_won"] else -1
    ks = np.sign(m["mid"] - 50).astype(int)
    ss, ps = spot_says_yes(m), proj_says_yes(m)
    for a, b in SPOT_BUCKETS:
        sel = (s <= a) & (s > b) & (ss != 0) & (ks != 0)
        if not sel.any(): continue
        dis = sel & (ss != ks)
        pr = sel & (ps != 0)
        acc.append({"bucket": f"{a}-{b}s left", "ticks": int(sel.sum()),
                    "kalshi_right": int((ks[sel] == truth).sum()),
                    "spot_right": int((ss[sel] == truth).sum()),
                    "proj_ticks": int(pr.sum()), "proj_right": int((ps[pr] == truth).sum()),
                    "disagree": int(dis.sum()), "spot_right_when_disagree": int((ss[dis] == truth).sum())})
A = pd.DataFrame(acc)
if len(A):
    A = A.groupby("bucket", sort=False).sum().reset_index()
    A["kalshi_accuracy"] = (A.kalshi_right / A.ticks).round(4)
    A["spot_accuracy"] = (A.spot_right / A.ticks).round(4)
    A["projected_avg_accuracy"] = np.where(A.proj_ticks > 0, (A.proj_right / A.proj_ticks.clip(lower=1)).round(4), np.nan)
    A["disagree_%"] = (A.disagree / A.ticks).round(4)
    A["spot_right_when_they_disagree"] = np.where(A.disagree > 0, (A.spot_right_when_disagree / A.disagree.clip(lower=1)).round(4), np.nan)
    A.to_csv(OUT_SPOT, index=False)
    print("   WHO CALLS THE WINNER BETTER? (every logged tick; Kalshi = side above 50c)")
    display(A[["bucket", "ticks", "kalshi_accuracy", "spot_accuracy", "projected_avg_accuracy",
               "disagree_%", "spot_right_when_they_disagree"]])

# %%
# 4b. Reversals filtered by spot: only reverse / stop if spot ALSO says the favorite is losing.
filt_rows = []
spot_set = {id(m) for m in spot_m}
for (lo, hi) in STRATEGY_BANDS:
    E = [e for e in ENTRIES[(lo, hi)] if id(e["m"]) in spot_set]
    if not E: continue
    Ts = [t for t in FILTER_TRIGGERS if t < lo - 1]
    for R in (60, 90, 120):
        X, I = triggers(E, Ts, R)
        conf = np.zeros_like(X, bool)
        for k, e in enumerate(E):
            ss = spot_says_yes(e["m"])
            fav = 1 if e["yes"] else -1
            for j in range(len(Ts)):
                if I[k, j] >= 0:
                    conf[k, j] = ss[I[k, j]] == -fav
        won = np.array([e["won"] for e in E])
        for mode in ("reverse", "stop"):
            P_all, hold = pnl(E, X, mode)
            P_conf, _ = pnl(E, np.where(conf, X, np.nan), mode)
            for j, t in enumerate(Ts):
                tr, tc = ~np.isnan(X[:, j]), conf[:, j]
                filt_rows.append({
                    "band": f"{lo}-{hi}c", "mode": mode, "window_s": R, "trigger_c": t,
                    "crypto_entries": len(E),
                    "price_only_n": int(tr.sum()), "price_only_right": int((tr & ~won).sum()),
                    "price_only_vs_hold_$": round((P_all[:, j].sum() - hold.sum()) / 100, 2),
                    "spot_confirmed_n": int(tc.sum()), "spot_confirmed_right": int((tc & ~won).sum()),
                    "spot_confirmed_vs_hold_$": round((P_conf[:, j].sum() - hold.sum()) / 100, 2)})
        # pure signal: reverse the first time spot (or the projected average) flips against it
        for sig_name, fn in (("spot crosses strike", spot_says_yes),
                             ("projected avg crosses", proj_says_yes)):
            Xs = np.full((len(E), 1), np.nan)
            for k, e in enumerate(E):
                s = e["m"]["s2c"]; sig = fn(e["m"]); fav = 1 if e["yes"] else -1
                hit = np.flatnonzero((np.arange(len(s)) > e["i"]) & (s <= R) & (sig == -fav))
                if len(hit): Xs[k, 0] = e["bid"][hit[0]]
            for mode in ("reverse", "stop"):
                P, hold = pnl(E, Xs, mode)
                tr = ~np.isnan(Xs[:, 0])
                filt_rows.append({
                    "band": f"{lo}-{hi}c", "mode": mode, "window_s": R, "trigger_c": sig_name,
                    "crypto_entries": len(E), "price_only_n": None, "price_only_right": None,
                    "price_only_vs_hold_$": None,
                    "spot_confirmed_n": int(tr.sum()), "spot_confirmed_right": int((tr & ~won).sum()),
                    "spot_confirmed_vs_hold_$": round((P[:, 0].sum() - hold.sum()) / 100, 2)})
FL = pd.DataFrame(filt_rows)
FL.to_csv(OUT_FILT, index=False)
print("4b. REVERSE/STOP ONLY WHEN SPOT AGREES THE FAVORITE IS LOSING (crypto markets)")
print("    right = the favorite really lost. vs_hold_$ > 0 = beats holding every crypto trade.")
for (lo, hi) in STRATEGY_BANDS:
    sub = FL[(FL.band == f"{lo}-{hi}c") & (FL["mode"] == "reverse") & (FL.window_s == 90)]
    if len(sub):
        print(f"\n{lo}-{hi}c entry, REVERSE, armed last 90s")
        display(sub.drop(columns=["band", "mode", "window_s"]).reset_index(drop=True))

# %%
print("PER MARKET — V29 band (88-92c): hold vs. V29 reversal")
lo, hi = V29["band"]
E = ENTRIES[(lo, hi)]
X, _ = triggers(E, [V29["trigger_c"]], V29["window_s"])
P, hold = pnl(E, X, V29["mode"])
per = collections.defaultdict(lambda: [0, 0, 0.0, 0.0])
for k, e in enumerate(E):
    r = per[e["m"]["asset"]]
    r[0] += 1; r[1] += e["won"]; r[2] += hold[k]; r[3] += P[k, 0]
display(pd.DataFrame([{"asset": a, "trades": r[0], "win_rate": round(r[1] / r[0], 4),
                       "hold_$": round(r[2] / 100, 2), "v29_$": round(r[3] / 100, 2)}
                      for a, r in sorted(per.items())]))
