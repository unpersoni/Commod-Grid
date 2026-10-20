# V39 (open favourite, all 11 markets) x the 13 grid conditions: does any condition at the moment of the buy
# separate winners from losers? (user's question, Oct 20; the user also saw day vs evening/night differences)
# Source: the grid's condition rows (analysis/grid_build2.py on V37 ticks + books, Oct 9-14: grid_rows2.pkl).
# V39's buy = first moment in the market's first 60 s with a side's ask at 56-95c and a spread <= 5c, held.
# Levels as in analysis/grid_run.py. Each result also split into the earlier and later half of the markets.
import pandas as pd, numpy as np, sys
g = pd.read_pickle(sys.argv[1])
e = g[(g.sec >= 840) & (g.px >= 56) & (g.px <= 95)].sort_values("ts").drop_duplicates("ticker").copy()
e["half"] = np.where(e.close <= e.close.median(), "1st", "2nd")
print(f"V39 buys: {len(e)} ({(e.cls == 'crypto').sum()} crypto, {(e.cls == 'commod').sum()} commodity), "
      f"{e.close.min():%b %d %H:%M} - {e.close.max():%b %d %H:%M} UTC")
cut3 = lambda x, a, b, labs: pd.Series(np.where(x.isna(), None, np.where(x <= a, labs[0], np.where(x >= b, labs[2], labs[1]))), index=x.index)
F = {}
F["01 minute"] = pd.Series("minute 0-1 (always, V39 buys at the open)", index=e.index)
F["02 spread"] = pd.Series(np.where(e.spread <= 1, "1c", np.where(e.spread == 2, "2c", "3-5c")), index=e.index)
F["03 30s direction"] = cut3(e.dir30, -2, 2, ["falling", "flat", "rising"])
F["04 60s direction"] = cut3(e.dir60, -3, 3, ["falling", "flat", "rising"])
F["05 speed (60s range)"] = cut3(e.range60, 9, 17, ["slow", "medium", "fast"])
F["06 open lean"] = cut3(e.open_lean.where(e.open_seen), -3, 3, ["opened underdog", "opened even", "opened favourite"])
F["07 spot vs strike"] = cut3(e.z_spot, 0, 0.5, ["spot against", "spot slightly with", "spot well with"])
F["08 coin spot 60s"] = cut3(e.coin_chg60, -0.01, 0.01, ["coin against", "coin flat", "coin with"])
F["09 BTC spot 60s"] = cut3(e.btc_chg60, -0.01, 0.01, ["BTC against", "BTC flat", "BTC with"])
F["10 cycle (other markets)"] = cut3(e.cyc_agree - e.cyc_disagree, -2, 2, ["others against", "others mixed", "others with"])
F["11 streak (previous market)"] = pd.Series(np.where(e.prev_same.isna(), None, np.where(e.prev_same == 1, "prev market won this side",
                                                                                     "prev market won other side")), index=e.index)
F["12 book (top level)"] = cut3(e.book_imb, 0.33, 0.67, ["book against", "book balanced", "book with"])
et_h = (e.ts - pd.Timedelta(hours=4)).dt.hour
F["13 time of day (ET)"] = pd.Series(np.where(et_h < 9, "night 0-9", np.where(et_h < 18, "day 9-18", "evening 18-24")), index=e.index)

def line(x, label, base):
    if len(x) < 15: return f"      {label:30} {len(x):4} buys (too few)"
    h = x.groupby("half").net_hold.mean()
    flag = "  <-- both halves above the class average" if (h.get("1st", -99) > base and h.get("2nd", -99) > base and len(x) >= 40) else \
           ("  <-- both halves below" if (h.get("1st", 99) < base and h.get("2nd", 99) < base and len(x) >= 40) else "")
    return (f"      {label:30} {len(x):4} buys  won {100 * x.win.mean():5.1f}%  avg buy {x.px.mean():5.1f}c  "
            f"net {x.net_hold.mean():+6.2f}c  (halves {h.get('1st', np.nan):+6.2f} / {h.get('2nd', np.nan):+6.2f}){flag}")

for c, cname in (("crypto", "CRYPTO"), ("commod", "COMMODITIES")):
    x0 = e[e.cls == c]; base = x0.net_hold.mean()
    hb = x0.groupby("half").net_hold.mean()
    print(f"\n{cname}: {len(x0)} buys, won {100 * x0.win.mean():.1f}%, avg buy {x0.px.mean():.1f}c, net {base:+.2f}c per contract "
          f"(halves {hb.get('1st'):+.2f} / {hb.get('2nd'):+.2f})")
    for fname, lev in F.items():
        lv = lev.reindex(x0.index)
        known = lv.notna().mean()
        print(f"   {fname}" + ("" if known > 0.99 else f"   (known for {100 * known:.0f}% of buys)"))
        for L in sorted(lv.dropna().unique()):
            print(line(x0[lv == L], L, base))
    print("   time of day, 4-hour blocks (ET):")
    blk = ((e.loc[x0.index, "ts"] - pd.Timedelta(hours=4)).dt.hour // 4) * 4
    for b0 in sorted(blk.unique()):
        print(line(x0[blk == b0], f"{b0:02d}:00-{b0 + 4:02d}:00 ET", base))
