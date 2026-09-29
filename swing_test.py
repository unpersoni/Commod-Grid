# %% [markdown]
# # Swing Test — buy, then sell when the price rises 15c
#
# Uses every tick log on your Drive (the old commod15min_ticks.csv plus the new bots' files).
# One trade per market: the first chance to buy, then sell as soon as the side's **bid** is
# up +T cents from what was paid (the ask). If the target never comes, one of these happens:
# - **hold** — ride it to settlement ($1 or $0)
# - **stop15 / stop25** — sell if the bid falls 15c / 25c below the price paid
# - **sell@30s** — sell at the bid with 30 seconds left
#
# **A. No signal** — all 11 markets. Buy the first time a side's ask is in a price band.
# **B. Crypto spot signal** — BTC, ETH, SOL, XRP, DOGE. Buy the side that Coinbase spot is
#    moving toward, the moment spot has moved at least M% in the last L seconds (and that
#    side's ask is 20-80c). Also shows how far Kalshi's price moved in the seconds AFTER the
#    signal — if Kalshi lags spot, that's where it shows up.
#
# How to read it: the hit rate will look high. Compare it with **breakeven_hit_rate** —
# the hit rate needed to pay for the losing trades. Only hit_rate above that makes money.
# Part B tests many combinations, so the single best row will look good by luck alone;
# trust a pattern only if neighbouring rows agree.
#
# Entries between 420s and 60s before close (the part of the market the old log covers).
# 10 contracts, Kalshi taker fees on every buy and sell. Runtime -> Run all.

# %%
from google.colab import drive
drive.mount('/content/drive')

# %%
import csv, os, time, collections
import numpy as np
import pandas as pd

D = "/content/drive/MyDrive/"
TICK_FILES = [D + f for f in ("commod15min_ticks.csv", "commod1dollar_v1_ticks.csv",
                              "commod15min_v30_ticks.csv", "commod1dollar_v2_ticks.csv",
                              "commod15min_v31_ticks.csv")]
SETTLE_FILES = [D + f for f in ("commod1dollar_v1_settlements.csv", "commod15min_v30_settlements.csv",
                                "commod1dollar_v2_settlements.csv", "commod15min_v31_settlements.csv")]
OUT_A      = D + "swing_no_signal.csv"
OUT_B      = D + "swing_spot_signal.csv"
OUT_DRIFT  = D + "swing_after_signal.csv"

CRYPTO       = {"BTC", "ETH", "SOL", "XRP", "DOGE"}
ENTRY_FROM   = 420     # earliest entry (seconds before close)
ENTRY_TO     = 60      # latest entry
COVER_MIN    = 400     # market must be logged from at least this many seconds before close
DEADBAND_SEC = 2       # ignore ticks this close to close (the book empties out)
LOTS         = 10
BANDS        = [(20, 35), (35, 50), (50, 65), (65, 80)]
TARGETS      = [10, 15, 20]
EXITS        = ["hold", "stop15", "stop25", "sell@30s"]
SIG_LOOKBACK = [5, 10, 20, 30]           # L seconds
SIG_MOVE     = [0.01, 0.02, 0.03, 0.05]  # M percent
SIG_ASK      = (20, 80)
MATCH_SEC    = 3.0
DRIFT_AT     = [5, 10, 30, 60]

def fee_c(p, n=LOTS):
    return float(np.ceil(0.07 * n * p * (100 - p) / 100 - 1e-9))

# %%
# LOAD — rows read by content (the old file mixes column layouts): ticker, asset, then the
# next four numbers are yes_bid, yes_ask, yes_mid, sec_to_close; spot and strike follow.
# Stored in compact arrays (not a big Python list) so all 11 markets fit in Colab's memory.
from array import array
import gc

def num(x):
    try: return float(x)
    except (TypeError, ValueError): return None

