# Builds one row per (tick, side) with the 12 entry conditions known at that moment + outcomes.
import pandas as pd, numpy as np, glob, math, sys
TD, BD, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
CR = ["BTC","ETH","SOL","XRP","DOGE"]
def fee(p): return math.ceil(0.07*p*(100-p)/100 - 1e-9)
FEE = np.array([fee(p) for p in range(101)])
t = pd.concat([pd.read_csv(f, usecols=["ts_iso","ticker","asset","yes_bid","yes_ask","sec_to_close","spot_px"]) for f in sorted(glob.glob(TD+"/ticks_*"))])
t = t[(t.sec_to_close>0)&(t.yes_bid>0)&(t.yes_ask<100)&(t.yes_bid<=t.yes_ask)].copy()
t["ts"] = pd.to_datetime(t.ts_iso, format="ISO8601", utc=True).dt.tz_convert(None); t = t.drop(columns="ts_iso")
st = pd.read_csv(TD+"/commod15min_v37_settlements.csv").drop_duplicates("ticker", keep="last")
st["close"] = pd.to_datetime(st.close_time, utc=True).dt.tz_convert(None)
t = t.merge(st[["ticker","result","floor_strike","close"]], on="ticker"); t = t[t.result.isin(["yes","no"])]
t = t.sort_values(["ticker","ts"]).reset_index(drop=True)
t["mid"] = (t.yes_bid + t.yes_ask)/2
print("ticks", len(t), "markets", t.ticker.nunique())
# ---- per-market price features (YES orientation) ----
ts_s = t.ts.values.astype("datetime64[ms]").astype("int64")/1000.0
t["tsec"] = ts_s
def ago(col, secs):
    out = np.full(len(t), np.nan)
    for _, idx in t.groupby("ticker").indices.items():
        tt = ts_s[idx]; v = t[col].values[idx]
        j = np.searchsorted(tt, tt - secs)
        ok = tt - tt[0] >= secs
        out[idx[ok]] = v[j[ok]]
    return out
t["mid30"] = ago("mid", 30); t["mid60"] = ago("mid", 60)
r = t.set_index("ts").groupby("ticker", sort=False).mid.rolling("60s")
t["range60"] = (r.max() - r.min()).values
t["ticks60"] = t.set_index("ts").groupby("ticker", sort=False).mid.rolling("60s").count().values
t["open_mid"] = t.groupby("ticker").mid.transform("first")
t["open_s2c"] = t.groupby("ticker").sec_to_close.transform("first")
# ---- spot: coin & BTC 60s change, spot vs strike (z, vol-scaled) ----
sp = {}
for a, g in t[t.asset.isin(CR) & t.spot_px.notna()].groupby("asset"):
    s = g.drop_duplicates("ts").set_index("ts").spot_px.sort_index()
    s = s[~s.index.duplicated()].resample("1s").last().ffill(limit=10)
    v = (np.log(s).diff()**2).rolling(900, min_periods=300).mean()
    sp[a] = pd.DataFrame({"s": s, "s60": s.shift(60), "var": v})
t["sec1"] = t.ts.dt.floor("1s")
for col in ["coin_chg60","btc_chg60","z_spot"]: t[col] = np.nan
for a in CR:
    m = t.asset == a
    j = sp[a].reindex(t.loc[m, "sec1"].values)
    t.loc[m, "coin_chg60"] = ((j.s.values / j.s60.values) - 1) * 100
    t.loc[m, "z_spot"] = np.log(t.loc[m, "spot_px"].values / t.loc[m, "floor_strike"].values) / np.sqrt(j["var"].values * t.loc[m, "sec_to_close"].values)
jb = sp["BTC"].reindex(t.sec1.values)
t["btc_chg60"] = ((jb.s.values / jb.s60.values) - 1) * 100
# ---- cycle: other markets of the same class/close leaning YES (mid > 55) or NO (< 45) ----
t["cls"] = np.where(t.asset.isin(CR), "crypto", "commod")
lean = np.sign(np.where(t.mid > 55, 1, np.where(t.mid < 45, -1, 0)))
t["lean"] = lean
others_yes = np.zeros(len(t)); others_no = np.zeros(len(t))
for a in t.asset.unique():
    src = t[t.asset == a][["ts","close","cls","lean"]].sort_values("ts").rename(columns={"lean":"l_other"})
    for cl in ["crypto","commod"]:
        tgt = t[(t.asset != a) & (t.cls == cl)][["ts","close"]].reset_index().sort_values("ts")
        s2 = src[src.cls == cl]
        if not len(s2) or not len(tgt): continue
        mm = pd.merge_asof(tgt, s2[["ts","close","l_other"]], on="ts", by="close", tolerance=pd.Timedelta("30s"))
        others_yes[mm["index"].values] += (mm.l_other.values == 1)
        others_no[mm["index"].values] += (mm.l_other.values == -1)
