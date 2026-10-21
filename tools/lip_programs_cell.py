# ===================== KALSHI LIQUIDITY REWARDS — which markets pay makers, and how much (nothing is traded) =====================
# Paste into a new Colab cell and run. Reads Kalshi's public list of liquidity incentive programs (GET /incentive_programs):
# the markets that pay for resting orders, the reward per period, the target size and the discount factor.
# These are Kalshi's own published numbers, not a backtest.
import requests, collections, json, datetime as dt

BASE = "https://api.elections.kalshi.com/trade-api/v2"
SHOW = 40                                   # how many of the best-paying programs to list

def fetch(status):
    out, cursor = [], None
    for _ in range(50):                     # up to 50 pages
        p = {"type": "liquidity", "status": status, "limit": 1000}
        if cursor: p["cursor"] = cursor
        r = requests.get(f"{BASE}/incentive_programs", params=p, timeout=20)
        if r.status_code != 200:
            print(f"Kalshi answered {r.status_code}: {r.text[:300]}"); break
        d = r.json()
        out += d.get("incentive_programs") or d.get("programs") or []
        cursor = d.get("next_cursor") or d.get("cursor")
        if not cursor: break
    return out

def num(x):
    try: return float(x)
    except (TypeError, ValueError): return None

def reward_usd(p):
    """The program's reward in dollars, from whichever field Kalshi sends."""
    for k in ("period_reward_dollars", "reward_dollars", "total_reward_dollars"):
        if num(p.get(k)) is not None: return num(p[k])
    for k in ("period_reward", "reward", "total_reward"):          # in 1/100 cents: 1,000,000 = $100
        if num(p.get(k)) is not None: return num(p[k]) / 10000
    return None

progs = fetch("active")
print(f"active liquidity programs: {len(progs)}")
if progs:
    print("fields Kalshi sends:", ", ".join(sorted(progs[0].keys())))
    print("first one, raw:", json.dumps(progs[0])[:600])
    tick = lambda p: p.get("market_ticker") or p.get("ticker") or "?"
    series = lambda p: tick(p).split("-")[0]
    by_series = collections.defaultdict(lambda: [0, 0.0])
    for p in progs:
        s = by_series[series(p)]; s[0] += 1; s[1] += reward_usd(p) or 0
    print("\nBY SERIES (programs, total reward $):")
    for s, (n, usd) in sorted(by_series.items(), key=lambda kv: -kv[1][1])[:60]:
        print(f"  {s:28} {n:5} programs   ${usd:10,.2f}")
    fifteen = [s for s in by_series if s.endswith("15M")]
    print("\n15-minute series with liquidity rewards:", ", ".join(sorted(fifteen)) if fifteen else "NONE")
    print(f"\nTOP {SHOW} PROGRAMS BY REWARD:")
    for p in sorted(progs, key=lambda p: -(reward_usd(p) or 0))[:SHOW]:
        print(f"  {tick(p):40} reward ${reward_usd(p) or 0:9,.2f}  target {p.get('target_size_fp', p.get('target_size'))}  "
              f"discount {p.get('discount_factor_bps', p.get('discount_factor'))}  "
              f"{p.get('start_date', p.get('start_ts', ''))} -> {p.get('end_date', p.get('end_ts', ''))}")
