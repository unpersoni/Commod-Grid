# First side to take the lead (bid >= 51c) -> buy it; if its bid falls to 20c, sell and buy the other side (reversal);
# else hold to settlement. Entry: taker (buy at the ask, fee) or a resting bid at the lead's bid (filled only if a seller
# comes down to it: bid falls below it or the ask comes down to it). 10 contracts, Kalshi taker fee on taker orders.
import pandas as pd, numpy as np, math, sys
S = sys.argv[1]
CR = {"BTC", "ETH", "SOL", "XRP", "DOGE"}
fee = lambda n, p: math.ceil(0.07 * n * p * (100 - p) / 100 - 1e-9)
a = pd.read_pickle(S + "/ana/v37ticks_all.pkl")[["ticker", "asset", "yes_bid", "yes_ask", "sec_to_close", "ts", "result"]]; a["period"] = "V37"
b = pd.read_pickle(S + "/mm/v38_ticks.pkl")[["ticker", "asset", "yes_bid", "yes_ask", "sec_to_close", "ts"]]
st = pd.read_csv(S + "/v38mm/commod15min_v38_settlements.csv").drop_duplicates("ticker", keep="last")[["ticker", "result"]]
b = b.merge(st, on="ticker"); b["period"] = "V38"
d = pd.concat([a, b]); d = d[d.result.isin(["yes", "no"]) & (d.yes_bid < d.yes_ask)].sort_values(["ticker", "ts"])
N = 10; STOP = 20; LEAD = 51; MINLEFT = 30
rows = []
for t, g in d.groupby("ticker"):
    s2c = g.sec_to_close.values
    if s2c.max() < 840: continue
    lb, la = g.yes_bid.values[-1], g.yes_ask.values[-1]
    if s2c.min() > 60 and max(lb, 100 - la) < 95: continue
    yb, ya = g.yes_bid.values, g.yes_ask.values; yes = int(g.result.values[0] == "yes")
    cls = "crypto" if g.asset.values[0] in CR else "commodity"
    lead = [i for i in range(len(yb)) if s2c[i] > MINLEFT and (yb[i] >= LEAD or 100 - ya[i] >= LEAD)]
    if not lead: continue
    i0 = lead[0]; side = "yes" if yb[i0] >= LEAD else "no"
    bid = yb if side == "yes" else 100 - ya; ask = ya if side == "yes" else 100 - yb
    win = yes if side == "yes" else 1 - yes
    for entry in ("taker", "resting bid"):
        if entry == "taker":
            j, px, efee = i0, ask[i0], fee(N, ask[i0])
        else:
            p = bid[i0]; j = None
            for k in range(i0 + 1, len(bid)):
                if s2c[k] <= MINLEFT: break
                if bid[k] < p or ask[k] <= p: j = k; break
            if j is None:
                rows.append((t, cls, g.period.values[0], entry, side, "not filled", np.nan, 0, 0, 0)); continue
            px, efee = p, 0
        hold = 100 * win * N - px * N - efee              # hold to settlement
        out = hold; how = "held"
        for k in range(j + 1, len(bid)):
            if s2c[k] <= MINLEFT: break
            if bid[k] <= STOP:                            # sell at the bid, buy the other side at its ask
                sp = bid[k]; rp = 100 - bid[k] if False else (100 - yb[k] if side == "yes" else ya[k])
                out = (sp - px) * N - efee - fee(N, sp) + (100 * (1 - win) - rp) * N - fee(N, rp)
                stop_only = (sp - px) * N - efee - fee(N, sp)
                how = "reversed"; break
        rows.append((t, cls, g.period.values[0], entry, side, how, px, hold, out, stop_only if how == "reversed" else hold))
r = pd.DataFrame(rows, columns=["ticker", "cls", "period", "entry", "side", "how", "px", "hold", "plan", "stop_only"])
r.to_pickle(S + "/mm/first_lead.pkl")
pd.set_option("display.width", 250)
for (c, e), x in r.groupby(["cls", "entry"]):
    f = x[x.how != "not filled"]
    rev = f[f.how == "reversed"]
    print(f"\n{c.upper()} — entry: {e}   markets {len(x)}, filled {len(f)}" + (f" (missed {len(x)-len(f)}: their lead side won {100*0:.0f}%)" if False else ""))
    print(f"  avg buy price {f.px.mean():.1f}c | lead side won {100*(f.hold > 0).mean():.1f}% | dipped to {STOP}c and reversed: {len(rev)} ({100*len(rev)/len(f):.1f}%), "
          f"reversal won {100*(rev.plan > rev.stop_only).mean():.0f}%")
    for col, nm in (("hold", "hold everything"), ("stop_only", f"sell at {STOP}c, no reversal"), ("plan", f"sell at {STOP}c + reversal (your plan)")):
        per = f.groupby("period")[col].mean() / N
        print(f"  {nm:32}: {f[col].mean()/N:+6.2f}c per contract   ${f[col].sum()/100:+8.2f} total   [V37 {per.get('V37', np.nan):+.2f} | V38 {per.get('V38', np.nan):+.2f}]")
missed = r[(r.entry == "resting bid") & (r.how == "not filled")].merge(r[r.entry == "taker"][["ticker", "hold"]], on="ticker", suffixes=("", "_t"))
print("\nresting bid NOT filled: the lead side then won", f"{100*(missed.hold_t > 0).mean():.0f}%", "of", len(missed), "markets")
