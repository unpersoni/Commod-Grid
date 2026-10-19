# ============================================================
# MM15Min V2 — market maker with a DRY-RUN order path (no real-money trading in this version)
#
# Same two strategies as V1 (see FINDINGS_log.md, "Market making"):
#   fav  "favourite": rest 10-contract bids at the best bid of any side priced 85-98c
#   mm   "two-sided": rest bids on both sides at 3-97c; stop a side 30+ contracts heavier than the other
#   from the start of the market to 30 s before the close; up to 5 filled orders per side; hold to settlement.
#
# MODE (settings):
#   paper   — V1: simulated orders, the order code does not run.
#   dry_run — (default) every order decision goes through the REAL order path: the exact Kalshi V2 request is
#             built and signed, rate-limited (Kalshi Basic tier: 10 actions/s), checked against the safety
#             limits and logged with its timing — but NOT SENT. Fills are simulated from the live book and
#             trades (an order joins the line only once its request is ready). Read-only account checks run
#             with your real key: Kalshi round-trip time, balance (per shard), resting orders.
#   demo    — orders really sent to Kalshi's demo site (play money; needs a demo account + demo key);
#             fills come from Kalshi's private fill channel. Checks the request format against Kalshi.
#   live    — locked in V2.
#
# SPEED (lessons from Commod15min V21-V25): decisions run on every price/trade event (no polling); orders
#   go out on 16 background threads so a slow request never holds up prices; one kept-alive HTTPS session;
#   no balance lookups before an order (Kalshi checks collateral itself); timing logged per order
#   (decision -> request ready, build + sign, Kalshi round trip).
# SAFETY: post-only (never takes liquidity / pays taker fees) · every order expires 25 s before the close on
#   Kalshi's side · max 60 resting orders · max 220 contracts per market · no new orders after a -$100 day ·
#   kill switch: create STOP_MM15MIN.txt on Drive (cancels everything) · cancel-all at start and stop ·
#   demo/live: a Kalshi order group cancels everything if 300+ contracts fill within 15 s ·
#   nothing is done when a market's close time is unknown.
#
# LOGS (Drive): mm15min_v2_fills.csv, mm15min_v2_markets.csv, mm15min_v2_settlements.csv,
#   mm15min_v2_data/orders_YYYY-MM-DD_HH.csv.gz — every order action with the exact request and its timing.
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
BOT_NAME  = "MM15Min V2"
# MODE:  "paper"   = V1 behaviour: simulated orders, no order code runs
#        "dry_run" = the full order path runs (exact Kalshi requests built, signed, rate-limited, logged)
#                    but NOTHING is sent; fills are simulated. Read-only account checks DO run.
#        "demo"    = orders really sent to Kalshi's DEMO site (play money, separate demo account + key)
#        "live"    = real money — LOCKED in this version
MODE      = "dry_run"
PEM_PATH  = '/content/drive/MyDrive/intraday key.pem'
KEY_ID    = "__KEY_ID__"
DEMO_PEM_PATH = '/content/drive/MyDrive/demo key.pem'   # demo mode only: a key made on demo.kalshi.co
DEMO_KEY_ID   = ""                                       # demo mode only
DRIVE     = '/content/drive/MyDrive/'
FILLS_CSV   = DRIVE + 'mm15min_v2_fills.csv'
MARKETS_CSV = DRIVE + 'mm15min_v2_markets.csv'
SETTLE_CSV  = DRIVE + 'mm15min_v2_settlements.csv'
DATA_DIR    = DRIVE + 'mm15min_v2_data/'      # orders_YYYY-MM-DD_HH.csv(.gz): every order action, hourly
KILL_FILE   = DRIVE + 'STOP_MM15MIN.txt'      # create this file on Drive to stop: cancels all orders, no new ones

SERIES = ["KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M",
          "KXGOLD15M", "KXSILVER15M", "KXWTI15M", "KXCOPPER15M",
          "KXPLATINUM15M", "KXPALLADIUM15M"]

# strategies (unchanged from V1): a side is bid while its best bid is inside [min_c, max_c]
STRATEGIES = {
    "fav": {"name": "favourite", "min_c": 85, "max_c": 98, "max_imbalance": None},
    "mm":  {"name": "two-sided", "min_c": 3,  "max_c": 97, "max_imbalance": 30},   # contracts, one side minus the other
}
CONTRACTS           = 10     # per order
MAX_ORDERS_PER_SIDE = 5      # filled orders per side per market per strategy
FROM_SEC            = 900    # place orders from the start of the market ...
STOP_SEC            = 30     # ... until 30 s before the close (unfilled orders are cancelled then)
MAKER_FEE_ON        = False  # True: take Kalshi's maker fee (0.0175 x P x (1-P)) off the net
PRINT_FILLS         = True   # one line in the log per fill

# ---- order path (dry_run / demo / live) ----
POST_ONLY          = True    # Kalshi rejects the order instead of letting it take liquidity (no taker fills/fees)
EXPIRE_BEFORE_CLOSE_SEC = 25 # every order also expires on Kalshi's side 25 s before the close (safety if the bot dies)
MAX_WRITES_PER_SEC = 10      # Kalshi Basic API tier: 10 order actions per second (Advanced: 30)
ORDER_WORKERS      = 16      # background threads sending orders, so a slow request never blocks price handling
LATENCY_PROBE_SEC  = 60      # time a round trip to Kalshi every minute (shown in the status)
LOG_ORDERS         = True    # every order action (with the exact request) to DATA_DIR/orders_*.csv(.gz)
# ---- safety limits (all order modes) ----
MAX_OPEN_ORDERS          = 60    # resting orders across all markets
MAX_CONTRACTS_PER_MARKET = 220   # filled + resting, both strategies together, per market
DAILY_LOSS_LIMIT_USD     = 100   # no new orders once today's settled net is below -$100 (UTC day)
ORDER_GROUP_LIMIT        = 300   # demo/live: Kalshi cancels all our orders if more than this fills within 15 s
# ---- Kalshi order endpoints (V2); checked by the demo run ----
ORDER_CREATE_PATH = "/portfolio/events/orders"
ORDER_CANCEL_PATH = "/portfolio/events/orders/{order_id}"
ORDERS_LIST_PATH  = "/portfolio/orders"
ORDER_GROUP_PATH  = "/portfolio/order_groups/create"

BOOK_DUST        = 0.005  # a book level with less than this is treated as empty (rounding leftovers)
STATUS_EVERY_SEC = 900    # status every 15 minutes
LOW_SPACE_MB     = 200    # loud warning when Drive has less free space than this

