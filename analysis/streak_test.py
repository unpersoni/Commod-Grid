import pandas as pd, numpy as np, sys
d=pd.read_pickle(sys.argv[1])
end=d.v_end.values; ent=d.entry.values; win=d.v_win.values; net=d.v_net.values
feats=[]
for k in range(len(d)):
    done = np.flatnonzero(end < ent[k])                 # results known when this trade is entered
    order = done[np.argsort(end[done])]
    w = win[order]
    consec = 0
    for x in w[::-1]:
        if x==0: consec+=1
        else: break
    last60 = done[end[done] >= ent[k]-np.timedelta64(60,'m')]
    feats.append(dict(loss5=(1-w[-5:]).sum() if len(w)>=5 else np.nan, loss10=(1-w[-10:]).sum() if len(w)>=10 else np.nan,
                      consec=consec, net60=net[last60].sum() if len(last60) else 0, n60=len(last60)))
d=pd.concat([d, pd.DataFrame(feats)], axis=1)
cut=d.entry.quantile(.5); d["half"]=np.where(d.entry<=cut,"H1","H2")
def row(g):
    return pd.Series(dict(n=len(g), fav_px=round(g.px.mean(),1), fav_won=round(g.fav_won.mean()*100,1),
        v37_edge=round(g.fav_won.mean()*100-g.px.mean(),1), v37_c=round(g.v_net.mean(),1), inv_c=round(g.inv_net.mean(),1),
        v37_H1=round(g[g.half=="H1"].v_net.mean(),1), v37_H2=round(g[g.half=="H2"].v_net.mean(),1)))
pd.set_option("display.width",200)
print(f"All V37 trades: {len(d)}, V37 {d.v_net.mean():+.1f}c/contract, inverse(38-42->70) {d.inv_net.mean():+.1f}c\n")
print("By losses in the last 5 known V37 results:"); print(d.groupby("loss5").apply(row).to_string())
print("\nBy losses in the last 10:"); print(d.groupby(pd.cut(d.loss10,[-1,2,4,6,10],labels=["0-2","3-4","5-6","7-10"])).apply(row).to_string())
print("\nBy current losing streak (consecutive losses):"); print(d.groupby(pd.cut(d.consec,[-1,0,1,2,3,20],labels=["0","1","2","3","4+"])).apply(row).to_string())
print("\nBy V37 net per contract over the last 60 min:"); print(d.groupby(pd.cut(d.net60,[-1e4,-150,-50,50,150,1e4],labels=["<-150","-150..-50","-50..50","50..150",">150"])).apply(row).to_string())
print("\nFlip rules (take the inverse trade when the signal fires, else V37); c/contract total, [H1 / H2]:")
base=d.v_net
for lab,cond in [("last5 >=3 losses",d.loss5>=3),("last5 >=4 losses",d.loss5>=4),("last10 >=5 losses",d.loss10>=5),("last10 >=6 losses",d.loss10>=6),
                 ("streak >=2",d.consec>=2),("streak >=3",d.consec>=3),("last 60m net < -100",d.net60<-100),("last 60m net < -200",d.net60<-200)]:
    alt=np.where(cond, d.inv_net, d.v_net); s=pd.Series(alt)
    print(f"  {lab:22} flips {cond.sum():4}  total {s.sum():+6.0f} vs V37 {base.sum():+6.0f}  [{s[d.half=='H1'].sum()-base[d.half=='H1'].sum():+5.0f} / {s[d.half=='H2'].sum()-base[d.half=='H2'].sum():+5.0f}]"
          f"   flipped trades: V37 {d.v_net[cond].mean():+.1f}c, inverse {d.inv_net[cond].mean():+.1f}c")
d.to_pickle(sys.argv[2])
