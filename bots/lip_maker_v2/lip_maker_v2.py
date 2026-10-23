# ============================================================
# LIP Maker V2 — a two-sided maker paid by Kalshi's Liquidity Incentive Program, on many markets · DRY RUN by default
#
# WHAT IT DOES: Kalshi pays a reward pool per market for resting orders near the best price, scored every second as
#   size x discount^(cents below the reference price), whether or not they fill. The bot picks the best-paying markets
#   in the series you choose (default: AAA daily gas prices by state, KXAAAGASD) and keeps one YES bid and one NO bid
#   resting at each market's reference price. A YES + NO pair always pays $1.00, so fills on both sides lock in the
#   gap between the two bids; the reward is paid on top.
# V2 (vs V1): many markets — order books and trades stream over Kalshi's websocket (no per-market polling) · the dry
#   run simulates our place in line (an order waits behind the contracts already at its price; a trade below our
#   price fills us) · live fills from one read of all our resting orders every few seconds · at most MAX_PER_STATE
#   markets per state (neighbouring strikes move together) · orders pulled if the price stream drops.
# RISK CONTROLS: post-only · at most MAX_NET contracts held one side minus the other per market · no orders in a
#   market's last PULL_BEFORE_CLOSE_MIN or outside its reward period · a FAST_MOVE_C move within FAST_MOVE_SEC pulls
#   both sides for COOL_OFF_SEC · sides quoted only at MIN_PX-MAX_PX · MAX_LOSS_USD: once fills are down this much
#   (marked at the best bids) every order is cancelled for the rest of the run · kill switch STOP_LIPMM.txt on Drive ·
#   a cancel is forgotten only once Kalshi confirms it · live: Kalshi-side expiry on every order, an order group,
#   leftover orders in SERIES cancelled at start, positions checked against Kalshi, 10-second countdown.
#   Every order expires on Kalshi's side ORDER_LIFE_MIN after it is placed and is renewed while the bot runs, so if
#   Colab stops without warning no order outlives the bot by more than 30 minutes.
# REWARD: the log shows the bot's ESTIMATE (each side scored against half the pool); Kalshi's real payout is on the
#   Rewards page / Account -> Activity -> Credits.
# LOGS (Drive): lip_maker_v2_fills.csv, lip_maker_v2_markets.csv, lip_maker_v2_data/orders_ (hourly, gzipped).
# ============================================================

import os, sys, collections, csv, json, time, math, base64, threading, uuid, datetime as dt
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
BOT_NAME = "LIP Maker V2"
# The SWITCHES cell (the cell that starts the bot) sets these; the values here are used only when it has not been run.
_sw = globals()
DRY_RUN       = _sw.get("DRY_RUN", True)            # True = nothing is sent.  False = LIVE, REAL MONEY
SERIES        = _sw.get("SERIES", ["KXAAAGASD"])    # markets whose ticker starts with one of these
CONTRACTS     = _sw.get("CONTRACTS", 10)            # resting on EACH side of each market
MAX_MARKETS   = _sw.get("MAX_MARKETS", 15)
MAX_PER_STATE = _sw.get("MAX_PER_STATE", 3)         # at most this many markets per series (per state for gas)
BUDGET_USD    = _sw.get("BUDGET_USD", 150)          # money tied up in resting orders + contracts held, all markets
MAX_NET       = _sw.get("MAX_NET", 20)              # per market: contracts held on one side minus the other
MAX_LOSS_USD  = _sw.get("MAX_LOSS_USD", 30)         # fills down this much: everything cancelled, no new quotes

PULL_BEFORE_CLOSE_MIN = 60    # no orders in a market's last 60 minutes
MIN_PX, MAX_PX = 5, 95        # quote a side only while its best bid is 5-95c (nearly decided markets are skipped)
FAST_MOVE_C    = 4            # the YES mid (median of the last STABLE_SEC) moving 4c+ ...
FAST_MOVE_SEC  = 60           # ... from its median a minute earlier pulls both sides ...
STABLE_SEC     = 15           # medians over 15 s: quotes vanishing for a few seconds (cancel/re-post) are not a move
COOL_OFF_SEC   = 300          # ... for 5 minutes
REQUOTE_HOLD_SEC = 5          # an order is moved once its price has been off target this long
ORDER_LIFE_MIN = 30           # every order expires on Kalshi's side 30 min after it is placed (if Colab dies, so do they)
ORDER_RENEW_MIN = 25          # ... and the bot replaces it after 25 min while it runs
KEEP_BONUS     = 1.25         # a market already quoted is kept unless another looks 25% better (fewer swaps)
PROGRAMS_REFRESH_SEC = 600    # re-read Kalshi's reward programs and re-pick markets every 10 minutes
LOOP_SEC       = 1            # one pass over the chosen markets every second (prices come from the stream)
FILL_CHECK_SEC = 4            # live: read all our resting orders (fills) every 4 s
RECONCILE_SEC  = 120          # live: compare our count of contracts held with Kalshi's every 2 minutes
FEED_LOST_SEC  = 20           # price stream down this long: every order pulled until it is back
STATUS_EVERY_SEC = 900
SETTLE_RETRY_SEC = 60
REWARD_UNIT    = 10000        # Kalshi's period_reward is in 1/100 cents (1,000,000 = $100)
BOOK_DUST      = 0.005

PEM_PATH  = '/content/drive/MyDrive/intraday key.pem'
KEY_ID    = "__KEY_ID__"
DRIVE     = '/content/drive/MyDrive/'
FILLS_CSV   = DRIVE + 'lip_maker_v2_fills.csv'
MARKETS_CSV = DRIVE + 'lip_maker_v2_markets.csv'
DATA_DIR    = DRIVE + 'lip_maker_v2_data/'
KILL_FILE   = DRIVE + 'STOP_LIPMM.txt'

MAX_WRITES_PER_SEC = 10       # Kalshi Basic API tier
ORDER_GROUP_LIMIT  = 300      # live: Kalshi cancels all our orders if this many contracts fill within 15 s
LIVE_COUNTDOWN_SEC = 10
ORDER_CREATE_PATH = "/portfolio/events/orders"
ORDER_CANCEL_PATH = "/portfolio/events/orders/{order_id}"
ORDERS_LIST_PATH  = "/portfolio/orders"
ORDER_GROUP_PATH  = "/portfolio/order_groups/create"

BASE    = "https://api.elections.kalshi.com/trade-api/v2"
PREF    = "/trade-api/v2"
WS_URL  = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
WS_PATH = "/trade-api/ws/v2"

