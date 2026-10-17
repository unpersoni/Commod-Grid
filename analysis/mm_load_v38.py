# Join V38 ticks (bid/ask each second) with 15s trade-flow buckets and settlement results.
import pandas as pd, numpy as np, glob, sys
D = sys.argv[1]; OUT = sys.argv[2]
CR = {"BTC","ETH","SOL","XRP","DOGE"}
tk = pd.concat([pd.read_csv(f, usecols=["ts_iso","ticker","asset","yes_bid","yes_ask","sec_to_close","spot_px","yes_bid_qty","no_bid_qty"])
                for f in sorted(glob.glob(D + "/ticks_*.csv*"))])
tk["ts"] = pd.to_datetime(tk.ts_iso, format="ISO8601", utc=True)
tk = tk[(tk.sec_to_close > 0) & (tk.yes_bid > 0) & (tk.yes_ask < 100) & (tk.yes_bid < tk.yes_ask)]
tk["bucket"] = tk.ts.dt.floor("15s")
fl = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(D + "/flow_*.csv*"))])
fl["bucket"] = pd.to_datetime(fl.bucket_iso, format="ISO8601", utc=True)
fl = fl.drop_duplicates(["bucket", "ticker"], keep="last")
agg = tk.groupby(["ticker", "bucket"]).agg(bid=("yes_bid", "mean"), ask=("yes_ask", "mean"),
        bid0=("yes_bid", "first"), ask0=("yes_ask", "first"), bid1=("yes_bid", "last"), ask1=("yes_ask", "last"),
        bidmin=("yes_bid", "min"), askmax=("yes_ask", "max"), n=("yes_bid", "size")).reset_index()
m = fl.merge(agg, on=["ticker", "bucket"])
st = pd.read_csv(D + "/commod15min_v38_settlements.csv").drop_duplicates("ticker", keep="last")
m = m.merge(st[["ticker", "result"]], on="ticker")
m = m[m.result.isin(["yes", "no"])]
m["yes"] = (m.result == "yes").astype(int)
m["cls"] = np.where(m.asset.isin(CR), "crypto", "commodity")
m["s2c"] = m.sec_to_close_end + 15
m.to_pickle(OUT + "/v38_flow.pkl")
tk.to_pickle(OUT + "/v38_ticks.pkl")
print("buckets", len(m), "markets", m.ticker.nunique(), m.groupby("cls").ticker.nunique().to_dict())
x = m[(m.ct_yes_taker + m.ct_no_taker) > 0]
imp = (x.ct_yes_taker * x.ask + x.ct_no_taker * x.bid) / (x.ct_yes_taker + x.ct_no_taker)
print("avg_yes_px vs implied (ask for yes-takers, bid for no-takers): mean diff", round((x.avg_yes_px - imp).mean(), 2),
      "median abs", round((x.avg_yes_px - imp).abs().median(), 2))
print("contracts per market by class (median):", m.groupby(["cls", "ticker"])[["ct_yes_taker", "ct_no_taker"]].sum().sum(axis=1).groupby("cls").median().to_dict())
