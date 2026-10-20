# ============================================================
# Commod15min V39 "open favourite" (V38b V3 + inverse markets) — DRY RUN by default (DRY_RUN = False trades LIVE)
#
# STRATEGY: in the first 60 seconds of each market, buy 10 contracts of the side whose ask is 56-95c
#   (quick order). One buy per market, no buy if the spread is over 5c. Hold to settlement ($1 or $0).
#
# SWITCHES (the SWITCHES cell in the notebook; in this file, set them before main() runs):
#   DRY_RUN = True            orders are built, signed and timed but NOT sent (fills simulated). False = LIVE.
#   TRADE_CRYPTO = True       BTC, ETH, SOL, XRP, DOGE        (False = don't trade any crypto market)
#   TRADE_COMMODITIES = True  GOLD, SILVER, WTI, COPPER, PLATINUM, PALLADIUM   (False = none of them)
#   INVERSE_MARKETS = ["WTI"] these markets buy the UNDERDOG instead: when one side's ask is 56-95c, buy the OTHER
#                             side at its ask (WTI's favourite lost in every source; replay: favourite -6.9c,
#                             underdog +2.2c per contract). Shown on their own "underdog" line in the status.
#   SKIP_MARKETS = []         turn off single markets by name, e.g. SKIP_MARKETS = ["WTI", "DOGE"]
#   Markets switched off are still followed in DRY RUN (nothing sent), so every market's results stay visible.
#
# HISTORY cell (run it any time the bot is stopped): how V38b's rule did in the last 5 cycles, rebuilt from
#   Kalshi's market list and 1-minute price history: per class, per price band and per market with streaks.
#   An estimate; nothing is traded.
# LOG: every 15 minutes, crypto and commodities each in a 🟢 LIVE section (green: real orders) and a ⚪ DRY RUN
#   section: totals, price bands, and per market: win rate, net, current streak (W3 = 3 wins in a row), last 10 results.
#
# QUICK ORDERS: fill-or-kill with a real limit (ask seen + 2c): all 10 fill at or below it, or nothing fills and
#   nothing is paid; a killed order is retried while still in the first 60 s (at most 10 tries per market).
#   SPEED (Commod15min V21-V25 lessons): the buy fires on the price update itself; one sender thread per market;
#   one kept-alive connection warmed up at start; no balance lookup before a buy; timing + slippage logged per order.
# LIVE SAFETY: 10-second countdown before the first live order · balance checked at start, and the trading shard
#   kept topped up from shard 0 in the background (never during a trade) · an order with no clear answer from
#   Kalshi is looked up instead of retried (no double buys) · no action within 2 s of the close or with an unknown
#   close time · daily loss stop: after a whole 15-minute round has settled, if today's net is below -$50, no new
#   buys until midnight UTC (no limit on gains; in dry run it is only reported) · kill switch: create
#   STOP_V38B.txt in My Drive · at most 10 order actions per second. Positions already bought always ride to settlement.
# No files are written: everything is in this log (status every 15 minutes).
# ============================================================

import os, sys, collections, csv, json, time, math, base64, threading, uuid, datetime as dt
from concurrent.futures import ThreadPoolExecutor
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
try:
    import websocket  # websocket-client package
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "websocket-client"], check=True)
    import websocket



# ---------------- settings ----------------
BOT_NAME  = "Commod15min V39"
# The SWITCHES cell (the cell that starts the bot) sets these. The values here are only used when that cell has
# not been run, so leaving it out can never turn live trading on.
_sw = globals()
DRY_RUN           = _sw.get("DRY_RUN", True)            # True = nothing is sent.  False = LIVE, REAL MONEY.
TRADE_CRYPTO      = _sw.get("TRADE_CRYPTO", True)       # BTC, ETH, SOL, XRP, DOGE
TRADE_COMMODITIES = _sw.get("TRADE_COMMODITIES", True)  # GOLD, SILVER, WTI, COPPER, PLATINUM, PALLADIUM
INVERSE_MARKETS   = _sw.get("INVERSE_MARKETS", ["WTI"])   # these markets buy the UNDERDOG (the side that isn't the favourite)
SKIP_MARKETS      = _sw.get("SKIP_MARKETS", [])         # single markets not to trade, by name, e.g. ["WTI", "DOGE"]
CONTRACTS         = _sw.get("CONTRACTS", 10)            # per buy
DAILY_LOSS_LIMIT_USD = _sw.get("DAILY_LOSS_LIMIT_USD", 50)  # checked once each 15-minute round has settled; no limit on gains

OPEN_WINDOW_SEC = 60      # buy only in the first 60 seconds of the market
OPEN_MIN_C      = 56      # buy the side whose ask is 56c ...
OPEN_MAX_C      = 95      # ... to 95c
MAX_SPREAD_C    = 5       # no buy when ask - bid is wider than this
MAX_SLIPPAGE_C  = 2       # fill-or-kill limit = ask seen + 2c (never above 99c)
MAX_TRIES_PER_MARKET = 10 # killed orders are retried while still in the window, at most this many tries
LIVE_COUNTDOWN_SEC   = 10 # live: seconds to stop the cell before the bot starts
COLORS = True             # live lines in green in the log (False if you see odd characters like [1;32m)
KILL_FILE = '/content/drive/MyDrive/STOP_V38B.txt'      # create this file on Drive: no new buys

PEM_PATH  = '/content/drive/MyDrive/intraday key.pem'
KEY_ID    = "__KEY_ID__"

# balance shards: Kalshi keeps the 15-minute markets' money on its own shard (shard 2 in the Commod15min V2x bots).
# Live: if that shard holds less than KEEP_ON_SHARD_USD, everything on shard 0 is moved there (checked every 30 s).
TRADING_SHARD     = 2
AUTO_MOVE_FUNDS   = True
KEEP_ON_SHARD_USD = 25

SERIES = ["KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M",
          "KXGOLD15M", "KXSILVER15M", "KXWTI15M", "KXCOPPER15M",
          "KXPLATINUM15M", "KXPALLADIUM15M"]
PRICE_BANDS     = [(56, 65), (66, 70), (71, 80), (81, 95)]   # opening-price bands, for the totals only
DEADBAND_SEC    = 2       # never act this close to the close

MAX_WRITES_PER_SEC = 10            # Kalshi Basic API tier
ORDER_WORKERS      = len(SERIES)   # one sender thread per market
LATENCY_PROBE_SEC  = 60
SHOW_REQUESTS      = 5             # print the exact request (and, live, Kalshi's raw answer) of the first 5 orders
ORDER_CREATE_PATH  = "/portfolio/events/orders"
ORDERS_LIST_PATH   = "/portfolio/orders"
TRANSFER_PATH      = "/portfolio/intra_exchange_instance_transfer"

STATUS_EVERY_SEC    = 900
MARKETS_REFRESH_SEC = 5
LOOP_SEC      = 0.5
SETTLE_RETRY_SEC  = 10
SETTLE_GIVEUP_SEC = 1800

BASE    = "https://api.elections.kalshi.com/trade-api/v2"
PREF    = "/trade-api/v2"
WS_URL  = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
WS_PATH = "/trade-api/ws/v2"
# ---------------- helpers ----------------
def asset(t): return t.split("15M")[0].replace("KX", "")
def series_of(t): return t.split("-")[0]
def now(): return dt.datetime.now(dt.timezone.utc)
def now_iso(): return now().isoformat()
def parse_iso(s):
    try: return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError): return None

