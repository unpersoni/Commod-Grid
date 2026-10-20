# V39 (= V38b, open favourite, all 11 markets): which stop rule limits losses without stopping too early?
# V38b's opening buy replayed on V37 + V38 ticks (Oct 9-16): first tick in the first 60 s with a spread <= 5c and an
# ask at 56-95c, 10 contracts at the ask, taker fee, held to settlement. Rules are run round by round in time order.
# Timing as in the live bot: round r is bought in the first minute of its window, while round r-1 is still settling,
# so a decision made when round r-1 settles can only change round r+1 (one round of lag).
import pandas as pd, numpy as np, sys
V37, V38T, V38S = sys.argv[1], sys.argv[2], sys.argv[3]
CR = {"BTC", "ETH", "SOL", "XRP", "DOGE"}
a = pd.read_pickle(V37)[["ticker", "asset", "yes_bid", "yes_ask", "sec_to_close", "ts", "result"]]
b = pd.read_pickle(V38T)[["ticker", "asset", "yes_bid", "yes_ask", "sec_to_close", "ts"]]
b = b.merge(pd.read_csv(V38S).drop_duplicates("ticker", keep="last")[["ticker", "result"]], on="ticker")
d = pd.concat([a, b]).drop_duplicates(["ticker", "ts"])
d = d[d.result.isin(["yes", "no"]) & d.sec_to_close.between(840, 905)]
d = d[(d.yes_bid > 0) & (d.yes_ask < 100) & (d.yes_bid < d.yes_ask) & (d.yes_ask - d.yes_bid <= 5)].sort_values(["ticker", "ts"])
fee = lambda p: np.ceil(0.07 * 10 * p * (100 - p) / 100 - 1e-9)        # taker fee, 10 contracts, cents
rows = []
for t, g in d.groupby("ticker"):
    for yb, ya, ts, res in zip(g.yes_bid.values, g.yes_ask.values, g.ts.values, g.result.values):
        side = "yes" if 56 <= ya <= 95 else ("no" if 56 <= 100 - yb <= 95 else None)
        if side:
            px = int(ya if side == "yes" else 100 - yb)
            won = (res == side)
            rows.append(dict(ticker=t, asset=g.asset.values[0], ts=ts, px=px, cost=10 * px + fee(px),
                             net=10 * (100 * won - px) - fee(px)))
            break
T = pd.DataFrame(rows)
T["cls"] = np.where(T.asset.isin(CR), "crypto", "commodity")
T["win"] = T.ticker.str.split("-").str[1]
T["close"] = T.groupby("win").ts.transform("min").dt.ceil("15min")
T = T.sort_values(["close", "ts"]).reset_index(drop=True)
rounds = sorted(T.close.unique())
R = {r: T[T.close == r] for r in rounds}
T["day"] = T.close.dt.strftime("%m-%d")
print(f"V39 replay: {len(T)} buys in {len(rounds)} rounds, {T.day.nunique()} UTC days "
      f"({pd.Timestamp(rounds[0]):%b %d %H:%M} - {pd.Timestamp(rounds[-1]):%b %d %H:%M})")
print(f"no stop: net ${T.net.sum() / 100:+,.2f} · crypto ${T[T.cls == 'crypto'].net.sum() / 100:+,.2f} · "
      f"commodity ${T[T.cls == 'commodity'].net.sum() / 100:+,.2f} · won {100 * (T.net > 0).mean():.1f}%")

def run(rule, L=50, K=4, thr=0, per_class=False):
    """Returns the live trades (a boolean per row of T) under the rule."""
    live = np.zeros(len(T), bool)
    settled = {}           # day -> settled live net (cents), as of the decision point
    hist = []              # per settled round: dict class -> net of ALL trades (live or not), for the rolling rules
    stopped = {}           # day -> stopped by a daily stop
    for i, r in enumerate(rounds):
        day = pd.Timestamp(r).strftime("%m-%d")
        x = R[r]
        prev = R[rounds[i - 1]] if i >= 1 else None                      # still settling while round r is bought
        # settled live net of today's rounds up to r-2 (r-1 settles while r is being bought)
        s = settled.get(day, 0)
        prev_live_cost = 0
        if prev is not None and pd.Timestamp(rounds[i - 1]).strftime("%m-%d") == day:
            prev_live_cost = T.cost[prev.index][live[prev.index]].sum()
        ok_round = True
        if rule == "round_stop":                                           # the current V38b V3 rule
            ok_round = not stopped.get(day, False)
        elif rule == "before_round":                                       # option B
            ok_round = s - prev_live_cost >= -L * 100
        elif rule in ("rolling", "rolling+cap"):
            h = hist[-K:] if len(hist) >= 1 else []
            if per_class:
                okc = {c: (len(h) < 1 or sum(z.get(c, 0) for z in h) >= thr * 100) for c in ("crypto", "commodity")}
            else:
                tot = sum(sum(z.values()) for z in h)
                okc = {c: (len(h) < 1 or tot >= thr * 100) for c in ("crypto", "commodity")}
        budget = L * 100 + s - prev_live_cost
        for j in x.index:
            if rule == "none": live[j] = True
            elif rule in ("round_stop", "before_round"): live[j] = ok_round
            elif rule == "cap":                                            # option A: hard cap
                if budget - T.cost[j] >= 0: live[j] = True; budget -= T.cost[j]
            elif rule == "rolling": live[j] = okc[T.cls[j]]
            elif rule == "rolling+cap":
                if okc[T.cls[j]] and budget - T.cost[j] >= 0: live[j] = True; budget -= T.cost[j]
        # round r-1 has now settled (in time for round r+1's decision)
        if prev is not None:
            pday = pd.Timestamp(rounds[i - 1]).strftime("%m-%d")
            settled[pday] = settled.get(pday, 0) + T.net[prev.index][live[prev.index]].sum()
            if rule == "round_stop" and settled[pday] < -L * 100: stopped[pday] = True
            hist.append({c: prev[prev.cls == c].net.sum() for c in ("crypto", "commodity")})
    return live

