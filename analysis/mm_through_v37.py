# Worst-case maker fills on V37 ticks: a resting bid at the best price counts as filled only when the
# price trades through it (bid drops below it). Hold to settlement. Up to MAXF fills per side per market.
import pandas as pd, numpy as np, sys
d = pd.read_pickle(sys.argv[1]).sort_values(["ticker", "ts"])
d = d[d.result.isin(["yes", "no"]) & (d.yes_bid > 0) & (d.yes_ask < 100) & (d.yes_bid < d.yes_ask)]
CR = {"BTC", "ETH", "SOL", "XRP", "DOGE"}
def run(lo, hi, from_s, until_s, maxf=5):
    out = []
    for t, g in d.groupby("ticker"):
        yb = g.yes_bid.values; ya = g.yes_ask.values; s = g.sec_to_close.values; yes = int(g.result.values[0] == "yes")
        for side in ("yes", "no"):
            bid = yb if side == "yes" else 100 - ya; p = None; n = 0
            for i in range(len(bid)):
                if n >= maxf: break
                if p is not None:
                    if bid[i] < p:
                        out.append((t, side, p, s[i], yes if side == "yes" else 1 - yes)); n += 1; p = None
                    elif bid[i] > p: p = None
                if p is None and until_s < s[i] <= from_s and lo <= bid[i] <= hi: p = bid[i]
    f = pd.DataFrame(out, columns=["ticker", "side", "px", "s2c", "win"])
    f["cls"] = np.where(f.ticker.str.extract(r"KX([A-Z]+)15M")[0].isin(CR), "crypto", "commodity")
    f["pnl"] = 100 * f.win - f.px
    f["day"] = f.ticker.str.extract(r"-26OCT(\d\d)")[0]
    return f
rows = []
for nm, lo, hi, a, b in [("fav 85-98 all", 85, 98, 900, 30), ("fav 91-98 all", 91, 98, 900, 30),
                         ("fav 85-98 7-15m", 85, 98, 900, 420), ("fav 85-98 0.5-7m", 85, 98, 420, 30),
                         ("middle 36-64", 36, 64, 900, 30), ("all prices", 1, 99, 900, 30)]:
    f = run(lo, hi, a, b)
    for c, x in f.groupby("cls"):
        per = x.groupby("ticker").pnl.sum(); cnt = x.groupby("ticker").size()
        rng = np.random.default_rng(2); ix = np.arange(len(per))
        bs = [per.values[k].sum() / cnt.values[k].sum() for k in (rng.integers(0, len(ix), len(ix)) for _ in range(300))]
        byday = x.groupby("day").pnl.mean().round(1).to_dict()
        rows.append(dict(test=nm, cls=c, fills=len(x), markets=x.ticker.nunique(), won=f"{100*x.win.mean():.1f}%",
                         avg_px=round(x.px.mean(), 1), c_per_fill=round(x.pnl.mean(), 2),
                         ci90=f"{np.percentile(bs,5):+.2f}..{np.percentile(bs,95):+.2f}", by_day=byday))
pd.set_option("display.width", 300); pd.set_option("display.max_colwidth", 200)
print(pd.DataFrame(rows).to_string(index=False))
