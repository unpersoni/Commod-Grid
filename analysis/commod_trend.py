import pandas as pd, numpy as np
t = pd.read_pickle("v37ticks_all.pkl"); t = t[t.result.isin(["yes","no"])].copy()
CR = {"BTC","ETH","SOL","XRP","DOGE"}; t["cls"] = np.where(t.asset.isin(CR), "crypto", "commodity")
t["mid"] = (t.yes_bid + t.yes_ask)/2
rows = []
for tk, g in t.groupby("ticker"):
    if g.sec_to_close.max() < 840: continue
    y = g.result.iat[0] == "yes"; mid = g.mid.values; sec = g.sec_to_close.values
    r = dict(ticker=tk, asset=g.asset.iat[0], cls=g.cls.iat[0], crosses=int((np.diff(np.sign(mid-50))!=0).sum()), swing=mid.max()-mid.min(),
             spread=(g.yes_ask-g.yes_bid).mean())
    for mnt in [3,5,8,11]:
        k = np.flatnonzero(sec <= 900-60*mnt)
        if len(k):
            m = mid[k[0]]; lead_yes = m > 50
            r[f"lead{mnt}_px"] = max(m, 100-m); r[f"lead{mnt}_won"] = int(lead_yes == y) if m != 50 else np.nan
    rows.append(r)
mk = pd.DataFrame(rows)
print("1) Inside a 15-min market (all markets V37 recorded):")
print(mk.groupby("cls").agg(markets=("ticker","size"), crosses_of_50=("crosses","mean"), swing_c=("swing","mean"), spread_c=("spread","mean")).round(2).to_string())
print("\n2) Side leading at minute M: how often it won vs its price (edge = won% - price)")
for mnt in [3,5,8,11]:
    for cls, d in mk.dropna(subset=[f"lead{mnt}_won"]).groupby("cls"):
        p = d[f"lead{mnt}_px"]; w = d[f"lead{mnt}_won"]
        strong = d[p>=65]
        print(f"   minute {mnt:2} {cls:9}: leader won {w.mean()*100:5.1f}% at avg price {p.mean():4.1f}c (edge {w.mean()*100-p.mean():+5.1f}, n={len(d)})"
              f" | leaders at 65c+: won {strong[f'lead{mnt}_won'].mean()*100:5.1f}% at {strong[f'lead{mnt}_px'].mean():4.1f}c (edge {strong[f'lead{mnt}_won'].mean()*100-strong[f'lead{mnt}_px'].mean():+5.1f}, n={len(strong)})")
# 3) across markets: 15-min moves from strike -> final price
st = pd.read_csv("../v37ticks/commod15min_v37_settlements.csv").drop_duplicates("ticker", keep="last")
st["close"] = pd.to_datetime(st.close_time, utc=True); st = st.sort_values(["asset","close"])
st["move"] = (st.expiration_value - st.floor_strike) / st.floor_strike * 100
st["prev_move"] = st.groupby("asset").move.shift(1)
st.loc[(st.close - st.groupby("asset").close.shift(1)) != pd.Timedelta("15min"), "prev_move"] = np.nan
st["cls"] = np.where(st.asset.isin(CR), "crypto", "commodity")
s = st.dropna(subset=["move","prev_move"]); s = s[(s.move != 0) & (s.prev_move != 0)]
print("\n3) Trend across markets: did the market move the same direction as the previous 15-min market?")
for cls, d in s.groupby("cls"):
    same = (np.sign(d.move) == np.sign(d.prev_move)).mean()*100
    print(f"   {cls:9}: same direction {same:4.1f}% of {len(d)} pairs (50% = no trend); avg |15-min move| {d.move.abs().mean():.3f}%")
for a, d in s.groupby("asset"):
    print(f"      {a:9} same direction {(np.sign(d.move)==np.sign(d.prev_move)).mean()*100:4.1f}%  (n={len(d)})  avg |move| {d.move.abs().mean():.3f}%")
mk.to_pickle("markets_trend.pkl")