FILL_COLS   = ["fill_iso", "ticker", "side", "price_c", "contracts", "how", "held_yes", "held_no", "live"]
MARKET_COLS = ["logged_iso", "ticker", "result", "yes_ct", "yes_cost_c", "no_ct", "no_cost_c", "pnl_c",
               "est_reward_c", "live"]
ORDER_COLS  = ["ts_iso", "mode", "action", "ticker", "side", "price_c", "count", "client_order_id", "order_id",
               "result", "http", "rtt_ms", "request"]

# ---------------- helpers ----------------
def now(): return dt.datetime.now(dt.timezone.utc)
def now_iso(): return now().isoformat()
def parse_iso(s):
    try: return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError): return None
def _usd(c): return f"{'+' if c >= 0 else '-'}${abs(c) / 100:,.2f}"

def num(x):
    try: return float(x)
    except (TypeError, ValueError): return None

def field(d, *names):
    """First present number among names (and their _fp / _dollars forms; _dollars converted to cents)."""
    for n in names:
        for k, mult in ((n + "_fp", 1), (n, 1), (n + "_dollars", 100)):
            v = num(d.get(k))
            if v is not None: return v * mult
    return None

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
        self._roll(); self.w.writerow(row); self.fh.flush()
    def close(self):
        if self.fh: self.fh.close(); self.fh = None

def compress_file(path):
    try:
        import gzip, shutil
        tmp = path[:-4] + ".tmp.gz"
        with open(path, "rb") as fi, gzip.open(tmp, "wb") as fo: shutil.copyfileobj(fi, fo)
        with open(path, "rb") as fi: n0 = sum(1 for _ in fi)
        with gzip.open(tmp, "rb") as fz: n1 = sum(1 for _ in fz)
        if n0 != n1: os.remove(tmp); return
        os.replace(tmp, path + ".gz"); os.remove(path)
    except Exception as e:
        print(f"[log] could not compress {os.path.basename(path)}: {e}")

class RateLimiter:
    """Never more than `rate` order actions in any 1-second span."""
    def __init__(self, rate):
        self.rate, self.times, self.lock = int(rate), collections.deque(), threading.Lock()
    def take(self):
        while True:
            with self.lock:
                t = time.monotonic()
                while self.times and t - self.times[0] >= 1.0: self.times.popleft()
                if len(self.times) < self.rate:
                    self.times.append(t); return
                need = 1.0 - (t - self.times[0]) + 0.001
            time.sleep(need)

def order_body(ticker, side, px, count, coid, expire_dt, group_id):
    """Kalshi V2 event order. Buying YES at px = a YES bid at px; buying NO at px = a YES ask at 100-px."""
    body = {"ticker": ticker, "client_order_id": coid, "side": "bid" if side == "yes" else "ask",
            "count": f"{int(count)}.00", "price": f"{(px if side == 'yes' else 100 - px) / 100:.4f}",
            "time_in_force": "good_till_canceled", "post_only": True,
            "self_trade_prevention_type": "taker_at_cross", "exchange_index": -1}
    if expire_dt is not None: body["expiration_time"] = int(expire_dt.timestamp())   # Unix seconds (Kalshi: int64)
    if group_id: body["order_group_id"] = group_id
    return body

# ---------------- order books and reward scoring ----------------
def book_levels(d):
    """{'yes': [(price c, size)], 'no': [...]} best (highest) first, from either order-book format."""
    book = (d or {}).get("orderbook_fp") or (d or {}).get("orderbook") or {}
    out = {}
    for side in ("yes", "no"):
        lv = book.get(f"{side}_dollars") or []
        if lv: rows = [(round(float(p) * 100), float(q)) for p, q in lv]
        else: rows = [(int(round(float(p))), float(q)) for p, q in (book.get(side) or [])]
        out[side] = sorted([(p, q) for p, q in rows if q > 0.005], reverse=True)
    return out

def minus_own(levels, own):
    """The book without our own resting size (own: {price: size})."""
    out = []
    for p, q in levels:
        q2 = q - own.get(p, 0)
        if q2 > 0.005: out.append((p, q2))
    return out

def reference(levels, target):
    """Walking down from the best bid: the first price where the size so far reaches 1/5 of the target."""
    cum = 0
    for p, q in levels:
        cum += q
        if cum >= target / 5: return p
    return None

def score(levels, ref, disc):
    return sum(q * disc ** max(0, ref - p) for p, q in levels)

