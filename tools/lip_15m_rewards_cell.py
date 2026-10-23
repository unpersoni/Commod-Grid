# ===================== 15-MINUTE REWARD PROGRAMS — what Kalshi pays for resting orders on the 15-min series (nothing is traded) =====================
# Lists every active liquidity-reward program on a series ending in 15M (BTC, ETH, ... GOLD, COPPER, WTI ...), what each
# pays per hour, and — from each market's order book right now — what YOUR resting orders would earn per hour at a few
# sizes. Kalshi scores resting bids every second as size x discount^(cents below the reference price) and splits each
# period's reward by score; the YES/NO split is not published (half each assumed), so $/hour is a guide, not a promise.
# Run it a few times over a day (e.g. morning, afternoon, night): programs and books change, metals/oil close overnight.
import requests, time, datetime as dt, collections, json

BASE = "https://api.elections.kalshi.com/trade-api/v2"
SIZES = [10, 25, 100]  # contracts resting on EACH side (YES bid and NO bid) to estimate for
REWARD_UNIT = 10000    # period_reward is in 1/100 cents: 1,000,000 = $100

def get(path, params=None):
    for i in range(4):
        try:
            r = requests.get(BASE + path, params=params, timeout=20)
            if r.status_code == 429: time.sleep(1 + i); continue
            return r.json() if r.status_code == 200 else None
        except requests.RequestException: time.sleep(1 + i)
    return None

def ts(s): return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
def series(t): return t.split("-")[0]

progs, cursor = [], None
for _ in range(50):
    p = {"type": "liquidity", "status": "active", "limit": 1000}
    if cursor: p["cursor"] = cursor
    d = get("/incentive_programs", p) or {}
    progs += d.get("incentive_programs") or []
    cursor = d.get("next_cursor")
    if not cursor: break
now = dt.datetime.now(dt.timezone.utc)
mine = [p for p in progs if "15M" in series(p.get("market_ticker", ""))]
print(f"{now:%Y-%m-%d %H:%M} UTC · active liquidity programs: {len(progs)} · on 15-minute series: {len(mine)}")
if not mine:
    print("No 15-minute series has a reward program right now. (Metals/oil 15-min markets only run in market hours — try again then.)")
else:
    print("first one, raw:", json.dumps(mine[0])[:500])

def bids(book, side):
    lv = book.get(f"{side}_dollars") or []
    if lv: return sorted(((round(float(p) * 100), float(q)) for p, q in lv), reverse=True)
    return sorted(((int(p), float(q)) for p, q in (book.get(side) or [])), reverse=True)

def side_score(levels, target, disc):
    """Reference price (walking down from the best bid until the size reaches 1/5 of the target) and the score of the
    orders already resting there. (None, None) if the side is too thin to count."""
    cum, ref = 0, None
    for px, q in levels:
        cum += q
        if cum >= target / 5: ref = px; break
    if ref is None: return None, None
    return ref, sum(q * disc ** max(0, ref - px) for px, q in levels)

rows = []
for p in mine:
    start, end = ts(p["start_date"]), ts(p["end_date"])
    hours = max((end - start).total_seconds() / 3600, 1 / 60)
    usd = p["period_reward"] / REWARD_UNIT
    rows.append(dict(t=p["market_ticker"], s=series(p["market_ticker"]), usd=usd, per_hour=usd / hours, hours=hours,
                     start=start, end=end, target=float(p.get("target_size_fp") or 0),
                     disc=p.get("discount_factor_bps", 5000) / 10000, live=start <= now < end))

by_s = collections.defaultdict(list)
for r in rows: by_s[r["s"]].append(r)
print(f"\nPROGRAMS BY SERIES")
print(f"  {'series':16} {'programs':>8} {'reward each':>12} {'program length':>15} {'pool $/h':>9} {'target':>7} {'discount':>8}   covers (UTC)")
for s, q in sorted(by_s.items()):
    q.sort(key=lambda r: r["start"])
    usd = sorted({round(r["usd"], 2) for r in q}); lens = sorted({round(60 * r["hours"]) for r in q})
    print(f"  {s:16} {len(q):8} {'$' + '/'.join(f'{u:g}' for u in usd[:3]):>12} {'/'.join(map(str, lens[:3])) + ' min':>15} "
          f"{max(r['per_hour'] for r in q):9.2f} {q[0]['target']:7.0f} {q[0]['disc']:8.2f}   "
          f"{q[0]['start']:%m-%d %H:%M} -> {max(r['end'] for r in q):%m-%d %H:%M}")

print(f"\nYOUR ESTIMATED REWARD on the market each program is paying for right now, resting at the reference price on both sides")
print(f"  {'market':34} {'pool $/h':>8} {'YES bid/ask':>11} {'others':>8}  " +
      "  ".join(f"{n:>3} each side: $/h (share)" for n in SIZES))
summary = {}
for r in sorted(rows, key=lambda r: (r["s"], r["start"])):
    if not r["live"]: continue
    d = get(f"/markets/{r['t']}/orderbook"); time.sleep(0.07)
    book = (d or {}).get("orderbook_fp") or (d or {}).get("orderbook") or {}
    y, n = bids(book, "yes"), bids(book, "no")
    (ry, sy), (rn, sn) = side_score(y, r["target"], r["disc"]), side_score(n, r["target"], r["disc"])
    if ry is None or rn is None:
        print(f"  {r['t']:34} {r['per_hour']:8.2f}  thin book (a side below 1/5 of the target): the first orders get most of it"); continue
    ask = 100 - n[0][0]
    cells = []
    for k in SIZES:
        share_y, share_n = k / (sy + k), k / (sn + k)
        est = r["per_hour"] / 2 * (share_y + share_n)
        cells.append(f"{est:13.2f} ({100 * (share_y + share_n) / 2:4.1f}%)")
        summary.setdefault(r["s"], {})[k] = summary.get(r["s"], {}).get(k, 0) + est
    print(f"  {r['t']:34} {r['per_hour']:8.2f} {y[0][0]:>5}/{ask:<5} {sy + sn:8.0f}  " + "  ".join(cells))

if summary:
    print("\nPER SERIES, if the next windows look like this one (est $/h with this many contracts on each side of the live market):")
    for s, v in sorted(summary.items()):
        print(f"  {s:16} " + "   ".join(f"{k} each: ${v[k]:.2f}/h" for k in SIZES))
    tot = {k: sum(v[k] for v in summary.values()) for k in SIZES}
    print(f"  {'ALL':16} " + "   ".join(f"{k} each: ${tot[k]:.2f}/h" for k in SIZES))
