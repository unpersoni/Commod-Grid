import pandas as pd, numpy as np, json
m = pd.read_pickle("v37_phase_trades.pkl")
m = m[m.dir.isin(["WIN","LOSS"])].copy()
m["won"] = m.usd > 0
sg = np.where(m.side_l == "yes", 1, -1)
num = lambda c: pd.to_numeric(m[c], errors="coerce")
N = {   # numeric conditions -> typical range
 "05 Speed: price range over last 60s (c)": num("range60_c"),
 "06 How the market opened: side's opening price minus 50c (c)": m.open_lean.where(m.open_seen == True),
 "07 Spot vs strike, own coin (%, + = already on our side)": num("spot_dist_pct"),
 "08 Coin spot move, last 60s (%, + = our way)": num("spot_chg60_pct"),
 "09 BTC spot move, last 60s (%, + = our way)": num("btc_chg60_pct") * sg,
 "12 Order book: share of top-of-book size on our side": m.book_imb,
}
def lvl(x, a, b, labs):
    return pd.Series(np.where(x.isna(), None, np.where(x <= a, labs[0], np.where(x >= b, labs[2], labs[1]))), index=x.index)
et_h = (m.ts - pd.Timedelta(hours=4)).dt.hour
T = {   # tally conditions -> count / percent / win rate
 "01 Minute of the market at the buy": (pd.Series(np.where(m.minute_in_market <= 0, "minute 0", np.where(m.minute_in_market == 1, "minute 1", "minute 2-3")), index=m.index), ["minute 0","minute 1","minute 2-3"]),
 "02 Spread at the buy": (pd.Series(np.where(m.spread <= 1, "1c", np.where(m.spread == 2, "2c", "3-5c")), index=m.index).where(m.spread.notna()), ["1c","2c","3-5c"]),
 "03 Kalshi price, last 30s (only when 30s of history)": (lvl(m.dir30, -2, 2, ["falling into the price","flat","rising into the price"]), ["rising into the price","flat","falling into the price"]),
 "04 Kalshi price since the market opened": (lvl(num("side_mid_c") - (50 + m.open_lean.where(m.open_seen == True)), -2, 2, ["falling into the price","flat","rising into the price"]), ["rising into the price","flat","falling into the price"]),
 "10 Cycle: other coins leaning": (lvl(m.cyc_agree - m.cyc_disagree, -2, 2, ["against our side","mixed","with our side"]), ["with our side","mixed","against our side"]),
 "11 Streak: same coin's previous market": (pd.Series(np.where(m.prev_same.isna(), None, np.where(m.prev_same == 1, "won by this side", "won by the other side")), index=m.index), ["won by this side","won by the other side"]),
 "13 Time of day (ET)": (pd.Series(np.where(et_h < 9, "night 12am-9am", np.where(et_h < 18, "day 9am-6pm", "evening 6pm-12am")), index=m.index), ["day 9am-6pm","evening 6pm-12am","night 12am-9am"]),
}
out = {"numeric": {}, "tally": {}, "totals": {}}
for d in ["WIN","LOSS"]:
    x = m[m.dir == d]; out["totals"][d] = dict(trades=int(len(x)), usd=round(float(x.usd.sum()), 2), won=round(float(x.won.mean()*100), 1))
for name, s in N.items():
    out["numeric"][name] = {d: dict(p10=float(s[m.dir==d].quantile(.1)), med=float(s[m.dir==d].median()), p90=float(s[m.dir==d].quantile(.9)), n=int(s[m.dir==d].notna().sum())) for d in ["WIN","LOSS"]}
for name, (s, order) in T.items():
    out["tally"][name] = {"order": order}
    for d in ["WIN","LOSS"]:
        x = s[m.dir == d].dropna(); w = m.won[x.index]
        out["tally"][name][d] = {k: dict(n=int((x == k).sum()), pct=float((x == k).mean()*100), won=float(w[x == k].mean()*100) if (x == k).any() else None) for k in order}
# running P&L with phases
r = pd.read_pickle("v37_phases.pkl").sort_values("ts").reset_index(drop=True)
out["curve"] = dict(cum=r.usd.cumsum().round(2).tolist(), dir=r.dir.tolist(), day=(r.ts - pd.Timedelta(hours=4)).dt.strftime("%a %d").tolist())
json.dump(out, open("streak_chart.json", "w"))
print(json.dumps(out["totals"]))
for k, v in out["numeric"].items(): print(k, {d: (round(v[d]['p10'],3), round(v[d]['med'],3), round(v[d]['p90'],3), v[d]['n']) for d in v})
for k, v in out["tally"].items(): print(k, {d: {kk: (vv['n'], round(vv['pct']), None if vv['won'] is None else round(vv['won'])) for kk, vv in v[d].items()} for d in ["WIN","LOSS"]})