def taker_fee_c(n, price_c):
    """Kalshi taker fee for one order: 0.07 x contracts x P x (1-P), rounded up to the cent."""
    return math.ceil(0.07 * n * price_c * (100 - price_c) / 100 - 1e-9)

def px_c(m, name):
    """Price field in cents. Kalshi sends '<name>_dollars' ("0.8900") and/or
    '<name>' in whole cents (89)."""
    v = m.get(name + "_dollars")
    if v is not None:
        try: return round(float(v) * 100)
        except (TypeError, ValueError): pass
    v = m.get(name)
    if v is None: return None
    try: return int(round(float(v)))
    except (TypeError, ValueError): return None

def load_key():
    p = os.path.expanduser(PEM_PATH)
    if not os.path.exists(p): sys.exit(f"PEM not found: {p}")
    return serialization.load_pem_private_key(open(p, "rb").read(), password=None)

def _sign(k, ts, method, path):
    return base64.b64encode(k.sign(f"{ts}{method}{path}".encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256())).decode()

def _hdrs(k, method, path):
    ts = str(int(time.time() * 1000))
    return {"KALSHI-ACCESS-KEY": KEY_ID, "KALSHI-ACCESS-SIGNATURE": _sign(k, ts, method, PREF + path),
            "KALSHI-ACCESS-TIMESTAMP": ts, "Accept": "application/json"}

def api_get(sess, k, path, params=None):
    for attempt in range(3):
        try:
            r = sess.get(BASE + path, headers=_hdrs(k, "GET", path), params=params, timeout=8)
            if r.status_code == 404: return None
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(0.5 * (attempt + 1)); continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            time.sleep(0.5 * (attempt + 1))
    return None

def _fetch_series_open(sess, k, ser):
    d = api_get(sess, k, "/markets", {"series_ticker": ser, "status": "open", "limit": 100}) or {}
    return d.get("markets", []) or []

def fetch_open(sess, k):
    out = []
    with ThreadPoolExecutor(max_workers=len(SERIES)) as ex:
        for res in ex.map(lambda s: _fetch_series_open(sess, k, s), SERIES):
            out += res
    return out

def fetch_market(sess, k, ticker):
    d = api_get(sess, k, f"/markets/{ticker}")
    return (d or {}).get("market", {}) if d else {}

