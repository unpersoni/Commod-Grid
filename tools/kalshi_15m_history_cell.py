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
# Public market data: no API key needed. Each market is saved to Drive as soon as it is read (done_<SERIES>.txt lists
# them), so after a Colab stop or crash just run it again: it carries on from the last saved market.
# Rows are grouped by market (in the order markets finished); sort by ticker and time when analysing.
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
def get(path, params=None, quiet=False):
    last = None
    for attempt in range(5 if quiet else 8):
        LIM.take(); stats["requests"] += 1
        try:
            r = sess.get(BASE + path, params=params, timeout=30)
            if r.status_code == 200: return r.json()
            if r.status_code == 404: return None
            last = f"HTTP {r.status_code}: {r.text[:120]}"
        except requests.RequestException as e:
            last = f"{type(e).__name__}"
        stats["retries"] += 1
        time.sleep(min(2 ** attempt, 30))
    if not quiet: say(f"  gave up on {path} ({last}) — this market is listed in incomplete.csv")
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
    """Every trade of one market. Pages are followed by cursor; if a page cannot be read, the rest is asked for again
    by time (from the last trade received) without the cursor, and duplicates are dropped by trade id."""
    t, close = m["ticker"], ts(m["close_time"])
    start = ts(m.get("open_time")) or (close - dt.timedelta(minutes=15))
    lo, hi = int(start.timestamp()) - 60, int(close.timestamp()) + 60
    rows, seen, cursor, resumes = [], set(), None, 0
    for _ in range(2000):
        p = {"ticker": t, "min_ts": lo, "max_ts": hi, "limit": 1000}
        if cursor: p["cursor"] = cursor
        d = get("/markets/trades", p, quiet=True)
        if d is FAILED:
            got = [ts(r[0]) for r in rows if ts(r[0])]
            if resumes >= 5 or not got:
                incomplete.append((t, f"trades after {len(rows)}"))
                say(f"  {t}: some trades could not be read (listed in incomplete.csv)"); break
            resumes += 1; cursor = None
            newest_first = getattr(trades, "newest_first", True)
            if newest_first: hi = int(min(got).timestamp()) + 1        # the rest is older than what we have
            else: lo = int(max(got).timestamp()) - 1                    # the rest is newer than what we have
            continue
        page = (d or {}).get("trades", []) or []
        if len(page) >= 2:
            a, b = ts(page[0].get("created_time")), ts(page[-1].get("created_time"))
            if a and b and a != b: trades.newest_first = a > b
        for tr in page:
            tid = tr.get("trade_id") or json.dumps(tr, sort_keys=True)
            if tid in seen: continue
            seen.add(tid)
            ct = ts(tr.get("created_time"))
            yp = cents(tr, "yes_price")
            rows.append([tr.get("created_time"), t, None if not ct else round((close - ct).total_seconds(), 3),
                         yp, cents(tr, "no_price") if cents(tr, "no_price") is not None else flip(yp),
                         num(tr, "count"), tr.get("taker_side"), tr.get("trade_id")])
        cursor = (d or {}).get("cursor")
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

def append_gz(path, cols, rows):
    """Adds rows to a .csv.gz file (one gzip block per market; the header only when the file is new)."""
    new = not os.path.exists(path)
    with gzip.open(path, "at", newline="") as f:
        w = csv.writer(f)
        if new: w.writerow(cols)
        w.writerows(rows)

def market_row(m, series):
    return [m["ticker"], series, m.get("event_ticker"), m.get("open_time"), m.get("close_time"),
            m.get("floor_strike", m.get("cap_strike")), m.get("result"), int(m.get("result") == "yes"),
            m.get("settlement_value_dollars", m.get("settlement_value")), m.get("expiration_value"),
            num(m, "volume"), num(m, "open_interest")]

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
    donepath, okpath = os.path.join(OUT, f"done_{series}.txt"), os.path.join(OUT, f"complete_{series}.txt")
    if os.path.exists(okpath):
        say(f"{series}: already complete — skipped (delete its files to fetch it again)"); continue
    done_set = set(open(donepath).read().split()) if os.path.exists(donepath) else set()
    ms = settled_markets(series, since)
    todo = [m for m in ms if m["ticker"] not in done_set]
    say(f"{series}: {len(ms)} settled markets" + (f", {len(ms) - len(todo)} already saved" if done_set else "")
        + " — reading minute prices and every trade (each market is saved as soon as it is read)...")
    n_tr, done, t_series = 0, 0, time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(lambda m: (candles(series, m), trades(m)), m): m for m in todo}
        for f in as_completed(futs):
            m = futs.pop(f)                                   # nothing is kept in memory once it is saved
            c, tr = f.result()
            c.sort(key=lambda r: r[0])
            append_gz(cpath, CANDLE_COLS, c); append_gz(tpath, TRADE_COLS, tr); append_gz(bpath, BUCKET_COLS, buckets(m, tr))
            if m["ticker"] not in known:
                new = not os.path.exists(mpath)
                with open(mpath, "a", newline="") as fh:
                    w = csv.writer(fh)
                    if new: w.writerow(MARKET_COLS)
                    w.writerow(market_row(m, series))
                known.add(m["ticker"])
            with open(donepath, "a") as fh: fh.write(m["ticker"] + "\n")
            n_tr += len(tr); done += 1
            del c, tr
            if done % 25 == 0:
                left = (time.time() - t_series) / done * (len(todo) - done) / 60
                say(f"  {series}: {done}/{len(todo)} markets, {n_tr:,} trades so far · about {left:.0f} min left for {series}")
    open(okpath, "w").write(dt.datetime.now(dt.timezone.utc).isoformat())
    say(f"{series}: complete — {n_tr:,} trades in this run")
if incomplete:
    ipath = os.path.join(OUT, "incomplete.csv")
    new = not os.path.exists(ipath)
    with open(ipath, "a", newline="") as f:
        w = csv.writer(f)
        if new: w.writerow(["ticker", "missing", "logged_utc"])
        for t, what in incomplete: w.writerow([t, what, dt.datetime.now(dt.timezone.utc).isoformat()])
say(f"DONE — {stats['requests']:,} requests ({stats['retries']} retried) · {len(incomplete)} market(s) incomplete"
    + (" (listed in incomplete.csv)" if incomplete else "") + f". Files in {OUT}")
