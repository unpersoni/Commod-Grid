# Can the two-sided MM ("mm" in MM15Min) be tuned to stop losing? Grid over price band, time window,
# spread and queue filters with the queue-position model of mm_queue_sim.py (V38 ticks + trade flow),
# each result split into the earlier and later half of the markets (a real fix must hold in both).
import pandas as pd, numpy as np, sys, itertools
D = sys.argv[1]
tk = pd.read_pickle(D + "/v38_ticks.pkl")
fl = pd.read_pickle(D + "/v38_flow.pkl")[["ticker", "bucket", "ct_yes_taker", "ct_no_taker", "yes", "cls"]]
tk = tk.merge(fl, on=["ticker", "bucket"], how="inner").sort_values(["ticker", "ts"])
nb = tk.groupby(["ticker", "bucket"]).ts.transform("size")
tk["v_hit_yes"] = tk.ct_no_taker / nb
tk["v_hit_no"] = tk.ct_yes_taker / nb
MK = {t: dict(yb=g.yes_bid.values, ya=g.yes_ask.values, s2c=g.sec_to_close.values, yq=g.yes_bid_qty.values,
              nq=g.no_bid_qty.values, vy=g.v_hit_yes.values, vn=g.v_hit_no.values, win_yes=g.yes.values[0],
              cls=g.cls.values[0], end=g.ts.values[-1]) for t, g in tk.groupby("ticker")}
ends = pd.Series({t: m["end"] for t, m in MK.items()})
HALF2 = set(ends[ends > ends.median()].index)
SIZE = 10

def run(lo, hi, from_s, until_s, model="trades", max_fills=5, min_spread=0, max_queue=None, min_queue=None):
    fills = []
    for t, m in MK.items():
        yb, ya, s2c = m["yb"], m["ya"], m["s2c"]
        for side in ("yes", "no"):
            bid = yb if side == "yes" else 100 - ya
            qty = m["yq"] if side == "yes" else m["nq"]
            vol = m["vy"] if side == "yes" else m["vn"]
            p = None; ahead = 0.0; left = 0.0; done = 0; prevq = 0.0
            for i in range(len(bid)):
                if done >= max_fills: break
                if p is not None:
                    if bid[i] < p:
                        fills.append((t, m["cls"], side, p, left, m["win_yes"], "through")); done += 1; p = None
                    elif bid[i] == p:
                        ahead -= vol[i]
                        if model == "prop" and qty[i] >= 1 and prevq >= 1 and qty[i] < prevq:
                            ahead = min(ahead, ahead * qty[i] / prevq)
                        if qty[i] >= 1: prevq = qty[i]
                        if ahead < 0:
                            got = min(left, -ahead); left -= got; ahead = 0
                            if got > 0: fills.append((t, m["cls"], side, p, got, m["win_yes"], "queue"))
                            if left <= 1e-9: done += 1; p = None
                    elif left == SIZE:
                        p = None
                if p is None and done < max_fills:
                    q = qty[i]
                    if (until_s < s2c[i] <= from_s and lo <= bid[i] <= hi and q >= 1 and ya[i] - yb[i] >= min_spread
                            and (max_queue is None or q <= max_queue) and (min_queue is None or q >= min_queue)):
                        p = bid[i]; ahead = q; left = SIZE; prevq = q
    f = pd.DataFrame(fills, columns=["ticker", "cls", "side", "px", "ct", "win_yes", "how"])
    f["win"] = np.where(f.side == "yes", f.win_yes, 1 - f.win_yes)
    f["pnl"] = f.ct * (100 * f.win - f.px)
    return f

def summ(f, label):
    out = []
    for c, x in f.groupby("cls"):
        cpc = lambda y: round(y.pnl.sum() / y.ct.sum(), 2) if len(y) else np.nan
        h2 = x.ticker.isin(HALF2)
        per = x.groupby("ticker").agg(p=("pnl", "sum"), c=("ct", "sum"))
        rng = np.random.default_rng(1)
        bs = [per.p.values[k].sum() / per.c.values[k].sum() for k in (rng.integers(0, len(per), len(per)) for _ in range(300))]
        out.append(dict(test=label, cls=c, mkts=x.ticker.nunique(), ct=int(x.ct.sum()),
                        won=round(100 * (x.win * x.ct).sum() / x.ct.sum(), 1), avg_px=round((x.px * x.ct).sum() / x.ct.sum(), 1),
                        cpc=cpc(x), half1=cpc(x[~h2]), half2=cpc(x[h2]),
                        ci90=f"{np.percentile(bs, 5):+.2f}..{np.percentile(bs, 95):+.2f}",
                        usd_per_mkt=round(x.pnl.sum() / 100 / x.ticker.nunique(), 2)))
    return out

if __name__ == "__main__":
    rows = []
    bands = [(3, 97), (10, 90), (20, 80), (36, 64), (3, 40), (50, 84), (60, 84), (70, 90), (75, 90), (80, 90), (85, 98)]
    wins = [(900, 30), (900, 600), (900, 300), (600, 30), (300, 30), (600, 120)]
    for model in ("trades", "prop"):
        for (lo, hi), (fs, us) in itertools.product(bands, wins):
            rows += summ(run(lo, hi, fs, us, model), f"{model} {lo}-{hi}c {fs}-{us}s")
        for kw, nm in [(dict(min_spread=2), "spread>=2"), (dict(min_spread=3), "spread>=3"),
                       (dict(max_queue=300), "queue<=300"), (dict(min_queue=3000), "queue>=3000"),
                       (dict(max_fills=1), "1 fill/side")]:
            rows += summ(run(3, 97, 900, 30, model, **kw), f"{model} 3-97c 900-30s {nm}")
    r = pd.DataFrame(rows)
    r.to_pickle(D + "/mm_tune.pkl")
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 500)
    print(r.to_string(index=False))
