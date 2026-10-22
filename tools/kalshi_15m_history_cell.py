# ===================== KALSHI 15-MINUTE HISTORY — a week of every trade and minute prices, 11 markets (nothing is traded) =====================
# For every settled 15-minute market of the last DAYS days in the 11 series, saves to Drive (OUT folder):
#   markets.csv                 one row per market: open/close time, strike, result (yes = UP won), settlement value, volume
#   candles_<SERIES>.csv.gz     one row per market per minute: best UP (YES) and DOWN (NO) bid/ask (open/high/low/close),
#                               traded price (open/high/low/close/mean), volume, open interest, seconds to close
#   trades_<SERIES>.csv.gz      every trade: time, seconds to close, UP and DOWN price, contracts, which side the taker bought
#   prices15s_<SERIES>.csv.gz   every 15 seconds of every market, built from the trades: last traded UP / DOWN price
#                               (carried forward when nothing traded), high / low in those 15 s, trades, contracts bought
#                               on each side. (Kalshi's own price history is per minute at the finest.)
# DOWN prices are the mirror of UP: DOWN bid = 100 - UP ask, DOWN ask = 100 - UP bid, DOWN trade price = 100 - UP price.
# Kalshi does not keep the full order book history (only live), so minute bid/ask + every trade is what history offers.
# Public market data: no API key needed. A series already saved is skipped, so after a Colab stop just run it again.
# incomplete.csv lists any market whose minute prices or trades could not all be read (leave them out of research).
import os, io, csv, gzip, json, time, threading, datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

DAYS = 7
SERIES = ["KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M",
          "KXGOLD15M", "KXSILVER15M", "KXWTI15M", "KXCOPPER15M", "KXPLATINUM15M", "KXPALLADIUM15M"]
OUT = "/content/drive/MyDrive/kalshi_15m_history/"
WORKERS = 8                  # markets fetched at once
MAX_REQ_PER_SEC = 15         # stays under Kalshi's read limit
BASE = "https://api.elections.kalshi.com/trade-api/v2"

T0 = time.time()
def say(msg): print(f"[{(time.time() - T0) / 60:5.1f} min] {msg}", flush=True)

class Limiter:
    def __init__(self, rate): self.rate, self.times, self.lock = rate, [], threading.Lock()
    def take(self):
        while True:
            with self.lock:
                t = time.monotonic()
                self.times = [x for x in self.times if t - x < 1.0]
                if len(self.times) < self.rate: self.times.append(t); return
            time.sleep(0.02)
LIM = Limiter(MAX_REQ_PER_SEC)
sess = requests.Session()
sess.mount("https://", requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=WORKERS + 4))
stats = {"requests": 0, "retries": 0}

FAILED = object()            # get() gave up (not the same as "nothing there")
def get(path, params=None):
    last = None
    for attempt in range(8):
        LIM.take(); stats["requests"] += 1
        try:
            r = sess.get(BASE + path, params=params, timeout=20)
            if r.status_code == 200: return r.json()
            if r.status_code == 404: return None
            last = f"HTTP {r.status_code}: {r.text[:120]}"
        except requests.RequestException as e:
            last = f"{type(e).__name__}"
        stats["retries"] += 1
        time.sleep(min(2 ** attempt, 30))
    say(f"  gave up on {path} ({last}) — this market is listed in incomplete.csv")
    return FAILED

def cents(d, key):
    """A price in cents from Kalshi's '<key>' (cents) or '<key>_dollars' ("0.5600") forms."""
    if not isinstance(d, dict): return None
    for k, mult in ((key + "_dollars", 100), (key, 1)):
        v = d.get(k)
        if v in (None, ""): continue
        try:
            f = float(v)
            return round(f * mult) if mult == 100 else (round(f * 100) if (0 < f < 1 and f != int(f)) else round(f))
        except (TypeError, ValueError): pass
    return None

def num(d, key):
    for k in (key + "_fp", key):
        v = (d or {}).get(k)
        if v not in (None, ""):
            try: return float(v)
            except (TypeError, ValueError): pass
    return None

def ts(s):
    try: return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError): return None