# ---------------- order gateway ----------------
class Gateway:
    """Every order action: rate limit -> exact signed request -> sent (live) or not (dry run) -> logged."""
    def __init__(self, live, k, sess, say):
        self.live, self.k, self.sess, self.say = live, k, sess, say
        self.bucket = RateLimiter(MAX_WRITES_PER_SEC)
        self.log = HourlyCSV(DATA_DIR, "orders_live" if live else "orders", ORDER_COLS)
        self.group_id, self.use_expiry, self.raw_shown = None, True, 0
        self.n = collections.Counter()

    def _send(self, method, path, body=None, params=None):
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

    def _write(self, row):
        try: self.log.writerow(row)
        except Exception as e: print(f"[log] orders file: {e}")

    def create(self, ticker, side, px, count, expire):
        """Returns the order id (live: Kalshi's; dry run: a made-up one) or None."""
        self.bucket.take()
        coid = str(uuid.uuid4())
        body = order_body(ticker, side, px, count, coid, expire if self.use_expiry else None, self.group_id)
        _hdrs(self.k, "POST", ORDER_CREATE_PATH)
        self.n["creates"] += 1
        if not self.live:
            oid = "dry-" + coid[:8]
            self._write([now_iso(), "dry_run", "create", ticker, side, px, count, coid, oid, "built, NOT sent", "", 0,
                         json.dumps(body, separators=(",", ":"))])
            return oid
        code, js, rtt = self._send("POST", ORDER_CREATE_PATH, body)
        if code == 400 and "expir" in json.dumps(js).lower() and "expiration_time" in body:
            self.use_expiry = False
            self.say(f"[orders] Kalshi rejected expiration_time ({json.dumps(js)[:160]}) — sending without it from now on")
            body.pop("expiration_time", None)
            code, js, rtt = self._send("POST", ORDER_CREATE_PATH, body)
        if self.raw_shown < 3:
            self.raw_shown += 1
            self.say(f"[orders] Kalshi's raw answer to a create (HTTP {code}): {json.dumps(js)[:300]}")
        od = js.get("order", js) if isinstance(js, dict) else {}
        oid = od.get("order_id") if code in (200, 201) else None
        if code == 0 or code >= 500:                        # no clear answer: look it up, never send it twice
            found = self.find(ticker, coid)
            if found: oid = found.get("order_id")
            self.say(f"[orders] {ticker} create had no clear answer (HTTP {code}) — "
                     + (f"found on Kalshi as {oid}" if oid else "not found on Kalshi"))
        if not oid: self.n["rejects"] += 1
        self._write([now_iso(), "live", "create", ticker, side, px, count, coid, oid or "",
                     "resting" if oid else f"rejected: {json.dumps(js)[:200]}", code, round(rtt, 1),
                     json.dumps(body, separators=(",", ":"))])
        if not oid and self.n["rejects"] <= 5:
            self.say(f"[orders] {ticker} {side.upper()} @ {px}c rejected (HTTP {code}): {json.dumps(js)[:200]}")
        return oid

    def cancel(self, ticker, o, reason):
        self.bucket.take()
        path = ORDER_CANCEL_PATH.format(order_id=o["oid"])
        _hdrs(self.k, "DELETE", path)
        self.n["cancels"] += 1
        if not self.live:
            self._write([now_iso(), "dry_run", f"cancel ({reason})", ticker, o["side"], o["px"], o["left"], "", o["oid"],
                         "built, NOT sent", "", 0, f"DELETE {path}"])
            return True
        code, js, rtt = self._send("DELETE", path)
        if code in (404, 405) and "not found" not in json.dumps(js).lower():
            path = f"{ORDERS_LIST_PATH}/{o['oid']}"             # older cancel path
            code, js, rtt = self._send("DELETE", path)
        ok = code in (200, 204, 404)
        self._write([now_iso(), "live", f"cancel ({reason})", ticker, o["side"], o["px"], o["left"], "", o["oid"],
                     "cancelled" if code in (200, 204) else ("gone" if code == 404 else f"error: {json.dumps(js)[:160]}"),
                     code, round(rtt, 1), f"DELETE {path}"])
        return ok

    def find(self, ticker, coid):
        for _ in range(3):
            d = api_get(self.sess, self.k, ORDERS_LIST_PATH, {"ticker": ticker, "limit": 100})
            for od in (d or {}).get("orders", []) or []:
                if od.get("client_order_id") == coid: return od
            time.sleep(1.0)
        return None

    def order_state(self, oid):
        """(filled so far, still resting?) of one of our live orders, or (None, None)."""
        d = api_get(self.sess, self.k, f"{ORDERS_LIST_PATH}/{oid}")
        od = (d or {}).get("order") or {}
        if not od: return None, None
        filled = field(od, "fill_count")
        init, left = field(od, "initial_count"), field(od, "remaining_count")
        if filled is None and init is not None and left is not None: filled = init - left
        return filled, od.get("status") == "resting"

    def startup(self):
        b = api_get(self.sess, self.k, "/portfolio/balance")
        if b is None: self.say("[account] could not read the balance — check the API key")
        else:
            parts = []
            for x in b.get("balance_breakdown", []) or []:
                try: parts.append(f"shard {x.get('exchange_index')}: ${float(x.get('balance')):,.2f}")
                except (TypeError, ValueError): pass
            try: tot = f"${int(b.get('balance')) / 100:,.2f}"
            except (TypeError, ValueError): tot = str(b.get("balance"))
            self.say(f"[account] balance {tot}" + (f" ({'; '.join(parts)})" if parts else ""))
        d = api_get(self.sess, self.k, ORDERS_LIST_PATH, {"status": "resting", "limit": 200})
        ours = [od for od in (d or {}).get("orders", []) or [] if str(od.get("ticker", "")).startswith(tuple(SERIES))]
        if self.live:
            for od in ours:
                code, _, _ = self._send("DELETE", ORDER_CANCEL_PATH.format(order_id=od.get("order_id")))
                self.say(f"[account] cancelled leftover order {od.get('order_id')} in {od.get('ticker')}: HTTP {code}")
            code, js, _ = self._send("POST", ORDER_GROUP_PATH, {"contracts_limit": ORDER_GROUP_LIMIT})
            self.group_id = (js or {}).get("order_group_id") if code in (200, 201) else None
            self.say(f"[orders] order group (Kalshi cancels everything if {ORDER_GROUP_LIMIT}+ contracts fill within 15 s): "
                     + (self.group_id or f"NOT created (HTTP {code} {json.dumps(js)[:160]}) — continuing without it"))
        else:
            self.say(f"[orders] dry run: {len(ours)} resting order(s) of yours in these series would be cancelled at a live start")
        held = {}
        d = api_get(self.sess, self.k, "/portfolio/positions", {"limit": 200})
        for p in (d or {}).get("market_positions", []) or []:
            t, pos = p.get("ticker", ""), field(p, "position")
            if t.startswith(tuple(SERIES)) and pos: held[t] = pos
        return held


# ---------------- Kalshi websocket: order books + trade prints (from MM15Min V3/V4, which ran on it for days) ----------------
def px_c(m, name):
    """Price field in cents. Kalshi sends '<name>_dollars' ("0.8900") and/or '<name>' in whole cents (89)."""
    v = m.get(name + "_dollars")
    if v is not None:
        try: return round(float(v) * 100)
        except (TypeError, ValueError): pass
    v = m.get(name)
    if v is None: return None
    try: return int(round(float(v)))
    except (TypeError, ValueError): return None

