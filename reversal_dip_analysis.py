# %% [markdown]
# # Reversal Dip Analysis — how far can a 90c favorite dip and still win?
#
# **Favorite:** in the final 2 minutes (with at least 10s left), the first side whose price (mid) reaches 90c+.
#
# **Question 1:** how often does that favorite lose (opposite side settles the win — a correct reversal)?
#
# **Question 2:** for favorites that dipped but still won, how low did they go? That gives the
# dip range a favorite can reliably bounce back from.
#
# **Question 3:** at each reversal trigger price (and reversal window), does reversing make or
# lose money vs. just holding the favorite?
#
# Dips are measured on the favorite's **bid** (what the bot's stop watches). The final 2 seconds
# are ignored for dips (the book empties at close and shows fake crashes). Settlement: the side
# priced 99-100c in the market's final seconds is the winner.
#
# Runtime → Run all.

# %%
from google.colab import drive
drive.mount('/content/drive')

# %%
import time
import numpy as np
import pandas as pd

TICK_CSV     = "/content/drive/MyDrive/commod15min_ticks.csv"
OUT_FAVS     = "/content/drive/MyDrive/rev_favorites.csv"
OUT_THRESH   = "/content/drive/MyDrive/rev_thresholds.csv"
OUT_ASSET    = "/content/drive/MyDrive/rev_by_asset.csv"

FAV_WINDOW_SEC = 120          # favorite must reach FAV_PRICE_C in the final 2 minutes
FAV_PRICE_C    = 90
FAV_MIN_SEC    = 10           # must reach 90c with at least this much time left — in the final
                              # seconds the winner always prints 99-100c, which isn't a tradeable favorite
DEADBAND_SEC   = 2            # ignore the final 2s (degenerate close ticks)
REV_WINDOWS    = [120, 90, 60, 30]   # reversal armed only when this many sec (or fewer) remain
THRESHOLDS     = np.arange(89, 9, -1)  # reversal trigger: favorite's bid <= T
LOTS           = 10

# %%
t0 = time.time()
df = pd.read_csv(TICK_CSV, usecols=["ticker", "asset", "yes_bid", "yes_ask",
                                    "yes_mid", "sec_to_close"])
df = df.dropna(subset=["yes_mid", "sec_to_close"])
df = df[df["sec_to_close"] <= FAV_WINDOW_SEC]   # includes the final seconds for settlement
df["yes_bid"] = df["yes_bid"].fillna(df["yes_mid"])
df["yes_ask"] = df["yes_ask"].fillna(df["yes_mid"])
tickers = sorted(df["ticker"].unique())
print(f"{len(df):,} ticks in the final {FAV_WINDOW_SEC}s across {len(tickers):,} markets "
      f"({time.time() - t0:.0f}s)")

# %%
# SETTLEMENT FROM THE TICK LOG: in the market's final seconds, the side priced 99-100c is the
# winner. Uses the last such tick (final 15s, including the last 2s and any post-close tick).
SETTLE_LOOK_SEC = 15

def settle_result(s2c, mid, yb, ya):
    m = s2c <= SETTLE_LOOK_SEC
    yes_win = m & ((mid >= 99) | (yb >= 99))
    no_win = m & ((mid <= 1) | (ya <= 1))
    iy = np.flatnonzero(yes_win); ino = np.flatnonzero(no_win)
    if not len(iy) and not len(ino): return None
    if not len(ino): return "yes"
    if not len(iy): return "no"
    return "yes" if iy[-1] > ino[-1] else "no"

