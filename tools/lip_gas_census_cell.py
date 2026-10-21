# ===================== GAS MARKETS CENSUS — how many AAA gas reward markets, by state, and what they could pay =====================
# Reads Kalshi's active liquidity programs for the AAA daily gas markets (KXAAAGASD...) and each market's order book.
# "Quotable" = what LIP Maker V1 would quote: both best bids 5-95c and both sides deep enough to count for the reward.
# Estimate per market with CONTRACTS on each side, scored like the bot does (each side against half the pool),
# using the books as they are right now. Nothing is traded.
import requests, time, collections, datetime as dt

BASE = "https://api.elections.kalshi.com/trade-api/v2"
PREFIX = "KXAAAGASD"
CONTRACTS = 10
REWARD_UNIT = 10000

def get(path, params=None):
    for _ in range(3):
        r = requests.get(BASE + path, params=params, timeout=20)
        if r.status_code == 429: time.sleep(1); continue
        return r.json() if r.status_code == 200 else None
    return None

def ts(s): return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))

def levels(d, side):
    book = (d or {}).get("orderbook_fp") or (d or {}).get("orderbook") or {}
    lv = book.get(f"{side}_dollars") or []
    rows = [(round(float(p) * 100), float(q)) for p, q in lv] if lv else [(int(p), float(q)) for p, q in (book.get(side) or [])]
    return sorted([(p, q) for p, q in rows if q > 0.005], reverse=True)

progs, cursor = [], None
for _ in range(50):
    p = {"type": "liquidity", "status": "active", "limit": 1000}
    if cursor: p["cursor"] = cursor
    d = get("/incentive_programs", p) or {}
    progs += d.get("incentive_programs") or []
    cursor = d.get("next_cursor")
    if not cursor: break
now = dt.datetime.now(dt.timezone.utc)
gas = {}
for p in progs:
    t = p.get("market_ticker", "")
    if not t.startswith(PREFIX): continue
    start, end = ts(p["start_date"]), ts(p["end_date"])
    if end <= now: continue
    per_h = p["period_reward"] / REWARD_UNIT / max((end - start).total_seconds() / 3600, 0.25)
    if t not in gas or per_h > gas[t]["per_h"]:
        gas[t] = dict(per_h=per_h, target=float(p.get("target_size_fp") or 0), disc=p.get("discount_factor_bps", 5000) / 10000)
print(f"AAA gas reward markets right now: {len(gas)} (reading their order books...)")

by_state = collections.defaultdict(lambda: {"markets": 0, "quotable": 0, "pool": 0.0, "est": 0.0, "cap": 0.0})
rows = []
for t, g in gas.items():
    st = t.split("-")[0][len(PREFIX):]
    b = by_state[st]; b["markets"] += 1; b["pool"] += g["per_h"]
    d = get(f"/markets/{t}/orderbook"); time.sleep(0.06)
    est, cap, ok = 0.0, 0.0, True
    for side in ("yes", "no"):
        lv = levels(d, side)
        if not lv or not (5 <= lv[0][0] <= 95): ok = False; break
        cum, ref = 0, None
        for px, q in lv:
            cum += q
            if cum >= g["target"] / 5: ref = px; break
        if ref is None: ok = False; break
        sc = sum(q * g["disc"] ** max(0, ref - px) for px, q in lv)
        est += g["per_h"] / 2 * CONTRACTS / (sc + CONTRACTS)
        cap += CONTRACTS * ref / 100
    if ok:
        b["quotable"] += 1; b["est"] += est; b["cap"] += cap
        rows.append((est, cap, t, g["per_h"]))

print(f"\n{'state':6} {'markets':>7} {'quotable':>8} {'pool $/h':>9} {'est $/h':>8} {'capital':>8}   (est and capital: quotable markets, {CONTRACTS} per side)")
for st, b in sorted(by_state.items(), key=lambda kv: -kv[1]["est"]):
    print(f"{st:6} {b['markets']:7} {b['quotable']:8} {b['pool']:9.2f} {b['est']:8.2f} {b['cap']:7.0f}$")
q = sorted(rows, reverse=True)
tot_est, tot_cap = sum(r[0] for r in q), sum(r[1] for r in q)
print(f"\nTOTAL: {len(gas)} gas reward markets in {len(by_state)} states · {len(q)} quotable now · all quotable at {CONTRACTS}/side: "
      f"est ${tot_est:.2f}/h (${24 * tot_est:,.0f}/day if this held all day) on ${tot_cap:,.0f} tied up")
for n in (3, 10, 20, 40):
    if len(q) >= n:
        e, c = sum(r[0] for r in q[:n]), sum(r[1] for r in q[:n])
        print(f"  best {n:2} markets: est ${e:.2f}/h (${24 * e:,.0f}/day) on ${c:,.0f} tied up")
print("\nBEST 15 MARKETS:")
for e, c, t, ph in q[:15]:
    print(f"  {t:34} pool ${ph:5.2f}/h · est ${e:.2f}/h · ${c:.0f} tied up")