class StreamFeed:
    """One websocket: the full order book of each tracked market (snapshot + deltas) and its trade prints."""
    CHANNELS = ("orderbook_delta", "trade")

    def __init__(self, k):
        self.k = k
        self.lock = threading.RLock()
        self.send_lock = threading.Lock()
        self.tracked, self.subscribed, self.sub_pending, self.sids = set(), set(), False, {}
        self.ws = None
        self.ready = threading.Event()
        self.stop_flag = threading.Event()
        self._next_id = 1
        self.connect_count = 0
        self.books = {}            # ticker -> {"yes": {price_c: qty}, "no": {price_c: qty}}
        self.last_seq = None
        self.gap_times = []
        self.on_trade = None       # on_trade(ticker, yes_price, count, taker_side)
        self.down_since = time.time()

    def _cmd_id(self):
        with self.lock:
            i = self._next_id; self._next_id += 1
        return i

    def _headers(self):
        ts = str(int(time.time() * 1000))
        return [f"KALSHI-ACCESS-KEY: {KEY_ID}", f"KALSHI-ACCESS-SIGNATURE: {_sign(self.k, ts, 'GET', WS_PATH)}",
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
                    self.ws, self.sids, self.subscribed, self.sub_pending = ws, {}, set(), False
                    self.books.clear(); self.last_seq = None
                self.connect_count += 1
                backoff = 1
                self._sync()
                print(f"[ws] connected (#{self.connect_count})", flush=True)
                self.ready.set(); self.down_since = None
                while not self.stop_flag.is_set():
                    raw = ws.recv()
                    if raw: self._on_message(raw)
            except Exception as e:
                if self.stop_flag.is_set(): break
                self.ready.clear()
                if self.down_since is None: self.down_since = time.time()
                try:
                    if self.ws: self.ws.close()
                except Exception: pass
                with self.lock:
                    self.ws, self.sids, self.subscribed, self.sub_pending = None, {}, set(), False
                print(f"[ws] disconnected ({e}) — reconnecting in {backoff:.0f}s", flush=True)
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)

    def _send(self, obj):
        with self.send_lock:
            if self.ws: self.ws.send(json.dumps(obj))

    def _subscribe(self, tickers):
        with self.lock:
            self.books.clear(); self.last_seq = None
        self.subscribed, self.sub_pending = set(tickers), True
        for ch in self.CHANNELS:
            self._send({"id": self._cmd_id(), "cmd": "subscribe", "params": {"channels": [ch], "market_tickers": sorted(tickers)}})

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
        m = msg.get("msg") or {}
        if mtype == "subscribed":
            if m.get("channel") in self.CHANNELS: self.sids[m["channel"]] = m.get("sid")
            if len(self.sids) == len(self.CHANNELS) and self.sub_pending:
                self.sub_pending = False
                self._sync()
        elif mtype in ("orderbook_snapshot", "orderbook_delta"):
            seq = msg.get("seq")
            if seq is not None and self.last_seq is not None and seq != self.last_seq + 1:
                t0 = time.time()
                self.gap_times = [x for x in self.gap_times if x > t0 - 3600] + [t0]
                if len(self.gap_times) <= 5:                     # at most 5 resubscribes per hour
                    print(f"[book] sequence gap ({self.last_seq} -> {seq}) — reconnecting for fresh snapshots", flush=True)
                    try: self.ws.close()
                    except Exception: pass
                    return
            if seq is not None: self.last_seq = seq
            t = m.get("market_ticker")
            if not t or t not in self.tracked: return
            with self.lock:
                if mtype == "orderbook_snapshot":
                    self.books[t] = {"yes": self._levels(m, "yes"), "no": self._levels(m, "no")}
                else:
                    b, side, p = self.books.get(t), m.get("side"), px_c(m, "price")
                    try: d = float(m.get("delta_fp", m.get("delta")))
                    except (TypeError, ValueError): return
                    if b is None or side not in ("yes", "no") or p is None: return
                    q = b[side].get(p, 0) + d
                    if q > BOOK_DUST: b[side][p] = q
                    else: b[side].pop(p, None)
        elif mtype == "trade":
            t = m.get("market_ticker")
            if t not in self.tracked: return
            yp = px_c(m, "yes_price")
            try: n = float(m.get("count_fp", m.get("count")))
            except (TypeError, ValueError): n = None
            side = m.get("taker_side")
            if yp is None or not n or side not in ("yes", "no"): return
            if self.on_trade:
                try: self.on_trade(t, yp, n, side)
                except Exception as e: print(f"[trade-error] {e}", flush=True)
        elif mtype == "error":
            print(f"[ws-error] {m}", flush=True)

    def track(self, tickers):
        with self.lock: self.tracked = set(tickers)
        self._sync()

    def _sync(self):
        with self.lock:
            if self.ws is None or self.sub_pending: return
            want = set(self.tracked)
            if not want: return
            if len(self.sids) < len(self.CHANNELS):
                self._subscribe(want); return
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

    def levels(self, t):
        """{'yes': [(price, qty)], 'no': [...]} best first, or None while the stream or this book is not known."""
        if not self.ready.is_set(): return None
        with self.lock:
            b = self.books.get(t)
            if b is None: return None
            return {s: sorted(b[s].items(), reverse=True) for s in ("yes", "no")}

def make_feed(k):
    f = StreamFeed(k); f.start(); return f

# ---------------- the bot ----------------
def check_switches():
    if not isinstance(DRY_RUN, bool): sys.exit(f"DRY_RUN must be True or False without quotes (it is {DRY_RUN!r})")
    if not (isinstance(SERIES, (list, tuple)) and SERIES and all(isinstance(s, str) and s.startswith("KX") for s in SERIES)):
        sys.exit(f'SERIES must be a list of ticker beginnings like ["KXAAAGASD"] (it is {SERIES!r})')
    for name, lo, hi in (("CONTRACTS", 1, 500), ("MAX_MARKETS", 1, 100), ("MAX_PER_STATE", 1, 100), ("MAX_NET", 1, 1000)):
        v = globals()[name]
        if not (isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi):
            sys.exit(f"{name} must be a whole number from {lo} to {hi} (it is {v!r})")
    for name in ("BUDGET_USD", "MAX_LOSS_USD"):
        v = globals()[name]
        if not (isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0):
            sys.exit(f"{name} must be a positive number of dollars (it is {v!r})")
    if MAX_NET < CONTRACTS:
        sys.exit(f"MAX_NET ({MAX_NET}) must be at least CONTRACTS ({CONTRACTS}), or a full fill could break it")
    if not DRY_RUN and (not KEY_ID or KEY_ID == "__KEY_ID__"):
        sys.exit("live trading needs KEY_ID (your Kalshi API key id) in the settings")

def group_of(t): return t.split("-")[0]           # the series: one state for the gas markets