MARKETS_REFRESH_SEC = 5
LOOP_SEC      = 0.5
SETTLE_RETRY_SEC  = 10
SETTLE_GIVEUP_SEC = 1800

BASE    = "https://api.elections.kalshi.com/trade-api/v2"
PREF    = "/trade-api/v2"
WS_URL  = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
WS_PATH = "/trade-api/ws/v2"
DEMO_BASE   = "https://external-api.demo.kalshi.co/trade-api/v2"
DEMO_WS_URL = "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2"

FILL_COLS = ["window", "asset", "class", "strategy", "side", "order_iso", "order_s2c", "px", "queue_ahead",
             "spread_c", "fill_iso", "fill_s2c", "wait_s", "contracts", "how", "spot_at_order", "spot_at_fill",
             "result", "won", "pnl_c", "maker_fee_c", "run_net_c"]
MARKET_COLS = ["window", "asset", "class", "strategy", "result", "orders_placed", "orders_moved_up",
               "orders_filled", "yes_ct", "yes_avg_px", "no_ct", "no_avg_px", "pnl_c", "maker_fee_c", "run_net_c"]
SETTLE_COLS = ["logged_iso", "ticker", "asset", "close_time", "result", "floor_strike",
               "expiration_value", "status"]
ORDER_COLS  = ["ts_iso", "mode", "action", "ticker", "strategy", "side", "price_c", "count", "client_order_id",
               "order_id", "result", "http", "rate_wait_ms", "build_ms", "rtt_ms", "decide_to_sent_ms", "request"]
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


# ---------------- Coinbase spot for the 5 coins (logged with each crypto fill, not used to trade) ----------------
SPOT_WS_URL = "wss://ws-feed.exchange.coinbase.com"
SPOT_ASSET_MAP = {"BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD",
                  "XRP": "XRP-USD", "DOGE": "DOGE-USD"}
SPOT_HISTORY_SEC = 130

class _History:
    """~2 minutes of (time, price) per asset."""
    def __init__(self, assets):
        self.hist = {a: collections.deque() for a in assets}
        self.lock = threading.Lock()
        self.stop_flag = threading.Event()

    def _add(self, a, px):
        now_t = time.time()
        with self.lock:
            h = self.hist[a]
            h.append((now_t, px))
            while h and h[0][0] < now_t - SPOT_HISTORY_SEC: h.popleft()

    def price(self, a, max_age=5.0):
        with self.lock:
            h = self.hist.get(a)
            if not h: return None
            ts, px = h[-1]
        return px if time.time() - ts <= max_age else None

    def change_pct(self, a, secs):
        """% change of the price over the last `secs` seconds (None if unknown)."""
        with self.lock:
            h = list(self.hist.get(a) or ())
        if not h or time.time() - h[-1][0] > 5: return None
        target = time.time() - secs
        old = [px for ts, px in h if ts <= target]
        if not old: return None
        return (h[-1][1] - old[-1]) / old[-1] * 100

    def start(self): threading.Thread(target=self._run, daemon=True).start()

