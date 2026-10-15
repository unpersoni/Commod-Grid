import pandas as pd, numpy as np
r = pd.read_csv("../v37ticks/commod15min_v37_trades.csv")
r["ts"] = pd.to_datetime(r.buy_iso, format="ISO8601", utc=True).dt.tz_convert(None)
r["end"] = pd.to_datetime(r.sell_iso, format="ISO8601", utc=True, errors="coerce").dt.tz_convert(None)
r = r.sort_values("ts").reset_index(drop=True)
r["usd"] = r.net_c/100
# sessions: a gap of more than 60 min between buys starts a new session
r["session"] = (r.ts.diff() > pd.Timedelta("60min")).cumsum()
# zigzag on cumulative $ within each session: a new phase starts when the running total reverses by >= $20
TH = 20.0
r["phase"] = -1; r["dir"] = ""
pid = 0
for s, g in r.groupby("session"):
    cum = g.usd.cumsum().values; idx = g.index.values
    start = 0; hi = lo = cum[0]; hi_i = lo_i = 0; d = None; marks = []
    for i, c in enumerate(cum):
        if c > hi: hi, hi_i = c, i
        if c < lo: lo, lo_i = c, i
        if d in (None, "up") and c <= hi - TH:
            marks.append((start, hi_i, "up")); start = hi_i + 1; d = "down"; lo, lo_i = c, i
        elif d in (None, "down") and c >= lo + TH:
            marks.append((start, lo_i, "down")); start = lo_i + 1; d = "up"; hi, hi_i = c, i
    marks.append((start, len(cum)-1, d or "flat"))
    for a, b, dd in marks:
        if b < a: continue
        sub = g.usd.values[a:b+1]
        lab = "WIN" if sub.sum() >= TH/2 else ("LOSS" if sub.sum() <= -TH/2 else "flat")
        r.loc[idx[a:b+1], "phase"] = pid; r.loc[idx[a:b+1], "dir"] = lab; pid += 1
ph = r.groupby("phase").agg(dir=("dir","first"), start=("ts","min"), end=("ts","max"), trades=("usd","size"),
                            usd=("usd","sum"), won=("usd", lambda x: (x>0).mean()*100))
ph["hours"] = (ph.end - ph.start).dt.total_seconds()/3600
ph["start_ET"] = (ph.start - pd.Timedelta(hours=4)).dt.strftime("%a %m-%d %H:%M")
print(ph[["dir","start_ET","hours","trades","won","usd"]].round(1).to_string())
print("\nTotals:", ph.groupby("dir").agg(phases=("usd","size"), trades=("trades","sum"), hours=("hours","sum"), usd=("usd","sum")).round(1).to_string())
r.to_pickle("v37_phases.pkl")
