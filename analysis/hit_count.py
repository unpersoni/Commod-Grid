# How many times a side's bid hits a target T (50, 53, ... 95c) in one market. A new hit counts only after the bid
# has dropped at least REARM cents below T. (a) by the final number of hits; (b) buying at the moment of the k-th hit.
import pandas as pd, numpy as np, math, sys
S = sys.argv[1]; REARM = int(sys.argv[2]) if len(sys.argv) > 2 else 3
CR = {"BTC", "ETH", "SOL", "XRP", "DOGE"}
a = pd.read_pickle(S + "/ana/v37ticks_all.pkl")[["ticker", "asset", "yes_bid", "yes_ask", "sec_to_close", "ts", "result"]]
a["period"] = "V37"
b = pd.read_pickle(S + "/mm/v38_ticks.pkl")[["ticker", "asset", "yes_bid", "yes_ask", "sec_to_close", "ts"]]
st = pd.read_csv(S + "/v38mm/commod15min_v38_settlements.csv").drop_duplicates("ticker", keep="last")[["ticker", "result"]]
b = b.merge(st, on="ticker"); b["period"] = "V38"
d = pd.concat([a, b]); d = d[d.result.isin(["yes", "no"]) & (d.yes_bid < d.yes_ask)].sort_values(["ticker", "ts"])
TH = list(range(50, 96, 3))
fee = lambda p: math.ceil(0.07 * 10 * p * (100 - p) / 100 - 1e-9) / 10
final, sig = [], []
for t, g in d.groupby("ticker"):
    s2c = g.sec_to_close.values
    if s2c.max() < 840: continue                                     # need the market from its start
    lb, la = g.yes_bid.values[-1], g.yes_ask.values[-1]
    if s2c.min() > 60 and max(lb, 100 - la) < 95: continue           # data gap (decided markets stop early: kept)
    yes = int(g.result.values[0] == "yes"); cls = "crypto" if g.asset.values[0] in CR else "commodity"; per = g.period.values[0]
    cyc = t.split("-")[1]
    for side in ("yes", "no"):
        bid = g.yes_bid.values if side == "yes" else 100 - g.yes_ask.values
        ask = g.yes_ask.values if side == "yes" else 100 - g.yes_bid.values
        win = yes if side == "yes" else 1 - yes
        for T in TH:
            hits, armed = 0, True
            for i in range(len(bid)):
                if armed and bid[i] >= T:
                    hits += 1; armed = False
                    if s2c[i] > 30: sig.append((t, cyc, cls, per, side, T, hits, win, ask[i], s2c[i]))
                elif not armed and bid[i] <= T - REARM:
                    armed = True
            if hits: final.append((t, cyc, cls, per, side, T, hits, win))
F = pd.DataFrame(final, columns=["ticker", "cyc", "cls", "period", "side", "T", "hits", "win"])
G = pd.DataFrame(sig, columns=["ticker", "cyc", "cls", "period", "side", "T", "k", "win", "ask", "left"])
G["edge"] = 100 * G.win - G.ask - G.ask.map(fee)
F.to_pickle(S + "/mm/hits_final.pkl"); G.to_pickle(S + "/mm/hits_sig.pkl")
F["hb"] = F.hits.clip(upper=5).map(lambda h: "5+" if h >= 5 else str(h))
G["kb"] = G.k.clip(upper=5).map(lambda h: "5+" if h >= 5 else str(h))
pd.set_option("display.width", 250)
print(f"re-arm: the bid must drop {REARM}c below the target before another hit counts | markets: {F.ticker.nunique()}")
for c in ("crypto", "commodity"):
    x = F[F.cls == c]
    print(f"\n===== {c.upper()} (a) by the FINAL number of hits in the market: won% (markets) =====")
    print(x.groupby(["T", "hb"]).apply(lambda y: f"{100*y.win.mean():.0f}% ({len(y)})").unstack("hb").to_string())
    y = G[G.cls == c]
    print(f"\n{c.upper()} (b) buying at the moment of the k-th hit: won% / ask paid / edge after fee  [V37|V38]  (n)")
    gg = y.groupby(["T", "kb"])
    e37 = y[y.period == "V37"].groupby(["T", "kb"]).edge.mean().round(1); e38 = y[y.period == "V38"].groupby(["T", "kb"]).edge.mean().round(1)
    cell = (gg.win.mean().mul(100).round(0).astype(int).astype(str) + "% " + gg.ask.mean().round(0).astype(int).astype(str) + "c "
            + gg.edge.mean().round(1).astype(str) + " [" + e37.astype(str) + "|" + e38.astype(str) + "] (" + gg.size().astype(str) + ")")
    print(cell.unstack("kb").to_string())