def settled_markets(series, since):
    out, cursor = [], None
    for _ in range(100):
        p = {"series_ticker": series, "status": "settled", "min_close_ts": since, "limit": 1000}
        if cursor: p["cursor"] = cursor
        d = get("/markets", p)
        if d is FAILED: say(f"  {series}: the list of markets could not be read completely"); break
        out += [m for m in (d or {}).get("markets", []) or [] if m.get("result") in ("yes", "no")]
        cursor = d.get("cursor")
        if not cursor: break
    return out

def flip(x): return None if x is None else 100 - x

def candles(series, m):
    t, close = m["ticker"], ts(m["close_time"])
    start = ts(m.get("open_time")) or (close - dt.timedelta(minutes=15))
    d = get(f"/series/{series}/markets/{t}/candlesticks",
            {"start_ts": int(start.timestamp()), "end_ts": int(close.timestamp()), "period_interval": 1})
    if d is FAILED: incomplete.append((t, "minute prices")); d = {}
    d = d or {}
    rows = []
    for c in d.get("candlesticks", []) or []:
        end = c.get("end_period_ts")
        if end is None: continue
        yb, ya, pr = c.get("yes_bid"), c.get("yes_ask"), c.get("price")
        b = {k: cents(yb, k) for k in ("open", "high", "low", "close")}
        a = {k: cents(ya, k) for k in ("open", "high", "low", "close")}
        rows.append([dt.datetime.fromtimestamp(end, dt.timezone.utc).isoformat(), t, int(close.timestamp() - end),
                     b["open"], b["high"], b["low"], b["close"], a["open"], a["high"], a["low"], a["close"],
                     flip(a["open"]), flip(a["low"]), flip(a["high"]), flip(a["close"]),      # DOWN bid = 100 - UP ask
                     flip(b["open"]), flip(b["low"]), flip(b["high"]), flip(b["close"]),      # DOWN ask = 100 - UP bid
                     cents(pr, "open"), cents(pr, "high"), cents(pr, "low"), cents(pr, "close"), cents(pr, "mean"),
                     num(c, "volume"), num(c, "open_interest")])
    return rows

def trades(m):
    t, close = m["ticker"], ts(m["close_time"])
    start = ts(m.get("open_time")) or (close - dt.timedelta(minutes=15))
    rows, cursor = [], None
    for _ in range(500):
        p = {"ticker": t, "min_ts": int(start.timestamp()) - 60, "max_ts": int(close.timestamp()) + 60, "limit": 1000}
        if cursor: p["cursor"] = cursor
        d = get("/markets/trades", p)
        if d is FAILED: incomplete.append((t, f"trades after {len(rows)}")); break
        d = d or {}
        for tr in d.get("trades", []) or []:
            ct = ts(tr.get("created_time"))
            yp = cents(tr, "yes_price")
            rows.append([tr.get("created_time"), t, None if not ct else round((close - ct).total_seconds(), 3),
                         yp, cents(tr, "no_price") if cents(tr, "no_price") is not None else flip(yp),
                         num(tr, "count"), tr.get("taker_side"), tr.get("trade_id")])
        cursor = d.get("cursor")
        if not cursor: break
    rows.sort(key=lambda r: r[0] or "")
    return rows

