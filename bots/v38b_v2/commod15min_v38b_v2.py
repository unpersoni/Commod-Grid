# ============================================================
# Commod15min V38b V2 "open favourite" — with a DRY-RUN quick-order path (no real-money trading in this version)
#
# STRATEGY (unchanged from V38b): in the first 60 seconds of each market, buy 10 contracts of the side whose ask
#   is 56-95c (quick order at the ask). One buy per market, no buy if the spread is over 5c. Hold to settlement.
#   Crypto and commodities, separate totals plus a line per opening-price band.
#
# MODE (settings):
#   paper   — V38b: the buy is assumed filled at the ask seen; the order code does not run.
#   dry_run — (default) every buy goes through the REAL quick-order path: the exact Kalshi V2 fill-or-kill request is
#             built and signed (with a real price limit), rate-limited and timed — but NOT SENT. The fill is
#             simulated at the ask seen when the request is ready; if the ask has moved past the limit, the
#             order counts as killed and the bot tries again on the next price update (still in the first 60 s).
#             Read-only account checks run with your real key: Kalshi round trip, balance (per shard), resting orders.
#   demo    — orders really sent to Kalshi's demo site (play money; needs a demo account + demo key).
#   live    — locked in this version.
#
# SPEED (lessons from Commod15min V21-V25): the buy fires on the price update itself (no polling, no wasted cycle);
#   one sender thread per market so markets never queue behind each other; one kept-alive HTTPS session warmed up at
#   start; no balance lookup before the buy (Kalshi checks collateral itself); the band is checked on the ask (the
#   price actually paid); every buy logs decision -> request ready, build + sign, round trip, and slippage.
# PRICE LIMIT (V23 fix): fill-or-kill at most MAX_SLIPPAGE_C above the ask seen (never above 99c): all 10
#   contracts fill at or below the limit, or nothing fills and nothing is paid.
# SAFETY: one buy per market · no action within 2 s of the close or when the close time is unknown ·
#   no new buys after a -$20 day (no limit on gains; dry run only reports it) · kill switch: create
#   STOP_V38B.txt on Drive · at most 10 order actions per second.
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
BOT_NAME  = "Commod15min V38b V2"
# MODE: "paper" | "dry_run" (default: exact orders built + signed, NOT sent) | "demo" (Kalshi demo site) | "live" (locked)
MODE      = "dry_run"
PEM_PATH  = '/content/drive/MyDrive/intraday key.pem'
KEY_ID    = "__KEY_ID__"
DEMO_PEM_PATH = '/content/drive/MyDrive/demo key.pem'   # demo mode only: a key made on demo.kalshi.co
DEMO_KEY_ID   = ""                                       # demo mode only
KILL_FILE = '/content/drive/MyDrive/STOP_V38B.txt'      # create this file on Drive to stop all new buys

SERIES = ["KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M",
          "KXGOLD15M", "KXSILVER15M", "KXWTI15M", "KXCOPPER15M",
          "KXPLATINUM15M", "KXPALLADIUM15M"]
TRADE_CLASSES = {"crypto", "commodity"}

OPEN_WINDOW_SEC = 60      # opening buy only in the first 60 seconds of the market
OPEN_MIN_C      = 56      # buy the side whose ask is 56c ...
OPEN_MAX_C      = 95      # ... to 95c
CONTRACTS       = 10      # per opening buy, held to settlement
# opening-price bands, for the totals only
PRICE_BANDS     = [(56, 65), (66, 70), (71, 80), (81, 95)]
MAX_SPREAD_C    = 5       # no buy when ask - bid is wider than this
DEADBAND_SEC    = 2       # never act this close to the close

# ---- quick-order path (dry_run / demo / live) ----
MAX_SLIPPAGE_C     = 2       # fill-or-kill limit = ask seen + 2c (never above 99c): all 10 fill at or below it, or none
MAX_WRITES_PER_SEC = 10      # Kalshi Basic API tier: 10 order actions per second
ORDER_WORKERS      = len(SERIES)   # one sender thread per market
LATENCY_PROBE_SEC  = 60      # time a round trip to Kalshi every minute (shown in the status)
SHOW_REQUESTS      = 5       # print the exact request of the first 5 orders
DAILY_LOSS_LIMIT_USD = 20    # no new buys once today's settled net is below -$20 (UTC day). No limit on gains.
                             # dry_run only reports "would have stopped" and keeps going
