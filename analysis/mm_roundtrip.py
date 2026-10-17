# Round-trip maker: rest a bid (queue model, as mm_queue_sim), and once filled rest a SELL K cents higher
# (also a resting order: waits its turn at that price, filled by takers buying). Whatever isn't sold by
# 30 s before the close is held to settlement. Up to 5 round trips per side per market. No fees (maker both ways).
import pandas as pd, numpy as np, sys
sys.path.insert(0, sys.path[0])
import mm_queue_sim as q
MK = q.MK; SIZE = 10
def run(lo, hi, K, maxf=5, stop=30, model="prop"):
    rows = []
    for t, g in MK.items():
        yb = g.yes_bid.values; ya = g.yes_ask.values; s2c = g.sec_to_close.values
        yq = g.yes_bid_qty.values; nq = g.no_bid_qty.values; vy = g.v_hit_yes.values; vn = g.v_hit_no.values
        win_yes = g.yes.values[0]; cls = g.cls.values[0]
        for side in ("yes", "no"):
            bid = yb if side == "yes" else 100 - ya          # our side's best bid
            ask = ya if side == "yes" else 100 - yb          # our side's best ask (where we sell)
            bq = yq if side == "yes" else nq                 # queue at our side's bid
            aq = nq if side == "yes" else yq                 # queue at our side's ask (the other side's bid)
            vbuy = vy if side == "yes" else vn               # takers selling our side (hit our bid)
            vsell = vn if side == "yes" else vy              # takers buying our side (lift our ask)
            state = "idle"; p = None; ahead = 0; left = 0; x = None; xa = None; trips = 0; prevq = 0
            for i in range(len(bid)):
                if state == "buy":
                    if s2c[i] <= stop: state = "idle"; p = None
                    elif bid[i] < p or ask[i] <= p:
                        state = "sell"; left = SIZE; x = min(99, p + K); xa = None
                    elif bid[i] == p:
                        ahead -= vbuy[i]
                        if model == "prop" and bq[i] >= 1 and prevq >= 1 and bq[i] < prevq: ahead = min(ahead, ahead * bq[i] / prevq)
                        if bq[i] >= 1: prevq = bq[i]
                        if ahead < 0: state = "sell"; left = SIZE; x = min(99, p + K); xa = None
                    elif bid[i] > p: state = "idle"; p = None
                if state == "sell":
                    if s2c[i] <= stop:
                        rows.append((t, cls, side, p, x, left, "held", win_yes)); trips += 1; state = "idle"; p = None
                    elif bid[i] >= x:                         # someone bids our sell price: sold
                        rows.append((t, cls, side, p, x, left, "sold", win_yes)); trips += 1; state = "idle"; p = None
                    elif ask[i] > x:                          # we'd be the best ask: any buyer takes ours
                        left -= vsell[i]
                    elif ask[i] == x:
                        if xa is None: xa = aq[i] if aq[i] >= 1 else 0
                        xa -= vsell[i]
                        if xa < 0: left += xa; xa = 0
                    else: xa = None                           # ask moved below our price: wait (line resets)
                    if state == "sell" and left <= 1e-9:
                        rows.append((t, cls, side, p, x, SIZE, "sold", win_yes)); trips += 1; state = "idle"; p = None
                if state == "idle" and trips < maxf and stop < s2c[i] <= 900 and lo <= bid[i] <= hi and bq[i] >= 1:
                    state = "buy"; p = bid[i]; ahead = bq[i]; prevq = bq[i]
            if state == "sell":                               # data for this market ended: settle what is still held
                rows.append((t, cls, side, p, x, left, "held", win_yes))
    f = pd.DataFrame(rows, columns=["ticker", "cls", "side", "px", "x", "ct", "how", "win_yes"])
    win = np.where(f.side == "yes", f.win_yes, 1 - f.win_yes)
    # sold part: x - px; held part (what wasn't sold): settlement. 'held' rows: ct = contracts left unsold.
    f["sold_ct"] = np.where(f.how == "sold", SIZE, SIZE - f.ct)
    f["held_ct"] = np.where(f.how == "sold", 0, f.ct)
    f["pnl"] = f.sold_ct * (f.x - f.px) + f.held_ct * (100 * win - f.px)
    return f
out = []
for nm, lo, hi in [("all prices 3-97", 3, 97), ("middle 36-64", 36, 64), ("fav 85-98", 85, 98)]:
    for K in (1, 2, 3, 5, 10, 999):
        f = run(lo, hi, K)
        for c, x in f.groupby("cls"):
            per = x.groupby("ticker").pnl.sum(); n = x.groupby("ticker").size() * SIZE; rng = np.random.default_rng(4)
            bs = [per.values[k].sum() / n.values[k].sum() for k in (rng.integers(0, len(per), len(per)) for _ in range(200))]
            out.append(dict(test=nm, sell="hold" if K == 999 else f"+{K}c", cls=c, trips=len(x),
                            sold=f"{100 * x.sold_ct.sum() / (len(x) * SIZE):.0f}%", c_per_contract=round(x.pnl.sum() / (len(x) * SIZE), 2),
                            ci90=f"{np.percentile(bs,5):+.2f}..{np.percentile(bs,95):+.2f}", usd=round(x.pnl.sum() / 100, 2)))
pd.set_option("display.width", 200)
print(pd.DataFrame(out).sort_values(["test", "cls"], kind="stable").to_string(index=False))