t0 = time.time()
tid_of, tickers, asset_of = {}, [], []
TID, YB, YA, S2C = array("i"), array("f"), array("f"), array("f")
SPOT, STRIKE = array("d"), array("d")
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
            if not (0 <= yb <= ya <= 100 and abs(mid - (yb + ya) / 2) <= 1 and -60 <= s <= 900):
                continue
            t = row[ti]
            if s > cover[t]: cover[t] = s
            if s > ENTRY_FROM + 40: continue
            spot = strike = NAN
            if row[ti + 1] in CRYPTO and first + 5 < len(row):
                sp, sk = num(row[first + 4]), num(row[first + 5])
                if sk and sk > 0:
                    strike = sk
                    if sp and abs(sp / sk - 1) < 0.05: spot = sp
            k = tid_of.get(t)
            if k is None:
                k = tid_of[t] = len(tickers); tickers.append(t); asset_of.append(row[ti + 1])
            TID.append(k); YB.append(yb); YA.append(ya); S2C.append(s)
            SPOT.append(spot); STRIKE.append(strike)
    print(f"{os.path.basename(path)}: {len(TID) - n0:,} rows kept ({time.time() - t0:.0f}s)")

TID, YB, YA, S2C = (np.frombuffer(a, dtype=d) for a, d in
                    ((TID, np.int32), (YB, np.float32), (YA, np.float32), (S2C, np.float32)))
SPOT, STRIKE = np.frombuffer(SPOT, dtype=np.float64), np.frombuffer(STRIKE, dtype=np.float64)
order = np.lexsort((-S2C, TID))            # by market, then time (seconds-to-close falling)
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
    if a == b: continue
    t = tickers[TID[a]]
    if cover[t] < COVER_MIN: continue
    s2c = S2C[a:b].astype(float)
    keep = np.r_[True, np.diff(s2c) != 0]  # drop duplicate timestamps
    s2c = s2c[keep]
    yb, ya = YB[a:b][keep].astype(float), YA[a:b][keep].astype(float)
    res = official.get(t)
    if res is None: res = price_result(s2c, yb, ya)
    if res is None: continue
    k = s2c > DEADBAND_SEC
    sk = STRIKE[a:b][np.isfinite(STRIKE[a:b])]
    markets.append({"t": t, "asset": asset_of[TID[a]], "yes_won": res,
                    "strike": float(sk[0]) if len(sk) else np.nan,
                    "s2c": s2c[k], "yb": yb[k], "ya": ya[k], "spot": SPOT[a:b][keep][k]})
del TID, YB, YA, S2C, SPOT, STRIKE; gc.collect()
print(f"{len(markets):,} markets logged from {COVER_MIN}s+ before close with a known result "
      f"({sum(m['t'] in official for m in markets):,} from official settlement files) "
      f"in {time.time() - t0:.0f}s")
print(pd.Series([m["asset"] for m in markets]).value_counts().to_dict())

# %%
# THE TRADE: bought side `yes` at tick i for `paid` (its ask). Returns (net cents, target hit?).
def run_trade(m, i, yes, target, exit_rule):
    s = m["s2c"]
    bid = m["yb"] if yes else 100 - m["ya"]
    paid = float(m["ya"][i] if yes else 100 - m["yb"][i])
    fut_b, fut_s = bid[i + 1:], s[i + 1:]
    n = len(fut_b)
    def first(mask):
        k = np.flatnonzero(mask)
        return int(k[0]) if len(k) else n
    j_tgt = first(fut_b >= paid + target)
    j_out = n
    if exit_rule.startswith("stop"):
        j_out = first(fut_b <= paid - int(exit_rule[4:]))
    elif exit_rule == "sell@30s":
        j_out = first(fut_s <= 30)
    j = min(j_tgt, j_out)
    if j < n:                                   # sold before settlement
        px = float(fut_b[j])
        return (px - paid) * LOTS - fee_c(paid) - fee_c(px), j == j_tgt
    won = yes == m["yes_won"]                   # held to settlement
    return ((100 - paid) if won else -paid) * LOTS - fee_c(paid), False

