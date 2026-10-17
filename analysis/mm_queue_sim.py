# Queue-position simulation of a small resting maker on V38 ticks + trade flow.
# One buy order of SIZE contracts at the best bid of a side; it waits behind the real queue
# (yes_bid_qty / no_bid_qty) and fills only from real taker volume (or if the price trades through it).
import pandas as pd, numpy as np, sys
D = sys.argv[1]
tk = pd.read_pickle(D + "/v38_ticks.pkl")
fl = pd.read_pickle(D + "/v38_flow.pkl")[["ticker", "bucket", "ct_yes_taker", "ct_no_taker", "yes", "cls"]]
tk = tk.merge(fl, on=["ticker", "bucket"], how="inner").sort_values(["ticker", "ts"])
nb = tk.groupby(["ticker", "bucket"]).ts.transform("size")
tk["v_hit_yes"] = tk.ct_no_taker / nb      # taker volume per tick that hits YES bids
tk["v_hit_no"] = tk.ct_yes_taker / nb      # taker volume per tick that hits NO bids
MK = {t: g for t, g in tk.groupby("ticker")}
SIZE = 10

def run(lo, hi, from_s, until_s, model, max_fills, sides=("yes", "no")):
    fills = []
    for t, g in MK.items():
        yb = g.yes_bid.values; ya = g.yes_ask.values; s2c = g.sec_to_close.values
        yq = g.yes_bid_qty.values; nq = g.no_bid_qty.values; vy = g.v_hit_yes.values; vn = g.v_hit_no.values
        win_yes = g.yes.values[0]; cls = g.cls.values[0]
        for side in sides:
            bid = yb if side == "yes" else 100 - ya
            qty = yq if side == "yes" else nq
            vol = vy if side == "yes" else vn
            p = None; ahead = 0.0; left = 0.0; done = 0; prevq = 0.0
            for i in range(len(bid)):
                if done >= max_fills: break
                if p is not None:
                    # trade-through: our level was cleared or the other side came down to us
                    if bid[i] < p:
                        fills.append((t, cls, side, p, left, s2c[i], win_yes, "through")); done += 1; p = None
                    else:
                        if bid[i] == p:
                            ahead -= vol[i]
                            if model == "prop" and qty[i] >= 1 and prevq >= 1 and qty[i] < prevq:
                                ahead = min(ahead, ahead * qty[i] / prevq)
                            if qty[i] >= 1: prevq = qty[i]
                            if ahead < 0:
                                got = min(left, -ahead); left -= got; ahead = 0
                                if got > 0:
                                    fills.append((t, cls, side, p, got, s2c[i], win_yes, "queue"))
                                if left <= 1e-9: done += 1; p = None
                        elif bid[i] > p and left == SIZE:
                            p = None            # someone improved: cancel and re-join below
                        elif bid[i] > p:
                            pass                # partly filled order stays
                if p is None and done < max_fills:
                    if until_s < s2c[i] <= from_s and lo <= bid[i] <= hi and qty[i] >= 1:
                        p = bid[i]; ahead = qty[i]; left = SIZE; prevq = qty[i]
    f = pd.DataFrame(fills, columns=["ticker", "cls", "side", "px", "ct", "s2c", "win_yes", "how"])
    f["win"] = np.where(f.side == "yes", f.win_yes, 1 - f.win_yes)
    f["pnl"] = f.ct * (100 * f.win - f.px)
    return f

def report(f, label):
    out = []
    for c, x in f.groupby("cls"):
        nm = x.ticker.nunique(); per = x.groupby("ticker").pnl.sum()
        rng = np.random.default_rng(1); ix = per.index.values; ct = x.groupby("ticker").ct.sum().reindex(ix).values
        bs = [per.values[k].sum() / ct[k].sum() for k in (rng.integers(0, len(ix), len(ix)) for _ in range(300))]
        out.append(dict(test=label, cls=c, markets=nm, contracts=int(x.ct.sum()), won=f"{100*(x.win*x.ct).sum()/x.ct.sum():.1f}%",
                        avg_px=round((x.px*x.ct).sum()/x.ct.sum(), 1), c_per_contract=round(x.pnl.sum()/x.ct.sum(), 2),
                        ci90=f"{np.percentile(bs,5):+.2f}..{np.percentile(bs,95):+.2f}",
                        through=f"{100*x[x.how=='through'].ct.sum()/x.ct.sum():.0f}%", usd_total=round(x.pnl.sum()/100, 2)))
    return out

if __name__ == "__main__":
    rows = []
    for model in ("trades", "prop"):
        for (lo, hi, nm) in [(1, 99, "all prices"), (91, 98, "fav 91-98"), (85, 98, "fav 85-98"), (36, 64, "middle 36-64"), (2, 9, "longshot 2-9")]:
            f = run(lo, hi, 900, 30, model, 5)
            rows += report(f, f"{nm} [{model}]")
    pd.set_option("display.width", 250)
    print(pd.DataFrame(rows).to_string(index=False))