BUCKET_SEC = 15
def buckets(m, trs):
    """One row per BUCKET_SEC of the market, from its trades (trs: rows as built by trades(), sorted by time)."""
    close = ts(m["close_time"])
    start = ts(m.get("open_time")) or (close - dt.timedelta(minutes=15))
    n = max(1, int((close - start).total_seconds() // BUCKET_SEC))
    rows, last, i = [], None, 0
    for b in range(n):
        end = start + dt.timedelta(seconds=BUCKET_SEC * (b + 1))
        hi = lo = None; cnt = 0; up_ct = down_ct = 0.0
        while i < len(trs):
            t_ = ts(trs[i][0])
            if t_ is None or t_ > end: break
            p, c, side = trs[i][3], trs[i][5] or 0, trs[i][6]
            if p is not None:
                last = p; hi = p if hi is None else max(hi, p); lo = p if lo is None else min(lo, p)
            cnt += 1
            if side == "yes": up_ct += c
            elif side == "no": down_ct += c
            i += 1
        rows.append([end.isoformat(), m["ticker"], int((close - end).total_seconds()), last, flip(last), hi, lo,
                     cnt, up_ct, down_ct])
    return rows

BUCKET_COLS = ["bucket_end_utc", "ticker", "sec_to_close", "up_last", "down_last", "up_high", "up_low", "trades",
               "contracts_taker_bought_up", "contracts_taker_bought_down"]
CANDLE_COLS = ["minute_end_utc", "ticker", "sec_to_close",
               "up_bid_open", "up_bid_high", "up_bid_low", "up_bid_close", "up_ask_open", "up_ask_high", "up_ask_low", "up_ask_close",
               "down_bid_open", "down_bid_high", "down_bid_low", "down_bid_close", "down_ask_open", "down_ask_high", "down_ask_low", "down_ask_close",
               "price_open", "price_high", "price_low", "price_close", "price_mean", "volume", "open_interest"]
TRADE_COLS = ["time_utc", "ticker", "sec_to_close", "up_price", "down_price", "contracts", "taker_bought", "trade_id"]
MARKET_COLS = ["ticker", "series", "event_ticker", "open_time", "close_time", "strike", "result", "up_won",
               "settlement_value", "expiration_value", "volume", "open_interest"]

def write_gz(path, cols, rows):
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", newline="") as f:
        w = csv.writer(f); w.writerow(cols); w.writerows(rows)
    os.replace(tmp, path)

incomplete = []
os.makedirs(OUT, exist_ok=True)
since = int(time.time()) - DAYS * 86400
mpath = os.path.join(OUT, "markets.csv")
known = set()
if os.path.exists(mpath):
    with open(mpath) as f: known = {r["ticker"] for r in csv.DictReader(f)}
say(f"fetching {DAYS} days of the 11 fifteen-minute series into {OUT}")
for series in SERIES:
    cpath, tpath = os.path.join(OUT, f"candles_{series}.csv.gz"), os.path.join(OUT, f"trades_{series}.csv.gz")
    bpath = os.path.join(OUT, f"prices15s_{series}.csv.gz")
    if os.path.exists(cpath) and os.path.exists(tpath) and os.path.exists(bpath):
        say(f"{series}: already saved — skipped (delete its files to fetch it again)"); continue
    ms = settled_markets(series, since)
    say(f"{series}: {len(ms)} settled markets — reading minute prices and every trade...")
    crow, trow, brow, done = [], [], [], 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(lambda m: (candles(series, m), trades(m)), m): m for m in ms}
        for f in as_completed(futs):
            c, tr = f.result(); crow += c; trow += tr; brow += buckets(futs[f], tr); done += 1
            if done % 100 == 0: say(f"  {series}: {done}/{len(ms)} markets, {len(trow):,} trades so far")
    crow.sort(key=lambda r: (r[1], r[0])); trow.sort(key=lambda r: (r[1], r[0] or "")); brow.sort(key=lambda r: (r[1], r[0]))
    write_gz(cpath, CANDLE_COLS, crow); write_gz(tpath, TRADE_COLS, trow); write_gz(bpath, BUCKET_COLS, brow)
    new = [m for m in ms if m["ticker"] not in known]
    with open(mpath, "a", newline="") as f:
        w = csv.writer(f)
        if not known and f.tell() == 0: w.writerow(MARKET_COLS)
        for m in sorted(new, key=lambda m: m["close_time"]):
            w.writerow([m["ticker"], series, m.get("event_ticker"), m.get("open_time"), m.get("close_time"),
                        m.get("floor_strike", m.get("cap_strike")), m.get("result"), int(m.get("result") == "yes"),
                        m.get("settlement_value_dollars", m.get("settlement_value")), m.get("expiration_value"),
                        num(m, "volume"), num(m, "open_interest")])
            known.add(m["ticker"])
    say(f"{series}: saved {len(crow):,} minute rows, {len(trow):,} trades and {len(brow):,} 15-second rows")
if incomplete:
    ipath = os.path.join(OUT, "incomplete.csv")
    new = not os.path.exists(ipath)
    with open(ipath, "a", newline="") as f:
        w = csv.writer(f)
        if new: w.writerow(["ticker", "missing", "logged_utc"])
        for t, what in incomplete: w.writerow([t, what, dt.datetime.now(dt.timezone.utc).isoformat()])
say(f"DONE — {stats['requests']:,} requests ({stats['retries']} retried) · {len(incomplete)} market(s) incomplete"
    + (" (listed in incomplete.csv)" if incomplete else "") + f". Files in {OUT}")