class Market:
    def __init__(self, ticker):
        self.t = ticker
        self.prog = None                  # the reward program: per_hour ($), target, disc, start, end
        self.close = None
        self.quoting = False              # picked now (False: only followed until settled)
        self.orders = {"yes": None, "no": None}     # {"oid", "side", "px", "left", "filled", "since_off", "ahead"}
        self.held = {"yes": 0.0, "no": 0.0}
        self.cost = {"yes": 0.0, "no": 0.0}         # cents
        self.best = {"yes": None, "no": None}
        self.mids = collections.deque()             # (time, YES mid)
        self.cool_until = 0
        self.reward_c = 0.0                         # estimated reward, cents
        self.share = {"yes": 0.0, "no": 0.0}
        self.adj = 0.0                              # live: Kalshi's net position minus ours (missed fills)
        self.est, self.cap, self.missed = 0.0, 0, 0  # at the last pick: $/h estimate, cents tied up, picks its book was unquotable
        self.done = False
    def net(self): return self.held["yes"] - self.held["no"] + self.adj
    def has_any(self): return bool(self.held["yes"] or self.held["no"] or self.orders["yes"] or self.orders["no"])
    def pnl_c(self):
        """Fills marked at the best bids; a YES + NO pair is worth 100c whatever happens."""
        pairs = min(self.held["yes"], self.held["no"])
        v = 100 * pairs
        for s in ("yes", "no"):
            v += (self.held[s] - pairs) * (self.best[s] or 0)
        return v - self.cost["yes"] - self.cost["no"]

