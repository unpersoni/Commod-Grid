# "Stays above": the first time a side's bid reaches T (50, 53, ... 98c), how long does it stay >= T without
# dipping below? If it stays D seconds, what is the win rate, and is that better than the price you'd pay then?
import pandas as pd, numpy as np, math, sys
S = sys.argv[1]
CR = {"BTC", "ETH", "SOL", "XRP", "DOGE"}
a = pd.read_pickle(S + "/ana/v37ticks_all.pkl")[["ticker", "asset", "yes_bid", "yes_ask", "sec_to_close", "ts", "result"]]
a["period"] = "V37 Oct9-14"
b = pd.read_pickle(S + "/mm/v38_ticks.pkl")[["ticker", "asset", "yes_bid", "yes_ask", "sec_to_close", "ts"]]
st = pd.read_csv(S + "/v38mm/commod15min_v38_settlements.csv").drop_duplicates("ticker", keep="last")[["ticker", "result"]]
b = b.merge(st, on="ticker"); b["period"] = "V38 Oct15-16"
d = pd.concat([a, b]); d = d[d.result.isin(["yes", "no"]) & (d.yes_bid >= 0) & (d.yes_ask <= 100) & (d.yes_bid < d.yes_ask)]
d["sec"] = (d.ts - pd.Timestamp("2026-10-01", tz="UTC")).dt.total_seconds()
d = d.sort_values(["ticker", "sec"])
TH = list(range(50, 99, 3)); DW = [0, 10, 30, 60, 90, 120, 180, 300]
fee = lambda p: math.ceil(0.07 * 10 * p * (100 - p) / 100 - 1e-9) / 10
rows = []; used = 0
for t, g in d.groupby("ticker"):
    s2c = g.sec_to_close.values
    if s2c.max() < 840: continue                               # need the market from its start
    lb, la = g.yes_bid.values[-1], g.yes_ask.values[-1]
    if s2c.min() > 60 and max(lb, 100 - la) < 95: continue     # data stops early without the market being decided (a gap)
    # (prices are only logged between 1c and 99c, so a decided market's data stops early: keep those)
    used += 1
    sec = g.sec.values; close = sec[-1] + s2c[-1]; yes = int(g.result.values[0] == "yes")
    cls = "crypto" if g.asset.values[0] in CR else "commodity"; per = g.period.values[0]
    for side in ("yes", "no"):
        bid = g.yes_bid.values if side == "yes" else 100 - g.yes_ask.values
        ask = g.yes_ask.values if side == "yes" else 100 - g.yes_bid.values
        win = yes if side == "yes" else 1 - yes
        for T in TH:
            ok = np.nonzero((bid >= T) & (s2c > 30))[0]
            if not len(ok): continue
            i = ok[0]
            below = np.nonzero(bid[i:] < T)[0]
            end = sec[i + below[0]] if len(below) else close
            L = end - sec[i]
            for D in DW:
                ts = sec[i] + D
                if L < D or close - ts <= 30: continue
                k = np.searchsorted(sec, ts, side="right") - 1
                rows.append((t, cls, per, side, T, D, win, ask[k], bid[k], close - ts, L))
r = pd.DataFrame(rows, columns=["ticker", "cls", "period", "side", "T", "D", "win", "ask", "bid", "left", "L"])
r["edge"] = 100 * r.win - r.ask - r.ask.map(fee)         # taker: buy at the ask, Kalshi fee
r["edge_maker"] = 100 * r.win - r.bid                       # if bought at the bid instead
r.to_pickle(S + "/mm/dwell.pkl")
print("markets used:", used, "| signals:", len(r))
pd.set_option("display.width", 250)
for c in ("crypto", "commodity"):
    x = r[r.cls == c]
    g = x.groupby(["T", "D"])
    print(f"\n===== {c.upper()}: win% (count) when the bid first reaches T and stays >= T for D seconds =====")
    print(g.apply(lambda y: f"{100*y.win.mean():.0f}% ({len(y)})").unstack("D").to_string())
    print(f"\n{c.upper()}: average ask at that moment (what a taker pays)")
    print(g.ask.mean().round(1).unstack("D").to_string())
    print(f"\n{c.upper()}: edge = win% - ask - taker fee (c per contract); both periods shown as V37 / V38")
    e = g.edge.mean().round(1).unstack("D")
    e37 = x[x.period.str.startswith("V37")].groupby(["T", "D"]).edge.mean().round(1).unstack("D")
    e38 = x[x.period.str.startswith("V38")].groupby(["T", "D"]).edge.mean().round(1).unstack("D")
    print((e.astype(str) + "  [" + e37.astype(str) + " / " + e38.astype(str) + "]").to_string())