favs = []          # one dict per qualified favorite
paths = []         # (s2c array, favorite-bid array) after qualification
n_unknown = 0
for t, g in df.groupby("ticker", sort=False):
    g = g.sort_values("sec_to_close", ascending=False)
    s2c = g["sec_to_close"].to_numpy(float)
    mid = g["yes_mid"].to_numpy(float)
    yb = g["yes_bid"].to_numpy(float)
    ya = g["yes_ask"].to_numpy(float)

    res = settle_result(s2c, mid, yb, ya)
    if res is None:
        n_unknown += 1
        continue
    keep = s2c > DEADBAND_SEC
    s2c, mid, yb, ya = s2c[keep], mid[keep], yb[keep], ya[keep]

    hit = np.flatnonzero(((mid >= FAV_PRICE_C) | (mid <= 100 - FAV_PRICE_C)) & (s2c >= FAV_MIN_SEC))
    if not len(hit):
        continue
    i0 = hit[0]
    fav_yes = bool(mid[i0] >= FAV_PRICE_C)
    bid = (yb if fav_yes else 100 - ya)[i0:]
    entry = float(min(99, ya[i0] if fav_yes else 100 - yb[i0]))
    ss = s2c[i0:]
    j = int(np.argmin(bid))
    favs.append({
        "ticker": t, "asset": g["asset"].iloc[0], "fav_side": "YES" if fav_yes else "NO",
        "qualify_s2c": round(ss[0], 1), "entry_ask": entry,
        "min_bid": float(bid[j]), "min_bid_s2c": round(ss[j], 1),
        "fav_won": (res == "yes") == fav_yes,
    })
    paths.append((ss, bid))

F = pd.DataFrame(favs)
F.to_csv(OUT_FAVS, index=False)
print(f"favorites: {len(F):,}   (markets skipped — no 99-100c price in the final "
      f"{SETTLE_LOOK_SEC}s to confirm the winner: {n_unknown:,})")

# %%
# Reversal simulation for every (window R, trigger T):
#   trigger = first tick with <= R sec left where the favorite's bid <= T
#   exit    = that tick's bid (so price gaps are included, not assumed away)
#   reversal leg bought at 100 - exit; held to settlement
won = F["fav_won"].to_numpy(bool)
entry = F["entry_ask"].to_numpy(float)
hold = np.where(won, 100 - entry, -entry)
nT = len(THRESHOLDS)
rows = []
for R in REV_WINDOWS:
    trig = np.zeros((len(F), nT), bool)
    exitp = np.zeros((len(F), nT))
    ts2c = np.full((len(F), nT), np.nan)
    for i, (ss, bid) in enumerate(paths):
        m = ss <= R
        if not m.any(): continue
        b, st = bid[m], ss[m]
        cm = np.minimum.accumulate(b)
        idx = np.searchsorted(-cm, -THRESHOLDS, side="left")
        ok = idx < len(b)
        trig[i, ok] = True
        exitp[i, ok] = b[idx[ok]]
        ts2c[i, ok] = st[idx[ok]]
    rev = np.where(trig,
                   (exitp - entry[:, None]) + np.where(won[:, None], -(100 - exitp), exitp),
                   hold[:, None])
    for k, T in enumerate(THRESHOLDS):
        tr = trig[:, k]
        n = int(tr.sum())
        bounced = int((tr & won).sum())
        lost = n - bounced
        rows.append({
            "rev_window_s": R, "trigger_c": int(T),
            "favs_dipped_to_T": n,
            "bounced_won": bounced, "fav_lost": lost,
            "bounce_rate": round(bounced / n, 4) if n else None,
            "reversal_win_rate": round(lost / n, 4) if n else None,
            "avg_exit_c": round(float(exitp[tr, k].mean()), 1) if n else None,
            "avg_gap_below_T_c": round(float(T - exitp[tr, k].mean()), 1) if n else None,
            "med_s2c_trigger_bounced": float(np.nanmedian(ts2c[tr & won, k])) if bounced else None,
            "med_s2c_trigger_lost": float(np.nanmedian(ts2c[tr & ~won, k])) if lost else None,
            "hold_all_$": round(hold.sum() * LOTS / 100, 2),
            "reverse_at_T_$": round(rev[:, k].sum() * LOTS / 100, 2),
            "reversal_edge_$": round((rev[:, k].sum() - hold.sum()) * LOTS / 100, 2),
        })
TH = pd.DataFrame(rows)
TH.to_csv(OUT_THRESH, index=False)

