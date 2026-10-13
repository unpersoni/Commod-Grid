import pandas as pd, numpy as np, math, sys
t = pd.read_pickle(sys.argv[1])
lg = lambda p: np.log(p/(1-p))
# blend without constant, fitted on H1
H1 = t[t.half=="H1"]
def fit(X,y,it=50):
    w=np.zeros(X.shape[1])
    for _ in range(it):
        p=1/(1+np.exp(-X@w)); g=X.T@(y-p); Hs=-(X*(p*(1-p))[:,None]).T@X - 1e-6*np.eye(len(w)); w-=np.linalg.solve(Hs,g)
    return w
Xn = lambda d: np.c_[lg(d.mid.values), lg(d.p_spot.values)]
w = fit(Xn(H1), H1.yes_win.values.astype(float)); print("no-constant blend weights [kalshi, spot] =", w.round(2))
t["p_blend0"] = 1/(1+np.exp(-Xn(t)@w))
def fee(p): return math.ceil(0.07*p*(100-p)/100 - 1e-9)
def run(model, thr, sec_lo=30, sec_hi=870):
    out=[]
    d = t[(t.sec_to_close>=sec_lo)&(t.sec_to_close<=sec_hi)]
    for side in ("yes","no"):
        p = d[model] if side=="yes" else 1-d[model]
        ask = d.yes_ask if side=="yes" else 100-d.yes_bid
        q = d[(p*100 - ask) >= thr].assign(ask=ask, pm=p*100)
        q = q.sort_values("ts").drop_duplicates("ticker")
        win = (q.result==side).astype(int)
        out.append(pd.DataFrame({"ticker":q.ticker,"half":q.half,"asset":q.asset,"px":q.ask,"pm":q.pm,"win":win,"sec":q.sec_to_close,
                                 "net":100*win-q.ask-q.ask.map(fee)}))
    return pd.concat(out)
def line(r):
    if not len(r): return "n=0"
    return f"n={len(r):4} px={r.px.mean():5.1f} model={r.pm.mean():5.1f} won={r.win.mean()*100:5.1f}% edge={r.win.mean()*100-r.px.mean():+5.1f} net={r.net.mean():+5.1f}c tot=${r.net.sum()/100:+7.2f}"
for model in ["p_spot","p_blend0","p_blend"]:
    print(f"\n=== {model}: buy the side when model% - ask >= threshold (first time per market & side), hold ===")
    for thr in [3,5,8,10,15]:
        r = run(model, thr)
        print(f" thr {thr:2}:  H1 {line(r[r.half=='H1'])}\n          H2 {line(r[r.half=='H2'])}")
r = run("p_blend0", 5); r=r[r.half=="H2"]
r["left"]=pd.cut(r.sec,[0,180,420,660,900],labels=["0-3m","3-7m","7-11m","11-15m"])
r["pxb"]=pd.cut(r.px,[0,30,50,70,90,100],labels=["<=30","31-50","51-70","71-90","91+"])
print("\np_blend0 thr 5, H2 (out of sample) by time left / price / coin:")
for c in ["left","pxb","asset"]:
    for k,g in r.groupby(c): print(f"  {c}={k:7} {line(g)}")
