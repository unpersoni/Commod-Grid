# %% [markdown]
# # Last Seconds Test — does a 99c favorite with 10 seconds left always win?
#
# Uses ONLY markets with Kalshi's official result (the new bots' settlement files), so the
# winner is never guessed from the price. All 11 markets.
#
# 1. **Checkpoints** — at 30, 20, 15, 10 and 5 seconds before close: the favorite's ask
#    (95c ... 99c) and how often it won. Profit per contract after the fee (10 contracts).
# 2. **The trade** — the first moment in the last 10 seconds when a side's ask is 99c: buy it.
#    How often does it lose?
# 3. **Every loss** listed, so you can see which markets and what happened.
#
# Runtime -> Run all.

# %%
from google.colab import drive
drive.mount('/content/drive')

# %%
import csv, os, time, collections
from array import array
import numpy as np
import pandas as pd

D = "/content/drive/MyDrive/"
TICK_FILES = [D + f for f in ("commod1dollar_v1_ticks.csv", "commod15min_v30_ticks.csv",
                              "commod1dollar_v2_ticks.csv", "commod15min_v31_ticks.csv",
                              "commod15min_ticks.csv")]
SETTLE_FILES = [D + f for f in ("commod1dollar_v1_settlements.csv", "commod15min_v30_settlements.csv",
                                "commod1dollar_v2_settlements.csv", "commod15min_v31_settlements.csv")]
OUT_CHECK  = D + "last_seconds_checkpoints.csv"
OUT_TRADE  = D + "last_seconds_99c_trade.csv"
OUT_LOSSES = D + "last_seconds_losses.csv"

CHECKPOINTS = [30, 20, 15, 10, 5]
MATCH_SEC   = 2.0     # use the tick within 2s of each checkpoint
ASKS        = [95, 96, 97, 98, 99]
TRADE_SEC   = 10      # the trade: first 99c ask with this many seconds (or fewer) left
DEADBAND    = 1.0     # ignore ticks this close to close (the book empties out)
LOTS        = 10

def fee_c(p, n=LOTS):
    return float(np.ceil(0.07 * n * p * (100 - p) / 100 - 1e-9))

# %%
official = {}
for path in SETTLE_FILES:
    if os.path.exists(path):
        s = pd.read_csv(path)
        official.update({t: r == "yes" for t, r in zip(s["ticker"], s["result"]) if r in ("yes", "no")})
print(f"{len(official):,} markets with an official result")

# LOAD — rows read by content: ticker, asset, then yes_bid, yes_ask, yes_mid, sec_to_close.
def num(x):
    try: return float(x)
    except (TypeError, ValueError): return None

t0 = time.time()
ticks = collections.defaultdict(lambda: (array("f"), array("f"), array("f")))
asset_of = {}
for path in TICK_FILES:
    if not os.path.exists(path):
        print("not found (skipped):", path); continue
    n0 = sum(len(v[0]) for v in ticks.values())
    with open(path, newline="") as fh:
        rd = csv.reader(fh); next(rd, None)
        for row in rd:
            ti = next((i for i, f in enumerate(row[:4]) if "15M-" in f), None)
            if ti is None or row[ti] not in official: continue
            vals = []
            for j in range(ti + 2, len(row)):
                v = num(row[j]) if row[j] != "" else None
                if v is None:
                    if vals: break
                    continue
                vals.append(v)
                if len(vals) == 4: break
            if len(vals) < 4: continue
            yb, ya, mid, s = vals
            if not (0 <= yb <= ya <= 100 and abs(mid - (yb + ya) / 2) <= 1 and DEADBAND < s <= 40):
                continue
            a, b, c = ticks[row[ti]]
            a.append(s); b.append(yb); c.append(ya)
            asset_of[row[ti]] = row[ti + 1]
    print(f"{os.path.basename(path)}: {sum(len(v[0]) for v in ticks.values()) - n0:,} rows kept "
          f"({time.time() - t0:.0f}s)")
print(f"{len(ticks):,} markets with ticks in the last 40s and an official result")