# ---------------- Kalshi websocket: prices + trade prints ----------------
class PriceFeed:
    CHANNELS = ("ticker", "trade")

    def __init__(self, k):
        self.k = k
        self.prices = {}
        self.lock = threading.RLock()
        self.send_lock = threading.Lock()
        self.tracked = set()
        self.subscribed = set()
        self.sub_pending = False
        self.sids = {}
        self.ws = None
        self.ready = threading.Event()
        self.stop_flag = threading.Event()
        self._next_id = 1
        self.connect_count = 0
        self.on_tick = None    # on_tick(ticker, yes_bid, yes_ask)
        self.on_trade = None   # on_trade(ticker, yes_price, count, taker_side)

    def _cmd_id(self):
        with self.lock:
            i = self._next_id; self._next_id += 1
        return i

    def _headers(self):
        ts = str(int(time.time() * 1000))
        return [f"KALSHI-ACCESS-KEY: {KEY_ID}",
                f"KALSHI-ACCESS-SIGNATURE: {_sign(self.k, ts, 'GET', WS_PATH)}",
                f"KALSHI-ACCESS-TIMESTAMP: {ts}"]

    def start(self): threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self.stop_flag.set()
        try:
            if self.ws: self.ws.close()
        except Exception: pass

    def _run(self):
        backoff = 1
        while not self.stop_flag.is_set():
            try:
                self.ready.clear()
                ws = websocket.create_connection(WS_URL, header=self._headers(), timeout=15)
                with self.lock:
                    self.ws, self.sids = ws, {}
                    self.subscribed, self.sub_pending = set(), False
                    self.prices.clear()      # fresh snapshot comes with the new subscription
                self.connect_count += 1
                backoff = 1
                self._sync()
                print(f"[ws] connected (#{self.connect_count})")
                self.ready.set()
                while not self.stop_flag.is_set():
                    raw = ws.recv()
                    if raw: self._on_message(raw)
            except Exception as e:
                if self.stop_flag.is_set(): break
                self.ready.clear()
                try:
                    if self.ws: self.ws.close()
                except Exception: pass
                with self.lock:
                    self.ws, self.sids = None, {}
                    self.subscribed, self.sub_pending = set(), False
                print(f"[ws] disconnected ({e}) — reconnecting in {backoff:.0f}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)

    def _send(self, obj):
        with self.send_lock:
            if self.ws: self.ws.send(json.dumps(obj))

    def _subscribe(self, tickers):
        self.subscribed, self.sub_pending = set(tickers), True
        for ch in self.CHANNELS:
            p = {"channels": [ch], "market_tickers": sorted(tickers)}
            if ch == "ticker": p["send_initial_snapshot"] = True
            self._send({"id": self._cmd_id(), "cmd": "subscribe", "params": p})

    def _on_message(self, raw):
        try: msg = json.loads(raw)
        except (json.JSONDecodeError, TypeError): return
        mtype = msg.get("type")
        m = msg.get("msg") or {}
        if mtype == "subscribed":
            if m.get("channel") in self.CHANNELS: self.sids[m["channel"]] = m.get("sid")
            if len(self.sids) == len(self.CHANNELS) and self.sub_pending:
                self.sub_pending = False
                self._sync()      # catch up on markets added while waiting
        elif mtype == "ticker":
            t = m.get("market_ticker")
            if t not in self.tracked: return        # only our markets (Kalshi can send others)
            yb, ya = px_c(m, "yes_bid"), px_c(m, "yes_ask")
            if not t or yb is None or ya is None: return
            with self.lock:
                self.prices[t] = (yb, ya, time.time())
            if self.on_tick:
                try: self.on_tick(t, yb, ya)
                except Exception as e: print(f"[tick-error] {e}")
        elif mtype == "trade":
            t = m.get("market_ticker")
            if t not in self.tracked: return
            yp = px_c(m, "yes_price")
            n = m.get("count", m.get("count_fp"))
            try: n = int(round(float(n)))
            except (TypeError, ValueError): n = None
            side = m.get("taker_side")
            if not t or yp is None or not n or side not in ("yes", "no"): return
            if self.on_trade:
                try: self.on_trade(t, yp, n, side)
                except Exception as e: print(f"[trade-error] {e}")
        elif mtype == "error":
            print(f"[ws-error] {m}")

    def track(self, tickers):
        with self.lock:
            self.tracked = set(tickers)
        self._sync()

    def _sync(self):
        """Bring the live subscription in line with self.tracked."""
        with self.lock:
            if self.ws is None or self.sub_pending: return
            want = set(self.tracked)
            if not want: return                      # never leave a subscription with no markets
            if len(self.sids) < len(self.CHANNELS):
                if want: self._subscribe(want)
                return
            add, remove = want - self.subscribed, self.subscribed - want
            self.subscribed = want
        for ch in self.CHANNELS:
            sid = self.sids.get(ch)
            if add:
                self._send({"id": self._cmd_id(), "cmd": "update_subscription",
                            "params": {"sid": sid, "market_tickers": sorted(add), "action": "add_markets"}})
            if remove:
                self._send({"id": self._cmd_id(), "cmd": "update_subscription",
                            "params": {"sid": sid, "market_tickers": sorted(remove), "action": "delete_markets"}})

    def get(self, ticker):
        """(yes_bid, yes_ask) while the feed is connected, else None. Kalshi only
        sends a ticker update when the price changes, so a quiet market's last
        price is still current as long as the connection is up."""
        if not self.ready.is_set(): return None
        with self.lock:
            d = self.prices.get(ticker)
        return (d[0], d[1]) if d else None

def make_feed(k):
    f = PriceFeed(k); f.start(); return f

# ---------------- order path helpers ----------------
class RateLimiter:
    """Never more than `rate` order actions in ANY 1-second span (a strict sliding window).
    take() waits until an action is allowed and returns the seconds waited."""
    def __init__(self, rate):
        self.rate = int(rate)
        self.times = collections.deque()
        self.lock = threading.Lock()
    def take(self):
        waited = 0.0
        while True:
            with self.lock:
                t = time.monotonic()
                while self.times and t - self.times[0] >= 1.0: self.times.popleft()
                if len(self.times) < self.rate:
                    self.times.append(t)
                    return waited
                need = 1.0 - (t - self.times[0]) + 0.001
            time.sleep(need); waited += need

def _pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


def fok_body(ticker, yes, limit_c, count, coid):
    """Kalshi V2 fill-or-kill buy. Buying YES at <= L = a YES bid at L; buying NO at <= L = a YES ask at >= 100-L."""
    return {"ticker": ticker, "client_order_id": coid,
            "side": "bid" if yes else "ask",
            "count": f"{int(count)}.00",
            "price": f"{(limit_c if yes else 100 - limit_c) / 100:.4f}",
            "time_in_force": "fill_or_kill",
            "self_trade_prevention_type": "taker_at_cross",
            "exchange_index": -1}

def _num(*vals):
    for v in vals:
        try:
            if v is not None and v != "": return float(v)
        except (TypeError, ValueError): pass
    return None

def read_fill(od, yes, limit, ask_seen):
    """(contracts filled, price paid in cents for OUR side) from a Kalshi order object.
    Kalshi V2 prices are YES prices; for a NO buy the paid price is 100 - that. Whichever reading is at or
    below the limit (a fill-or-kill never fills above it) and closest to the ask seen is used."""
    n = _num(od.get("fill_count_fp"), od.get("fill_count"), od.get("taker_fill_count"))
    raw = _num(od.get("average_fill_price"), od.get("average_fill_price_dollars"))
    if raw is None:
        cost = _num(od.get("taker_fill_cost_dollars"), od.get("taker_fill_cost"))
        if cost is not None and n:
            raw = cost / n                                    # per contract (dollars, or cents in the older field)
    px = None
    if raw is not None:
        c = raw * 100 if raw <= 1.0 else raw
        cands = [round(c), round(100 - c)]
        ok = [x for x in cands if 1 <= x <= limit] or cands
        expected = cands[0] if yes else cands[1]
        px = min(ok, key=lambda x: (abs(x - ask_seen), x != expected))
    if n is None and str(od.get("status", "")).lower() in ("executed", "filled"): n = float(CONTRACTS)
    return (n or 0.0), px

class QuickGateway:
    """Quick (taker) buys: rate limit -> build + sign the exact fill-or-kill request -> send it (live) or simulate
    the fill at the ask when the request is ready (dry run) -> report fill, slippage and timing.
    One worker thread per market, so markets never wait behind each other."""
    def __init__(self, live, k, sess, say, price_now):
        self.live, self.k, self.sess, self.say, self.price_now = live, k, sess, say, price_now
        self.pool = ThreadPoolExecutor(max_workers=ORDER_WORKERS)
        self.limiter = RateLimiter(MAX_WRITES_PER_SEC)
        self.lock = threading.Lock()
        self.n = collections.Counter()
        self.decide_ms, self.build_ms = collections.deque(maxlen=5000), collections.deque(maxlen=5000)
        self.rtt_ms, self.probe_ms = collections.deque(maxlen=5000), collections.deque(maxlen=500)
        self.slip = collections.deque(maxlen=5000)
        self.per_sec = collections.Counter()
        self.shown = 0

    def buy(self, ticker, yes, ask_seen, on_done):
        t_dec = time.perf_counter()
        self.pool.submit(self._buy, ticker, yes, ask_seen, t_dec, on_done)

    def _lookup(self, ticker, coid):
        """After an unclear answer: find our order by its client id. Returns the order dict or None."""
        for _ in range(3):
            d = api_get(self.sess, self.k, ORDERS_LIST_PATH, {"ticker": ticker, "limit": 50})
            for od in (d or {}).get("orders", []) or []:
                if od.get("client_order_id") == coid: return od
            time.sleep(1.0)
        return None

    def _buy(self, ticker, yes, ask_seen, t_dec, on_done):
        try:
            wait = self.limiter.take()
            if wait > 0.001: self.n["throttled"] += 1
            t0 = time.perf_counter()
            limit = min(99, ask_seen + MAX_SLIPPAGE_C)
            coid = str(uuid.uuid4())
            body = fok_body(ticker, yes, limit, CONTRACTS, coid)
            h = _hdrs(self.k, "POST", ORDER_CREATE_PATH)
            build = (time.perf_counter() - t0) * 1000
            ready = (time.perf_counter() - t_dec) * 1000
            with self.lock:
                self.build_ms.append(build); self.decide_ms.append(ready)
                self.per_sec[int(time.time())] += 1
                if len(self.per_sec) > 4000:
                    for s0 in sorted(self.per_sec)[:2000]: del self.per_sec[s0]
            unclear = False
            if self.live:
                t1 = time.perf_counter()
                try:
                    r = self.sess.post(BASE + ORDER_CREATE_PATH, headers=h, json=body, timeout=8)
                    code = r.status_code
                    try: js = r.json()
                    except ValueError: js = {"text": r.text[:300]}
                except requests.RequestException as e:
                    code, js = 0, {"error": str(e)}
                rtt = (time.perf_counter() - t1) * 1000
                self.rtt_ms.append(rtt)
                if self.shown < SHOW_REQUESTS:
                    self.say(f"[order] Kalshi's raw answer HTTP {code}: {json.dumps(js)[:300]}")
                ok = code in (200, 201)
                od = (js.get("order", js) if isinstance(js, dict) else {}) if ok else {}    # an error body is not an order
                if code == 0 or code >= 500:                     # no clear answer: look the order up, never re-send blindly
                    unclear = True
                    self.say(f"[order] {ticker[-7:]} {asset(ticker)} no clear answer (HTTP {code}) — looking the order up by its id")
                    od = self._lookup(ticker, coid) or {}
                n, fill = read_fill(od, yes, limit, ask_seen)
                n = int(round(n))
                filled = n >= 1 and fill is not None
                if filled and n >= CONTRACTS: result = "FILLED"
                elif filled: result = f"PARTIAL ({n} of {CONTRACTS}) — kept, this market is done"
                elif unclear and not od: result = "UNKNOWN (not found on Kalshi) — this market is skipped"
                elif ok or (unclear and od): result = "killed (not enough at or below the limit)"
                elif "kill" in json.dumps(js).lower(): result = f"killed (HTTP {code}: {json.dumps(js)[:120]})"
                else: result = f"rejected HTTP {code}: {json.dumps(js)[:160]}"
            else:
                rtt, code = 0.0, 0
                px = self.price_now(ticker)                           # the ask at the moment the order would go out
                cur = None if px is None else (px[1] if yes else 100 - px[0])
                filled = cur is not None and cur <= limit
                fill = cur if filled else None
                n = CONTRACTS if filled else 0
                result = "FILLED (simulated: not sent)" if filled else f"killed (simulated: ask {cur}c > limit {limit}c)"
            if filled:
                self.slip.append(fill - ask_seen); self.n["filled"] += 1
            elif result.startswith("killed"): self.n["killed"] += 1
            elif result.startswith("UNKNOWN"): self.n["unknown"] += 1
            else: self.n["rejected"] += 1
            self.n["orders"] += 1
            side = "YES" if yes else "NO"
            self.say(f"[order] {ticker[-7:]} {asset(ticker)} buy {CONTRACTS} {side} · ask seen {ask_seen}c, limit {limit}c · {result}"
                     + (f" @ {fill}c (slippage {fill - ask_seen:+d}c)" if filled else "")
                     + f" · decision->ready {ready:.1f} ms (rate wait {wait * 1000:.0f}), build+sign {build:.1f} ms"
                     + (f", round trip {rtt:.0f} ms" if self.live else ""))
            if self.shown < SHOW_REQUESTS:
                self.shown += 1
                self.say(f"[order] request {'SENT (LIVE)' if self.live else 'built (NOT sent)'}: POST {ORDER_CREATE_PATH} "
                         + json.dumps(body, separators=(",", ":")))
            stop_market = result.startswith(("UNKNOWN", "PARTIAL")) or (result.startswith("rejected") and code != 429)
            on_done(ticker, yes, n if filled else 0, fill, ask_seen, stop_market)
        except Exception as e:
            self.n["errors"] += 1
            print(f"[order] failed: {e}")
            try: on_done(ticker, yes, 0, None, ask_seen, True)
            except Exception: pass

    def probe(self):
        t0 = time.perf_counter()
        d = api_get(self.sess, self.k, "/exchange/status")
        ms = (time.perf_counter() - t0) * 1000
        if d is not None: self.probe_ms.append(ms)
        return ms if d is not None else None

    def shards(self):
        """{shard: balance in cents} and the total, from Kalshi (read-only)."""
        b = api_get(self.sess, self.k, "/portfolio/balance")
        if b is None: return None, None
        out = {}
        for x in b.get("balance_breakdown", []) or []:
            try: out[int(x.get("exchange_index"))] = int(round(float(x.get("balance")) * 100))
            except (TypeError, ValueError): pass
        try: tot = int(b.get("balance"))
        except (TypeError, ValueError): tot = None
        return out, tot

    def keep_funded(self, quiet=False):
        """Live: if the trading shard is low and shard 0 holds money, move it all there and check it arrived.
        The transfer amount is in centicents (1/100 of a cent) — the Commod15min V2x bots' tested fix."""
        sh, tot = self.shards()
        if not sh: return
        have, src0 = sh.get(TRADING_SHARD, 0), sh.get(0, 0)
        if have >= KEEP_ON_SHARD_USD * 100 or src0 <= 100 or TRADING_SHARD == 0:
            return
        if not self.live:
            if not quiet: self.say(f"[funds] dry run: would move ${src0 / 100:,.2f} from shard 0 to shard {TRADING_SHARD}")
            return
        body = {"source": "event_contract", "destination": "event_contract", "amount": src0 * 100,
                "source_exchange_shard": 0, "destination_exchange_shard": TRADING_SHARD}
        try:
            r = self.sess.post(BASE + TRANSFER_PATH, headers=_hdrs(self.k, "POST", TRANSFER_PATH), json=body, timeout=8)
            code = r.status_code
        except requests.RequestException as e:
            code = 0; self.say(f"[funds] transfer failed: {e}")
        new = have
        if code in (200, 201):
            for _ in range(3):
                time.sleep(0.5)
                sh2, _ = self.shards()
                new = (sh2 or {}).get(TRADING_SHARD, have)
                if new > have: break
        self.say(f"[funds] moved ${src0 / 100:,.2f} shard 0 -> shard {TRADING_SHARD}: HTTP {code}, shard {TRADING_SHARD} now ${new / 100:,.2f}"
                 + ("" if new > have else "  — DID NOT ARRIVE, check the account"))

    def held_tickers(self):
        """Markets where the account already holds contracts (e.g. after a restart): the bot never buys them again."""
        d = api_get(self.sess, self.k, "/portfolio/positions", {"limit": 200, "count_filter": "position"})
        held = set()
        for p in (d or {}).get("market_positions", []) or []:
            if _num(p.get("position_fp"), p.get("position")) and p.get("ticker"): held.add(p["ticker"])
        return held

    def startup_checks(self):
        ms = self.probe()
        self.say(f"[account] Kalshi round trip: {'failed' if ms is None else f'{ms:.0f} ms'}")
        sh, tot = self.shards()
        if sh is None:
            self.say("[account] could not read the balance — check the API key")
        else:
            parts = "; ".join(f"shard {s}: ${v / 100:,.2f}" for s, v in sorted(sh.items()))
            self.say(f"[account] balance {'?' if tot is None else f'${tot / 100:,.2f}'}" + (f" ({parts})" if parts else "")
                     + f" · trading shard {TRADING_SHARD}")
            if AUTO_MOVE_FUNDS: self.keep_funded()
        d = api_get(self.sess, self.k, ORDERS_LIST_PATH, {"status": "resting", "limit": 200})
        self.say(f"[account] resting orders on the account now: {len((d or {}).get('orders', []) or [])}")
        held = self.held_tickers()
        self.say(f"[account] markets already held: {len(held)}" + (f" ({', '.join(sorted(held))[:300]}) — not bought again" if held else ""))
        return held

    def summary(self):
        n = self.n
        f = lambda xs: "n/a" if not xs else f"{_pct(list(xs), 0.5):.1f} / {_pct(list(xs), 0.95):.1f} ms"
        peak = max(self.per_sec.values()) if self.per_sec else 0
        avg_slip = (sum(self.slip) / len(self.slip)) if self.slip else 0
        return [f"quick orders {'SENT (live)' if self.live else 'built, not sent'}: {n['orders']} · filled {n['filled']}, "
                f"killed {n['killed']}, rejected {n['rejected']}, unknown {n['unknown']}, errors {n['errors']} · "
                f"avg slippage {avg_slip:+.2f}c · busiest second {peak} (limit {MAX_WRITES_PER_SEC}/s)",
                f"speed (median / 95%): decision -> request ready {f(self.decide_ms)} · build + sign {f(self.build_ms)} · "
                f"Kalshi round trip {f(self.probe_ms)}" + (f" · order round trip {f(self.rtt_ms)}" if self.live else "")]

# ---------------- start-up history: the last cycles before this run, from Kalshi ----------------
def _cents(d, key):
    """A candle value in cents. Kalshi sends '<key>' in whole cents (56) and/or '<key>_dollars' ("0.5600")."""
    if not isinstance(d, dict): return None
    v = d.get(key + "_dollars")
    if v not in (None, ""):
        try: return round(float(v) * 100)
        except (TypeError, ValueError): pass
    v = d.get(key)
    if v in (None, ""): return None
    try: f = float(v)
    except (TypeError, ValueError): return None
    return round(f * 100) if (0 < f < 1 and f != int(f)) else round(f)

def opening_buy(candle):
    """V38b's buy from a market's first 1-minute candle: the opening quote, or the quote at the end of the
    minute if the opening one does not qualify. Returns (yes?, ask paid, the other side's ask) or None."""
    for key in ("open", "close"):
        yb, ya = _cents(candle.get("yes_bid"), key), _cents(candle.get("yes_ask"), key)
        if yb is None or ya is None or not (0 <= yb < ya <= 100) or ya - yb > MAX_SPREAD_C: continue
        for yes, ask in ((True, ya), (False, 100 - yb)):
            if OPEN_MIN_C <= ask <= OPEN_MAX_C: return yes, ask, (100 - yb if yes else ya)
    return None

def fetch_history(sess, k, cycles, say):
    """The last `cycles` settled 15-minute windows of every market and V38b's opening buy in each.
    Returns (rows, closes, no_price) or (None, None, None) when Kalshi's history cannot be read."""
    since = int(time.time()) - (cycles + 4) * 900
    def settled(ser):
        d = api_get(sess, k, "/markets", {"series_ticker": ser, "status": "settled", "min_close_ts": since, "limit": 100})
        return [m for m in (d or {}).get("markets", []) or [] if m.get("result") in ("yes", "no") and parse_iso(m.get("close_time"))]
    with ThreadPoolExecutor(max_workers=8) as ex:
        ms = [m for lst in ex.map(settled, SERIES) for m in lst]
    if not ms: return None, None, None
    closes = sorted({parse_iso(m["close_time"]) for m in ms})[-cycles:]
    ms = [m for m in ms if parse_iso(m["close_time"]) in closes]
    shown = {"raw": False}
    def one(m):
        t, close = m["ticker"], parse_iso(m["close_time"])
        start = parse_iso(m.get("open_time")) or (close - dt.timedelta(seconds=900))
        s0 = int(start.timestamp())
        d = api_get(sess, k, f"/series/{series_of(t)}/markets/{t}/candlesticks",
                    {"start_ts": s0, "end_ts": s0 + 60, "period_interval": 1})
        cs = sorted((d or {}).get("candlesticks", []) or [], key=lambda c: c.get("end_period_ts") or 0)
        buy = opening_buy(cs[0]) if cs else None
        if cs and buy is None and not shown["raw"] and _cents(cs[0].get("yes_ask"), "open") is None \
                and _cents(cs[0].get("yes_ask"), "close") is None:
            shown["raw"] = True
            say(f"[history] could not read the prices in Kalshi's candle (first one shown): {json.dumps(cs[0])[:300]}")
        return dict(ticker=t, asset=asset(t), cls=cls(asset(t)), close=close, result=m["result"],
                    have_price=bool(cs), buy=buy)
    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = list(ex.map(one, ms))
    no_price = sum(1 for r in rows if not r["have_price"])
    return rows, closes, no_price

def history_report(sess, k, say, switched_on, cycles):
    """How V38b's rule did in the last `cycles` cycles (an estimate from Kalshi's history; nothing is traded)."""
    try:
        rows, closes, no_price = fetch_history(sess, k, cycles, say)
    except Exception as e:
        say(f"[history] not available ({type(e).__name__}: {e})"); return
    if not rows:
        say("[history] Kalshi's market history could not be read — check the key and the connection, then run the cell again"); return
    say(f"[history] THE LAST {len(closes)} CYCLES (closing {closes[0]:%H:%M}-{closes[-1]:%H:%M} UTC), "
        f"from Kalshi's price history:")
    print(f"           V38b's rule on each market's first-minute prices · not traded: an estimate (bought at the ask, "
          f"{CONTRACTS} contracts, taker fee)")
    for c in ("crypto", "commodity"):
        rs = sorted([r for r in rows if r["cls"] == c and r["buy"]], key=lambda r: r["close"])
        for r in rs:
            yes, px, other = r["buy"]
            r["inv"] = r["asset"] in {str(m).upper() for m in INVERSE_MARKETS}
            if r["inv"]: yes, px = (not yes), other                     # inverse market: the underdog
            r["won"] = (r["result"] == "yes") == yes
            r["px"] = px
            r["net"] = CONTRACTS * (100 * r["won"] - px) - taker_fee_c(CONTRACTS, px)
        n, w = len(rs), sum(r["won"] for r in rs)
        avg = sum(r["px"] for r in rs) / n if n else 0
        print(f"  {c.upper():9} {n:3} markets, {w:3} won ({100 * w / n if n else 0:.0f}%), avg buy {avg:.1f}c, "
              f"net {_usd(sum(r['net'] for r in rs))}")
        for lo, hi in PRICE_BANDS:
            q = [r for r in rs if not r["inv"] and band_name(r["px"]) == f"{lo}-{hi}c"]
            if q:
                qw = sum(r["won"] for r in q)
                print(f"              bought {lo}-{hi}c: {len(q):3} markets, {qw:3} won ({100 * qw / len(q):.0f}%), "
                      f"net {_usd(sum(r['net'] for r in q))}")
        q = [r for r in rs if r["inv"]]
        if q:
            qw = sum(r["won"] for r in q)
            print(f"              bought the UNDERDOG (inverse): {len(q):3} markets, {qw:3} won ({100 * qw / len(q):.0f}%), "
                  f"net {_usd(sum(r['net'] for r in q))}")
        for a in [x for x in ALL_MARKETS if cls(x) == c]:
            q = [r for r in rs if r["asset"] == a]
            res = ["W" if r["won"] else "L" for r in q]
            if q:
                qw = sum(r["won"] for r in q)
                print(f"              {a:9} {len(q):3} markets, {qw:3} won ({100 * qw / len(q):3.0f}%), "
                      f"net {_usd(sum(r['net'] for r in q)):>9} · streak {streak(res):4} · last {len(closes)}: {''.join(res)}"
                      + ("   ★ switched on" if a in switched_on else ""))
    no_buy = sum(1 for r in rows if r["have_price"] and not r["buy"])
    print(f"           no buy (no side at {OPEN_MIN_C}-{OPEN_MAX_C}c with a spread of {MAX_SPREAD_C}c or less in the first "
          f"minute): {no_buy} markets" + (f" · no price history: {no_price}" if no_price else ""))

def show_history(cycles=5):
    """The HISTORY cell: how V38b's rule did in the last `cycles` 15-minute cycles (nothing is traded)."""
    if not (isinstance(cycles, int) and 1 <= cycles <= 20):
        sys.exit(f"HISTORY_CYCLES must be a whole number from 1 to 20 (it is {cycles!r})")
    try: check_switches()
    except SystemExit as e: print(f"(switches not checked: {e})")
    k = load_key()
    sess = requests.Session()
    sess.mount("https://", requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=16))
    history_report(sess, k, lambda m: print(f"[{now():%H:%M:%S}] {m}"), set(trading_markets()), cycles)
    print("           ★ switched on = in TRADE_CRYPTO / TRADE_COMMODITIES and not in SKIP_MARKETS (live when DRY_RUN = False)")