class SpotFeed(_History):
    """Coinbase spot for the 5 coins (public websocket, no key)."""
    def __init__(self):
        super().__init__(SPOT_ASSET_MAP)
        self.by_pid = {p: a for a, p in SPOT_ASSET_MAP.items()}
        self.ws = None
        self.connect_count = 0

    def stop(self):
        self.stop_flag.set()
        try:
            if self.ws: self.ws.close()
        except Exception: pass

    def _run(self):
        backoff = 1
        while not self.stop_flag.is_set():
            try:
                ws = websocket.create_connection(SPOT_WS_URL, timeout=15)
                self.ws = ws
                self.connect_count += 1
                backoff = 1
                ws.send(json.dumps({"type": "subscribe", "product_ids": list(SPOT_ASSET_MAP.values()),
                                    "channels": ["ticker"]}))
                print(f"[spot] coinbase connected (#{self.connect_count})")
                while not self.stop_flag.is_set():
                    raw = ws.recv()
                    if raw: self._on_message(raw)
            except Exception as e:
                if self.stop_flag.is_set(): break
                try:
                    if self.ws: self.ws.close()
                except Exception: pass
                self.ws = None
                print(f"[spot] coinbase disconnected ({e}) — reconnecting in {backoff:.0f}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)

    def _on_message(self, raw):
        try: msg = json.loads(raw)
        except (json.JSONDecodeError, TypeError): return
        if msg.get("type") != "ticker": return
        a = self.by_pid.get(msg.get("product_id"))
        try: px = float(msg.get("price"))
        except (TypeError, ValueError): return
        if a: self._add(a, px)

def make_spot():
    s = SpotFeed(); s.start(); return s

# ---------------- Kalshi order book (queue size at each price; its own connection) ----------------
class BookFeed(PriceFeed):
    """Resting orders per price level from the orderbook_delta channel. Runs on a
    separate websocket so a problem here can never stop the price feed."""
    CHANNELS = ("orderbook_delta",)

    def __init__(self, k):
        super().__init__(k)
        self.books = {}        # ticker -> {"yes": {price_c: qty}, "no": {price_c: qty}}
        self.version = {}      # ticker -> change counter
        self.last_seq = None
        self.check_seq, self.gaps = True, 0

    def _subscribe(self, tickers):
        with self.lock:
            self.books.clear(); self.last_seq = None    # fresh snapshots follow
        super()._subscribe(tickers)

    @staticmethod
    def _levels(m, side):
        for key in (side + "_dollars_fp", side + "_dollars", side):
            lv = m.get(key)
            if lv is None: continue
            out = {}
            for p, q in lv:
                try:
                    pc = round(float(p) * 100) if "dollars" in key else round(float(p))
                    q = float(q)
                except (TypeError, ValueError): continue
                if q > BOOK_DUST: out[pc] = q
            return out
        return {}

    def _on_message(self, raw):
        try: msg = json.loads(raw)
        except (json.JSONDecodeError, TypeError): return
        mtype = msg.get("type")
        if mtype not in ("orderbook_snapshot", "orderbook_delta"):
            return super()._on_message(raw)
        seq = msg.get("seq")
        if self.check_seq and seq is not None and self.last_seq is not None and seq != self.last_seq + 1:
            now_g = time.time()
            self.gap_times = [x for x in getattr(self, "gap_times", []) if x > now_g - 3600] + [now_g]
            if len(self.gap_times) <= 5:            # at most 5 resubscribes per hour
                print(f"[book] sequence gap ({self.last_seq} -> {seq}) — resubscribing for fresh snapshots")
                try: self.ws.close()       # the run loop reconnects and resubscribes
                except Exception: pass
                return
            elif len(self.gap_times) == 6:
                print("[book] many sequence gaps this hour — not resubscribing again until the hour passes")
        if seq is not None: self.last_seq = seq
        m = msg.get("msg") or {}
        t = m.get("market_ticker")
        if not t or t not in self.tracked: return    # only our markets
        with self.lock:
            if mtype == "orderbook_snapshot":
                self.books[t] = {"yes": self._levels(m, "yes"), "no": self._levels(m, "no")}
            else:
                b = self.books.get(t)
                side = m.get("side")
                p = px_c(m, "price")
                try: d = float(m.get("delta_fp", m.get("delta")))
                except (TypeError, ValueError): return
                if b is None or side not in ("yes", "no") or p is None: return
                q = b[side].get(p, 0) + d
                if q > BOOK_DUST: b[side][p] = q     # leftover rounding dust = empty level
                else: b[side].pop(p, None)
            self.version[t] = self.version.get(t, 0) + 1

    def level_qty(self, t, side, p):
        """Contracts resting at price p (cents) on side "yes"/"no"; None if the book is not known."""
        if not self.ready.is_set(): return None
        with self.lock:
            b = self.books.get(t)
            return None if b is None else b[side].get(p, 0.0)

    def top(self, t, n=3):
        """([(price, qty)] best YES bids, [(price, qty)] best NO bids), best first."""
        if not self.ready.is_set(): return None
        with self.lock:
            b = self.books.get(t)
            if b is None: return None
            return (sorted(b["yes"].items(), reverse=True)[:n], sorted(b["no"].items(), reverse=True)[:n])

def make_book(k):
    f = BookFeed(k); f.start(); return f
# ---------------- order path: rate limit, exact Kalshi requests, send (or not), timing ----------------
class HourlyCSV:
    """Appends rows to <folder>/<prefix>_YYYY-MM-DD_HH.csv (UTC), a new file every hour (gzipped when done)."""
    def __init__(self, folder, prefix, cols):
        self.folder, self.prefix, self.cols = folder, prefix, cols
        os.makedirs(folder, exist_ok=True)
        self.key, self.fh, self.w = None, None, None
    def _roll(self):
        key = now().strftime("%Y-%m-%d_%H")
        if key == self.key: return
        if self.fh:
            self.fh.close()
            done = os.path.join(self.folder, f"{self.prefix}_{self.key}.csv")
            threading.Thread(target=compress_file, args=(done,), daemon=True).start()
        path = os.path.join(self.folder, f"{self.prefix}_{key}.csv")
        new = not os.path.exists(path)
        self.fh = open(path, "a", newline=""); self.w = csv.writer(self.fh); self.key = key
        if new: self.w.writerow(self.cols)
    def writerow(self, row):
        self._roll(); self.w.writerow(row)
    def flush(self):
        if self.fh: self.fh.flush()
    def close(self):
        if self.fh: self.fh.close(); self.fh = None

def compress_file(path):
    """Gzip a finished hourly file, check it reads back with the same number of lines, then remove the .csv."""
    try:
        import gzip, shutil
        tmp = path[:-4] + ".tmp.gz"
        with open(path, "rb") as fi, gzip.open(tmp, "wb") as fo: shutil.copyfileobj(fi, fo)
        with open(path, "rb") as fi: n0 = sum(1 for _ in fi)
        with gzip.open(tmp, "rb") as fz: n1 = sum(1 for _ in fz)
        if n0 != n1:
            os.remove(tmp); print(f"[log] compress check failed, kept {os.path.basename(path)}"); return
        os.replace(tmp, path + ".gz"); os.remove(path)
    except Exception as e:
        print(f"[log] could not compress {os.path.basename(path)}: {e}")

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

def order_body(ticker, side, px, count, coid, expire_dt, group_id):
    """Kalshi V2 event order. Buying YES at px = a YES bid at px; buying NO at px = a YES ask at 100-px."""
    body = {"ticker": ticker, "client_order_id": coid,
            "side": "bid" if side == "yes" else "ask",
            "count": f"{int(count)}.00",
            "price": f"{(px if side == 'yes' else 100 - px) / 100:.4f}",
            "time_in_force": "good_till_canceled",
            "post_only": bool(POST_ONLY),
            "self_trade_prevention_type": "taker_at_cross",
            "exchange_index": -1}
    if expire_dt is not None:
        body["expiration_time"] = expire_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    if group_id: body["order_group_id"] = group_id
    return body

class OrderGateway:
    """Every order action goes through here: rate limit -> build + sign the exact request -> send it (demo/live)
    or not (dry_run) -> log it with its timing. Runs on worker threads so prices are never held up."""
    def __init__(self, mode, k, sess, say):
        self.mode, self.k, self.sess, self.say = mode, k, sess, say
        self.send = mode in ("demo", "live")
        self.pool = ThreadPoolExecutor(max_workers=ORDER_WORKERS)
        self.bucket = RateLimiter(MAX_WRITES_PER_SEC)
        self.lock = threading.Lock()
        self.log = HourlyCSV(DATA_DIR, "orders", ORDER_COLS) if LOG_ORDERS else None
        self.group_id = None
        self.use_expiry = True
        self.n = collections.Counter()                      # creates / cancels / rejects / errors / throttled
        self.build_ms = collections.deque(maxlen=5000)
        self.rtt_ms = collections.deque(maxlen=5000)
        self.decide_ms = collections.deque(maxlen=5000)
        self.wait_ms = collections.deque(maxlen=5000)
        self.probe_ms = collections.deque(maxlen=500)
        self.per_sec = collections.Counter()                # unix second -> actions (for the peak rate)
        self.by_oid = {}                                    # exchange order id -> (ticker, order) while it can still fill
        self.pending = 0                                    # actions submitted and not finished yet

    def _submit(self, fn, *a):
        with self.lock: self.pending += 1
        def run():
            try: fn(*a)
            finally:
                with self.lock: self.pending -= 1
        self.pool.submit(run)

    def drain(self, timeout=15):
        """Wait until every submitted action has finished (cancels started by late creates included)."""
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                if self.pending == 0: return True
            time.sleep(0.05)
        return False

    def _write(self, row):
        if not self.log: return
        with self.lock:
            try: self.log.writerow(row); self.log.flush()
            except Exception as e: print(f"[log] orders file: {e}")

    def _timed_send(self, method, path, body=None, params=None):
        h = _hdrs(self.k, method, path)
        t0 = time.perf_counter()
        try:
            if method == "POST": r = self.sess.post(BASE + path, headers=h, json=body, timeout=8)
            else: r = self.sess.delete(BASE + path, headers=h, params=params, timeout=8)
            try: js = r.json()
            except ValueError: js = {"text": r.text[:300]}
            return r.status_code, js, (time.perf_counter() - t0) * 1000
        except requests.RequestException as e:
            return 0, {"error": str(e)}, (time.perf_counter() - t0) * 1000

    def _account(self, t_dec):
        sec = int(time.time())
        with self.lock:
            self.per_sec[sec] += 1
            if len(self.per_sec) > 4000:
                for s0 in sorted(self.per_sec)[:2000]: del self.per_sec[s0]
            self.decide_ms.append((time.perf_counter() - t_dec) * 1000)

    def create(self, ticker, o, close_dt, on_done):
        o.t_decide = time.perf_counter()
        self._submit(self._create, ticker, o, close_dt, on_done)

    def _create(self, ticker, o, close_dt, on_done):
        try:
            wait = self.bucket.take()
            if wait > 0.001: self.n["throttled"] += 1
            self.wait_ms.append(wait * 1000)
            t0 = time.perf_counter()
            o.coid = str(uuid.uuid4())
            expire = (close_dt - dt.timedelta(seconds=EXPIRE_BEFORE_CLOSE_SEC)) if (self.use_expiry and close_dt) else None
            body = order_body(ticker, o.side, o.px, o.size, o.coid, expire, self.group_id)
            _hdrs(self.k, "POST", ORDER_CREATE_PATH)              # signing cost is part of the timing in every mode
            self.build_ms.append((time.perf_counter() - t0) * 1000)
            self._account(o.t_decide)
            if self.send:
                code, js, rtt = self._timed_send("POST", ORDER_CREATE_PATH, body)
                if code == 400 and "expir" in json.dumps(js).lower() and expire is not None:
                    self.use_expiry = False                         # Kalshi did not accept the expiry field: drop it once
                    self.say(f"[orders] Kalshi rejected expiration_time ({json.dumps(js)[:160]}) — sending without it from now on")
                    body.pop("expiration_time", None)
                    code, js, rtt = self._timed_send("POST", ORDER_CREATE_PATH, body)
                self.rtt_ms.append(rtt)
                od = js.get("order", js) if isinstance(js, dict) else {}
                oid = od.get("order_id")
                ok = code in (200, 201) and bool(oid)
                result = "resting" if ok else ("rejected" if 400 <= code < 500 else "error")
            else:
                code, js, rtt, ok, oid, result = "", {}, 0.0, True, "dry-" + o.coid[:8], "built, NOT sent (dry run)"
            self.n["creates"] += 1
            if not ok: self.n["rejects" if result == "rejected" else "errors"] += 1
            if ok:
                with self.lock: self.by_oid[oid] = (ticker, o)
            self._write([now_iso(), self.mode, "create", ticker, o.strat, o.side, o.px, o.size, o.coid, oid or "",
                         result if ok else f"{result}: {json.dumps(js)[:200]}", code, round(wait * 1000, 1),
                         round(self.build_ms[-1], 2), round(rtt, 1), round(self.decide_ms[-1], 1),
                         json.dumps(body, separators=(",", ":"))])
            on_done(ticker, o, ok, oid)
        except Exception as e:
            self.n["errors"] += 1
            print(f"[orders] create failed: {e}")
            try: on_done(ticker, o, False, None)
            except Exception: pass

    def cancel(self, ticker, o, reason):
        o.t_decide = time.perf_counter()
        self._submit(self._cancel, ticker, o, reason)

    def _cancel(self, ticker, o, reason):
        try:
            wait = self.bucket.take()
            if wait > 0.001: self.n["throttled"] += 1
            self.wait_ms.append(wait * 1000)
            t0 = time.perf_counter()
            path = ORDER_CANCEL_PATH.format(order_id=o.oid)
            _hdrs(self.k, "DELETE", path)
            self.build_ms.append((time.perf_counter() - t0) * 1000)
            self._account(o.t_decide)
            if self.send:
                code, js, rtt = self._timed_send("DELETE", path)
                self.rtt_ms.append(rtt)
                result = "cancelled" if code in (200, 204) else ("gone (filled or already cancelled)" if code == 404 else f"error: {json.dumps(js)[:160]}")
            else:
                code, rtt, result = "", 0.0, "built, NOT sent (dry run)"
            self.n["cancels"] += 1
            self._write([now_iso(), self.mode, f"cancel ({reason})", ticker, o.strat, o.side, o.px, o.left, o.coid or "",
                         o.oid or "", result, code, round(wait * 1000, 1), round(self.build_ms[-1], 2), round(rtt, 1),
                         round(self.decide_ms[-1], 1), f"DELETE {path}"])
            if self.send and code in (200, 204):
                threading.Thread(target=self._forget_later, args=(o.oid,), daemon=True).start()
        except Exception as e:
            self.n["errors"] += 1
            print(f"[orders] cancel failed: {e}")

    def _forget_later(self, oid):
        time.sleep(30)                                      # a fill can still arrive just after a cancel
        with self.lock: self.by_oid.pop(oid, None)

    def lookup(self, oid):
        with self.lock: return self.by_oid.get(oid)

    def probe(self):
        """Round trip to Kalshi (read-only, public market status) — the network time an order would take."""
        t0 = time.perf_counter()
        d = api_get(self.sess, self.k, "/exchange/status")
        ms = (time.perf_counter() - t0) * 1000
        if d is not None: self.probe_ms.append(ms)
        return ms if d is not None else None

    def startup_checks(self):
        """Read-only checks against the account (safe in every mode), then demo/live housekeeping."""
        ms = self.probe()
        self.say(f"[account] Kalshi round trip: {'failed' if ms is None else f'{ms:.0f} ms'}")
        b = api_get(self.sess, self.k, "/portfolio/balance")
        if b is None:
            self.say("[account] could not read the balance — check the API key")
        else:
            parts = []
            for x in b.get("balance_breakdown", []) or []:
                try: parts.append(f"shard {x.get('exchange_index')}: ${float(x.get('balance')):,.2f}")
                except (TypeError, ValueError): pass
            try: tot = f"${int(b.get('balance')) / 100:,.2f}"
            except (TypeError, ValueError): tot = str(b.get("balance"))
            self.say(f"[account] balance {tot}" + (f" ({'; '.join(parts)})" if parts else ""))
        d = api_get(self.sess, self.k, ORDERS_LIST_PATH, {"status": "resting", "limit": 200})
        resting = (d or {}).get("orders", []) or []
        self.say(f"[account] resting orders on the account now: {len(resting)}")
        if self.send:
            for od in resting:                              # start clean: nothing of ours left from a previous run
                oid = od.get("order_id")
                if oid:
                    code, js, _ = self._timed_send("DELETE", ORDER_CANCEL_PATH.format(order_id=oid))
                    self.say(f"[account] cancelled leftover order {oid}: HTTP {code}")
            code, js, _ = self._timed_send("POST", ORDER_GROUP_PATH, {"contracts_limit": ORDER_GROUP_LIMIT})
            self.group_id = (js or {}).get("order_group_id") if code in (200, 201) else None
            self.say(f"[orders] order group (Kalshi cancels everything if {ORDER_GROUP_LIMIT}+ contracts fill within 15 s): "
                     + (self.group_id if self.group_id else f"NOT created (HTTP {code} {json.dumps(js)[:160]}) — continuing without it"))
        else:
            self.say(f"[orders] dry run: would create an order group (limit {ORDER_GROUP_LIMIT} contracts / 15 s) "
                     f"and cancel the {len(resting)} resting order(s) above")

    def summary(self):
        n = self.n
        peak = max(self.per_sec.values()) if self.per_sec else 0
        f = lambda xs: "n/a" if not xs else f"{_pct(xs, 0.5):.1f} / {_pct(xs, 0.95):.1f} ms"
        sent = "sent" if self.send else "built, not sent"
        lines = [f"order actions {sent}: {n['creates']} creates, {n['cancels']} cancels"
                 + (f", {n['rejects']} rejected, {n['errors']} errors" if self.send or n['errors'] else "")
                 + f" · busiest second {peak} actions (limit {MAX_WRITES_PER_SEC}/s) · waited for the limit {n['throttled']} times"
                 + (f" (longest {max(self.wait_ms):.0f} ms)" if self.wait_ms and max(self.wait_ms) > 1 else ""),
                 f"speed (median / 95%): decision -> request ready {f(self.decide_ms)} · build + sign {f(self.build_ms)} · "
                 f"Kalshi round trip {f(self.probe_ms)}" + (f" · order round trip {f(self.rtt_ms)}" if self.send else "")]
        return lines

class PrivateFeed(PriceFeed):
    """Price feed that, in demo/live, also subscribes to Kalshi's private 'fill' channel (our own fills)."""
    CHANNELS = ("ticker", "trade", "fill")
    def __init__(self, k):
        super().__init__(k)
        self.on_fill = None
        self.raw_logged = 0
    def _on_message(self, raw):
        try: msg = json.loads(raw)
        except (json.JSONDecodeError, TypeError): return
        if msg.get("type") != "fill": return super()._on_message(raw)
        m = msg.get("msg") or {}
        if self.raw_logged < 20:                          # first fills printed raw, to check the field names
            self.raw_logged += 1; print(f"[fill-raw] {json.dumps(m)[:300]}")
        try: n = float(m.get("count_fp", m.get("count")))
        except (TypeError, ValueError): n = None
        if self.on_fill and m.get("order_id") and n:
            try: self.on_fill(m.get("order_id"), n, m)
            except Exception as e: print(f"[fill-error] {e}")
# ---------------- the bot ----------------
CRYPTO = {"BTC", "ETH", "SOL", "XRP", "DOGE"}
def cls(a): return "crypto" if a in CRYPTO else "commodity"

def maker_fee_c(n, price_c):
    """Kalshi maker fee for one fill (series on its maker-fee list only): 0.0175 x n x P x (1-P), rounded up."""
    return math.ceil(0.0175 * n * price_c * (100 - price_c) / 100 - 1e-9)

def side_bid(side, yb, ya): return yb if side == "yes" else 100 - ya
def side_ask(side, yb, ya): return ya if side == "yes" else 100 - yb
def other(side): return "no" if side == "yes" else "yes"

class Order:
    """One resting bid of CONTRACTS at px on `side`, with the line of contracts ahead of it."""
    def __init__(self, strat, side, px, ahead, s2c, spread, spot, active):
        self.strat, self.side, self.px = strat, side, px
        self.size = self.left = CONTRACTS
        self.ahead = self.ahead0 = ahead
        self.s2c, self.spread, self.spot = s2c, spread, spot
        self.iso, self.t0 = now_iso(), time.time()
        self.active = active          # on the book (paper: at once; other modes: once the create is done)
        self.oid = self.coid = None   # exchange / client order ids
        self.cancel_on_ack = False    # cancelled before its create finished
        self.t_decide = None

class MarketState:
    def __init__(self):
        self.orders = {}                                  # (strat, side) -> resting Order
        self.done = collections.Counter()                 # (strat, side) -> fully filled orders
        self.filled = collections.Counter()               # (strat, side) -> contracts filled
        self.placed = collections.Counter()               # strat -> orders placed
        self.moved = collections.Counter()                # strat -> orders moved up to a new best bid
        self.fills = []                                   # fill dicts, settled at the close

def drive_free_mb(folder):
    try:
        import shutil
        return shutil.disk_usage(folder).free / 1e6
    except Exception:
        return None

def _usd(c): return f"{'+' if c >= 0 else '-'}${abs(c) / 100:,.2f}"

def main():
    global KEY_ID, PEM_PATH, BASE, WS_URL
    if MODE not in ("paper", "dry_run", "demo", "live"):
        sys.exit(f"MODE must be paper, dry_run, demo or live (it is {MODE!r})")
    if MODE == "live":
        sys.exit("LIVE mode is locked in MM15Min V2. Run dry_run, then demo, and review the results before a live version is built.")
    if MODE == "demo":
        if not DEMO_KEY_ID: sys.exit("demo mode needs DEMO_KEY_ID and DEMO_PEM_PATH (a key made on demo.kalshi.co)")
        KEY_ID, PEM_PATH, BASE, WS_URL = DEMO_KEY_ID, DEMO_PEM_PATH, DEMO_BASE, DEMO_WS_URL
    SIM = MODE in ("paper", "dry_run")        # fills simulated from the live book + trades
    GATE = MODE != "paper"                    # every order action goes through the order path
    banner = {"paper": "PAPER (simulated orders, order code not used)",
              "dry_run": "DRY RUN (exact orders built, signed, rate-limited and logged — NOTHING is sent; fills simulated)",
              "demo": "DEMO (orders SENT to Kalshi's demo site — play money)"}[MODE]
    print(f"{BOT_NAME} — {banner}")
    print(f"markets: {', '.join(asset(s) for s in SERIES)}")
    for s, r in STRATEGIES.items():
        cap = f" · stop bidding a side {r['max_imbalance']}+ contracts heavier than the other" if r["max_imbalance"] else ""
        print(f"strategy {s} ({r['name']}): rest {CONTRACTS}-contract bids at the best bid of any side priced "
              f"{r['min_c']}-{r['max_c']}c · {FROM_SEC // 60} min to {STOP_SEC}s before the close · up to "
              f"{MAX_ORDERS_PER_SIDE} filled orders per side per market{cap} · hold to settlement")
    if GATE:
        print(f"orders: post-only={POST_ONLY} · expire {EXPIRE_BEFORE_CLOSE_SEC}s before the close · at most "
              f"{MAX_WRITES_PER_SEC} actions/s · {ORDER_WORKERS} sender threads")
        print(f"safety: max {MAX_OPEN_ORDERS} resting orders · max {MAX_CONTRACTS_PER_MARKET} contracts per market · "
              f"stop new orders below -${DAILY_LOSS_LIMIT_USD} for the day · kill switch: create {KILL_FILE}")
    fm = drive_free_mb(os.path.dirname(FILLS_CSV))
    print(f"Drive free space: {'unknown' if fm is None else f'{fm:,.0f} MB'}")
    k = load_key()
    sess = requests.Session()
    sess.mount("https://", requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=len(SERIES) + ORDER_WORKERS + 10))
    lock = threading.RLock()
    markets, rest_px, state = {}, {}, {}
    settle_due, settled = {}, set()
    KEYS = [(s, c) for s in STRATEGIES for c in ("crypto", "commodity")]
    run = {key: {"net": 0, "fee": 0, "fills": 0, "ct": 0.0, "won_ct": 0.0, "markets": 0} for key in KEYS}
    day = {"date": now().date(), "net": 0}
    blocked = collections.Counter()
    flags = {"kill": False, "halted": False}

    def open_csv(path, cols):
        if os.path.exists(path):                       # a file with other columns: set it aside
            with open(path, newline="") as fh0:
                head = next(csv.reader(fh0), None)
            if head is not None and head != cols:
                old = path[:-4] + f"_old_{now():%Y%m%d_%H%M%S}.csv"
                os.replace(path, old); print(f"[log] {os.path.basename(path)} had different columns — moved to {os.path.basename(old)}")
        new = not os.path.exists(path)
        fh = open(path, "a", newline="")
        w = csv.writer(fh)
        if new: w.writerow(cols); fh.flush()
        return fh, w
    ffh, fw = open_csv(FILLS_CSV, FILL_COLS)
    mfh, mw = open_csv(MARKETS_CSV, MARKET_COLS)
    sfh, sw = open_csv(SETTLE_CSV, SETTLE_COLS)
    io_lock = threading.Lock()

    def s2c_of(t):
        m = markets.get(t)
        return (m["close"] - now()).total_seconds() if m else None

    def say(msg): print(f"[{now():%H:%M:%S}] {msg}")

    gw = OrderGateway(MODE, k, sess, say) if GATE else None

    def prices(t):
        return feed.get(t) or rest_px.get(t)

    def spot_of(t):
        a = asset(t)
        return spot.price(a) if (spot and a in CRYPTO) else None

    def fill(t, st, key, o, n, how, s2c):
        n = float(min(n, o.left))
        if n <= 1e-9: return
        o.left -= n
        st.filled[key] += n
        st.fills.append({"strat": o.strat, "side": o.side, "order_iso": o.iso, "order_s2c": o.s2c, "px": o.px,
                         "queue": o.ahead0, "spread": o.spread, "fill_iso": now_iso(), "fill_s2c": s2c,
                         "wait": time.time() - o.t0, "n": n, "how": how, "spot0": o.spot, "spot1": spot_of(t)})
        if o.left <= 1e-9:
            if st.orders.get(key) is o: st.orders.pop(key)
            st.done[key] += 1
        if PRINT_FILLS:
            s2 = "" if s2c is None else f"{s2c:.0f}s left"
            say(f"FILL [{o.strat}] {n:g}x {o.side.upper()} @ {o.px}c  {asset(t)} ({cls(asset(t))}) {t[-7:]}  {s2}  "
                f"({how}; {o.ahead0:,.0f} were ahead, waited {time.time() - o.t0:.0f}s)")

    def drop(t, st, key, o, reason):
        """Take an order off the book: remove it here and, in the order modes, cancel it on Kalshi."""
        if st.orders.get(key) is o: st.orders.pop(key)
        if not GATE: return
        if o.active and o.oid: gw.cancel(t, o, reason)
        else: o.cancel_on_ack = reason                 # its create is still on the way: cancel it when it lands

    def on_created(t, o, ok, oid):
        """Called by the order path when a create has been sent (demo) or built (dry run)."""
        with lock:
            st = state.get(t)
            if not ok:
                if st:
                    for key, x in list(st.orders.items()):
                        if x is o: st.orders.pop(key)
                return
            o.oid, o.active, o.t0 = oid, True, time.time()
            if SIM and book:                               # the line ahead is what rests at our price NOW
                q = book.level_qty(t, o.side, o.px)
                if q is not None: o.ahead = o.ahead0 = q
            if o.cancel_on_ack: gw.cancel(t, o, o.cancel_on_ack)

    def on_fill(oid, n, raw):
        """demo/live: a real fill of one of our orders (Kalshi's private fill channel)."""
        hit = gw.lookup(oid) if gw else None
        if not hit: return
        t, o = hit
        with lock:
            st = state.get(t)
            if st: fill(t, st, (o.strat, o.side), o, n, "exchange fill", s2c_of(t))

    def open_orders():
        return sum(len(x.orders) for x in state.values())

    def market_contracts(st):
        return sum(st.filled.values()) + sum(o.left for o in st.orders.values())

    def can_place(st):
        if flags["kill"]: return "kill switch"
        if flags["halted"]: return "daily loss limit"
        if GATE and open_orders() >= MAX_OPEN_ORDERS: return "max open orders"
        if market_contracts(st) + CONTRACTS > MAX_CONTRACTS_PER_MARKET: return "max contracts per market"
        return None

    def evaluate(t):
        px = prices(t)
        s2c = s2c_of(t)
        if px is None or s2c is None: return                    # unknown timing or price: do nothing
        yb, ya = px
        if not (0 <= yb < ya <= 100): return
        with lock:
            st = state.setdefault(t, MarketState())
            # 1) resting orders: time stop, the other side came down to us, line ahead, moved up / out of band
            for key, o in list(st.orders.items()):
                b, a = side_bid(o.side, yb, ya), side_ask(o.side, yb, ya)
                r = STRATEGIES[o.strat]
                if s2c <= STOP_SEC or flags["kill"]:
                    drop(t, st, key, o, "time stop" if s2c <= STOP_SEC else "kill switch"); continue
                if not o.active: continue
                if SIM:
                    if a <= o.px:
                        fill(t, st, key, o, o.left, "ask reached our bid", s2c); continue
                    q = book.level_qty(t, o.side, o.px) if book else None
                    if q is not None: o.ahead = min(o.ahead, q)
                if b > o.px and o.left >= o.size:              # outbid before any fill: move up (or cancel)
                    drop(t, st, key, o, "outbid")
                    if r["min_c"] <= b <= r["max_c"]: st.moved[o.strat] += 1
            # 2) new orders at the best bid
            if not (STOP_SEC < s2c <= FROM_SEC) or not book: return
            for s, r in STRATEGIES.items():
                for side in ("yes", "no"):
                    key = (s, side)
                    if key in st.orders or st.done[key] >= MAX_ORDERS_PER_SIDE: continue
                    b = side_bid(side, yb, ya)
                    if not (r["min_c"] <= b <= r["max_c"]): continue
                    if r["max_imbalance"] is not None and st.filled[key] - st.filled[(s, other(side))] >= r["max_imbalance"]:
                        continue
                    q = book.level_qty(t, side, b)
                    if q is None or q < 1: continue           # book not known at this price yet
                    why = can_place(st)
                    if why:
                        blocked[why] += 1; continue
                    o = Order(s, side, b, q, s2c, ya - yb, spot_of(t), active=not GATE)
                    st.orders[key] = o
                    st.placed[s] += 1
                    if GATE: gw.create(t, o, markets[t]["close"], on_created)

    def on_tick(t, yb, ya):
        if t in markets: evaluate(t)

    def on_trade(t, yes_price, count, taker_side):
        """Simulated fills: a taker selling into the YES bids (taker bought NO) or into the NO bids (taker bought YES)."""
        if t not in markets or not SIM: return
        s2c = s2c_of(t)
        with lock:
            st = state.get(t)
            if not st: return
            for key, o in list(st.orders.items()):
                if not o.active: continue
                if o.side == "yes" and taker_side == "no": p = yes_price
                elif o.side == "no" and taker_side == "yes": p = 100 - yes_price
                else: continue
                if p < o.px:                                  # sold below our bid: our level was used up first
                    o.ahead = 0
                    fill(t, st, key, o, count, "sold through", s2c)
                elif p == o.px:
                    if o.ahead >= count: o.ahead -= count
                    else:
                        n = count - o.ahead; o.ahead = 0
                        fill(t, st, key, o, n, "queue", s2c)

    def finish(t, result):
        with lock:
            st = state.get(t)
            if not st: return
            for key, o in list(st.orders.items()): drop(t, st, key, o, "market closed")
            fills = list(st.fills)
        c = cls(asset(t))
        by = collections.defaultdict(list)
        for f in fills: by[f["strat"]].append(f)
        if now().date() != day["date"]: day.update(date=now().date(), net=0); flags["halted"] = False
        for s in STRATEGIES:
            fs = by.get(s, [])
            if not fs and not st.placed[s]: continue
            key = (s, c); r = run[key]
            tot = fee_t = 0
            rows = []
            for f in fs:
                won = int((result == "yes") == (f["side"] == "yes"))
                pnl = f["n"] * (100 * won - f["px"])
                fee = maker_fee_c(f["n"], f["px"])
                net = pnl - (fee if MAKER_FEE_ON else 0)
                tot += net; fee_t += fee
                r["net"] += net; r["fee"] += fee; r["fills"] += 1; r["ct"] += f["n"]; r["won_ct"] += f["n"] * won
                rows.append([t, asset(t), c, s, f["side"].upper(), f["order_iso"], round(f["order_s2c"], 1), f["px"],
                             round(f["queue"], 2), f["spread"], f["fill_iso"],
                             "" if f["fill_s2c"] is None else round(f["fill_s2c"], 1), round(f["wait"], 1), round(f["n"], 2),
                             f["how"], "" if f["spot0"] is None else f["spot0"], "" if f["spot1"] is None else f["spot1"],
                             result, won, round(pnl, 2), fee, round(r["net"], 2)])
            day["net"] += tot
            if fs: r["markets"] += 1
            sides = {}
            for sd in ("yes", "no"):
                xs = [f for f in fs if f["side"] == sd]
                n = sum(f["n"] for f in xs)
                sides[sd] = (n, round(sum(f["n"] * f["px"] for f in xs) / n, 2) if n else "")
            with io_lock:
                for row in rows: fw.writerow(row)
                mw.writerow([t, asset(t), c, s, result, st.placed[s], st.moved[s],
                             sum(v for (ss, _), v in st.done.items() if ss == s),
                             round(sides["yes"][0], 2), sides["yes"][1], round(sides["no"][0], 2), sides["no"][1],
                             round(tot, 2), fee_t, round(r["net"], 2)])
                ffh.flush(); mfh.flush()
            if fs:
                parts = [f"{sides[sd][0]:g} {sd.upper()} @ {sides[sd][1]}c" for sd in ("yes", "no") if sides[sd][0]]
                say(f"SETTLED [{s}] {asset(t)} ({c}) {t[-7:]} result {result.upper()}: {len(fs)} fills, {', '.join(parts)}  "
                    f"net {_usd(tot)}  | {s} {c} run {_usd(r['net'])} ({r['ct']:,.0f} contracts)")
        if GATE and not flags["halted"] and day["net"] < -DAILY_LOSS_LIMIT_USD * 100:
            flags["halted"] = True
            say(f"!!! DAILY LOSS LIMIT: today's net {_usd(day['net'])} is below -${DAILY_LOSS_LIMIT_USD} — no new orders until tomorrow (UTC) !!!")

    def status():
        fm = drive_free_mb(os.path.dirname(FILLS_CSV))
        with lock:
            resting = sum(1 for st in state.values() for o in st.orders.values() if o.active)
        nb = sum(1 for t in markets if book and book.books.get(t) is not None) if book else 0
        say(f"[status] {MODE} · resting orders {resting} · order book known for {nb} markets"
            + ("" if fm is None else f" · Drive free {fm:,.0f} MB"))
        for s, rr in STRATEGIES.items():
            for c in ("crypto", "commodity"):
                r = run[(s, c)]
                per = r["net"] / r["ct"] if r["ct"] else 0
                won = 100 * r["won_ct"] / r["ct"] if r["ct"] else 0
                alt = (r["net"] + r["fee"]) if MAKER_FEE_ON else (r["net"] - r["fee"])
                print(f"           {s:3} {rr['name']:9} {c:9}: {r['markets']:3} markets, {r['fills']:4} fills, {r['ct']:7,.0f} contracts, "
                      f"{won:5.1f}% won, net {_usd(r['net']):>9} ({per:+.2f}c/contract)"
                      f"   {'without' if MAKER_FEE_ON else 'if'} maker fees: {_usd(alt)}")
        for c in ("crypto", "commodity"):
            net = sum(run[(s, c)]["net"] for s in STRATEGIES); ct = sum(run[(s, c)]["ct"] for s in STRATEGIES)
            print(f"           ALL {c:9}: net {_usd(net)} over {ct:,.0f} contracts")
        if gw:
            for line in gw.summary(): print(f"           {line}")
            if blocked: print("           blocked by safety limits: " + ", ".join(f"{k_} {v}" for k_, v in blocked.items()))
            print(f"           today (UTC) net {_usd(day['net'])}" + ("  — DAILY LOSS LIMIT HIT, no new orders" if flags["halted"] else ""))
        if fm is not None and fm < LOW_SPACE_MB:
            say(f"!!! DRIVE NEARLY FULL ({fm:,.0f} MB free) — logs may stop saving. Free up space now. !!!")

    def probe_loop():
        while True:
            time.sleep(LATENCY_PROBE_SEC)
            try: gw.probe()
            except Exception: pass

    global FEED, SPOT, BOOK
    if gw:
        gw.startup_checks()
        threading.Thread(target=probe_loop, daemon=True).start()
    spot = SPOT = make_spot()
    try: book = BOOK = make_book(k)
    except Exception as e: print(f"[book] not started ({e}) — no orders can be placed without it"); book = None
    feed = FEED = make_feed(k) if MODE in ("paper", "dry_run") else make_private_feed(k)
    feed.on_tick = on_tick
    feed.on_trade = on_trade
    if MODE == "demo": feed.on_fill = on_fill
    feed.ready.wait(timeout=10)

    last_refresh, last_status, last_kill = 0, time.time(), 0
    last_bookchk, last_vs, book_stale = time.time(), -1, 0
    try:
        while True:
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
                    live = {t for t in markets if 0 < (s2c_of(t) or 0) <= 15 * 60 + 60}
                    feed.track(live)
                    if book: book.track(live)
            if GATE and time.time() - last_kill >= 5:          # kill switch: a file on Drive
                last_kill = time.time()
                if not flags["kill"] and os.path.exists(KILL_FILE):
                    flags["kill"] = True
                    say(f"!!! KILL SWITCH ({KILL_FILE} found): cancelling every order, no new orders. Delete the file and restart to resume. !!!")
                    with lock:
                        for t, st in state.items():
                            for key, o in list(st.orders.items()): drop(t, st, key, o, "kill switch")
            for t in list(markets):
                s2c = s2c_of(t)
                if s2c is None: continue
                if s2c > 0:
                    evaluate(t)
                elif t not in settle_due:
                    with lock:
                        st = state.get(t)
                        if st:
                            for key, o in list(st.orders.items()): drop(t, st, key, o, "market closed")
                    settle_due[t] = [time.time(), time.time() + SETTLE_GIVEUP_SEC]
            checked = 0
            for t, due in list(settle_due.items()):
                if checked >= 4: break
                if time.time() < due[0]: continue
                checked += 1
                m = fetch_market(sess, k, t)
                res = m.get("result")
                if res in ("yes", "no"):
                    with io_lock:
                        sw.writerow([now_iso(), t, asset(t), m.get("close_time"), res,
                                     m.get("floor_strike"), m.get("expiration_value"), m.get("status")])
                        sfh.flush()
                    finish(t, res)
                    settled.add(t)
                    del settle_due[t]; markets.pop(t, None); rest_px.pop(t, None)
                    with lock: state.pop(t, None)
                elif time.time() > due[1]:
                    say(f"[settle] no result for {t} after {SETTLE_GIVEUP_SEC // 60} min — its fills are dropped")
                    settled.add(t)
                    del settle_due[t]; markets.pop(t, None)
                    with lock: state.pop(t, None)
                else:
                    due[0] = time.time() + SETTLE_RETRY_SEC
            if time.time() - last_status >= STATUS_EVERY_SEC:
                last_status = time.time()
                status()
            if book and time.time() - last_bookchk >= 60:          # order-book watchdog
                last_bookchk = time.time()
                vs = sum(book.version.values()) if book.version else 0
                live_now = {t for t in markets if 0 < (s2c_of(t) or 0) <= 900}
                book_stale = book_stale + 60 if (live_now and vs == last_vs) else 0
                last_vs = vs
                if book_stale >= 300:
                    say("[book] no order-book updates for 5 min — restarting the book feed")
                    try: book.stop()
                    except Exception: pass
                    try: book = BOOK = make_book(k); book.track(live_now)
                    except Exception as e: print(f"[book] restart failed ({e})"); book = None
                    book_stale, last_vs = 0, -1
            time.sleep(LOOP_SEC)
    except KeyboardInterrupt:
        print("[stopped] cancelling every resting order, then totals so far:")
        with lock:
            for t, st in state.items():
                for key, o in list(st.orders.items()): drop(t, st, key, o, "bot stopped")
        if gw:
            if not gw.drain(15): print("[stopped] some order actions were still running after 15 s")
            gw.pool.shutdown(wait=True)
        status()
    finally:
        feed.stop(); spot.stop()
        if book: book.stop()
        with io_lock:
            for fh in (ffh, mfh, sfh): fh.close()
        if gw and gw.log: gw.log.close()

def make_private_feed(k):
    f = PrivateFeed(k); f.start(); return f

FEED = None
SPOT = None
BOOK = None
