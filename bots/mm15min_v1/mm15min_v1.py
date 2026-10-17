# ============================================================
# MM15Min V1 — paper MARKET MAKER on Kalshi 15-minute markets — PAPER ONLY (no order is ever sent to Kalshi)
#
# IDEA: instead of buying at the ask (taker, pays a fee), rest buy orders at the BID and wait for a seller
#   to come to us (maker, no taker fee). Small edge per contract, many contracts.
#   Backtest (V38 trade flow, Oct 15-16): all Kalshi makers together earned about +0.9c per contract;
#   a slow bot waiting at the back of the queue did best resting bids on the favourite (85-98c).
#
# TWO STRATEGIES, run side by side on all 11 markets (5 crypto + 6 commodities), each with its own totals:
#   fav  "favourite":  rest a bid on any side whose best bid is 85-98c.
#                      Backtest: commodities +4.2c per contract, crypto +1.3c (uncertain).
#   mm   "two-sided":  rest bids on BOTH sides (YES and NO) whenever the side's bid is 3-97c, like a
#                      classic market maker. If one side has 30+ more contracts filled than the other,
#                      stop bidding that side. A YES + NO pair locks in the gap between the two prices.
#                      Backtest: crypto -0.5c, commodities about 0 — tested live because live may differ.
#   - 10 contracts per order at the best bid, from the start of the market until 30 s before the close.
#   - Up to 5 filled orders per side per market per strategy. Every fill is held to $1 or $0.
#   - If someone bids higher before we are filled, the order moves up to the new best bid
#     (back of the line). If the bid leaves the price band, the order is cancelled.
#
# FILLS (paper, modelled on the live order book and live trades):
#   - When the order is placed, the contracts already resting at that price are AHEAD of us.
#   - Every live trade where a taker sells into our price uses up the line ahead of us; once the line
#     ahead is used up, further selling at our price fills us. (The line also shrinks when orders
#     ahead are cancelled: it can never be longer than what the book shows at that price.)
#   - A taker selling BELOW our price means our price level was used up: we are filled first.
#   - If the other side's ask comes down to our price, we are filled (a seller would have hit us).
#   - No taker fee. Kalshi charges a small maker fee (0.0175 x P x (1-P)) on some series only;
#     it is logged in every row and shown as "if maker fees" in the status, not taken from the net.
#
# LOG (Colab): every fill, every settled market per strategy, and a status every 15 minutes with
#   totals for each strategy, crypto and commodities SEPARATELY, plus all crypto / all commodities.
# LOGS (Google Drive, small):
#   mm15min_v1_fills.csv        one row per fill (written when the market settles, with the result)
#   mm15min_v1_markets.csv      one row per market per strategy: orders, fills, average prices, net
#   mm15min_v1_settlements.csv  Kalshi's official result for every market
#   (no tick files — run V38 alongside if tick/flow data is wanted for the same hours)
# ============================================================

import os, sys, collections, csv, json, time, math, base64, threading, datetime as dt
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
BOT_NAME  = "MM15Min V1"
PEM_PATH  = '/content/drive/MyDrive/intraday key.pem'
KEY_ID    = "__KEY_ID__"
DRIVE     = '/content/drive/MyDrive/'
FILLS_CSV   = DRIVE + 'mm15min_v1_fills.csv'
MARKETS_CSV = DRIVE + 'mm15min_v1_markets.csv'
SETTLE_CSV  = DRIVE + 'mm15min_v1_settlements.csv'

SERIES = ["KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M",
          "KXGOLD15M", "KXSILVER15M", "KXWTI15M", "KXCOPPER15M",
          "KXPLATINUM15M", "KXPALLADIUM15M"]

# strategies: a side is bid while its best bid is inside [min_c, max_c]
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

FILL_COLS = ["window", "asset", "class", "strategy", "side", "order_iso", "order_s2c", "px", "queue_ahead",
             "spread_c", "fill_iso", "fill_s2c", "wait_s", "contracts", "how", "spot_at_order", "spot_at_fill",
             "result", "won", "pnl_c", "maker_fee_c", "run_net_c"]
MARKET_COLS = ["window", "asset", "class", "strategy", "result", "orders_placed", "orders_moved_up",
               "orders_filled", "yes_ct", "yes_avg_px", "no_ct", "no_avg_px", "pnl_c", "maker_fee_c", "run_net_c"]
