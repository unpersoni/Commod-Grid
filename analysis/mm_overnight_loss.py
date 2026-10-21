# MM15Min V4 overnight loss (user's report, Oct 21: about -$800 overnight). Inputs: the bot's Drive files
# mm15min_v4_markets.csv, _fills.csv, _settlements.csv and mm15min_v4_data/ticks_2026-10-21_01..05.csv.
# usage: python3 mm_overnight_loss.py <dir with those files, ticks as ticks_01.csv .. ticks_05.csv>
import pandas as pd, numpy as np, sys
D = sys.argv[1]
CR = {"BTC", "ETH", "SOL", "XRP", "DOGE"}
m = pd.read_csv(f"{D}/v4_markets.csv"); f = pd.read_csv(f"{D}/v4_fills.csv")
m["ct"] = m.yes_ct.fillna(0) + m.no_ct.fillna(0)
g = m.groupby(["strategy", "class"]).agg(markets=("pnl_c", "size"), contracts=("ct", "sum"), net=("pnl_c", "sum"))
g["net$"] = g.net / 100; g["c/ct"] = g.net / g.contracts
print(g.round(2).to_string(), "\nTOTAL $", round(m.pnl_c.sum() / 100, 2))
# rounds: the 5 coins as one bet
f["round"] = f.window.str.extract(r"-(\d\d[A-Z]{3}\d{6})")[0]
c = f[f["class"] == "crypto"].groupby("round").pnl_c.sum() / 100
print(f"crypto rounds {len(c)}: {int((c > 0).sum())} up, avg win ${c[c > 0].mean():.2f}, avg loss ${c[c < 0].mean():.2f}, "
      f"5 worst ${c.nsmallest(5).sum():.2f}")
# per-market contract caps (does buying more into a falling favourite cause it?)
f["t"] = pd.to_datetime(f.fill_iso, format="ISO8601"); f = f.sort_values("t")
for cap in (10, 20):
    x = f.copy(); x["cum"] = x.groupby(["window", "strategy"]).contracts.cumsum()
    x = x[x.cum - x.contracts < cap].copy(); x["n"] = np.minimum(x.contracts, cap - (x.cum - x.contracts))
    p = x.n * (100 * x.won - x.px)
    print(f"cap {cap} contracts per market and strategy: c per contract", (p.groupby([x["class"], x.strategy]).sum()
          / x.n.groupby([x["class"], x.strategy]).sum()).round(2).to_dict())
# every crypto market whose favourite reached the band in the first 5 min, filled or not (01:15-06:00 UTC)
st = pd.read_csv(f"{D}/v4_settlements.csv").drop_duplicates("ticker", keep="last").set_index("ticker").result
t = pd.concat([pd.read_csv(f"{D}/ticks_{h:02d}.csv") for h in range(1, 6)])
t = t[t.asset.isin(CR) & t.ticker.isin(st.index)]
first = t.groupby("ticker").sec_to_close.max(); ok = first[first >= 840].index
t = t[t.ticker.isin(ok) & (t.sec_to_close >= 600) & (t.yes_bid > 0) & (t.yes_ask < 100)]
rows = []
for tk, g_ in t.groupby("ticker"):
    for strat, lo, hi in (("fav", 85, 98), ("lean", 75, 84)):
        for side in ("YES", "NO"):
            bid = g_.yes_bid if side == "YES" else 100 - g_.yes_ask
            hit = bid[(bid >= lo) & (bid <= hi)]
            if len(hit):
                rows.append(dict(strat=strat, first_bid=hit.iloc[0], won=(st[tk] == "yes") == (side == "YES"),
                                 filled=((f.window == tk) & (f.strategy == strat) & (f.side == side)).any()))
x = pd.DataFrame(rows)
print(f"crypto markets seen from the start: {len(ok)}")
print(x.groupby(["strat", "filled"]).agg(n=("won", "size"), won=("won", "mean"), first_bid=("first_bid", "mean")).round(3).to_string())
