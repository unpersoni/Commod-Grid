# MM "fav" entry points before the live build (user's question, Oct 20): price band x time window, per class.
# Two tests: V38 queue-position model (mm_tune.run, split in earlier/later half of the markets) and V37 worst case
# (fills only when the price trades through the bid; Oct 9-14, days the V38 data never saw), as in mm_tune_v37.py.
import pandas as pd, numpy as np, sys, importlib.util, itertools
V37, V38 = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("mt", __file__.replace("mm_fav_entry.py", "mm_tune.py"))
sys.argv = [sys.argv[0], V38]; mt = importlib.util.module_from_spec(spec); spec.loader.exec_module(mt)

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
                    if bid[i] < p: out.append((t, p, yes if side == "yes" else 1 - yes)); n += 1; p = None
                    elif bid[i] > p: p = None
                if p is None and until_s < s[i] <= from_s and lo <= bid[i] <= hi: p = bid[i]
    f = pd.DataFrame(out, columns=["ticker", "px", "win"])
    f["cls"] = np.where(f.ticker.str.extract(r"KX([A-Z]+)15M")[0].isin(CR), "crypto", "commodity")
    f["pnl"] = 100 * f.win - f.px
    f["day"] = f.ticker.str.extract(r"-26OCT(\d\d)")[0]
    return f

bands = [(85, 98), (85, 95), (85, 90), (88, 98), (91, 98), (80, 98), (80, 90)]
wins = [(900, 30, "whole market"), (900, 420, "first 8 min"), (900, 300, "first 10 min"), (900, 600, "first 5 min"),
        (600, 30, "after minute 5"), (420, 30, "last 7 min")]
rows = []
for (lo, hi), (fs, us, wn) in itertools.product(bands, wins):
    q = {z["cls"]: z for z in mt.summ(mt.run(lo, hi, fs, us, "trades"), "")}
    w = through(lo, hi, fs, us)
    for c in ("crypto", "commodity"):
        x = w[w.cls == c]; dd = x.groupby("day").pnl.mean()
        z = q.get(c, {})
        rows.append(dict(cls=c, band=f"{lo}-{hi}c", window=wn, v38_ct=z.get("ct", 0), v38_won=z.get("won", np.nan),
                         v38_cpc=z.get("cpc", np.nan), v38_halves=f"{z.get('half1', np.nan):+.2f} / {z.get('half2', np.nan):+.2f}",
                         v38_usd_mkt=z.get("usd_per_mkt", np.nan), v37_fills=len(x), v37_cpc=round(x.pnl.mean(), 2) if len(x) else np.nan,
                         v37_days_up=f"{(dd > 0).sum()}/{len(dd)}"))
r = pd.DataFrame(rows)
r.to_pickle(V38 + "/mm_fav_entry.pkl")
pd.set_option("display.width", 250); pd.set_option("display.max_rows", 500)
for c in ("crypto", "commodity"):
    print(f"\n{c.upper()} fav — V38 queue model (c/contract, halves, $ per market) and V37 worst case (c/fill, days up)")
    print(r[r.cls == c].drop(columns="cls").sort_values("v38_cpc", ascending=False).to_string(index=False))
