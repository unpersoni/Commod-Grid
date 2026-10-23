# ============================================================
# LIP settlement check — each fill the LIP Maker bot logged (dry run or live), against how Kalshi settled the market.
# Reads lip_maker_v2_fills.csv from Drive; asks Kalshi's public market data (no key needed) for each market's result.
# A YES contract pays $1.00 if the market settled YES; a NO contract pays $1.00 if it settled NO.
# ============================================================
import csv, time, collections, requests

FILLS_CSV = '/content/drive/MyDrive/lip_maker_v2_fills.csv'
BASE = "https://api.elections.kalshi.com/trade-api/v2"

def market(t):
    for i in range(4):
        try:
            r = requests.get(f"{BASE}/markets/{t}", timeout=15)
            if r.status_code == 200: return r.json().get("market") or {}
            if r.status_code == 404: return {}
        except requests.RequestException: pass
        time.sleep(2 ** i)
    return None

rows = [r for r in csv.DictReader(open(FILLS_CSV)) if float(r["contracts"]) >= 0.01]   # skip rounding dust
info = {}
for t in sorted({r["ticker"] for r in rows}):
    info[t] = market(t)

by_day = collections.defaultdict(lambda: [0.0, 0.0, 0])       # day -> [cost $, payout $, open fills]
print(f"{'fill time (UTC)':20} {'market':32} {'bought':12} {'result':9} {'P&L':>8}")
for r in rows:
    t, side, px, n = r["ticker"], r["side"].lower(), int(r["price_c"]), float(r["contracts"])
    m = info.get(t)
    res = (m or {}).get("result") or ""
    day = t.split("-")[1]
    cost = n * px / 100
    if res in ("yes", "no"):
        pay = n * (1.0 if res == side else 0.0)
        by_day[day][0] += cost; by_day[day][1] += pay
        pnl = f"{pay - cost:+8.2f}"
    else:
        by_day[day][2] += 1
        pnl = "     ?"
        res = (m or {}).get("status", "unknown") if m is not None else "no answer"
    live = " LIVE" if r.get("live") == "1" else ""
    print(f"{r['fill_iso'][:19]:20} {t:32} {n:g} {side.upper()} @{px}c{'':<1} {res:9} {pnl}{live}")

print("\nSETTLED FILL P&L BY MARKET DAY")
tot = 0.0
for day, (cost, pay, open_n) in sorted(by_day.items()):
    tot += pay - cost
    print(f"  {day}: paid ${cost:.2f}, settled ${pay:.2f}  ->  {pay - cost:+.2f}" +
          (f"   ({open_n} fill(s) not settled yet)" if open_n else ""))
print(f"  all days: {tot:+.2f}")