# ---------------- the bot ----------------
CRYPTO = {"BTC", "ETH", "SOL", "XRP", "DOGE"}
ALL_MARKETS = [asset(s) for s in SERIES]
def cls(a): return "crypto" if a in CRYPTO else "commodity"

def band_name(px):
    """Band of the price paid. A fill can be outside 56-95c (up to +2c slippage above 95c, or better than the ask
    below 56c): it is counted in the nearest band."""
    px = min(max(px, PRICE_BANDS[0][0]), PRICE_BANDS[-1][1])
    for lo, hi in PRICE_BANDS:
        if lo <= px <= hi: return f"{lo}-{hi}c"
    return "?"

class Trade:
    """The opening buy in one market, held to settlement. live: a real order; counts: part of the daily loss stop."""
    def __init__(self, yes, px, n, s2c, close, live, counts):
        self.yes, self.px, self.n, self.s2c, self.close = yes, px, n, s2c, close
        self.live, self.counts = live, counts

def _usd(c): return f"{'+' if c >= 0 else '-'}${abs(c) / 100:,.2f}"

LIVE_TAG, DRY_TAG = "🟢 LIVE", "⚪ DRY RUN"
def tag(live): return LIVE_TAG if live else DRY_TAG
def paint(text, live):
    """Live lines in bold green, dry-run lines in the normal colour (COLORS = False: no colour codes)."""
    return f"\x1b[1;32m{text}\x1b[0m" if (live and COLORS) else text