# %%
chk, trades, losses = [], [], []
for t, (s, yb, ya) in ticks.items():
    s, yb, ya = (np.frombuffer(x, dtype=np.float32).astype(float) for x in (s, yb, ya))
    o = np.argsort(-s); s, yb, ya = s[o], yb[o], ya[o]
    yes_won = official[t]
    # 1. checkpoints: the favorite (side with the higher ask) at each moment
    for c in CHECKPOINTS:
        near = np.flatnonzero(np.abs(s - c) <= MATCH_SEC)
        if not len(near): continue
        i = near[np.argmin(np.abs(s[near] - c))]
        yes = ya[i] >= 100 - yb[i]
        ask = ya[i] if yes else 100 - yb[i]
        if ask < ASKS[0] or ask > ASKS[-1]: continue
        won = yes == yes_won
        chk.append((asset_of[t], c, int(ask), won))
        if not won:
            losses.append({"market": t, "asset": asset_of[t], "where": f"{c}s checkpoint",
                           "sec_left": round(s[i], 1), "fav_side": "YES" if yes else "NO",
                           "ask_paid": ask, "result": "yes" if yes_won else "no"})
    # 2. the trade: first tick with <= TRADE_SEC left where a side's ask is exactly 99c
    for i in np.flatnonzero(s <= TRADE_SEC):
        yes = None
        if ya[i] == 99: yes = True
        elif 100 - yb[i] == 99: yes = False
        if yes is None: continue
        won = yes == yes_won
        trades.append((asset_of[t], round(s[i], 1), won))
        if not won:
            losses.append({"market": t, "asset": asset_of[t], "where": "99c trade",
                           "sec_left": round(s[i], 1), "fav_side": "YES" if yes else "NO",
                           "ask_paid": 99, "result": "yes" if yes_won else "no"})
        break

def table(rows, keys):
    g = rows.groupby(keys)
    out = g.won.agg(cases="count", won="sum").reset_index()
    out["lost"] = out.cases - out.won
    out["loss_rate"] = (out.lost / out.cases).map("{:.2%}".format)
    return out

# %%
C = pd.DataFrame(chk, columns=["asset", "sec_left", "ask_c", "won"])
CT = table(C, ["sec_left", "ask_c"])
CT["breakeven_loss_rate"] = CT.ask_c.map(lambda a: f"{(100 - a - fee_c(a) / LOTS) / 100:.2%}")
CT["profit_per_contract_c"] = [round((w * (100 - a) - (n - w) * a) / n - fee_c(a) / LOTS, 2)
                               for w, n, a in zip(CT.won, CT.cases, CT.ask_c)]
CT["true_loss_rate_could_be_up_to"] = [f"{min(1, 3 / n):.1%}" if l == 0 else "" for n, l in zip(CT.cases, CT.lost)]
CT = CT.sort_values(["sec_left", "ask_c"], ascending=[False, True])
CT.to_csv(OUT_CHECK, index=False)
print("1. FAVORITE AT EACH CHECKPOINT — lost vs. the loss rate it can afford (breakeven_loss_rate)")
display(CT.reset_index(drop=True))

# %%
T = pd.DataFrame(trades, columns=["asset", "sec_left", "won"])
if len(T):
    TT = pd.concat([table(T.assign(all="ALL"), ["all"]).rename(columns={"all": "asset"}),
                    table(T, ["asset"])], ignore_index=True)
    TT["profit_per_contract_c"] = [round((w * 1 - (n - w) * 99) / n - fee_c(99) / LOTS, 2)
                                   for w, n in zip(TT.won, TT.cases)]
    TT.to_csv(OUT_TRADE, index=False)
    print(f"2. THE TRADE — buy the first 99c ask with {TRADE_SEC}s or less left "
          f"(needs to win more than {1 - (1 - fee_c(99) / LOTS) / 100:.2%} to profit)")
    display(TT)
else:
    print("2. no 99c asks found in the last 10 seconds")

# %%
L = pd.DataFrame(losses)
L.to_csv(OUT_LOSSES, index=False)
print(f"3. EVERY LOSS ({len(L)})")
display(L if len(L) else "none")