def report(name, live):
    x = T[live]; days = T.day.unique()
    by_day = x.groupby("day").net.sum().reindex(days, fill_value=0) / 100
    eq = x.sort_values("close").net.cumsum() / 100
    dd = (eq.cummax().clip(lower=0) - eq).max() if len(eq) else 0
    skipped = T[~live].net.sum() / 100                                     # what the trades it skipped would have made
    half = T.close < rounds[len(rounds) // 2]
    h1, h2 = T[live & half].net.sum() / 100, T[live & ~half].net.sum() / 100
    print(f"  {name:44} live {len(x):4} ({100 * len(x) / len(T):3.0f}%)  net ${x.net.sum() / 100:+8.2f}  "
          f"halves ${h1:+7.2f} / ${h2:+7.2f}  worst day ${by_day.min():+7.2f}  max drawdown ${dd:6.2f}  "
          f"skipped trades made ${skipped:+8.2f}")

print("\nrule (10 contracts, all 11 markets)          trades taken live        net      (earlier / later half)   worst day   drawdown   what the skipped trades made")
report("no stop", run("none"))
for L in (50, 100):
    report(f"current: stop after a round, -${L}", run("round_stop", L))
    report(f"B: skip a round if settled + open < -${L}", run("before_round", L))
    report(f"A: hard cap -${L} (open positions counted)", run("cap", L))
for K in (2, 4, 8):
    for thr in (0, -10):
        report(f"rolling: live while last {K} rounds >= ${thr}", run("rolling", K=K, thr=thr))
        report(f"rolling per class: last {K} rounds >= ${thr}", run("rolling", K=K, thr=thr, per_class=True))
report("rolling per class (4, $0) + hard cap -$100", run("rolling+cap", L=100, K=4, thr=0, per_class=True))
print("\nno-stop net by day:", ", ".join(f"{dd_} ${v / 100:+.2f}" for dd_, v in T.groupby("day").net.sum().items()))

# Better than skipping trades at random? Random selections of whole round x class blocks (the coins move together),
# matched to each rule's share of trades taken live; the rule's percentile among 1,000 of them.
T["blk"] = T.win + "|" + T.cls
blk_net = T.groupby("blk").net.sum(); blk_n = T.groupby("blk").size()
rng = np.random.default_rng(7)
print("\nvs random skipping (same share of trades, whole round x class blocks):")
for name, live in [("current: stop after a round, -$50", run("round_stop", 50)),
                   ("A: hard cap -$50", run("cap", 50)), ("A: hard cap -$100", run("cap", 100)),
                   ("rolling per class, last 2 rounds >= $0", run("rolling", K=2, thr=0, per_class=True)),
                   ("rolling per class, last 4 rounds >= $0", run("rolling", K=4, thr=0, per_class=True)),
                   ("rolling per class, last 8 rounds >= $0", run("rolling", K=8, thr=0, per_class=True)),
                   ("rolling (all classes), last 4 rounds >= $0", run("rolling", K=4, thr=0)),
                   ("rolling per class (4, $0) + hard cap -$100", run("rolling+cap", L=100, K=4, thr=0, per_class=True))]:
    share = live.mean(); actual = T[live].net.sum() / 100
    sims = []
    for _ in range(1000):
        pick = blk_net.index[rng.random(len(blk_net)) < share]
        sims.append(blk_net[pick].sum() / 100)
    sims = np.array(sims)
    print(f"  {name:44} actual ${actual:+8.2f}  random median ${np.median(sims):+8.2f}  "
          f"(5-95%: ${np.percentile(sims, 5):+.0f}..${np.percentile(sims, 95):+.0f})  beats {100 * (sims < actual).mean():.0f}% of random")