def streak(res):
    """Current run of wins or losses from a list of 'W'/'L', e.g. 'W3'."""
    if not res: return "-"
    n = 1
    while n < len(res) and res[-n - 1] == res[-1]: n += 1
    return f"{res[-1]}{n}"

def check_switches():
    """Stop with a clear message on a switch that is mistyped (e.g. "False" in quotes, or a market name typo)."""
    for name in ("DRY_RUN", "TRADE_CRYPTO", "TRADE_COMMODITIES"):
        if not isinstance(globals()[name], bool):
            sys.exit(f"{name} must be True or False without quotes (it is {globals()[name]!r})")
    if not isinstance(SKIP_MARKETS, (list, tuple, set)):
        sys.exit(f'SKIP_MARKETS must be a list like ["WTI", "DOGE"] (it is {SKIP_MARKETS!r})')
    bad = [m for m in SKIP_MARKETS if str(m).upper() not in ALL_MARKETS]
    if bad:
        sys.exit(f"SKIP_MARKETS has unknown names {bad}. Use these names: {', '.join(ALL_MARKETS)}")
    if not (isinstance(CONTRACTS, int) and 1 <= CONTRACTS <= 100):
        sys.exit(f"CONTRACTS must be a whole number from 1 to 100 (it is {CONTRACTS!r})")
    if not (isinstance(DAILY_LOSS_LIMIT_USD, (int, float)) and DAILY_LOSS_LIMIT_USD > 0):
        sys.exit(f"DAILY_LOSS_LIMIT_USD must be a positive number of dollars (it is {DAILY_LOSS_LIMIT_USD!r})")
    if not isinstance(INVERSE_MARKETS, (list, tuple, set)) or [m for m in INVERSE_MARKETS if str(m).upper() not in ALL_MARKETS]:
        sys.exit(f"INVERSE_MARKETS must be a list of market names like [\"WTI\"] (it is {INVERSE_MARKETS!r}). "
                 f"Names: {', '.join(ALL_MARKETS)}")
    if not DRY_RUN and (not KEY_ID or KEY_ID == "__KEY_ID__"):
        sys.exit("live trading needs KEY_ID (your Kalshi API key id) in the settings")