ORDER_CREATE_PATH  = "/portfolio/events/orders"
ORDERS_LIST_PATH   = "/portfolio/orders"

STATUS_EVERY_SEC    = 900
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

class QuickGateway:
    """Quick (taker) buys: rate limit -> build + sign the exact fill-or-kill request -> send it (demo/live) or simulate
    the fill at the ask seen when the request is ready (dry run) -> report fill, slippage and timing.
    One worker thread per market, so markets never wait behind each other."""
    def __init__(self, mode, k, sess, say, price_now):
        self.mode, self.k, self.sess, self.say, self.price_now = mode, k, sess, say, price_now
        self.send = mode in ("demo", "live")
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

    def _buy(self, ticker, yes, ask_seen, t_dec, on_done):
        try:
            wait = self.limiter.take()
            if wait > 0.001: self.n["throttled"] += 1
            t0 = time.perf_counter()
            limit = min(99, ask_seen + MAX_SLIPPAGE_C)
            coid = str(uuid.uuid4())
            body = fok_body(ticker, yes, limit, CONTRACTS, coid)
            _hdrs(self.k, "POST", ORDER_CREATE_PATH)                # signing cost counted in every mode
            build = (time.perf_counter() - t0) * 1000
            ready = (time.perf_counter() - t_dec) * 1000
            with self.lock:
                self.build_ms.append(build); self.decide_ms.append(ready)
                self.per_sec[int(time.time())] += 1
                if len(self.per_sec) > 4000:
                    for s0 in sorted(self.per_sec)[:2000]: del self.per_sec[s0]
            fee = None
            if self.send:
                h = _hdrs(self.k, "POST", ORDER_CREATE_PATH)
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
                od = js.get("order", js) if isinstance(js, dict) else {}
                try: fc = float(od.get("fill_count") or 0)
                except (TypeError, ValueError): fc = 0
                try: fill = round(float(od.get("average_fill_price")) * 100)
                except (TypeError, ValueError): fill = None
                if fill is not None and not yes:
                    fill = 100 - fill                                  # V2 orders are in YES prices: a NO buy fills at 100 - YES
                if self.shown < SHOW_REQUESTS:
                    self.say(f"[order] raw response HTTP {code}: {json.dumps(js)[:300]}")   # to check the field names
                try: fee = round(float(od.get("average_fee_paid")) * 100 * CONTRACTS)
                except (TypeError, ValueError): fee = None
                filled = fc >= CONTRACTS
                result = "FILLED" if filled else ("killed (not enough at or below the limit)" if code in (200, 201) else
                                                  f"rejected HTTP {code}: {json.dumps(js)[:160]}")
            else:
                rtt = 0.0
                px = self.price_now(ticker)                           # the ask at the moment the order would go out
                cur = None if px is None else (px[1] if yes else 100 - px[0])
                filled = cur is not None and cur <= limit
                fill = cur if filled else None
                result = "FILLED (simulated: not sent)" if filled else f"killed (simulated: ask {cur}c > limit {limit}c)"
            if filled:
                if fee is None: fee = taker_fee_c(CONTRACTS, fill)
                self.slip.append(fill - ask_seen)
                self.n["filled"] += 1
            else:
                self.n["killed" if "killed" in result else "rejected"] += 1
            self.n["orders"] += 1
            side = "YES" if yes else "NO"
            self.say(f"[order] {ticker[-7:]} {asset(ticker)} buy {CONTRACTS} {side} · ask seen {ask_seen}c, limit {limit}c · {result}"
                     + (f" @ {fill}c (slippage {fill - ask_seen:+d}c)" if filled else "")
                     + f" · decision->ready {ready:.1f} ms (rate wait {wait * 1000:.0f}), build+sign {build:.1f} ms"
                     + (f", round trip {rtt:.0f} ms" if self.send else ""))
            if self.shown < SHOW_REQUESTS:
                self.shown += 1
                self.say(f"[order] request {'SENT' if self.send else 'built (NOT sent)'}: POST {ORDER_CREATE_PATH} "
                         + json.dumps(body, separators=(",", ":")))
            on_done(ticker, yes, filled, fill, fee, ask_seen)
        except Exception as e:
            self.n["errors"] += 1
            print(f"[order] failed: {e}")
            try: on_done(ticker, yes, False, None, None, ask_seen)
            except Exception: pass

    def probe(self):
        t0 = time.perf_counter()
        d = api_get(self.sess, self.k, "/exchange/status")
        ms = (time.perf_counter() - t0) * 1000
        if d is not None: self.probe_ms.append(ms)
        return ms if d is not None else None

    def startup_checks(self):
        """Read-only checks against the account (safe in every mode); warms up the connection."""
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
        self.say(f"[account] resting orders on the account now: {len((d or {}).get('orders', []) or [])}")

    def summary(self):
        n = self.n
        f = lambda xs: "n/a" if not xs else f"{_pct(list(xs), 0.5):.1f} / {_pct(list(xs), 0.95):.1f} ms"
        peak = max(self.per_sec.values()) if self.per_sec else 0
        avg_slip = (sum(self.slip) / len(self.slip)) if self.slip else 0
        return [f"quick orders {'sent' if self.send else 'built, not sent'}: {n['orders']} · filled {n['filled']}, "
                f"killed {n['killed']}, rejected {n['rejected']}, errors {n['errors']} · avg slippage {avg_slip:+.2f}c · "
                f"busiest second {peak} (limit {MAX_WRITES_PER_SEC}/s)",
                f"speed (median / 95%): decision -> request ready {f(self.decide_ms)} · build + sign {f(self.build_ms)} · "
                f"Kalshi round trip {f(self.probe_ms)}" + (f" · order round trip {f(self.rtt_ms)}" if self.send else "")]
