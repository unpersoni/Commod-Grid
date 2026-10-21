# ===================== KALSHI REWARD FINDER — where would a small maker earn the most reward? (nothing is traded) =====================
# For the best-paying active liquidity programs, reads each market's order book and estimates what YOUR resting orders
# would earn per hour: Kalshi scores resting bids every second as size x discount^(cents below the reference price) and
# splits each period's reward by score. The estimate uses the book as it is right now (others' orders change), and the
# exact split between the YES and NO sides is not published, so treat $/hour as a guide, not a promise.
import requests, time, datetime as dt

BASE = "https://api.elections.kalshi.com/trade-api/v2"
MY_SIZE = 100          # contracts you would rest on EACH side (YES bid and NO bid)
BOOKS = 300            # how many of the highest-paying markets to check (one order-book read each)
SHOW = 30              # rows to show
REWARD_UNIT = 10000    # period_reward is in 1/100 cents: 1,000,000 = $100

def get(path, params=None):
    for _ in range(3):
        r = requests.get(BASE + path, params=params, timeout=20)
        if r.status_code == 429: time.sleep(1); continue
        return r.json() if r.status_code == 200 else None
    return None

def ts(s): return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))

progs, cursor = [], None
for _ in range(50):
    p = {"type": "liquidity", "status": "active", "limit": 1000}
    if cursor: p["cursor"] = cursor
    d = get("/incentive_programs", p) or {}
    progs += d.get("incentive_programs") or []
    cursor = d.get("next_cursor")
    if not cursor: break
now = dt.datetime.now(dt.timezone.utc)
rows = []
for p in progs:
    end, start = ts(p["end_date"]), ts(p["start_date"])
    hours_left = (end - now).total_seconds() / 3600
    if hours_left < 0.5: continue
    period_h = max((end - start).total_seconds() / 3600, 0.25)
    rows.append(dict(ticker=p["market_ticker"], usd=p["period_reward"] / REWARD_UNIT, per_hour=p["period_reward"] / REWARD_UNIT / period_h,
                     target=float(p.get("target_size_fp") or 0), disc=p.get("discount_factor_bps", 5000) / 10000,
                     hours_left=hours_left, end=end))
print(f"active liquidity programs: {len(progs)}, with 30+ min left: {len(rows)} · total reward on offer "
      f"${sum(r['usd'] for r in rows):,.0f} · e.g. {progs[0]['market_ticker']} pays ${progs[0]['period_reward'] / REWARD_UNIT:,.2f}"
      f" for its period (check this against that market's page on Kalshi)")

def bids(book, side):
    """[(price in cents, size)] best first, from either order-book format."""
    lv = book.get(f"{side}_dollars") or []
    if lv: return sorted(((round(float(p) * 100), float(q)) for p, q in lv), reverse=True)
    return sorted(((int(p), float(q)) for p, q in (book.get(side) or [])), reverse=True)

def side_score(levels, target, disc):
    """Reference price (first level, walking down from the best bid, where the size reaches 1/5 of the target) and
    the score already resting there. None if the side is too thin to count."""
    cum, ref = 0, None
    for px, q in levels:
        cum += q
        if cum >= target / 5: ref = px; break
    if ref is None: return None, None
    return ref, sum(q * disc ** max(0, ref - px) for px, q in levels)

rows.sort(key=lambda r: -r["per_hour"])
seen, out = set(), []
for r in rows:
    if r["ticker"] in seen: continue
    seen.add(r["ticker"])
    if len(seen) > BOOKS: break
    d = get(f"/markets/{r['ticker']}/orderbook")
    time.sleep(0.07)
    if not d: continue
    book = d.get("orderbook_fp") or d.get("orderbook") or {}
    y, n = bids(book, "yes"), bids(book, "no")
    (ry, sy), (rn, sn) = side_score(y, r["target"], r["disc"]), side_score(n, r["target"], r["disc"])
    if ry is None or rn is None:
        r.update(note="thin book (a side below 1/5 of target)", est=None); out.append(r); continue
    share = 2 * MY_SIZE / (sy + sn + 2 * MY_SIZE)
    cap = MY_SIZE * (ry + rn) / 100
    r.update(ref_yes=ry, ref_no=rn, best_yes=y[0][0], best_no=n[0][0], others=sy + sn, share=share,
             est=r["per_hour"] * share, capital=cap, note="")
    out.append(r)

ok = [r for r in out if r.get("est") is not None]
ok.sort(key=lambda r: -r["est"])
print(f"\nYOUR ESTIMATED REWARD with {MY_SIZE} contracts resting on each side at the reference price "
      f"(books read: {len(out)}; thin books skipped: {len(out) - len(ok)})")
print(f"  {'market':38} {'$/h pool':>8} {'your share':>10} {'est $/h':>8} {'capital':>8} {'YES bid':>7} {'NO bid':>6}  ends")
for r in ok[:SHOW]:
    print(f"  {r['ticker']:38} {r['per_hour']:8.2f} {100 * r['share']:9.1f}% {r['est']:8.2f} {r['capital']:8.0f}$ "
          f"{r['best_yes']:6}c {r['best_no']:5}c  {r['end']:%b %d %H:%M} UTC")
best = sorted(ok, key=lambda r: -r["est"] / max(r["capital"], 1))[:SHOW]
print(f"\nBEST PER DOLLAR TIED UP (est $/h per $100 of resting orders):")
for r in best:
    print(f"  {r['ticker']:38} {100 * r['est'] / max(r['capital'], 1):6.2f} $/h per $100 · est {r['est']:.2f} $/h on {r['capital']:.0f}$ "
          f"· book {r['best_yes']}c / {r['best_no']}c")
fifteen = sorted({r["ticker"].split("-")[0] for r in rows if r["ticker"].split("-")[0].endswith("15M")})
for s in fifteen:
    q = [r for r in rows if r["ticker"].startswith(s + "-")]
    print(f"15-min {s}: {len(q)} programs, pool ${max(r['per_hour'] for r in q):.2f}/h at most per market")
