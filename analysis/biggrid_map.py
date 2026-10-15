import pandas as pd, numpy as np
L = pd.read_pickle("biggrid_long.pkl")
e = L[(L.cls=="crypto") & (L.n_A>=20) & (L.n_B>=20) & (L.n_NEW>=10)].copy()
e["all3"] = (e.A>0)&(e.B>0)&(e.NEW>0)
BL = {i: f"{10+5*i}-{14+5*i}c" for i in range(18)}
e["band_l"] = e.band.map(BL); e["minute"] = e.win_//2
pd.set_option("display.width", 250)
print("Share of settings profitable in ALL THREE periods (chance level ~1.2%), crypto:")
print("\nby price band:"); print((e.groupby("band_l", sort=False).all3.mean()*100).round(1).reindex([BL[i] for i in range(18)]).dropna().to_string())
print("\nby minute of the market:"); print((e.groupby("minute").all3.mean()*100).round(1).to_string())
print("\nby exit:"); print((e.groupby("exit", sort=False).all3.mean()*100).round(1).to_string())
print("\nby condition:"); print((e.groupby(["factor","level"]).all3.mean()*100).round(1).sort_values(ascending=False).head(15).to_string())
# heat map band x minute for 'no condition', best exit per cell judged on Fri-Mon only
n = e[e.factor=="00 none"]
print("\nNo condition: band x minute, share of the 22 exits profitable in all three periods (%):")
hm = n.groupby(["band_l","minute"]).all3.mean().unstack()*100
print(hm.reindex([BL[i] for i in range(18)]).dropna(how="all").round(0).fillna(-1).astype(int).replace(-1,".").to_string())