t["others_yes"] = others_yes; t["others_no"] = others_no
# ---- streak: previous market result, same asset ----
st = st.sort_values(["asset","close"]); st["prev_result"] = st.groupby("asset").result.shift(1)
st.loc[(st.close - st.groupby("asset").close.shift(1)) != pd.Timedelta("15min"), "prev_result"] = np.nan
t = t.merge(st[["ticker","prev_result"]], on="ticker", how="left")
# ---- book: best-level quantities ----
bk = []
for f in sorted(glob.glob(BD+"/book_*")):
    b = pd.read_csv(f, usecols=["ts_iso","ticker","yes_bids","no_bids"])
    bk.append(b)
b = pd.concat(bk); b["ts"] = pd.to_datetime(b.ts_iso, format="ISO8601", utc=True).dt.tz_convert(None)
def top(s):
    s = s.fillna("").astype(str).str.split(";").str[0].str.split(":")
    return pd.to_numeric(s.str[1], errors="coerce")
b["yq"] = top(b.yes_bids); b["nq"] = top(b.no_bids)
b = b[["ts","ticker","yq","nq"]].sort_values("ts")
t = pd.merge_asof(t.sort_values("ts"), b, on="ts", by="ticker", tolerance=pd.Timedelta("6s")).sort_values(["ticker","ts"]).reset_index(drop=True)
print("book matched:", t.yq.notna().mean().round(3))
# ---- future max bid per side (for the sell-at-90 exit) ----
t["fmax_yes"] = t.groupby("ticker").yes_bid.transform(lambda x: x[::-1].cummax()[::-1].shift(-1))
t["fmax_no"]  = t.groupby("ticker").yes_ask.transform(lambda x: (100 - x)[::-1].cummax()[::-1].shift(-1))
# ---- two sides ----
rows = []
for side in ("yes","no"):
    y = side == "yes"; sg = 1 if y else -1
    d = pd.DataFrame({"ticker": t.ticker, "asset": t.asset, "cls": t.cls, "close": t.close, "ts": t.ts, "side": side,
        "sec": t.sec_to_close, "px": (t.yes_ask if y else 100 - t.yes_bid).astype(int),
        "spread": (t.yes_ask - t.yes_bid).astype(int),
        "dir30": sg*(t.mid - t.mid30), "dir60": sg*(t.mid - t.mid60), "range60": t.range60, "ticks60": t.ticks60,
        "open_lean": sg*(t.open_mid - 50), "open_seen": t.open_s2c >= 840,
        "z_spot": sg*t.z_spot, "coin_chg60": sg*t.coin_chg60, "btc_chg60": sg*t.btc_chg60,
        "cyc_agree": t.others_yes if y else t.others_no, "cyc_disagree": t.others_no if y else t.others_yes,
        "prev_same": np.where(t.prev_result.isna(), np.nan, (t.prev_result == side).astype(float)),
        "book_imb": (t.yq if y else t.nq) / ((t.yq if y else t.nq) + (t.nq if y else t.yq)),
        "win": (t.result == side).astype(int), "fmax": t.fmax_yes if y else t.fmax_no})
    rows.append(d)
g = pd.concat(rows, ignore_index=True)
g = g[(g.sec > 2) & (g.sec <= 900) & (g.spread <= 5) & (g.px >= 10) & (g.px <= 99)]
g["net_hold"] = 100*g.win - g.px - FEE[g.px.values]
s90 = (g.fmax >= 90) & (g.px < 88)
g["net_s90"] = np.where(s90, 90 - g.px - FEE[g.px.values] - FEE[90], g.net_hold)
g.reset_index(drop=True).to_pickle(OUT)
print("rows", len(g), g.groupby("cls").ticker.nunique().to_dict())