def summarize(rows, keys):
    out = []
    for key, g in rows.groupby(keys, sort=False):
        net, hit = g.net.to_numpy(), g.hit.to_numpy()
        wins, losses = net[net > 0], net[net <= 0]
        aw = wins.mean() if len(wins) else 0.0
        al = -losses.mean() if len(losses) else 0.0
        out.append(dict(zip(keys, key if isinstance(key, tuple) else (key,))) | {
            "trades": len(g), "target_hit_rate": round(hit.mean(), 3),
            "win_rate": round((net > 0).mean(), 3),
            "breakeven_hit_rate": round(al / (aw + al), 3) if aw + al else None,
            "avg_win_$": round(aw / 100, 2), "avg_loss_$": round(al / 100, 2),
            "total_$": round(net.sum() / 100, 2), "per_trade_$": round(net.mean() / 100, 3),
            "avg_paid_c": round(g.paid.mean(), 1)})
    return pd.DataFrame(out)

# %%
# A. NO SIGNAL — first time either side's ask is in the band (favorite if both are)
assert "markets" in globals(), ("the LOAD cell above didn't finish — run all cells from the top "
                                "(if Colab said it ran out of memory, Runtime -> Restart, then Run all)")
rows = []
for m in markets:
    s, yb, ya = m["s2c"], m["yb"], m["ya"]
    ok = (s <= ENTRY_FROM) & (s >= ENTRY_TO)
    for lo, hi in BANDS:
        in_yes = ok & (ya >= lo) & (ya <= hi)
        in_no = ok & (100 - yb >= lo) & (100 - yb <= hi)
        idx = np.flatnonzero(in_yes | in_no)
        if not len(idx): continue
        i = int(idx[0])
        yes = bool(in_yes[i] and (not in_no[i] or ya[i] >= 100 - yb[i]))
        paid = float(ya[i] if yes else 100 - yb[i])
        for T in TARGETS:
            for ex in EXITS:
                net, hit = run_trade(m, i, yes, T, ex)
                rows.append((m["asset"], f"{lo}-{hi}c", T, ex, paid, net, hit))
RA = pd.DataFrame(rows, columns=["asset", "band", "target_c", "exit", "paid", "net", "hit"])
assert len(RA), "no entries found — are the tick files on Drive?"
A = pd.concat([summarize(RA, ["band", "target_c", "exit"]).assign(asset="ALL"),
               summarize(RA, ["asset", "band", "target_c", "exit"])], ignore_index=True)
A.to_csv(OUT_A, index=False)
print("A. NO SIGNAL — all 11 markets, one trade per market per band")
print("   total_$ (10 contracts, fees included) — rows: band + target, columns: what happens if the target never comes")
display(A[A.asset == "ALL"].pivot(index=["band", "target_c"], columns="exit", values="total_$")[EXITS])
print("   the +15c target in detail")
display(A[(A.asset == "ALL") & (A.target_c == 15)]
        .drop(columns=["asset"]).reset_index(drop=True))
print("   +15c target, hold if missed — total_$ per asset")
display(A[(A.asset != "ALL") & (A.target_c == 15) & (A.exit == "hold")]
        .pivot(index="asset", columns="band", values="total_$"))

# %%
# B. CRYPTO SPOT SIGNAL
cm = [m for m in markets if m["asset"] in CRYPTO and np.isfinite(m["strike"])]
agree = n_dir = 0
for m in cm:
    ok = np.isfinite(m["spot"]) & (m["s2c"] <= 10)
    if ok.any():
        n_dir += 1
        agree += (m["spot"][ok][-1] > m["strike"]) == m["yes_won"]
UP_IS_YES = agree >= n_dir / 2
print(f"{len(cm):,} crypto markets. Spot above strike at the end matched YES in "
      f"{agree / max(n_dir, 1):.1%} -> spot rising favors {'YES' if UP_IS_YES else 'NO'}")

