# V38b (open favourite) on crypto: does a coin's opening buy do worse when the other coins' openers lean the other way?
# User's idea (Oct 20): only buy each coin in the direction most of the 5 coins are leaning at the open.
# V37 ticks (Oct 9-14) + V38 ticks (Oct 15-16). At each market's open (first 60 s) every coin "leans" up if its
# YES mid is above 50c, down if below. The V38b buy = the first tick in the first 60 s with spread <= 5c and an ask
# at 56-95c (that side, at the ask, 10 contracts, taker fee), held to settlement.
import pandas as pd, numpy as np, sys
V37, V38T, V38S = sys.argv[1], sys.argv[2], sys.argv[3]
CR = ["BTC", "ETH", "SOL", "XRP", "DOGE"]
a = pd.read_pickle(V37)[["ticker", "asset", "yes_bid", "yes_ask", "sec_to_close", "ts", "result"]]
b = pd.read_pickle(V38T)[["ticker", "asset", "yes_bid", "yes_ask", "sec_to_close", "ts"]]
st = pd.read_csv(V38S).drop_duplicates("ticker", keep="last")[["ticker", "result"]]
b = b.merge(st, on="ticker")
d = pd.concat([a, b]).drop_duplicates(["ticker", "ts"])
d = d[d.asset.isin(CR) & d.result.isin(["yes", "no"]) & (d.sec_to_close.between(840, 905))]
d = d[(d.yes_bid > 0) & (d.yes_ask < 100) & (d.yes_bid < d.yes_ask)].sort_values(["ticker", "ts"])
fee = lambda p: np.ceil(0.07 * 10 * p * (100 - p) / 100 - 1e-9) / 10           # taker fee per contract, 10-lot

rows = []
for t, g in d.groupby("ticker"):
    yb, ya = g.yes_bid.values, g.yes_ask.values
    mid0 = (yb[0] + ya[0]) / 2
    lean = "up" if mid0 > 50 else ("down" if mid0 < 50 else None)
    trade = None
    for i in range(len(g)):
        if ya[i] - yb[i] > 5: continue
        if 56 <= ya[i] <= 95: trade = ("up", int(ya[i]), int(100 - yb[i])); break
        if 56 <= 100 - yb[i] <= 95: trade = ("down", int(100 - yb[i]), int(ya[i])); break
    rows.append(dict(ticker=t, asset=g.asset.values[0], win=t.split("-")[1], lean=lean, mid0=mid0,
                     up=int(g.result.values[0] == "yes"),
                     side=trade[0] if trade else None, px=trade[1] if trade else np.nan,
                     other_px=trade[2] if trade else np.nan))      # other_px: ask of the opposite side at that moment
m = pd.DataFrame(rows)
full = m.groupby("win").asset.nunique()
m = m[m.win.isin(full[full == 5].index) & m.lean.notna()]
m = m[m.win.isin(m.groupby("win").size()[lambda x: x == 5].index)]
nwin = m.win.nunique()
m["n_up"] = m.groupby("win").lean.transform(lambda x: (x == "up").sum())
tr = m[m.side.notna()].copy()
tr["others_same"] = np.where(tr.side == "up", tr.n_up - (tr.lean == "up"), (5 - tr.n_up) - (tr.lean == "down"))
tr["won"] = np.where(tr.side == "up", tr.up, 1 - tr.up)
tr["pnl"] = 100 * tr.won - tr.px - fee(tr.px)

def show(x, label):
    if not len(x): print(f"   {label:52} -"); return
    print(f"   {label:52} {len(x):4} trades  won {100 * x.won.mean():5.1f}%  avg buy {x.px.mean():5.1f}c  "
          f"net {x.pnl.mean():+6.2f}c/contract  (${x.pnl.sum() * 10 / 100:+8.2f} at 10 contracts)")

print(f"windows with all 5 coins' openers: {nwin}; V38b crypto buys in them: {len(tr)}")
lean_split = m.groupby("win").n_up.first().map(lambda k: f"{max(k, 5 - k)}-{min(k, 5 - k)}")
print("how the 5 coins lean at the open: " + ", ".join(f"{k} {100 * (lean_split == k).mean():.0f}%" for k in ("5-0", "4-1", "3-2")))
print("\nV38b buys by how many of the OTHER 4 coins lean the same way at the open:")
for k in range(4, -1, -1): show(tr[tr.others_same == k], f"{k} of 4 others agree")
show(tr, "all V38b crypto buys (now)")
print("\nthe rule 'only buy in the majority direction of the 5':")
maj = tr[tr.others_same >= 2]; mino = tr[tr.others_same <= 1]
show(maj, "kept: buys with the majority (2+ of 4 others agree)")
show(mino, "skipped: buys against the majority (0-1 agree)")
# flipping the minority instead: buy the majority side (the coin's underdog) at its ask
fl = mino.copy()
fl["px"] = fl.other_px; fl["won"] = 1 - fl.won
fl["pnl"] = 100 * fl.won - fl.px - fee(fl.px)
show(fl, "flipped: minority coins bought the majority way instead")
print("\nsplit by the price paid:")
for lo, hi in [(56, 65), (66, 80), (81, 95)]:
    x = tr[tr.px.between(lo, hi)]
    show(x[x.others_same >= 2], f"{lo}-{hi}c with the majority")
    show(x[x.others_same <= 1], f"{lo}-{hi}c against the majority")
tr["day"] = tr.win.str[2:7]
print("\nby day, buys against the majority (0-1 of 4 agree): won % / net c per contract")
for day, x in tr[tr.others_same <= 1].groupby("day"):
    print(f"   {day}: {len(x):3} trades  won {100 * x.won.mean():5.1f}%  net {x.pnl.mean():+6.2f}c")
print("\nby day: buys when all 5 coins lean the same way vs the rest (net c per contract, trades)")
for day, x in tr.groupby("day"):
    a5, rest = x[x.others_same == 4], x[x.others_same < 4]
    print(f"   {day}: all 5 agree {a5.pnl.mean():+6.2f}c ({len(a5):3})   the rest {rest.pnl.mean():+6.2f}c ({len(rest):3})")
w = tr.assign(split=tr.n_up.map(lambda k: f"{max(k, 5 - k)}-{min(k, 5 - k)}"))
print("\nby how the 5 coins split at the open:")
for k in ("5-0", "4-1", "3-2"): show(w[w.split == k], f"window split {k}")
print("\nby day: buys in 3-2 split windows vs 5-0 and 4-1 windows (net c per contract, trades)")
for day, x in w.groupby("day"):
    s32, oth = x[x.split == "3-2"], x[x.split != "3-2"]
    print(f"   {day}: 3-2 split {s32.pnl.mean():+6.2f}c ({len(s32):3})   5-0 / 4-1 {oth.pnl.mean():+6.2f}c ({len(oth):3})")
