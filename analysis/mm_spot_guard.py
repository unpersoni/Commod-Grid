# Crypto worst-case maker fills (V37), with a spot "pull the bid" guard: if Coinbase moved against our side
# over the last LB seconds by more than K normal moves (seen one second earlier), cancel and stay out.
import pandas as pd, numpy as np, sys
d = pd.read_pickle(sys.argv[1]).sort_values(["ticker", "ts"])
d = d[(d.yes_bid > 0) & (d.yes_ask < 100) & (d.yes_bid < d.yes_ask)]
def run(lo, hi, from_s, until_s, K, LB, maxf=5):
    out = []
    for t, g in d.groupby("ticker"):
        yb = g.yes_bid.values; ya = g.yes_ask.values; s = g.sec_to_close.values; yes = int(g.yes_win.values[0])
        lsp = np.log(g.spot_px.values); sd = np.sqrt(g.var_s.values * LB)
        mv = np.full(len(lsp), 0.0)
        mv[LB + 1:] = (lsp[LB:-1] - lsp[:-LB - 1]) / sd[LB + 1:]      # move over (i-1-LB, i-1], seen at i
        for side in ("yes", "no"):
            bid = yb if side == "yes" else 100 - ya; adv = -mv if side == "yes" else mv
            p = None; n = 0
            for i in range(len(bid)):
                if n >= maxf: break
                if p is not None and K is not None and adv[i] > K: p = None
                if p is not None:
                    if bid[i] < p: out.append((t, side, p, s[i], yes if side == "yes" else 1 - yes)); n += 1; p = None
                    elif bid[i] > p: p = None
                if p is None and until_s < s[i] <= from_s and lo <= bid[i] <= hi and not (K is not None and adv[i] > K):
                    p = bid[i]
    f = pd.DataFrame(out, columns=["ticker", "side", "px", "s2c", "win"]); f["pnl"] = 100 * f.win - f.px
    return f
rows = []
for nm, lo, hi, a, b in [("all prices", 1, 99, 870, 30), ("middle 36-64", 36, 64, 870, 30), ("fav 85-98", 85, 98, 870, 30)]:
    for K, LB in [(None, 5), (1.0, 5), (0.5, 5), (0.5, 10), (0.25, 3)]:
        f = run(lo, hi, a, b, K, LB)
        per = f.groupby("ticker").pnl.sum(); cnt = f.groupby("ticker").size(); rng = np.random.default_rng(3)
        bs = [per.values[k].sum() / cnt.values[k].sum() for k in (rng.integers(0, len(per), len(per)) for _ in range(200))]
        rows.append(dict(test=nm, guard="none" if K is None else f">{K} moves in {LB}s", fills=len(f), won=f"{100*f.win.mean():.1f}%",
                         avg_px=round(f.px.mean(), 1), c_per_fill=round(f.pnl.mean(), 2), ci90=f"{np.percentile(bs,5):+.2f}..{np.percentile(bs,95):+.2f}"))
print(pd.DataFrame(rows).to_string(index=False))