rows, drift = [], []
for m in cm:
    s, sp, yb, ya = m["s2c"], m["spot"], m["yb"], m["ya"]
    has = np.isfinite(sp)
    if has.sum() < 10: continue
    si, ss, sv = np.flatnonzero(has), s[has], sp[has]
    order = np.argsort(ss)                       # ascending s2c, for searchsorted
    ss_a, sv_a = ss[order], sv[order]
    for L in SIG_LOOKBACK:
        # spot L seconds earlier (= s2c + L), from the nearest spot tick within MATCH_SEC
        k = np.clip(np.searchsorted(ss_a, ss + L), 1, len(ss_a) - 1)
        k = np.where(np.abs(ss_a[k - 1] - (ss + L)) < np.abs(ss_a[k] - (ss + L)), k - 1, k)
        good = np.abs(ss_a[k] - (ss + L)) <= MATCH_SEC
        move = np.where(good, (sv - sv_a[k]) / sv_a[k] * 100, 0.0)
        for M in SIG_MOVE:
            fired = None
            for q in np.flatnonzero(np.abs(move) >= M):
                i = int(si[q])
                if not (ENTRY_TO <= s[i] <= ENTRY_FROM): continue
                yes = (move[q] > 0) == UP_IS_YES
                ask = ya[i] if yes else 100 - yb[i]
                if SIG_ASK[0] <= ask <= SIG_ASK[1]:
                    fired = (i, yes, float(ask)); break
            if fired is None: continue
            i, yes, paid = fired
            for T in TARGETS:
                for ex in ("hold", "stop15", "sell@30s"):
                    net, hit = run_trade(m, i, yes, T, ex)
                    rows.append((m["asset"], L, M, T, ex, paid, net, hit))
            # what Kalshi did around the signal (side's bid, cents)
            bid = yb if yes else 100 - ya
            d = {"asset": m["asset"], "lookback_s": L, "spot_move_pct": M,
                 "paid": paid, "spread": paid - bid[i]}
            back = np.flatnonzero(np.abs(s - (s[i] + L)) <= MATCH_SEC)
            d["bid_move_before"] = bid[i] - bid[back[np.argmin(np.abs(s[back] - (s[i] + L)))]] \
                if len(back) else np.nan
            for h in DRIFT_AT:
                fw = np.flatnonzero(np.abs(s - (s[i] - h)) <= MATCH_SEC)
                d[f"bid_gain_{h}s"] = bid[fw[np.argmin(np.abs(s[fw] - (s[i] - h)))]] - bid[i] \
                    if len(fw) else np.nan
            drift.append(d)
RB = pd.DataFrame(rows, columns=["asset", "lookback_s", "spot_move_pct", "target_c", "exit",
                                 "paid", "net", "hit"])
if len(RB):
    B = summarize(RB, ["lookback_s", "spot_move_pct", "target_c", "exit"])
    B.to_csv(OUT_B, index=False)
    print("B. SPOT SIGNAL — buy the side spot is moving toward. total_$ with a +15c target")
    display(B[B.target_c == 15].pivot(index=["lookback_s", "spot_move_pct"], columns="exit",
                                     values="total_$")[["hold", "stop15", "sell@30s"]])
    print("   ...number of trades behind each row")
    display(B[(B.target_c == 15) & (B.exit == "hold")]
            .pivot(index="lookback_s", columns="spot_move_pct", values="trades"))
    print("   +15c target, all rows")
    display(B[B.target_c == 15].reset_index(drop=True))
else:
    print("B. no spot data found")

# %%
# DOES KALSHI LAG SPOT? Side's bid (cents) before and after the signal.
# bid_move_before: how much Kalshi already moved during the L seconds of the spot move.
# bid_gain_Xs: how much further it moved X seconds after the signal.
# To make money buying at the ask, bid_gain must beat the spread plus about 2-4c of fees.
if drift:
    DR = pd.DataFrame(drift)
    cols = ["spread", "bid_move_before"] + [f"bid_gain_{h}s" for h in DRIFT_AT]
    DG = DR.groupby(["lookback_s", "spot_move_pct"])[cols].mean().round(2)
    DG.insert(0, "signals", DR.groupby(["lookback_s", "spot_move_pct"]).size())
    DG.to_csv(OUT_DRIFT)
    display(DG)
