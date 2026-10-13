import pandas as pd, numpy as np, sys
b = pd.read_pickle(sys.argv[1])
cut = b.close.quantile(.5); b["half"] = np.where(b.close<=cut,"H1","H2")
b["pxb"] = pd.cut(b.px,[29,44,59,74,89,95],labels=["30-44","45-59","60-74","75-89","90-95"])
b["was_low"] = pd.cut(b.px-b.runmin,[-1,4,14,29,200],labels=["never 5c+ lower","5-14 lower","15-29 lower","30+ lower"])
b["was_high"] = pd.cut(b.runmax-b.px,[-200,4,14,29,200],labels=["never 5c+ higher","5-14 higher","15-29 higher","30+ higher"])
b["mom60"] = pd.cut(b.chg60,[-200,-15,-5,5,15,200],labels=["falling 15+","falling 5-15","flat","rising 5-15","rising 15+"])
b["mom180"] = pd.cut(b.chg180,[-200,-30,-10,10,30,200],labels=["falling 30+","falling 10-30","flat","rising 10-30","rising 30+"])
b["left"] = pd.cut(b.sec,[0,180,420,660,900],labels=["0-3m left","3-7m","7-11m","11-15m"])
def cell(g):
    if len(g) < 40: return "-"
    e = g.win.mean()*100-g.px.mean(); se = np.sqrt(max(g.win.mean()*(1-g.win.mean()),.01)/len(g))*100
    h = [g[g.half==k] for k in ("H1","H2")]
    hs = "/".join(f"{x.win.mean()*100-x.px.mean():+.0f}" if len(x)>=15 else "." for x in h)
    flag = "*" if abs(e) > 2*se else " "
    return f"{e:+5.1f}{flag} n={len(g):<5} h={hs}"
pd.set_option("display.width",250); pd.set_option("display.max_colwidth",40)
for cls in ["crypto","commod"]:
    bb = b[b.cls==cls]
    print(f"\n######## {cls}: {len(bb)} entries, {bb.ticker.nunique()} markets.  cell = edge (win% - price)  * = beyond 2 SE   h = 1st/2nd half")
    for col in ["was_low","was_high","mom60","mom180"]:
        print(f"\n-- {col} --"); print(bb.groupby(["pxb",col]).apply(cell).unstack().to_string())
    print("\n-- whole class by mom60 --"); print(bb.groupby("mom60").apply(cell).to_string())
    print("\n-- whole class by was_low --"); print(bb.groupby("was_low").apply(cell).to_string())
# rising combined with time left, crypto
c = b[b.cls=="crypto"]
print("\n-- crypto: rising 15+ in last 60s, by time left x price --")
print(c[c.mom60=="rising 15+"].groupby(["pxb","left"]).apply(cell).unstack().to_string())
print("\n-- crypto: net c/trade after fee, rising 15+ vs all, by price --")
print(pd.DataFrame({"all": c.groupby("pxb").net.mean().round(1), "rising6+": c[c.mom60=="rising 15+"].groupby("pxb").net.mean().round(1),
    "was 15+ lower": c[c.px-c.runmin>=15].groupby("pxb").net.mean().round(1)}).to_string())
