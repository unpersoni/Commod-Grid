# ============================================================
# Commod15min V38b "open favourite" — PAPER ONLY (no order is ever sent to Kalshi)
#
# OPENING BUY: in the first 60 seconds of each market, buy 10 contracts of the side whose ask is 56-95c
#   (quick order: at the ask, Kalshi taker fee). One buy per market. No buy if the spread is over 5c.
# HOLD every position to settlement ($1 or $0). No sells, no reversals.
# Crypto and commodities, with separate totals and a line per opening-price band.
# No files are written: results are in this log only (status every 15 minutes).
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
BOT_NAME  = "Commod15min V38b"
PEM_PATH  = '/content/drive/MyDrive/intraday key.pem'
KEY_ID    = "__KEY_ID__"

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

# ---------------- the bot ----------------
CRYPTO = {"BTC", "ETH", "SOL", "XRP", "DOGE"}
def cls(a): return "crypto" if a in CRYPTO else "commodity"

def band_name(px):
    for lo, hi in PRICE_BANDS:
        if lo <= px <= hi: return f"{lo}-{hi}c"
    return "?"

class Trade:
    """The opening buy in one market, held to settlement."""
    def __init__(self, yes, px, s2c):
        self.yes, self.px, self.s2c = yes, px, s2c

def _usd(c): return f"{'+' if c >= 0 else '-'}${abs(c) / 100:,.2f}"

def main():
    print(f"{BOT_NAME} — PAPER ONLY (no orders are sent)")
    print(f"markets: {', '.join(asset(s) for s in SERIES)}")
    print(f"opening buy: {CONTRACTS} contracts of the side whose ask is {OPEN_MIN_C}-{OPEN_MAX_C}c in the first "
          f"{OPEN_WINDOW_SEC}s of the market (at the ask, taker fee) · no buy if the spread is over {MAX_SPREAD_C}c · "
          f"hold to settlement")
    print("files: none — results are shown here only")
    k = load_key()
    sess = requests.Session()
    sess.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=len(SERIES) + 10))
    lock = threading.RLock()
    markets, rest_px, trades = {}, {}, {}
    settle_due, settled = {}, set()
    def new_tally(): return {"markets": 0, "won": 0, "net": 0, "cost": 0}
    run = {c: new_tally() for c in ("crypto", "commodity")}
    by_band = {(c, f"{lo}-{hi}c"): new_tally() for c in ("crypto", "commodity") for lo, hi in PRICE_BANDS}

    def s2c_of(t):
        m = markets.get(t)
        return (m["close"] - now()).total_seconds() if m else None

    def say(msg): print(f"[{now():%H:%M:%S}] {msg}")

    def prices(t):
        return feed.get(t) or rest_px.get(t)

    def evaluate(t):
        px = prices(t)
        s2c = s2c_of(t)
        if px is None or s2c is None or s2c <= DEADBAND_SEC: return
        yb, ya = px
        if not (0 <= yb < ya <= 100): return
        c = cls(asset(t))
        if c not in TRADE_CLASSES: return
        with lock:
            if t in trades: return
            if not (900 - OPEN_WINDOW_SEC <= s2c <= 905) or ya - yb > MAX_SPREAD_C: return
            for yes, ask in ((True, ya), (False, 100 - yb)):
                if OPEN_MIN_C <= ask <= OPEN_MAX_C:
                    trades[t] = Trade(yes, ask, s2c)
                    say(f"BUY  {CONTRACTS}x {'YES' if yes else 'NO'} @ {ask}c  {asset(t)} ({c}) {t[-7:]}  {s2c:.0f}s left  · hold to settlement")
                    return

    def on_tick(t, yb, ya):
        if t in markets: evaluate(t)

    def finish(t, result):
        with lock:
            tr = trades.pop(t, None)
        if tr is None: return
        c = cls(asset(t))
        won = (result == "yes") == tr.yes
        net = CONTRACTS * (100 * won - tr.px) - taker_fee_c(CONTRACTS, tr.px)
        for r in (run[c], by_band[(c, band_name(tr.px))]):
            r["markets"] += 1; r["won"] += int(won); r["net"] += net; r["cost"] += CONTRACTS * tr.px
        r = run[c]
        say(f"SETTLED {result.upper()}  {asset(t)} ({c}) {t[-7:]}: {'YES' if tr.yes else 'NO'} @ {tr.px}c {'WON' if won else 'lost'} "
            f"{_usd(net)}  | {c} run {_usd(r['net'])} over {r['markets']} markets ({r['won']} won)")

    def status():
        say("[status]")
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

    global FEED
    feed = FEED = make_feed(k)
    feed.on_tick = on_tick
    feed.ready.wait(timeout=10)

    last_refresh, last_status = 0, time.time()
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
        print("[stopped] totals so far:")
        status()
    finally:
        feed.stop()

FEED = None