def trading_markets():
    """The markets switched ON (live when DRY_RUN = False). Every other market is still followed in dry run."""
    skip = {str(m).upper() for m in SKIP_MARKETS}
    return [a for a in ALL_MARKETS if a not in skip
            and (TRADE_CRYPTO if cls(a) == "crypto" else TRADE_COMMODITIES)]

def main():
    check_switches()
    live = not DRY_RUN
    on = trading_markets()
    on_set = set(on)
    inv = {str(m).upper() for m in INVERSE_MARKETS}
    off = [a for a in ALL_MARKETS if a not in on_set]
    if live:
        print(paint("!" * 100, True))
        print(paint(f"!!!  {BOT_NAME} — LIVE: REAL ORDERS WITH REAL MONEY on the markets marked {LIVE_TAG}  !!!", True))
        print(paint("!" * 100, True))
        print(paint(f"{LIVE_TAG}    (real orders): {', '.join(on) if on else 'none'}", True))
        print(f"{DRY_TAG} (followed, nothing sent): {', '.join(off) if off else 'none'}")
    else:
        print(f"{BOT_NAME} — DRY RUN (exact fill-or-kill orders built, signed and timed — NOTHING is sent; fills simulated)")
        print(f"{DRY_TAG}: all markets · switched on (would be live with DRY_RUN = False): {', '.join(on) if on else 'none'}")
    print(f"crypto: {'ON' if TRADE_CRYPTO else 'OFF'} · commodities: {'ON' if TRADE_COMMODITIES else 'OFF'}"
          + (f" · skipped: {', '.join(str(m).upper() for m in SKIP_MARKETS)}" if SKIP_MARKETS else "")
          + (f" · INVERSE (buy the underdog): {', '.join(sorted(inv))}" if inv else ""))
    print(f"opening buy: {CONTRACTS} contracts of the side whose ask is {OPEN_MIN_C}-{OPEN_MAX_C}c in the first "
          f"{OPEN_WINDOW_SEC}s of the market · no buy if the spread is over {MAX_SPREAD_C}c · hold to settlement")
    print(f"quick orders: fill-or-kill, limit = ask seen + {MAX_SLIPPAGE_C}c · killed orders retried in the window "
          f"(at most {MAX_TRIES_PER_MARKET} tries per market) · {ORDER_WORKERS} sender threads · at most {MAX_WRITES_PER_SEC} actions/s")
    print(f"daily loss stop: after each 15-minute round settles, no new live buys if today's (UTC) live net is below "
          f"-${DAILY_LOSS_LIMIT_USD} (no limit on gains{'; dry run only reports it, for the switched-on markets' if not live else ''}) · "
          f"kill switch: create {KILL_FILE}")
    print("dry-run fills assume all contracts are there at the ask; live, the books are thin at the open, so live fills "
          "can be a few cents worse")
    print("files: none — results are shown here only")
    if live and not on:
        sys.exit("DRY_RUN = False but every market is switched off: nothing would be live. Switch markets on, or set DRY_RUN = True")
    if live and LIVE_COUNTDOWN_SEC > 0:
        for i in range(int(LIVE_COUNTDOWN_SEC), 0, -1):
            print(f"LIVE trading starts in {i} s — stop the cell now to cancel", flush=True)
            time.sleep(1)
    k = load_key()
    sess = requests.Session()
    sess.mount("https://", requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=len(SERIES) + ORDER_WORKERS + 10))
    lock = threading.RLock()
    markets, rest_px, trades, pending, done = {}, {}, {}, set(), set()
    tries = collections.Counter()
    settle_due, settled = {}, set()
    open_by_close = collections.Counter()            # stop-counting trades still waiting for their result, per close time
    def new_tally(): return {"markets": 0, "won": 0, "net": 0, "cost": 0, "n": 0}
    MODES = ("live", "dry")
    run = {(c, md): new_tally() for c in ("crypto", "commodity") for md in MODES}
    by_band = {(c, md, f"{lo}-{hi}c"): new_tally() for c in ("crypto", "commodity") for md in MODES for lo, hi in PRICE_BANDS}
    by_band.update({(c, md, "underdog"): new_tally() for c in ("crypto", "commodity") for md in MODES})
    per_mkt = {(a, md): dict(new_tally(), res=[]) for a in ALL_MARKETS for md in MODES}
    day = {"date": now().date(), "net": 0, "low": 0, "live": 0, "dry": 0}
    flags = {"kill": False, "halted": False, "would_halt": None}

    def s2c_of(t):
        m = markets.get(t)
        return (m["close"] - now()).total_seconds() if m else None

    def say(msg, lv=None): print(f"[{now():%H:%M:%S}] " + (msg if lv is None else paint(msg, lv)))

    def prices(t):
        return feed.get(t) or rest_px.get(t)

    gw_dry = QuickGateway(False, k, sess, lambda m: say((DRY_TAG + " " + m) if live else m, False), prices)
    gw_live = QuickGateway(True, k, sess, lambda m: say(LIVE_TAG + " " + m, True), prices) if live else None
    gw = gw_live or gw_dry                           # account checks and the latency probe
    if live: gw_dry.shown = SHOW_REQUESTS            # the raw-request lines are for the live orders

    def is_live(a):
        """Real orders for this market now? Switched on, DRY_RUN = False, no kill switch, no daily stop."""
        return live and a in on_set and not flags["kill"] and not flags["halted"]

    def on_bought(t, yes, n, fill, ask_seen, stop_market, lv):
        """Called by the quick-order path when the buy has been answered (live) or simulated (dry run)."""
        a = asset(t)
        with lock:
            pending.discard(t)
            if stop_market: done.add(t)
            if not n or t in trades: return
            m = markets.get(t)
            s2c = s2c_of(t)
            counts = lv if live else a in on_set
            trades[t] = Trade(yes, fill, n, s2c, m["close"] if m else None, lv, counts)
            trades[t].inv = a in inv
            if counts: open_by_close[trades[t].close] += 1
        say(f"{tag(lv)}  BUY  {n}x {'YES' if yes else 'NO'} @ {fill}c  {a} ({cls(a)}) {t[-7:]}  "
            f"{'' if s2c is None else f'{s2c:.0f}s left'}  (ask seen {ask_seen}c)" + (" · INVERSE: the underdog" if a in inv else "")
            + " · hold to settlement", lv)

    def evaluate(t):
        a = asset(t)
        px = prices(t)
        s2c = s2c_of(t)
        if px is None or s2c is None or s2c <= DEADBAND_SEC: return       # unknown timing: do nothing
        yb, ya = px
        if not (0 <= yb < ya <= 100): return
        with lock:
            if t in trades or t in pending or t in done: return
            if not (900 - OPEN_WINDOW_SEC <= s2c <= 905) or ya - yb > MAX_SPREAD_C: return
            for yes, ask in ((True, ya), (False, 100 - yb)):
                if OPEN_MIN_C <= ask <= OPEN_MAX_C:
                    if a in inv:                                     # inverse market: buy the other side (the underdog)
                        yes, ask = (not yes), (100 - yb if yes else ya)
                    if tries[t] >= MAX_TRIES_PER_MARKET:
                        done.add(t)
                        say(f"[order] {a} {t[-7:]}: {MAX_TRIES_PER_MARKET} tries, none filled — no more tries in this market")
                        return
                    tries[t] += 1
                    pending.add(t)                                   # one order in flight per market
                    lv = is_live(a)
                    (gw_live if lv else gw_dry).buy(t, yes, ask, lambda *r, lv=lv: on_bought(*r, lv))
                    return

    def on_tick(t, yb, ya):
        if t in markets: evaluate(t)              # the buy fires on the price update itself

    def new_day_check():
        if now().date() != day["date"]:
            day.update(date=now().date(), net=0, low=0, live=0, dry=0)
            if flags["halted"] or flags["would_halt"]:
                say("[safety] new day (UTC): daily loss stop reset")
            flags.update(halted=False, would_halt=None)

    def round_settled(close):
        """A whole 15-minute round has its results in: this is when the daily loss stop is checked."""
        if day["net"] >= -DAILY_LOSS_LIMIT_USD * 100: return
        when = "" if close is None else f" (round closing {close:%H:%M} UTC settled)"
        if not live:
            if flags["would_halt"] is None:
                flags["would_halt"] = now()
                say(f"[safety] DAILY LOSS STOP would stop new buys now{when}: today's net of the switched-on markets "
                    f"{_usd(day['net'])} is below -${DAILY_LOSS_LIMIT_USD} — dry run keeps going")
        elif not flags["halted"]:
            flags["halted"] = True
            say(f"!!! DAILY LOSS STOP{when}: today's live net {_usd(day['net'])} is below -${DAILY_LOSS_LIMIT_USD} — "
                f"no new live buys until midnight UTC; every market carries on in dry run !!!", True)

    def finish(t, result):
        with lock:
            tr = trades.pop(t, None)
        if tr is None: return
        a, c, md = asset(t), cls(asset(t)), ("live" if tr.live else "dry")
        won = (result == "yes") == tr.yes
        net = tr.n * (100 * won - tr.px) - taker_fee_c(tr.n, tr.px)
        new_day_check()
        day[md] += net
        if tr.counts:
            day["net"] += net
            day["low"] = min(day["low"], day["net"])
        for r in (run[(c, md)], by_band[(c, md, "underdog" if getattr(tr, "inv", False) else band_name(tr.px))], per_mkt[(a, md)]):
            r["markets"] += 1; r["won"] += int(won); r["net"] += net; r["cost"] += tr.n * tr.px; r["n"] += tr.n
        pm = per_mkt[(a, md)]
        pm["res"].append("W" if won else "L")
        r = run[(c, md)]
        say(f"{tag(tr.live)}  SETTLED {result.upper()}  {a} ({c}) {t[-7:]}: {tr.n}x {'YES' if tr.yes else 'NO'} @ {tr.px}c "
            + ("(inverse) " if getattr(tr, "inv", False) else "") +
            f"{'WON' if won else 'lost'} {_usd(net)}  | {a} streak {streak(pm['res'])}"
            f"  | {c} {'live' if tr.live else 'dry-run'} run {_usd(r['net'])} over {r['markets']} markets ({r['won']} won)"
            f"  | today {'live' if live else 'switched-on'} {_usd(day['net'])}", tr.live)
        if tr.counts: close_done(tr.close)

    def close_done(close):
        with lock:
            open_by_close[close] -= 1
            last = open_by_close[close] <= 0
            if last: del open_by_close[close]
        if last: round_settled(close)

    def status():
        say(f"[status] {'LIVE on ' + ', '.join(on) if live else 'dry run (all markets)'}")
        for c in ("crypto", "commodity"):
            names = [a for a in ALL_MARKETS if cls(a) == c]
            for md in MODES:
                lv = md == "live"
                now_in = [a for a in names if (is_live(a) if lv else not is_live(a))]
                r = run[(c, md)]
                if not now_in and not r["markets"]: continue
                avg = r["cost"] / r["n"] if r["n"] else 0
                won = 100 * r["won"] / r["markets"] if r["markets"] else 0
                head = f"  {tag(lv)}  {c.upper():9} {r['markets']:3} markets, {r['won']:3} won ({won:.0f}%), avg buy {avg:.1f}c, net {_usd(r['net'])}"
                print(paint(head, lv))
                for lo, hi in PRICE_BANDS:
                    q = by_band[(c, md, f"{lo}-{hi}c")]
                    if q["markets"]:
                        print(paint(f"              bought {lo}-{hi}c: {q['markets']:3} markets, {q['won']:3} won "
                                    f"({100 * q['won'] / q['markets']:.0f}%), net {_usd(q['net'])}", lv))
                q = by_band[(c, md, "underdog")]
                if q["markets"]:
                    print(paint(f"              bought the UNDERDOG (inverse: {', '.join(sorted(x for x in inv if cls(x) == c))}): "
                                f"{q['markets']:3} markets, {q['won']:3} won ({100 * q['won'] / q['markets']:.0f}%), "
                                f"avg buy {q['cost'] / q['n']:.1f}c, net {_usd(q['net'])}", lv))
                for a in names:
                    pm = per_mkt[(a, md)]
                    if not pm["markets"] and a not in now_in: continue
                    wr = f"{100 * pm['won'] / pm['markets']:3.0f}%" if pm["markets"] else "  - "
                    print(paint(f"              {a:9} {pm['markets']:3} markets, {pm['won']:3} won ({wr}), net {_usd(pm['net']):>9}"
                                f" · streak {streak(pm['res']):4} · last 10: {''.join(pm['res'][-10:]) or '-'}"
                                + ("" if a in now_in else "  (not in this mode now)"), lv))
        for g_, lv in ((gw_live, True), (gw_dry, False)):
            if g_:
                for line in g_.summary(): print(paint(f"           {tag(lv) if live else ''}{' ' if live else ''}{line}", lv))
        wh = flags["would_halt"]
        if live:
            print(paint(f"           today (UTC) live net {_usd(day['live'])}, lowest {_usd(day['low'])} (stop at -${DAILY_LOSS_LIMIT_USD} "
                        f"after a round settles, no limit on gains)"
                        + ("  — DAILY LOSS STOP HIT, no new live buys today" if flags["halted"] else "")
                        + ("  — KILL SWITCH ON" if flags["kill"] else ""), True))
            print(f"           today (UTC) dry-run net {_usd(day['dry'])} (not real money)")
        else:
            print(f"           today (UTC) net of the switched-on markets {_usd(day['net'])}, lowest {_usd(day['low'])} "
                  f"(stop at -${DAILY_LOSS_LIMIT_USD} after a round settles, no limit on gains) · all markets {_usd(day['dry'])}"
                  + (f"  — dry run: the stop would have ended new buys at {wh:%H:%M} UTC" if wh else "")
                  + ("  — KILL SWITCH ON" if flags["kill"] else ""))

    def probe_loop():
        while True:
            time.sleep(LATENCY_PROBE_SEC)
            try: gw.probe()
            except Exception: pass

    def funds_loop():
        while True:
            time.sleep(30)
            try: gw.keep_funded(quiet=True)
            except Exception as e: print(f"[funds] {e}")

    global FEED
    held = gw.startup_checks()                   # read-only (apart from a funds move, live); warms up the connection
    done.update(held)
    threading.Thread(target=probe_loop, daemon=True).start()
    if live and AUTO_MOVE_FUNDS: threading.Thread(target=funds_loop, daemon=True).start()
    feed = FEED = make_feed(k)
    feed.on_tick = on_tick
    feed.ready.wait(timeout=10)

    last_refresh, last_status, last_kill = 0, time.time(), 0
    errors = {"n": 0}
    try:
        while True:
            try:
                if time.time() - last_refresh >= MARKETS_REFRESH_SEC:
                    last_refresh = time.time()
                    try:
                        ms = fetch_open(sess, k)
                    except Exception as e:
                        print(f"[rest] {e}"); ms = None
                    if ms is not None:
                        for m in ms:
                            t, ct = m.get("ticker"), parse_iso(m.get("close_time"))
                            if not t or not ct or t in settled: continue
                            markets[t] = {"close": ct}
                            yb, ya = px_c(m, "yes_bid"), px_c(m, "yes_ask")
                            if yb is not None and ya is not None: rest_px[t] = (yb, ya)
                        feed.track({t for t in markets if 0 < (s2c_of(t) or 0) <= 15 * 60 + 60})
                if time.time() - last_kill >= 5:                     # kill switch: a file on Drive
                    last_kill = time.time()
                    if not flags["kill"] and os.path.exists(KILL_FILE):
                        flags["kill"] = True
                        say(f"!!! KILL SWITCH ({KILL_FILE} found): no new live buys; every market carries on in dry run. "
                            f"Positions already held ride to settlement. Delete the file and restart to resume. !!!"
                            if live else f"[safety] kill switch file found ({KILL_FILE}) — dry run: nothing is live anyway", live)
                new_day_check()
                for t in list(markets):
                    s2c = s2c_of(t)
                    if s2c is None: continue
                    if s2c > -5:
                        evaluate(t)
                    elif t not in settle_due:
                        settle_due[t] = [time.time(), time.time() + SETTLE_GIVEUP_SEC]
                checked = 0
                for t, due in list(settle_due.items()):
                    if checked >= 4: break
                    if time.time() < due[0]: continue
                    if t not in trades and t not in pending:               # nothing bought here: no lookup needed
                        settled.add(t); del settle_due[t]; markets.pop(t, None); rest_px.pop(t, None)
                        continue
                    checked += 1
                    m = fetch_market(sess, k, t)
                    res = m.get("result")
                    if res in ("yes", "no"):
                        finish(t, res)
                        settled.add(t)
                        del settle_due[t]; markets.pop(t, None); rest_px.pop(t, None)
                    elif time.time() > due[1]:
                        with lock: tr = trades.pop(t, None)
                        if tr:
                            say(f"[settle] no result for {t} after {SETTLE_GIVEUP_SEC // 60} min — left out of the totals; "
                                f"check it on Kalshi")
                            close_done(tr.close)
                        settled.add(t)
                        del settle_due[t]; markets.pop(t, None)
                    else:
                        due[0] = time.time() + SETTLE_RETRY_SEC
                if time.time() - last_status >= STATUS_EVERY_SEC:
                    last_status = time.time()
                    status()
            except Exception as e:                              # never let one bad pass stop the bot
                errors["n"] += 1
                if errors["n"] <= 20 or errors["n"] % 100 == 0:
                    say(f"[error] {type(e).__name__}: {e} — the bot keeps running (error #{errors['n']})")
            time.sleep(LOOP_SEC)
    except KeyboardInterrupt:
        for g_ in (gw_live, gw_dry):
            if g_: g_.pool.shutdown(wait=True)
        print("[stopped] no new buys. Positions already bought stay on Kalshi and settle there. Totals so far:")
        status()
    finally:
        feed.stop()

FEED = None
