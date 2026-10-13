import pandas as pd, numpy as np, math, sys
t = pd.read_pickle(sys.argv[1])
erf = np.vectorize(math.erf)
Phi = lambda z: 0.5*(1+erf(np.asarray(z)/math.sqrt(2)))
cut = t.close.quantile(.5); t["half"] = np.where(t.close<=cut,"H1","H2")
print("split at", cut, "| markets H1/H2:", t[t.half=="H1"].ticker.nunique(), t[t.half=="H2"].ticker.nunique())
t["mid"] = ((t.yes_bid+t.yes_ask)/2/100).clip(.01,.99)
lg = lambda p: np.log(p/(1-p))
def ll(p,y): p=np.clip(p,.005,.995); return -np.mean(y*np.log(p)+(1-y)*np.log(1-p))
H1, H2 = t[t.half=="H1"], t[t.half=="H2"]
# fit scale k on H1
ks = np.arange(.3,2.01,.05); best = min(ks, key=lambda k: ll(Phi(k*H1.z0.values), H1.yes_win.values)); print("fitted k =", round(best,2))
t["p_spot"] = np.clip(Phi(best*t.z0.values), .001, .999)
# blend: logistic on [1, logit(mid), logit(p_spot)] fitted on H1
def fit(X,y,it=50):
    w=np.zeros(X.shape[1])
    for _ in range(it):
        p=1/(1+np.exp(-X@w)); g=X.T@(y-p); Hs=-(X*(p*(1-p))[:,None]).T@X - 1e-6*np.eye(len(w)); w-=np.linalg.solve(Hs,g)
    return w
X = lambda d: np.c_[np.ones(len(d)), lg(d.mid.values), lg(t.loc[d.index,"p_spot"].values.clip(.001,.999))]
H1 = t[t.half=="H1"]; H2 = t[t.half=="H2"]
w = fit(X(H1), H1.yes_win.values.astype(float)); print("blend weights [const, kalshi, spot] =", w.round(2))
t["p_blend"] = 1/(1+np.exp(-X(t)@w))
for h,d in t.groupby("half"):
    y=d.yes_win.values
    print(f"{h}: log-loss (lower=better)  kalshi mid {ll(d.mid.values,y):.4f}   spot model {ll(d.p_spot.values,y):.4f}   blend {ll(d.p_blend.values,y):.4f}")
# by time left
t["left"]=pd.cut(t.sec_to_close,[0,180,420,660,900],labels=["0-3m","3-7m","7-11m","11-15m"])
print("\nH2 log-loss by time left:")
for k,d in t[t.half=="H2"].groupby("left"):
    y=d.yes_win.values; print(f"  {k:7} kalshi {ll(d.mid.values,y):.4f}  spot {ll(d.p_spot.values,y):.4f}  blend {ll(d.p_blend.values,y):.4f}  n={len(d)}")
t.to_pickle(sys.argv[2])
