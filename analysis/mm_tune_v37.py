# Out-of-sample check of the mm_tune.py candidates on V37 (Oct 9-14, 2,419 markets, days the grid never saw),
# with the worst-case fill rule of mm_through_v37.py: a resting bid fills only when the price trades through it.
# Also re-runs the candidates (and 70-84c variants that do not overlap the 85-98c favourite) on V38 with the queue model.
import pandas as pd, numpy as np, sys, importlib.util
V37, V38 = sys.argv[1], sys.argv[2]
d = pd.read_pickle(V37).sort_values(["ticker", "ts"])
d = d[d.result.isin(["yes", "no"]) & (d.yes_bid > 0) & (d.yes_ask < 100) & (d.yes_bid < d.yes_ask)]
CR = {"BTC", "ETH", "SOL", "XRP", "DOGE"}
G = [(t, g.yes_bid.values, g.yes_ask.values, g.sec_to_close.values, int(g.result.values[0] == "yes"))
     for t, g in d.groupby("ticker")]

def through(lo, hi, from_s, until_s, maxf=5):
    out = []
    for t, yb, ya, s, yes in G:
        for side in ("yes", "no"):
            bid = yb if side == "yes" else 100 - ya; p = None; n = 0
            for i in range(len(bid)):
                if n >= maxf: break
                if p is not None:
                    if bid[i] < p:
                        out.append((t, p, yes if side == "yes" else 1 - yes)); n += 1; p = None
                    elif bid[i] > p: p = None
                if p is None and until_s < s[i] <= from_s and lo <= bid[i] <= hi: p = bid[i]
    f = pd.DataFrame(out, columns=["ticker", "px", "win"])
    f["cls"] = np.where(f.ticker.str.extract(r"KX([A-Z]+)15M")[0].isin(CR), "crypto", "commodity")
    f["pnl"] = 100 * f.win - f.px
    f["day"] = f.ticker.str.extract(r"-26OCT(\d\d)")[0]
    return f

CAND = {"crypto": [("now: 3-97c whole market", 3, 97, 900, 30), ("70-90c first 5 min", 70, 90, 900, 600),
                   ("75-90c first 5 min", 75, 90, 900, 600), ("70-84c first 5 min", 70, 84, 900, 600),
                   ("60-84c first 5 min", 60, 84, 900, 600), ("70-90c first 10 min", 70, 90, 900, 300),
                   ("fav 85-98c (reference)", 85, 98, 900, 30)],
        "commodity": [("now: 3-97c whole market", 3, 97, 900, 30), ("75-90c whole market", 75, 90, 900, 30),
                      ("70-90c whole market", 70, 90, 900, 30), ("70-84c whole market", 70, 84, 900, 30),
                      ("75-84c whole market", 75, 84, 900, 30), ("60-84c whole market", 60, 84, 900, 30),
                      ("3-97c after minute 5", 3, 97, 600, 30), ("3-40c whole market", 3, 40, 900, 30),
                      ("fav 85-98c (reference)", 85, 98, 900, 30)]}
rows = []
for c, cands in CAND.items():
    for nm, lo, hi, a, b in cands:
        x = through(lo, hi, a, b); x = x[x.cls == c]
        per = x.groupby("ticker").pnl.sum(); cnt = x.groupby("ticker").size()
        rng = np.random.default_rng(2)
        bs = [per.values[k].sum() / cnt.values[k].sum() for k in (rng.integers(0, len(per), len(per)) for _ in range(300))]
        days = x.groupby("day").pnl.mean()
        rows.append(dict(cls=c, test=nm, fills=len(x), mkts=x.ticker.nunique(), won=round(100 * x.win.mean(), 1),
                         avg_px=round(x.px.mean(), 1), cpc_v37=round(x.pnl.mean(), 2),
                         ci90=f"{np.percentile(bs, 5):+.2f}..{np.percentile(bs, 95):+.2f}",
                         days_up=f"{(days > 0).sum()}/{len(days)}", by_day=" ".join(f"{v:+.1f}" for v in days.values)))
r = pd.DataFrame(rows)

spec = importlib.util.spec_from_file_location("mt", __file__.replace("mm_tune_v37.py", "mm_tune.py"))
sys.argv = [sys.argv[0], V38]; mt = importlib.util.module_from_spec(spec); spec.loader.exec_module(mt)
v38 = []
for c, cands in CAND.items():
    for nm, lo, hi, a, b in cands:
        s = [z for z in mt.summ(mt.run(lo, hi, a, b, "trades"), nm) if z["cls"] == c]
        if s: v38.append(dict(cls=c, test=nm, cpc_v38=s[0]["cpc"], v38_halves=f"{s[0]['half1']:+.2f} / {s[0]['half2']:+.2f}",
                              v38_won=s[0]["won"], v38_usd_per_mkt=s[0]["usd_per_mkt"]))
r = r.merge(pd.DataFrame(v38), on=["cls", "test"], how="left")
pd.set_option("display.width", 300); pd.set_option("display.max_colwidth", 80)
print(r.to_string(index=False))
r.to_pickle(V38 + "/mm_tune_v37.pkl")
