# Aggregate maker P&L: every taker trade has a resting maker on the other side.
# Maker bought YES at the bid when a NO-taker hit it; maker bought NO at 100-ask when a YES-taker lifted the ask.
import pandas as pd, numpy as np, sys
m = pd.read_pickle(sys.argv[1] + "/v38_flow.pkl")
# one row per (bucket, maker side)
a = pd.DataFrame({"ticker": m.ticker, "cls": m.cls, "asset": m.asset, "bucket": m.bucket, "s2c": m.s2c,
                  "side": "yes", "px": m.bid, "ct": m.ct_no_taker, "n": m.n_no_taker, "win": m.yes, "spr": m.ask - m.bid})
b = pd.DataFrame({"ticker": m.ticker, "cls": m.cls, "asset": m.asset, "bucket": m.bucket, "s2c": m.s2c,
                  "side": "no", "px": 100 - m.ask, "ct": m.ct_yes_taker, "n": m.n_yes_taker, "win": 1 - m.yes, "spr": m.ask - m.bid})
t = pd.concat([a, b]); t = t[t.ct > 0]
t["edge"] = 100 * t.win - t.px                       # cents per contract, before any maker fee
t["mfee"] = 0.0175 * t.px * (100 - t.px) / 100       # Kalshi maker fee formula, if the series has one
t["late"] = t.bucket >= pd.Timestamp("2026-10-16 12:00", tz="UTC")
t.to_pickle(sys.argv[1] + "/maker_rows.pkl")
def summ(g):
    w = g.ct
    per_mkt = g.assign(p=g.edge * g.ct).groupby("ticker").p.sum()
    # bootstrap across markets (outcomes are shared within a market)
    rng = np.random.default_rng(0); tick = per_mkt.index.values
    ctm = g.groupby("ticker").ct.sum().reindex(tick).values; pm = per_mkt.values
    bs = [pm[i].sum() / ctm[i].sum() for i in (rng.integers(0, len(tick), len(tick)) for _ in range(300))]
    return pd.Series({"contracts": int(w.sum()), "markets": g.ticker.nunique(), "avg_px": round((g.px * w).sum() / w.sum(), 1),
        "won%": round(100 * (g.win * w).sum() / w.sum(), 1), "maker_edge_c": round((g.edge * w).sum() / w.sum(), 2),
        "ci90": f"{np.percentile(bs,5):+.2f}..{np.percentile(bs,95):+.2f}",
        "after_maker_fee": round(((g.edge - g.mfee) * w).sum() / w.sum(), 2),
        "first_half": round((g[~g.late].edge * g[~g.late].ct).sum() / max(1, g[~g.late].ct.sum()), 2),
        "second_half": round((g[g.late].edge * g[g.late].ct).sum() / max(1, g[g.late].ct.sum()), 2)})
pd.set_option("display.width", 250)
print("ALL MAKERS, volume-weighted (cents per contract the resting side earned at settlement)")
print(t.groupby("cls").apply(summ).to_string())
t["zone"] = pd.cut(t.px, [0, 10, 20, 35, 50, 65, 80, 90, 100], labels=["1-10", "11-20", "21-35", "36-50", "51-65", "66-80", "81-90", "91-99"])
t["tleft"] = pd.cut(t.s2c, [0, 60, 180, 420, 660, 900], labels=["0-1m", "1-3m", "3-7m", "7-11m", "11-15m"])
for c in ["crypto", "commodity"]:
    x = t[t.cls == c]
    print(f"\n{c.upper()} by maker's buy price"); print(x.groupby("zone", observed=True).apply(summ).to_string())
    print(f"\n{c.upper()} by time left"); print(x.groupby("tleft", observed=True).apply(summ).to_string())
    print(f"\n{c.upper()} by spread at the time"); print(x.groupby(x.spr.round().clip(upper=4)).apply(summ).to_string())
    print(f"\n{c.upper()} by asset"); print(x.groupby("asset").apply(summ).to_string())
