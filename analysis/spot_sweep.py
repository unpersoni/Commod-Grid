import pandas as pd, numpy as np
m = pd.read_pickle("v37_spotcut.pkl")
def row(lab, k):
    d = m[k]; a = m
    if not len(d): return
    f = lambda x: f"{x.usd.mean():+.2f}" if len(x) else "  -  "
    print(f"{lab:52} trades {len(d):4} ({len(d)/len(a)*100:3.0f}%)  won {(d.usd>0).mean()*100:5.1f}%  $/trade {d.usd.mean():+.2f}  total ${d.usd.sum():+8.2f}"
          f"  | Fri-Mon {f(d[d.per=='Fri-Mon'])} Tue-Wed {f(d[d.per=='Tue-Wed'])} | kept WIN-phase {(d.dir=='WIN').sum()/(a.dir=='WIN').sum()*100:3.0f}% LOSS-phase {(d.dir=='LOSS').sum()/(a.dir=='LOSS').sum()*100:3.0f}%")
print(f"All V37 trades in phases: {len(m)}, won {(m.usd>0).mean()*100:.1f}%, $/trade {m.usd.mean():+.2f}, total ${m.usd.sum():+.2f}\n")
ok = m.dist.notna()
print("== Own coin: keep only trades where spot is within X% of the strike (either side) ==")
for x in [0.01,0.02,0.03,0.05,0.075,0.1,0.15,0.5,0.65]: row(f"|distance| <= {x}%", ok & (m.dist.abs()<=x))
print("\n== Own coin: spot already on our side by at least X% ==")
for x in [0,0.01,0.02,0.03,0.05,0.075,0.1]: row(f"distance >= +{x}%", ok & (m.dist>=x))
print("\n== Own coin: spot on our side by 0..X% (close, but on our side) ==")
for x in [0.02,0.03,0.05,0.075,0.1]: row(f"0 <= distance <= {x}%", ok & (m.dist>=0) & (m.dist<=x))
print("\n== Add direction: own coin moving our way over 60s (> 0.01%) ==")
okd = ok & m.cdir.notna()
for lab,k in [("any distance, moving our way", okd & (m.cdir>0.01)),("any distance, NOT moving our way", okd & (m.cdir<=0.01)),
              ("|distance| <= 0.05%, moving our way", okd & (m.dist.abs()<=0.05) & (m.cdir>0.01)),
              ("|distance| <= 0.05%, not moving our way", okd & (m.dist.abs()<=0.05) & (m.cdir<=0.01)),
              ("distance >= +0.03%, moving our way", okd & (m.dist>=0.03) & (m.cdir>0.01)),
              ("distance >= +0.03%, not moving our way", okd & (m.dist>=0.03) & (m.cdir<=0.01))]: row(lab,k)
print("\n== BTC: its spot vs its strike, and its 60s direction (oriented to our side) ==")
okb = m.bdist.notna()
for x in [0.01,0.02,0.03,0.05,0.1,0.5]: row(f"BTC |distance| <= {x}%", okb & (m.bdist.abs()<=x))
for x in [0,0.02,0.03,0.05]: row(f"BTC distance >= +{x}% (on our side)", okb & (m.bdist>=x))
okbd = okb & m.bdir.notna()
for lab,k in [("BTC on our side, moving our way", okbd & (m.bdist>0) & (m.bdir>0.01)),
              ("BTC on our side, not moving our way", okbd & (m.bdist>0) & (m.bdir<=0.01)),
              ("BTC against us", okbd & (m.bdist<=0))]: row(lab,k)
