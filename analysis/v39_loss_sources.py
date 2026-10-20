# V39 (= V38b) losses: does a market or a price band stand out, overall and in the rounds that lost money?
# Same replay as v39_stops.py (V37 + V38 ticks, Oct 9-16, all 11 markets, 10 contracts at the ask, taker fee).
import pandas as pd, numpy as np, sys
V37, V38T, V38S = sys.argv[1], sys.argv[2], sys.argv[3]
CR = ["BTC", "ETH", "SOL", "XRP", "DOGE"]; CM = ["GOLD", "SILVER", "WTI", "COPPER", "PLATINUM", "PALLADIUM"]
a = pd.read_pickle(V37)[["ticker", "asset", "yes_bid", "yes_ask", "sec_to_close", "ts", "result"]]
b = pd.read_pickle(V38T)[["ticker", "asset", "yes_bid", "yes_ask", "sec_to_close", "ts"]]
b = b.merge(pd.read_csv(V38S).drop_duplicates("ticker", keep="last")[["ticker", "result"]], on="ticker")
d = pd.concat([a, b]).drop_duplicates(["ticker", "ts"])
d = d[d.result.isin(["yes", "no"]) & d.sec_to_close.between(840, 905)]
d = d[(d.yes_bid > 0) & (d.yes_ask < 100) & (d.yes_bid < d.yes_ask) & (d.yes_ask - d.yes_bid <= 5)].sort_values(["ticker", "ts"])
fee = lambda p: np.ceil(0.07 * 10 * p * (100 - p) / 100 - 1e-9)
rows = []
for t, g in d.groupby("ticker"):
    for yb, ya, ts, res in zip(g.yes_bid.values, g.yes_ask.values, g.ts.values, g.result.values):
        side = "yes" if 56 <= ya <= 95 else ("no" if 56 <= 100 - yb <= 95 else None)
        if side:
            px = int(ya if side == "yes" else 100 - yb); won = res == side
            rows.append(dict(ticker=t, asset=g.asset.values[0], ts=ts, px=px, won=int(won), net=10 * (100 * won - px) - fee(px)))
            break
T = pd.DataFrame(rows)
T["cls"] = np.where(T.asset.isin(CR), "crypto", "commodity")
T["win"] = T.ticker.str.split("-").str[1]
T["band"] = pd.cut(T.px, [55, 65, 70, 80, 95], labels=["56-65c", "66-70c", "71-80c", "81-95c"])
T["half"] = np.where(T.ts < T.ts.sort_values().iloc[len(T) // 2], "1st", "2nd")
T["c"] = T.net / 10                                                   # cents per contract

def line(x, label):
    be = x.px.mean() + (x.net / 10 - (100 * x.won - x.px)).mean() * -1   # break-even win rate incl. fee, roughly avg price + fee
    h = x.groupby("half").c.mean()
    return (f"  {label:22} {len(x):5} buys  won {100 * x.won.mean():5.1f}%  avg buy {x.px.mean():5.1f}c  "
            f"(break-even ~{be:4.1f}%)  net {x.c.mean():+6.2f}c/contract  ${x.net.sum() / 100:+8.2f}   "
            f"halves {h.get('1st', np.nan):+6.2f} / {h.get('2nd', np.nan):+6.2f}")

print(f"{len(T)} buys, {T.win.nunique()} rounds. Break-even win rate = average price paid + fee.")
print("\nBY MARKET:")
for a_ in CR + CM: print(line(T[T.asset == a_], a_))
print("\nBY PRICE BAND (all markets / crypto / commodities):")
for bd in T.band.cat.categories:
    x = T[T.band == bd]
    print(line(x, f"{bd} all")); print(line(x[x.cls == "crypto"], f"{bd} crypto")); print(line(x[x.cls == "commodity"], f"{bd} commodity"))
print("\nMARKET x BAND, net c/contract (buys):")
tab = T.pivot_table(index="asset", columns="band", values="c", aggfunc="mean", observed=False).reindex(CR + CM)
cnt = T.pivot_table(index="asset", columns="band", values="c", aggfunc="size", observed=False).reindex(CR + CM)
print("  " + " " * 10 + "".join(f"{c_:>18}" for c_ in tab.columns))
for a_ in tab.index:
    print(f"  {a_:10}" + "".join(f"{tab.loc[a_, c_]:+9.2f} ({int(cnt.loc[a_, c_]):4})" if cnt.loc[a_, c_] else f"{'-':>18}" for c_ in tab.columns))

# rounds (one 15-minute window, all markets) that lost money in total
rn = T.groupby("win").net.sum()
lose = rn[rn < 0].index
L = T[T.win.isin(lose)]
print(f"\nLOSING ROUNDS: {len(lose)} of {len(rn)} rounds ({100 * len(lose) / len(rn):.0f}%) lost money; they lost ${rn[rn < 0].sum() / 100:,.2f} in total.")
print("  who lost in them: share of all losing buys in losing rounds vs share of all buys, and how often each market lost there")
tot_loss = -L[L.net < 0].net.sum()
for a_ in CR + CM:
    x = L[L.asset == a_]
    share_loss = -x[x.net < 0].net.sum() / tot_loss
    share_buys = (T.asset == a_).mean()
    print(f"  {a_:10} share of losses {100 * share_loss:5.1f}%  (share of all buys {100 * share_buys:4.1f}%)  "
          f"lost in {100 * (1 - x.won.mean()):5.1f}% of its losing-round buys  (overall {100 * (1 - T[T.asset == a_].won.mean()):4.1f}%)")
print("  by band in losing rounds:")
for bd in T.band.cat.categories:
    x = L[L.band == bd]
    print(f"  {bd:8} share of losses {100 * -x[x.net < 0].net.sum() / tot_loss:5.1f}%  (share of all buys {100 * (T.band == bd).mean():4.1f}%)  "
          f"lost {100 * (1 - x.won.mean()):5.1f}% there (overall {100 * (1 - T[T.band == bd].won.mean()):4.1f}%)")
# how the losses split inside a losing round: everyone, or a few?
k = L.groupby("win").apply(lambda x: (x.net < 0).sum() / len(x), include_groups=False)
print(f"  in a losing round, the share of its buys that lost: median {100 * k.median():.0f}%  "
      f"(25-75%: {100 * k.quantile(.25):.0f}-{100 * k.quantile(.75):.0f}%)")
cl = T.groupby(["win", "cls"]).net.sum().unstack()
print(f"  losing rounds where crypto lost: {100 * (cl.loc[lose, 'crypto'] < 0).mean():.0f}%, commodities lost: "
      f"{100 * (cl.loc[lose, 'commodity'] < 0).mean():.0f}%, both: {100 * ((cl.loc[lose, 'crypto'] < 0) & (cl.loc[lose, 'commodity'] < 0)).mean():.0f}%")