SETTLE_COLS = ["logged_iso", "ticker", "asset", "close_time", "result", "floor_strike",
               "expiration_value", "status"]
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
    """One resting paper bid of CONTRACTS at px on `side`, with the line of contracts ahead of it."""
    def __init__(self, strat, side, px, ahead, s2c, spread, spot):
        self.strat, self.side, self.px = strat, side, px
        self.size = self.left = CONTRACTS
        self.ahead = self.ahead0 = ahead
        self.s2c, self.spread, self.spot = s2c, spread, spot
        self.iso, self.t0 = now_iso(), time.time()

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
    print(f"{BOT_NAME} — PAPER market maker (no orders are sent)")
    print(f"markets: {', '.join(asset(s) for s in SERIES)}")
    for s, r in STRATEGIES.items():
        cap = f" · stop bidding a side {r['max_imbalance']}+ contracts heavier than the other" if r["max_imbalance"] else ""
        print(f"strategy {s} ({r['name']}): rest {CONTRACTS}-contract bids at the best bid of any side priced "
              f"{r['min_c']}-{r['max_c']}c · {FROM_SEC // 60} min to {STOP_SEC}s before the close · up to "
              f"{MAX_ORDERS_PER_SIDE} filled orders per side per market{cap} · hold to settlement")
    print(f"fills: wait behind the contracts already at our price, filled by live trades · no taker fee · "
          f"maker fee {'TAKEN OFF' if MAKER_FEE_ON else 'shown only (if maker fees)'}")
    fm = drive_free_mb(os.path.dirname(FILLS_CSV))
    print(f"Drive free space: {'unknown' if fm is None else f'{fm:,.0f} MB'}")
    k = load_key()
    sess = requests.Session()
    sess.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=len(SERIES) + 10))
    lock = threading.RLock()
    markets, rest_px, state = {}, {}, {}
    settle_due, settled = {}, set()
    KEYS = [(s, c) for s in STRATEGIES for c in ("crypto", "commodity")]
    run = {key: {"net": 0, "fee": 0, "fills": 0, "ct": 0.0, "won_ct": 0.0, "markets": 0} for key in KEYS}

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
            st.orders.pop(key, None); st.done[key] += 1
        if PRINT_FILLS:
            s2 = "" if s2c is None else f"{s2c:.0f}s left"
            say(f"FILL [{o.strat}] {n:g}x {o.side.upper()} @ {o.px}c  {asset(t)} ({cls(asset(t))}) {t[-7:]}  {s2}  "
                f"({how}; {o.ahead0:,.0f} were ahead, waited {time.time() - o.t0:.0f}s)")

    def evaluate(t):
        px = prices(t)
        s2c = s2c_of(t)
        if px is None or s2c is None: return
        yb, ya = px
        if not (0 <= yb < ya <= 100): return
        with lock:
            st = state.setdefault(t, MarketState())
            # 1) resting orders: time stop, the other side came down to us, line ahead, moved up / out of band
            for key, o in list(st.orders.items()):
                b, a = side_bid(o.side, yb, ya), side_ask(o.side, yb, ya)
                r = STRATEGIES[o.strat]
                if s2c <= STOP_SEC:
                    st.orders.pop(key); continue
                if a <= o.px:
                    fill(t, st, key, o, o.left, "ask reached our bid", s2c); continue
                q = book.level_qty(t, o.side, o.px) if book else None
                if q is not None: o.ahead = min(o.ahead, q)
                if b > o.px and o.left >= o.size:              # outbid before any fill: move up (or cancel)
                    st.orders.pop(key)
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
                    st.orders[key] = Order(s, side, b, q, s2c, ya - yb, spot_of(t))
                    st.placed[s] += 1

    def on_tick(t, yb, ya):
        if t in markets: evaluate(t)

    def on_trade(t, yes_price, count, taker_side):
        """A taker selling into the YES bids (taker bought NO) or into the NO bids (taker bought YES)."""
        if t not in markets: return
        s2c = s2c_of(t)
        with lock:
            st = state.get(t)
            if not st: return
            for key, o in list(st.orders.items()):
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
            st.orders = {}
            fills = list(st.fills)
        c = cls(asset(t))
        by = collections.defaultdict(list)
        for f in fills: by[f["strat"]].append(f)
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

    def status():
        fm = drive_free_mb(os.path.dirname(FILLS_CSV))
        with lock:
            resting = sum(len(st.orders) for st in state.values())
        nb = sum(1 for t in markets if book and book.books.get(t) is not None) if book else 0
        say(f"[status] resting orders {resting} · order book known for {nb} markets"
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
        if fm is not None and fm < LOW_SPACE_MB:
            say(f"!!! DRIVE NEARLY FULL ({fm:,.0f} MB free) — logs may stop saving. Free up space now. !!!")

    global FEED, SPOT, BOOK
    spot = SPOT = make_spot()
    try: book = BOOK = make_book(k)
    except Exception as e: print(f"[book] not started ({e}) — no orders can be placed without it"); book = None
    feed = FEED = make_feed(k)
    feed.on_tick = on_tick
    feed.on_trade = on_trade
    feed.ready.wait(timeout=10)

    last_refresh, last_status = 0, time.time()
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
            for t in list(markets):
                s2c = s2c_of(t)
                if s2c is None: continue
                if s2c > 0:
                    evaluate(t)
                elif t not in settle_due:
                    with lock:
                        st = state.get(t)
                        if st: st.orders = {}
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
        print("[stopped] totals so far:")
        status()
    finally:
        feed.stop(); spot.stop()
        if book: book.stop()
        with io_lock:
            for fh in (ffh, mfh, sfh): fh.close()

FEED = None
SPOT = None
BOOK = None