# ---------------- the bot ----------------
CRYPTO = {"BTC", "ETH", "SOL", "XRP", "DOGE"}
def cls(a): return "crypto" if a in CRYPTO else "commodity"

def band_name(px):
    for lo, hi in PRICE_BANDS:
        if lo <= px <= hi: return f"{lo}-{hi}c"
    return "?"

class Trade:
    """The opening buy in one market, held to settlement."""
    def __init__(self, yes, px, s2c, fee):
        self.yes, self.px, self.s2c, self.fee = yes, px, s2c, fee

def _usd(c): return f"{'+' if c >= 0 else '-'}${abs(c) / 100:,.2f}"

def main():
    global KEY_ID, PEM_PATH, BASE, WS_URL
    if MODE not in ("paper", "dry_run", "demo", "live"):
        sys.exit(f"MODE must be paper, dry_run, demo or live (it is {MODE!r})")
    if MODE == "live":
        sys.exit("LIVE mode is locked in V38b V2. Run dry_run, then demo, and review the results before a live version is built.")
    if MODE == "demo":
        if not DEMO_KEY_ID: sys.exit("demo mode needs DEMO_KEY_ID and DEMO_PEM_PATH (a key made on demo.kalshi.co)")
        KEY_ID, PEM_PATH, BASE, WS_URL = DEMO_KEY_ID, DEMO_PEM_PATH, DEMO_BASE, DEMO_WS_URL
    GATE = MODE != "paper"
    banner = {"paper": "PAPER (buys assumed filled at the ask seen)",
              "dry_run": "DRY RUN (exact fill-or-kill orders built, signed and timed — NOTHING is sent; fills simulated)",
              "demo": "DEMO (orders SENT to Kalshi's demo site — play money)"}[MODE]
    print(f"{BOT_NAME} — {banner}")
    print(f"markets: {', '.join(asset(s) for s in SERIES)}")
    print(f"opening buy: {CONTRACTS} contracts of the side whose ask is {OPEN_MIN_C}-{OPEN_MAX_C}c in the first "
          f"{OPEN_WINDOW_SEC}s of the market · no buy if the spread is over {MAX_SPREAD_C}c · hold to settlement")
    if GATE:
        print(f"quick orders: fill-or-kill, limit = ask seen + {MAX_SLIPPAGE_C}c · retried on the next price update if killed · "
              f"{ORDER_WORKERS} sender threads · at most {MAX_WRITES_PER_SEC} actions/s")
        print(f"safety: no new buys below -${DAILY_LOSS_LIMIT_USD} for the day (no limit on gains"
              f"{'; dry run only reports it' if MODE == 'dry_run' else ''}) · kill switch: create {KILL_FILE}")
    print("files: none — results are shown here only")
    k = load_key()
    sess = requests.Session()
    sess.mount("https://", requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=len(SERIES) + ORDER_WORKERS + 10))
    lock = threading.RLock()
    markets, rest_px, trades, pending = {}, {}, {}, set()
    settle_due, settled = {}, set()
    def new_tally(): return {"markets": 0, "won": 0, "net": 0, "cost": 0}
    run = {c: new_tally() for c in ("crypto", "commodity")}
    by_band = {(c, f"{lo}-{hi}c"): new_tally() for c in ("crypto", "commodity") for lo, hi in PRICE_BANDS}
    day = {"date": now().date(), "net": 0}
    flags = {"kill": False, "halted": False, "would_halt": None}

    def s2c_of(t):
        m = markets.get(t)
        return (m["close"] - now()).total_seconds() if m else None

    def say(msg): print(f"[{now():%H:%M:%S}] {msg}")

    def prices(t):
        return feed.get(t) or rest_px.get(t)

    gw = QuickGateway(MODE, k, sess, say, prices) if GATE else None

    def on_bought(t, yes, filled, fill, fee, ask_seen):
        """Called by the quick-order path when the buy has been sent (demo) or simulated (dry run)."""
        with lock:
            pending.discard(t)
            if not filled or t in trades: return
            s2c = s2c_of(t)
            trades[t] = Trade(yes, fill, s2c, fee)
        say(f"BUY  {CONTRACTS}x {'YES' if yes else 'NO'} @ {fill}c  {asset(t)} ({cls(asset(t))}) {t[-7:]}  "
            f"{'' if s2c is None else f'{s2c:.0f}s left'}  (ask seen {ask_seen}c) · hold to settlement")

    def evaluate(t):
        px = prices(t)
        s2c = s2c_of(t)
        if px is None or s2c is None or s2c <= DEADBAND_SEC: return       # unknown timing: do nothing
        yb, ya = px
        if not (0 <= yb < ya <= 100): return
        c = cls(asset(t))
        if c not in TRADE_CLASSES: return
        with lock:
            if t in trades or t in pending or flags["kill"] or flags["halted"]: return
            if not (900 - OPEN_WINDOW_SEC <= s2c <= 905) or ya - yb > MAX_SPREAD_C: return
            for yes, ask in ((True, ya), (False, 100 - yb)):
                if OPEN_MIN_C <= ask <= OPEN_MAX_C:
                    if not GATE:
                        trades[t] = Trade(yes, ask, s2c, taker_fee_c(CONTRACTS, ask))
                        say(f"BUY  {CONTRACTS}x {'YES' if yes else 'NO'} @ {ask}c  {asset(t)} ({c}) {t[-7:]}  {s2c:.0f}s left  · hold to settlement")
                    else:
                        pending.add(t)                                   # one order in flight per market
                        gw.buy(t, yes, ask, on_bought)
                    return

    def on_tick(t, yb, ya):
        if t in markets: evaluate(t)              # the buy fires on the price update itself

    def finish(t, result):
        with lock:
            tr = trades.pop(t, None)
        if tr is None: return
        c = cls(asset(t))
        won = (result == "yes") == tr.yes
        net = CONTRACTS * (100 * won - tr.px) - tr.fee
        if now().date() != day["date"]: day.update(date=now().date(), net=0); flags.update(halted=False, would_halt=None)
        day["net"] += net
        for r in (run[c], by_band[(c, band_name(tr.px))]):
            r["markets"] += 1; r["won"] += int(won); r["net"] += net; r["cost"] += CONTRACTS * tr.px
        r = run[c]
        say(f"SETTLED {result.upper()}  {asset(t)} ({c}) {t[-7:]}: {'YES' if tr.yes else 'NO'} @ {tr.px}c {'WON' if won else 'lost'} "
            f"{_usd(net)}  | {c} run {_usd(r['net'])} over {r['markets']} markets ({r['won']} won)")
        if GATE and day["net"] < -DAILY_LOSS_LIMIT_USD * 100:
            if MODE == "dry_run":
                if flags["would_halt"] is None:
                    flags["would_halt"] = now()
                    say(f"[safety] DAILY LOSS LIMIT would stop new buys now (today's net {_usd(day['net'])} is below "
                        f"-${DAILY_LOSS_LIMIT_USD}) — dry run keeps going")
            elif not flags["halted"]:
                flags["halted"] = True
                say(f"!!! DAILY LOSS LIMIT: today's net {_usd(day['net'])} is below -${DAILY_LOSS_LIMIT_USD} — no new buys until tomorrow (UTC) !!!")

    def status():
        say(f"[status] {MODE}")
        for c in ("crypto", "commodity"):
            r = run[c]
            avg = r["cost"] / CONTRACTS / r["markets"] if r["markets"] else 0
            won = 100 * r["won"] / r["markets"] if r["markets"] else 0
            print(f"           {c:9}: {r['markets']:3} markets, {r['won']:3} won ({won:.0f}%), avg buy {avg:.1f}c, net {_usd(r['net'])}")
            for lo, hi in PRICE_BANDS:
                q = by_band[(c, f"{lo}-{hi}c")]
                if q["markets"]:
                    print(f"              bought {lo}-{hi}c: {q['markets']:3} markets, {q['won']:3} won "
                          f"({100 * q['won'] / q['markets']:.0f}%), net {_usd(q['net'])}")
        if gw:
            for line in gw.summary(): print(f"           {line}")
            wh = flags["would_halt"]
            print(f"           today (UTC) net {_usd(day['net'])} (stop at -${DAILY_LOSS_LIMIT_USD}, no limit on gains)"
                  + ("  — DAILY LOSS LIMIT HIT, no new buys" if flags["halted"] else "")
                  + (f"  — dry run: the limit would have stopped new buys at {wh:%H:%M} UTC" if wh else "")
                  + ("  — KILL SWITCH ON" if flags["kill"] else ""))

    def probe_loop():
        while True:
            time.sleep(LATENCY_PROBE_SEC)
            try: gw.probe()
            except Exception: pass

    global FEED
    if gw:
        gw.startup_checks()                      # read-only; also warms up the connection before the first buy
        threading.Thread(target=probe_loop, daemon=True).start()
    feed = FEED = make_feed(k)
    feed.on_tick = on_tick
    feed.ready.wait(timeout=10)

    last_refresh, last_status, last_kill = 0, time.time(), 0
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
                    feed.track({t for t in markets if 0 < (s2c_of(t) or 0) <= 15 * 60 + 60})
            if GATE and time.time() - last_kill >= 5:            # kill switch: a file on Drive
                last_kill = time.time()
                if not flags["kill"] and os.path.exists(KILL_FILE):
                    flags["kill"] = True
                    say(f"!!! KILL SWITCH ({KILL_FILE} found): no new buys. Positions already held ride to settlement. "
                        f"Delete the file and restart to resume. !!!")
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
                checked += 1
                m = fetch_market(sess, k, t)
                res = m.get("result")
                if res in ("yes", "no"):
                    finish(t, res)
                    settled.add(t)
                    del settle_due[t]; markets.pop(t, None); rest_px.pop(t, None)
                elif time.time() > due[1]:
                    with lock: tr = trades.pop(t, None)
                    if tr: say(f"[settle] no result for {t} after {SETTLE_GIVEUP_SEC // 60} min — its trade is dropped")
                    settled.add(t)
                    del settle_due[t]; markets.pop(t, None)
                else:
                    due[0] = time.time() + SETTLE_RETRY_SEC
            if time.time() - last_status >= STATUS_EVERY_SEC:
                last_status = time.time()
                status()
            time.sleep(LOOP_SEC)
    except KeyboardInterrupt:
        if gw: gw.pool.shutdown(wait=True)
        print("[stopped] totals so far:")
        status()
    finally:
        feed.stop()

FEED = None