def main():
    check_switches()
    live = not DRY_RUN
    if live:
        print("!" * 100); print(f"!!!  {BOT_NAME} — LIVE: REAL ORDERS WITH REAL MONEY  !!!"); print("!" * 100)
    else:
        print(f"{BOT_NAME} — DRY RUN (exact orders built and signed — NOTHING is sent; fills simulated from Kalshi's trades, "
              f"each order waiting its turn behind the contracts already at its price)")
    print(f"markets: Kalshi liquidity-reward programs in {', '.join(SERIES)} · best {MAX_MARKETS} by estimated reward, at most "
          f"{MAX_PER_STATE} per state/series · {CONTRACTS} contracts resting on EACH side · budget ${BUDGET_USD}")
    print(f"limits: at most {MAX_NET} contracts net per market · fills down ${MAX_LOSS_USD}: everything cancelled, no new quotes · "
          f"no orders in the last {PULL_BEFORE_CLOSE_MIN} min · {FAST_MOVE_C}c move in {FAST_MOVE_SEC}s (medians over "
          f"{STABLE_SEC}s, so a quote vanishing for a few seconds is not a move): both sides pulled for "
          f"{COOL_OFF_SEC / 60:g} min · sides quoted only at {MIN_PX}-{MAX_PX}c · kill switch: create {KILL_FILE}")
    print("reward shown here is the bot's ESTIMATE; Kalshi's real payout: Rewards page / Account -> Activity -> Credits")
    print(f"every order expires on Kalshi's side {ORDER_LIFE_MIN} min after it is placed (renewed after {ORDER_RENEW_MIN} min "
          f"while the bot runs) · a quoted market is kept unless another looks {round(100 * (KEEP_BONUS - 1))}% better")
    if live and LIVE_COUNTDOWN_SEC > 0:
        for i in range(int(LIVE_COUNTDOWN_SEC), 0, -1):
            print(f"LIVE trading starts in {i} s — stop the cell now to cancel", flush=True); time.sleep(1)
    k = load_key()
    sess = requests.Session()
    sess.mount("https://", requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=16))
    def say(msg): print(f"[{now():%H:%M:%S}] {msg}", flush=True)
    gw = Gateway(live, k, sess, say)
    held0 = gw.startup()
    lock = threading.RLock()
    mk = {}                                            # ticker -> Market
    for t, pos in held0.items():
        m = mk.setdefault(t, Market(t))
        m.held["yes" if pos > 0 else "no"] = abs(pos)
        say(f"[account] already holding {abs(pos):.0f} {'YES' if pos > 0 else 'NO'} in {t} — counted in the net limit")
    flags = {"halted": False, "kill": False, "feed_lost": False}
    os.makedirs(DRIVE, exist_ok=True)
    def open_csv(path, cols):
        new = not os.path.exists(path)
        fh = open(path, "a", newline=""); w = csv.writer(fh)
        if new: w.writerow(cols); fh.flush()
        return fh, w
    fills_fh, fills_w = open_csv(FILLS_CSV, FILL_COLS)
    mkts_fh, mkts_w = open_csv(MARKETS_CSV, MARKET_COLS)
    totals = {"settled_pnl": 0.0, "settled_reward": 0.0, "fills": 0, "markets_settled": 0}
    info = {}                                          # ticker -> {"close", "status"} (from Kalshi, cached)
    feed = make_feed(k)

    def fetch_programs():
        progs, cursor = [], None
        for _ in range(50):
            p = {"type": "liquidity", "status": "active", "limit": 1000}
            if cursor: p["cursor"] = cursor
            d = api_get(sess, k, "/incentive_programs", p)
            if d is None: return None
            progs += d.get("incentive_programs") or []
            cursor = d.get("next_cursor")
            if not cursor: break
        out = {}
        for p in progs:
            t = p.get("market_ticker", "")
            if not t.startswith(tuple(SERIES)): continue
            start, end = parse_iso(p.get("start_date")), parse_iso(p.get("end_date"))
            if not start or not end or end <= now(): continue
            hours = max((end - start).total_seconds() / 3600, 0.25)
            pr = dict(per_hour=num(p.get("period_reward")) / REWARD_UNIT / hours, start=start, end=end,
                      target=field(p, "target_size") or 0, disc=(num(p.get("discount_factor_bps")) or 5000) / 10000)
            if t not in out or pr["per_hour"] > out[t]["per_hour"]: out[t] = pr
        return out

    def load_info(tickers):
        """Close time and status of new markets, one request per event (all its strikes at once)."""
        events = sorted({t.rsplit("-", 1)[0] for t in tickers if t not in info})
        for ev in events:
            d = api_get(sess, k, "/markets", {"event_ticker": ev, "limit": 200})
            for md in (d or {}).get("markets", []) or []:
                info[md.get("ticker")] = {"close": parse_iso(md.get("close_time")), "status": md.get("status")}
            time.sleep(0.05)
        for t in tickers:                              # anything the event read did not cover
            if t not in info:
                md = (api_get(sess, k, f"/markets/{t}") or {}).get("market") or {}
                info[t] = {"close": parse_iso(md.get("close_time")), "status": md.get("status")}

    def own_of(m, s):
        o = m.orders[s]
        return {o["px"]: o["left"]} if (o and live) else {}   # dry run: our orders are not in Kalshi's book

    def book_of(m):
        """(the book without our orders, the book as scored: with them) or (None, None)."""
        lv = feed.levels(m.t)
        if lv is None: return None, None
        others = {s: minus_own(lv[s], own_of(m, s)) for s in ("yes", "no")}
        if live: return others, lv
        withus = {}
        for s in ("yes", "no"):
            o = m.orders[s]
            rows = dict(others[s])
            if o: rows[o["px"]] = rows.get(o["px"], 0) + o["left"]
            withus[s] = sorted(rows.items(), reverse=True)
        return others, withus

    def estimate(pr, lv):
        refs, est = {}, 0.0
        for s in ("yes", "no"):
            if not lv[s]: return None, None
            ref = reference(lv[s], pr["target"])
            if ref is None or not (MIN_PX <= lv[s][0][0] <= MAX_PX): return None, None
            refs[s] = ref
            est += pr["per_hour"] / 2 * CONTRACTS / (score(lv[s], ref, pr["disc"]) + CONTRACTS)
        if refs["yes"] + refs["no"] >= 100: return None, None
        return est, CONTRACTS * (refs["yes"] + refs["no"])

    def held_cost():
        return sum(m.cost["yes"] + m.cost["no"] for m in mk.values() if not m.done)

    first = {"pick": True}
    def pick():
        progs = fetch_programs()
        if progs is None: say("[programs] Kalshi's reward programs could not be read — keeping the current markets"); return
        load_info(list(progs))
        open_ok = lambda t: (info.get(t, {}).get("status") in (None, "open", "active") and info.get(t, {}).get("close")
                             and (info[t]["close"] - now()).total_seconds() >= (PULL_BEFORE_CLOSE_MIN + 30) * 60)
        cand_t = [t for t, pr in progs.items() if pr["start"] <= now() and open_ok(t)]
        with lock: keep = [t for t, m in mk.items() if not m.done and m.has_any()]
        feed.track(set(cand_t) | set(keep))
        if first["pick"]:                                # wait for the first snapshots
            first["pick"] = False
            end = time.time() + 20
            while time.time() < end and sum(feed.levels(t) is not None for t in cand_t) < 0.9 * len(cand_t): time.sleep(0.5)
        cands = []
        with lock:
            for t in cand_t:
                m = mk.get(t)
                lv = feed.levels(t)
                if lv is None: continue
                if m and live: lv = {s: minus_own(lv[s], own_of(m, s)) for s in ("yes", "no")}
                est, cap = estimate(progs[t], lv)
                if est: cands.append((est, cap, t))
            # a quoted market whose book is unquotable for a moment (a big quoter stepping out of every state at once) keeps
            # its place for one pick on its last estimate, instead of being swapped for a worse market and back again
            seen = {c[2] for c in cands}
            for t in cand_t:
                m = mk.get(t)
                if m and m.quoting and t not in seen and m.est and m.missed < 1:
                    m.missed += 1; cands.append((m.est, m.cap, t))
            # markets already quoted get a bonus in the ranking, so small changes in the estimate do not swap them
            cands.sort(key=lambda c: -c[0] * (KEEP_BONUS if (mk.get(c[2]) and mk[c[2]].quoting) else 1))
            chosen, used, per_grp = [], held_cost(), collections.Counter()
            for est, cap, t in cands:
                if len(chosen) >= MAX_MARKETS: break
                if per_grp[group_of(t)] >= MAX_PER_STATE or used + cap > BUDGET_USD * 100: continue
                chosen.append((est, cap, t)); used += cap; per_grp[group_of(t)] += 1
            names = {c[2] for c in chosen}
            for t, m in mk.items():
                if m.quoting and t not in names:
                    m.quoting = False; pull(m, "no longer picked")
                    say(f"[programs] {t}: no longer among the best — orders pulled (contracts held are followed to the close)")
            new = []
            for est, cap, t in chosen:
                m = mk.setdefault(t, Market(t))
                if not m.quoting: new.append(f"{t} (${est:.2f}/h)")
                if t in seen: m.est, m.cap, m.missed = est, cap, 0
                m.prog, m.close, m.quoting = progs[t], info[t]["close"], True
            for t, m in mk.items():                      # markets followed for their contracts: keep the close time
                if m.close is None and t in info: m.close = info[t]["close"]
        if new: say(f"[programs] PICKED {len(new)}: " + ", ".join(new))
        est_all = sum(c[0] for c in chosen)
        # over the rest of each market's reward period, stopping PULL_BEFORE_CLOSE_MIN before its close
        stop_at = lambda t: min(progs[t]["end"], info[t]["close"] - dt.timedelta(minutes=PULL_BEFORE_CLOSE_MIN))
        est_left = sum(c[0] * max(0.0, (stop_at(c[2]) - now()).total_seconds() / 3600) for c in chosen)
        say(f"[programs] {len(progs)} reward programs in {', '.join(SERIES)} · {len(cands)} quotable now · quoting {len(chosen)} "
            f"in {len(per_grp)} states/series · ${used / 100:,.0f} of the ${BUDGET_USD} budget · estimated ${est_all:.2f}/h "
            f"(${est_left:,.0f} by the end of today's quoting if this held)" + ("" if chosen else " — nothing suitable right now"))

    def drop(m, s, reason):
        """Cancel our order on this side. It is forgotten only once the cancel is confirmed (else retried next pass)."""
        o = m.orders[s]
        if not o: return True
        ok = gw.cancel(m.t, o, reason)
        if live: check_one(m, s)
        if ok: m.orders[s] = None
        elif not o.get("warned"):
            o["warned"] = True
            say(f"[orders] {m.t} {s.upper()}: cancel NOT confirmed by Kalshi — retrying every pass (it also expires on Kalshi's side)")
        return ok

    def pull(m, reason):
        for s in ("yes", "no"): drop(m, s, reason)

    def record_fill(m, s, n, px, how):
        m.held[s] += n; m.cost[s] += n * px
        totals["fills"] += 1
        fills_w.writerow([now_iso(), m.t, s.upper(), px, n, how, m.held["yes"], m.held["no"], int(live)]); fills_fh.flush()
        say(f"FILL  {m.t}: bought {n:g} {s.upper()} @ {px}c ({how}) · held YES {m.held['yes']:g} / NO {m.held['no']:g}"
            f" · net {m.net():+g} · this market {_usd(m.pnl_c())}")

    def apply_filled(m, s, filled, resting):
        o = m.orders[s]
        if filled is not None and filled > o["filled"]:
            n = filled - o["filled"]; o["filled"] = filled; o["left"] = max(0.0, o["left"] - n)
            record_fill(m, s, n, o["px"], "Kalshi")
        if resting is False: m.orders[s] = None

    def check_one(m, s):
        o = m.orders[s]
        if o:
            filled, resting = gw.order_state(o["oid"])
            apply_filled(m, s, filled, resting)

    def live_fills():
        """Live: every resting order of ours in one read; an order no longer resting is looked up once."""
        rest, cursor = {}, None
        for _ in range(20):
            p = {"status": "resting", "limit": 200}
            if cursor: p["cursor"] = cursor
            d = api_get(sess, k, ORDERS_LIST_PATH, p)
            if d is None: return
            for od in d.get("orders", []) or []:
                filled = field(od, "fill_count")
                init, left = field(od, "initial_count"), field(od, "remaining_count")
                if filled is None and init is not None and left is not None: filled = init - left
                rest[od.get("order_id")] = filled
            cursor = d.get("cursor") or d.get("next_cursor")
            if not cursor: break
        with lock:
            for m in mk.values():
                for s in ("yes", "no"):
                    o = m.orders[s]
                    if not o: continue
                    if o["oid"] in rest: apply_filled(m, s, rest[o["oid"]], True)
                    else: check_one(m, s)

    def on_trade(t, yp, n, taker):
        """Dry run: a trade at our price first uses up the contracts ahead of us; a trade below our price fills us."""
        if live: return
        with lock:
            m = mk.get(t)
            if not m: return
            for s in ("yes", "no"):
                o = m.orders[s]
                if not o or o["left"] <= 0: continue
                if (s == "yes" and taker != "no") or (s == "no" and taker != "yes"): continue
                px = yp if s == "yes" else 100 - yp           # the price this trade paid on our side
                if px > o["px"]: continue
                left = n
                if px == o["px"]:
                    use = min(left, o["ahead"]); o["ahead"] -= use; left -= use
                x = min(left, o["left"])
                if x > BOOK_DUST:                             # not rounding dust (e.g. 9e-15 left after the line ahead)
                    o["left"] -= x; o["filled"] += x
                    record_fill(m, s, x, o["px"], "simulated, " + ("our turn in line" if px == o["px"] else "traded through"))
                    if o["left"] <= 0: m.orders[s] = None
    feed.on_trade = on_trade

    def work(m, dt_s):
        others, scored = book_of(m)
        if others is None: return                       # this book not known yet: nothing changes
        for s in ("yes", "no"): m.best[s] = scored[s][0][0] if scored[s] else None
        t_now = time.time()
        if m.quoting and m.best["yes"] is not None and m.best["no"] is not None:   # only markets we quote can be pulled
            mid = (m.best["yes"] + 100 - m.best["no"]) / 2
            m.mids.append((t_now, mid))
            while m.mids and t_now - m.mids[0][0] > FAST_MOVE_SEC + STABLE_SEC: m.mids.popleft()
            recent = sorted(x for t_, x in m.mids if t_ >= t_now - STABLE_SEC)
            old = sorted(x for t_, x in m.mids if t_ <= t_now - FAST_MOVE_SEC)
            moved = bool(recent and old) and abs(recent[len(recent) // 2] - old[len(old) // 2]) >= FAST_MOVE_C
            if moved and t_now >= m.cool_until:
                m.cool_until = t_now + COOL_OFF_SEC
                m.mids.clear(); m.mids.append((t_now, mid))
                pull(m, "fast move")
                say(f"[safety] {m.t}: price moved {FAST_MOVE_C}c+ within {FAST_MOVE_SEC}s — both sides pulled for {COOL_OFF_SEC / 60:g} min")
        pr = m.prog
        if not live:                                    # dry run: people cancelling ahead of us move us up the line
            for s in ("yes", "no"):
                o = m.orders[s]
                if o: o["ahead"] = min(o["ahead"], dict(others[s]).get(o["px"], 0.0))
        if pr and m.quoting and now() < pr["end"]:      # the reward estimate: half the pool per side
            for s in ("yes", "no"):
                o = m.orders[s]
                ref = reference(scored[s], pr["target"]) if scored[s] else None
                if not o or ref is None: m.share[s] = 0.0; continue
                mine = o["left"] * pr["disc"] ** max(0, ref - o["px"])
                total = score(others[s], ref, pr["disc"]) + mine
                m.share[s] = mine / total if total > 0 else 0.0
                m.reward_c += 100 * pr["per_hour"] / 2 * m.share[s] * dt_s / 3600
        stop = (not m.quoting or flags["halted"] or flags["kill"] or flags["feed_lost"] or t_now < m.cool_until or not pr
                or now() >= pr["end"] or (m.close and (m.close - now()).total_seconds() < PULL_BEFORE_CLOSE_MIN * 60))
        for s in ("yes", "no"):
            o = m.orders[s]
            want_n = min(CONTRACTS, MAX_NET - m.net()) if s == "yes" else min(CONTRACTS, MAX_NET + m.net())
            ref = reference(others[s], pr["target"]) if (pr and others[s]) else None
            best = others[s][0][0] if others[s] else None
            ok = not stop and want_n >= 1 and ref is not None and best is not None and MIN_PX <= best <= MAX_PX
            if ok:
                other = m.best["no" if s == "yes" else "yes"]
                if other is not None and ref + other >= 100: ok = False
            if not ok:
                drop(m, s, "pulled"); continue
            renew = bool(o) and t_now - o["born"] >= ORDER_RENEW_MIN * 60
            if o and (renew or o["px"] != ref or o["left"] > want_n):
                if not renew:
                    o["since_off"] = o.get("since_off") or t_now
                    if t_now - o["since_off"] < REQUOTE_HOLD_SEC and o["left"] <= want_n: continue
                if not drop(m, s, "renewed (expiry)" if renew else f"moved {o['px']}c -> {ref}c"): continue
                o = None
            elif o:
                o["since_off"] = None
            if not o:
                expire = min(pr["end"], now() + dt.timedelta(minutes=ORDER_LIFE_MIN))
                if m.close: expire = min(expire, m.close - dt.timedelta(minutes=PULL_BEFORE_CLOSE_MIN))
                oid = gw.create(m.t, s, ref, int(want_n), expire)
                if oid:
                    m.orders[s] = {"oid": oid, "side": s, "px": ref, "left": float(int(want_n)), "filled": 0.0,
                                   "since_off": None, "ahead": dict(others[s]).get(ref, 0.0), "born": t_now}

    def settle(m):
        md = (api_get(sess, k, f"/markets/{m.t}") or {}).get("market") or {}
        res = md.get("result")
        if res not in ("yes", "no"): return False
        pnl = 100 * m.held[res] - m.cost["yes"] - m.cost["no"]
        totals["settled_pnl"] += pnl; totals["settled_reward"] += m.reward_c; totals["markets_settled"] += 1
        mkts_w.writerow([now_iso(), m.t, res, m.held["yes"], m.cost["yes"], m.held["no"], m.cost["no"], pnl,
                         round(m.reward_c, 1), int(live)]); mkts_fh.flush()
        say(f"SETTLED {res.upper()}  {m.t}: held YES {m.held['yes']:g} / NO {m.held['no']:g} -> fills {_usd(pnl)} · "
            f"estimated reward {_usd(m.reward_c)}")
        m.done = True
        return True

    def status():
        with lock:
            open_ = [m for m in mk.values() if not m.done]
            fills_pnl = sum(m.pnl_c() for m in open_) + totals["settled_pnl"]
            est = sum(m.reward_c for m in open_) + totals["settled_reward"]
            rate = sum(m.prog["per_hour"] / 2 * (m.share["yes"] + m.share["no"]) for m in open_ if m.quoting and m.prog)
            say(f"[status] {'LIVE' if live else 'DRY RUN'} · quoting {sum(m.quoting for m in open_)} market(s) · fills "
                f"{totals['fills']} · fills P&L {_usd(fills_pnl)} (open positions at the best bids) · estimated reward "
                f"{_usd(est)} (now ${rate:.2f}/h) · together {_usd(fills_pnl + est)}"
                + ("  — STOPPED: loss limit" if flags["halted"] else "") + ("  — KILL SWITCH" if flags["kill"] else "")
                + ("  — PRICE STREAM DOWN: orders pulled" if flags["feed_lost"] else ""))
            for m in sorted(open_, key=lambda m: m.t):
                if not (m.quoting or m.has_any()): continue
                qs = " · ".join(f"{s.upper()} {o['left']:g} @ {o['px']}c" for s, o in m.orders.items() if o) or "no orders"
                print(f"           {m.t:34} {qs:28} · held YES {m.held['yes']:g} / NO {m.held['no']:g} · {_usd(m.pnl_c()):>7} · "
                      f"share YES {100 * m.share['yes']:.0f}% NO {100 * m.share['no']:.0f}% · est reward {_usd(m.reward_c)}"
                      + ("" if m.quoting else "  (not quoting: followed to the close)"))
            print(f"           order actions: {gw.n['creates']} creates, {gw.n['cancels']} cancels"
                  + (f", {gw.n['rejects']} rejected" if live else " (built, not sent)")
                  + f" · markets settled {totals['markets_settled']} (fills {_usd(totals['settled_pnl'])}, est reward "
                  f"{_usd(totals['settled_reward'])})", flush=True)

    def reconcile():
        d = api_get(sess, k, "/portfolio/positions", {"limit": 200})
        if d is None: return
        pos = {p.get("ticker"): field(p, "position") or 0 for p in d.get("market_positions", []) or []}
        with lock:
            for m in mk.values():
                if m.done: continue
                gap = pos.get(m.t, 0) - (m.held["yes"] - m.held["no"])
                if abs(gap - m.adj) >= 1:
                    say(f"[account] {m.t}: Kalshi shows net {pos.get(m.t, 0):+g}, the bot counted {m.held['yes'] - m.held['no']:+g} "
                        f"— the difference ({gap:+g}) now counts in the net limit")
                    m.adj = gap

    pick()
    t0 = time.time()
    last = {"pick": t0, "status": t0, "settle": 0, "rec": t0, "fills": t0, "loop": t0}
    errors = 0
    try:
        while True:
            try:
                t_now = time.time(); dt_s = t_now - last["loop"]; last["loop"] = t_now
                if not flags["kill"] and os.path.exists(KILL_FILE):
                    flags["kill"] = True
                    say(f"!!! KILL SWITCH ({KILL_FILE} found): every order cancelled, nothing new. Delete the file and restart to resume. !!!")
                    with lock:
                        for m in mk.values(): pull(m, "kill switch")
                down = feed.down_since
                lost = down is not None and t_now - down >= FEED_LOST_SEC
                if lost and not flags["feed_lost"]:
                    flags["feed_lost"] = True
                    say(f"[safety] price stream down for {FEED_LOST_SEC}s+ — every order pulled until it is back")
                    with lock:
                        for m in mk.values(): pull(m, "price stream down")
                elif not lost and flags["feed_lost"] and feed.ready.is_set():
                    flags["feed_lost"] = False
                    say("[safety] price stream back — quoting again")
                if t_now - last["pick"] >= PROGRAMS_REFRESH_SEC and not flags["kill"]:
                    last["pick"] = t_now; pick()
                if live and t_now - last["fills"] >= FILL_CHECK_SEC:
                    last["fills"] = t_now; live_fills()
                settle_now = t_now - last["settle"] >= SETTLE_RETRY_SEC
                with lock:
                    for m in list(mk.values()):
                        if m.done: continue
                        if m.close and now() >= m.close:
                            if m.orders["yes"] or m.orders["no"]: pull(m, "closed")
                            m.quoting = False
                            if not (m.held["yes"] or m.held["no"]):
                                totals["settled_reward"] += m.reward_c; m.done = True; continue
                            if settle_now: settle(m)
                            continue
                        work(m, dt_s)
                    loss = sum(m.pnl_c() for m in mk.values() if not m.done) + totals["settled_pnl"]
                    if not flags["halted"] and loss <= -MAX_LOSS_USD * 100:
                        flags["halted"] = True
                        for m in mk.values(): pull(m, "loss limit")
                        say(f"!!! LOSS LIMIT: fills are {_usd(loss)} (limit -${MAX_LOSS_USD}) — every order cancelled, no new "
                            f"quotes in this run; contracts held are followed to the close !!!")
                if settle_now: last["settle"] = t_now
                if live and t_now - last["rec"] >= RECONCILE_SEC:
                    last["rec"] = t_now; reconcile()
                if t_now - last["status"] >= STATUS_EVERY_SEC:
                    last["status"] = t_now; status()
            except Exception as e:
                errors += 1
                if errors <= 20 or errors % 100 == 0:
                    say(f"[error] {type(e).__name__}: {e} — the bot keeps running (error #{errors})")
            time.sleep(LOOP_SEC)
    except KeyboardInterrupt:
        with lock:
            for m in mk.values(): pull(m, "stopped")
        print("[stopped] every order cancelled. Contracts already held stay on Kalshi and settle there. Totals so far:")
        status()
    finally:
        feed.stop()
        for fh in (fills_fh, mkts_fh):
            try: fh.close()
            except Exception: pass
        gw.log.close()