per_asset = []
for a, sub in F.groupby("asset"):
    w = sub[sub.fav_won]
    l = sub[~sub.fav_won]
    per_asset.append({
        "asset": a, "favorites": len(sub), "fav_win_rate": round(len(w) / len(sub), 4),
        "fav_lost": len(l),
        "winners_never_below_c_(95%)": float(w.min_bid.quantile(0.05)) if len(w) else None,
        "winners_never_below_c_(90%)": float(w.min_bid.quantile(0.10)) if len(w) else None,
        "winners_median_low_c": float(w.min_bid.median()) if len(w) else None,
        "winners_dipped_<=70": int((w.min_bid <= 70).sum()),
        "losers_dipped_<=70": int((l.min_bid <= 70).sum()),
    })
A = pd.DataFrame(per_asset)
A.to_csv(OUT_ASSET, index=False)
print(f"wrote {OUT_FAVS}, {OUT_THRESH}, {OUT_ASSET}  ({time.time() - t0:.0f}s total)")

# %%
n, nw = len(F), int(F.fav_won.sum())
print("=" * 90)
print(f"Q1. FAVORITES (reached {FAV_PRICE_C}c+ in the final {FAV_WINDOW_SEC}s): {n:,}")
print(f"    favorite won : {nw:,} ({nw / n:.1%})")
print(f"    favorite lost: {n - nw:,} ({(n - nw) / n:.1%})  <- the only cases a reversal can win")
print("=" * 90)
w = F[F.fav_won]
print("Q2. HOW LOW DID WINNING FAVORITES DIP? (lowest bid after reaching 90c)")
for q, lab in [(0.50, "50%"), (0.25, "75%"), (0.10, "90%"), (0.05, "95%"), (0.01, "99%")]:
    print(f"    {lab} of winners never dipped below {w.min_bid.quantile(q):.0f}c")

# %%
edges = [(90, 101, "90c+ (no dip)"), (85, 90, "85-89"), (80, 85, "80-84"), (75, 80, "75-79"),
         (70, 75, "70-74"), (65, 70, "65-69"), (60, 65, "60-64"), (50, 60, "50-59"),
         (40, 50, "40-49"), (30, 40, "30-39"), (0, 30, "under 30")]
out = []
for lo, hi, lab in edges:
    s = F[(F.min_bid >= lo) & (F.min_bid < hi)]
    out.append({"lowest bid reached": lab, "favorites": len(s),
                "won (bounced back)": int(s.fav_won.sum()),
                "lost (reversal right)": int((~s.fav_won).sum()),
                "bounce-back rate": f"{s.fav_won.mean():.1%}" if len(s) else "—"})
print("DIP DEPTH vs OUTCOME — by the LOWEST bid each favorite reached")
pd.DataFrame(out)

# %%
cols = ["trigger_c", "favs_dipped_to_T", "bounced_won", "fav_lost", "bounce_rate",
        "reversal_win_rate", "avg_gap_below_T_c", "med_s2c_trigger_bounced",
        "med_s2c_trigger_lost", "reversal_edge_$"]
for R in [90, 120]:
    print(f"REVERSAL AT TRIGGER T, armed in the last {R}s  "
          f"(reversal_edge_$ > 0 means reversing beat holding, {LOTS} contracts)")
    display(TH[(TH.rev_window_s == R) & TH.trigger_c.isin(range(40, 90, 2))][cols]
            .reset_index(drop=True))

# %%
print("BEST TRIGGER PRICE FOR EACH REVERSAL WINDOW (by reversal_edge_$)")
best = TH.loc[TH.groupby("rev_window_s")["reversal_edge_$"].idxmax()]
display(best[["rev_window_s", "trigger_c", "favs_dipped_to_T", "bounced_won", "fav_lost",
              "reversal_win_rate", "hold_all_$", "reverse_at_T_$", "reversal_edge_$"]]
        .reset_index(drop=True))
print("CURRENT V29 SETTING (70c, last 90s):")
display(TH[(TH.rev_window_s == 90) & (TH.trigger_c == 70)][cols].reset_index(drop=True))

# %%
print("PER MARKET")
A
