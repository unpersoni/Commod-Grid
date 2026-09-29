# ============================================================
# Commod15min V26 — 88-92c entry / 70c reversal, 7-minute window
# ============================================================
# STRATEGY
#   - Armed for the last 7 minutes of each 15-min market (TRIGGER_MIN).
#   - Favorite = the first side whose ASK (the price a buy pays) is in
#     [ENTRY_C, ENTRY_CAP_C] = [88c, 92c]. Buy CONTRACTS (10), FOK, never
#     above 92c. A favorite that jumps past 92c is only bought if it
#     pulls back into the band.
#   - Stop: if the held side's BID dips to STOP_C (70c) or less, reverse:
#     one order buys 2x the opposite side (closes the 10 held, opens 10
#     new). The reversal is held to settlement — no second stop.
#   - Stop is live the whole time the position is held, except inside the
#     last STOP_DEADBAND_SEC (Kalshi's book empties at close and the last
#     tick is not a real price).
#
# LATENCY / WEBSOCKET FIXES vs V25
#   1. 401 ROOT CAUSE: V25 (Sep 28) read the API key ID at import time from
#      /content/drive/MyDrive/kalshi_key_id.txt. That file did not exist,
#      so the key ID was "" -> every websocket handshake got 401 and the
#      bot silently traded on 0.5s REST polling (SOL stop filled 43c vs a
#      68c stop). Now the key ID is loaded when the bot starts (Colab
#      Secret KALSHI_KEY_ID, env var, or the Drive file), and the bot
#      REFUSES TO START if REST auth fails — no more silent fallback.
#   2. ORDER-BOOK FEED: subscribes to orderbook_delta (every book change,
#      sequenced, exchange ms timestamps) in addition to ticker. The Sep 27
#      run reacted in ~1ms yet many stops still printed 3-14c below 70:
#      the first price the ticker channel showed was already past the
#      stop. The book feed sees every level change, and the tick handler
#      only fires when the best bid/ask actually moves.
#   3. NO ENTRIES WITHOUT THE WEBSOCKET (REQUIRE_WS_FOR_ENTRY). If the feed
#      drops, open positions keep their stop (REST backstop) but no new
#      position is opened until the feed is back.
#   4. HOT PATH CLEANED: no balance API call before a stop order, and all
#      CSV writes go through a background writer thread — V25 flushed to
#      the Drive mount while holding the lock every WS tick needs.
#   5. STOP ORDERS: first attempt is a combined 2x order limited to
#      STOP_SLIPPAGE_C below the bid seen; if the book can't fill that,
#      the held side is sold at market (guaranteed exit) and the reversal
#      is bought separately, limited to REVERSAL_SLIPPAGE_C.
#   6. DEAD-FEED DETECTION: client pings every 3s, recv timeout 10s, auto
#      reconnect (alternating the two Kalshi WS hosts on network errors),
#      order-book sequence gaps force an immediate resync.
#   7. PAPER fills walk the real order book depth for 10 contracts, so
#      paper results reflect what a live FOK would actually get.
#   Every BUY/STOP line prints: price seen, feed source, exchange->bot
#   feed lag, fill, slippage vs target, trigger->fill ms, order round trip.
# ============================================================
LIVE     = False  # PAPER mode. Flip to True only when explicitly decided.
PEM_PATH    = '/content/drive/MyDrive/intraday key.pem'
KEY_ID_PATH = '/content/drive/MyDrive/kalshi_key_id.txt'
OUT_CSV     = '/content/drive/MyDrive/commod15min_v26_trades.csv'
TICK_CSV    = '/content/drive/MyDrive/commod15min_ticks.csv'

SERIES = [
    "KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M",
    "KXGOLD15M", "KXSILVER15M", "KXWTI15M", "KXCOPPER15M",
    "KXPLATINUM15M", "KXPALLADIUM15M",
]

TRIGGER_MIN          = 7.0   # armed for the last 7 minutes of each market
ENTRY_C              = 88    # favorite's ask must be >= this ...
ENTRY_CAP_C          = 92    # ... and <= this. Also the entry order's limit.
STOP_C               = 70    # held side's bid <= this -> reverse
CONTRACTS            = 10
STOP_ACTIVATE_SEC    = TRIGGER_MIN * 60  # stop live the whole armed window
STOP_DEADBAND_SEC    = 2.0   # no stops this close to (or after) close
STOP_SLIPPAGE_C      = 3     # 1st stop attempt: accept <= 3c below bid seen
REVERSAL_SLIPPAGE_C  = 3     # split-path reversal buy: <= 3c above expected
REQUIRE_WS_FOR_ENTRY = True  # never open a position off stale REST prices
ORDER_RETRY_SEC      = 0.25  # min gap between retries after a FOK kill
POLL_SEC             = 0.5   # REST loop: market discovery, settle, backstop
DAILY_LOSS_LIMIT_C   = 1000  # LIVE only
MAX_TRADES_DAY       = 40    # LIVE only
WS_MAX_AGE_SEC       = 3.0   # ticker-channel fallback price max age
WS_PING_SEC          = 3.0
WS_RECV_TIMEOUT_SEC  = 10.0
BALANCE_RESYNC_SEC   = 10.0  # LIVE: re-read real shard balance this often

import os, sys, csv, time, math, json, uuid, base64, queue, threading
import statistics, collections, datetime as dt
import requests
from concurrent.futures import ThreadPoolExecutor
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

try:
    import websocket  # websocket-client package
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "websocket-client"],
                   check=True)
    import websocket

BASE    = "https://api.elections.kalshi.com/trade-api/v2"
PREF    = "/trade-api/v2"
WS_PATH = "/trade-api/ws/v2"
WS_URLS = ["wss://api.elections.kalshi.com/trade-api/ws/v2",
           "wss://external-api-ws.kalshi.com/trade-api/ws/v2"]
DEST_SHARD = 2

COLS = ["ticker", "mode", "side", "entry_iso", "entry_px", "exit_iso", "exit_px",
        "reason", "net_c", "run_net_c", "entry_s2c", "exit_s2c",
        "trigger_px", "seen_px", "src", "feed_lag_ms", "trigger_to_fill_ms"]
TICK_COLS = ["ts_iso", "ticker", "asset", "phase", "src",
             "yes_bid", "yes_ask", "yes_mid", "sec_to_close",
             "spot_px", "floor_strike", "pct_from_strike", "spot_age"]

KEY_ID  = ""
KEY_SRC = ""


def asset(t): return t.split("15M")[0].replace("KX", "")
def window_key(t): return t.split("15M-", 1)[1] if "15M-" in t else t
def fee_est(px): return math.ceil(7 * (px / 100) * (1 - px / 100))
def now(): return dt.datetime.now(dt.timezone.utc)
def now_iso(): return now().isoformat()
def parse_iso(s):
    try: return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError): return None
def fmt_ms(x): return "?" if x is None else f"{x:.0f}ms"


# ============================================================
# AUTH
# ============================================================
def load_key_id():
    """Colab Secret -> env var -> Drive file. Read at startup, never at
    import (Drive may not be mounted yet when the code cell runs)."""
    try:
        from google.colab import userdata
        v = (userdata.get("KALSHI_KEY_ID") or "").strip()
        if v: return v, "Colab Secret KALSHI_KEY_ID"
    except Exception:
        pass
    v = os.environ.get("KALSHI_KEY_ID", "").strip()
    if v: return v, "env KALSHI_KEY_ID"
    if os.path.exists(KEY_ID_PATH):
        v = open(KEY_ID_PATH).read().strip()
        if v: return v, KEY_ID_PATH
    return "", ""

def init_auth():
    global KEY_ID, KEY_SRC
    KEY_ID, KEY_SRC = load_key_id()
    if not KEY_ID:
        raise SystemExit(
            "No Kalshi API key ID found. Do ONE of these, then re-run:\n"
            "  - Colab left sidebar > key icon (Secrets) > add KALSHI_KEY_ID, "
            "enable notebook access\n"
            f"  - or put the key ID alone in {KEY_ID_PATH}")
    if not os.path.exists(PEM_PATH):
        raise SystemExit(f"Private key PEM not found: {PEM_PATH}")
    return serialization.load_pem_private_key(open(PEM_PATH, "rb").read(), password=None)

def _sign(k, ts, method, path):
    return base64.b64encode(k.sign(
        f"{ts}{method}{path}".encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256())).decode()

def _hdrs(k, method, path):
    ts = str(int(time.time() * 1000))
    return {"KALSHI-ACCESS-KEY": KEY_ID,
            "KALSHI-ACCESS-SIGNATURE": _sign(k, ts, method, PREF + path),
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "Accept": "application/json",
            "Content-Type": "application/json"}

def make_session():
    s = requests.Session()
    a = requests.adapters.HTTPAdapter(pool_maxsize=max(24, 2 * len(SERIES) + 4))
    s.mount("https://", a)
    return s

def rest_auth_status(sess, k):
    try:
        r = sess.get(BASE + "/portfolio/balance",
                     headers=_hdrs(k, "GET", "/portfolio/balance"), timeout=8)
        return r.status_code, r.text[:200]
    except requests.RequestException as e:
        return 0, str(e)[:200]

def require_rest_auth(sess, k):
    code, body = rest_auth_status(sess, k)
    if code != 200:
        raise SystemExit(
            f"Kalshi REST auth failed (HTTP {code}): {body}\n"
            f"  key ID {KEY_ID[:8]}... from {KEY_SRC}, PEM {PEM_PATH}\n"
            "  The key ID and the .pem must be the SAME key pair "
            "(Kalshi > Account > API Keys). If you created a new key, "
            "update both.")


# ============================================================
# REST
# ============================================================
def api_get(sess, k, path, params=None):
    for attempt in range(3):
        try:
            r = sess.get(BASE + path, headers=_hdrs(k, "GET", path),
                         params=params, timeout=8)
            if r.status_code == 404: return None
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(0.5 * (attempt + 1)); continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            time.sleep(0.5 * (attempt + 1))
    return None

def api_post(sess, k, path, body):
    try:
        r = sess.post(BASE + path, headers=_hdrs(k, "POST", path), json=body, timeout=8)
        return r.status_code, r.json()
    except Exception as e:
        return 0, {"error": str(e)}

def _fetch_series_open(sess, k, ser):
    out, cur = [], None
    while True:
        p = {"series_ticker": ser, "status": "open", "limit": 100}
        if cur: p["cursor"] = cur
        d = api_get(sess, k, "/markets", p) or {}
        out += d.get("markets", []) or []
        cur = d.get("cursor")
        if not cur: break
    return out

_FETCH_POOL = ThreadPoolExecutor(max_workers=len(SERIES))

def fetch_open(sess, k):
    out = []
    for res in _FETCH_POOL.map(lambda s: _fetch_series_open(sess, k, s), SERIES):
        out += res
    return out

def fetch_market(sess, k, ticker):
    d = api_get(sess, k, f"/markets/{ticker}")
    return (d or {}).get("market", {}) if d else {}

def _cents(dollars, legacy_cents=None):
    try:
        if dollars is not None: return round(float(dollars) * 100)
        if legacy_cents is not None: return int(round(float(legacy_cents)))
    except (TypeError, ValueError):
        pass
    return None

def rest_prices(m):
    yb = _cents(m.get("yes_bid_dollars"), m.get("yes_bid"))
    ya = _cents(m.get("yes_ask_dollars"), m.get("yes_ask"))
    if yb is not None and ya is not None and ya > yb:
        return yb, ya, (yb + ya) // 2
    return None

def balance_by_shard(sess, k):
    d = api_get(sess, k, "/portfolio/balance") or {}
    out = {}
    for b in d.get("balance_breakdown", []):
        try: out[int(b["exchange_index"])] = int(round(float(b["balance"]) * 100))
        except (TypeError, ValueError, KeyError): pass
    return out

def get_balance(sess, k):
    b = (api_get(sess, k, "/portfolio/balance") or {}).get("balance")
    return int(b) if b is not None else None

def move_funds_to_shard(sess, k, dest=DEST_SHARD, quiet=False):
    """Moves free balance from shard 0 to dest ('amount' is in centicents)."""
    bs = balance_by_shard(sess, k)
    have_dest, src0 = bs.get(dest, 0), bs.get(0, 0)
    if src0 <= 100:
        if not quiet:
            print(f"[transfer] shard {dest} has {have_dest}c, shard 0 has {src0}c — nothing to move")
        return True
    body = {"source": "event_contract", "destination": "event_contract",
            "amount": src0 * 100, "source_exchange_shard": 0,
            "destination_exchange_shard": dest}
    code, resp = api_post(sess, k, "/portfolio/intra_exchange_instance_transfer", body)
    new_dest = have_dest
    if code in (200, 201):
        for _ in range(3):
            time.sleep(0.5)
            new_dest = balance_by_shard(sess, k).get(dest, 0)
            if new_dest > have_dest: break
    if not quiet:
        print(f"[transfer] {'OK' if new_dest > have_dest else 'FAILED'} {src0}c shard 0 -> {dest}: "
              f"HTTP {code} dest {have_dest}c -> {new_dest}c {json.dumps(resp)[:150]}")
    return new_dest > have_dest

def cancel_all_resting(sess, k):
    d = api_get(sess, k, "/portfolio/orders", {"status": "resting", "limit": 200})
    n = 0
    for o in (d or {}).get("orders", []) or []:
        oid = o.get("order_id")
        if not oid: continue
        try:
            sess.delete(BASE + f"/portfolio/orders/{oid}",
                        headers=_hdrs(k, "DELETE", f"/portfolio/orders/{oid}"), timeout=8)
            n += 1
        except Exception:
            pass
    if n: print(f"[cleanup] cancelled {n} resting order(s)")

def place_order(sess, k, ticker, action, yes_side, count, limit_c=None):
    """FOK order. limit_c = worst acceptable price of the side traded
    (most paid on a buy, least accepted on a sell); None = market
    (99c buy / 1c sell). Returns (filled, avg_yes_px_c, fee_c, rtt_ms)."""
    if limit_c is None:
        limit_c = 99 if action == "buy" else 1
    limit_c = max(1, min(99, int(round(limit_c))))
    if action == "buy":
        book_side = "bid" if yes_side else "ask"
    else:
        book_side = "ask" if yes_side else "bid"
    yes_px = limit_c if yes_side else 100 - limit_c
    body = {"ticker": ticker, "client_order_id": str(uuid.uuid4()),
            "side": book_side, "count": f"{int(count)}.00",
            "price": f"{yes_px / 100:.4f}", "time_in_force": "fill_or_kill",
            "self_trade_prevention_type": "taker_at_cross", "exchange_index": -1}
    t0 = time.perf_counter()
    code, resp = api_post(sess, k, "/portfolio/events/orders", body)
    rtt = (time.perf_counter() - t0) * 1000
    o = resp if isinstance(resp, dict) else {}
    try: fc = float(o.get("fill_count") or 0)
    except (TypeError, ValueError): fc = 0
    avg = fee = None
    try:
        if o.get("average_fill_price") is not None: avg = round(float(o["average_fill_price"]) * 100)
        if o.get("average_fee_paid"): fee = round(float(o["average_fee_paid"]) * 100)
    except (TypeError, ValueError):
        pass
    if fc < 1:
        print(f"[order-fail] {ticker[-7:]} {action} {count}x limit {limit_c}c "
              f"HTTP {code} {rtt:.0f}ms {json.dumps(resp)[:150]}")
    return fc >= 1, avg, fee, rtt


def book_take(levels, n, worst=None, buy=False):
    """Walk best-first levels [(px, qty)] for n contracts. Returns the list
    of per-contract prices, or None if the book (within `worst`) can't
    fill all n — i.e. what a FOK with that limit would do."""
    out = []
    for px, q in levels:
        if worst is not None and ((buy and px > worst) or (not buy and px < worst)):
            break
        take = min(int(q), n - len(out))
        out += [px] * take
        if len(out) >= n:
            return out
    return None

def book_skip(levels, n):
    """The same levels with the best n contracts already taken."""
    out = []
    for px, q in levels:
        used = min(int(q), n)
        n -= used
        if q - used >= 1:
            out.append((px, q - used))
    return out

def avg(xs): return sum(xs) / len(xs)


# ============================================================
# WEBSOCKET FEED — orderbook_delta (primary) + ticker (fallback).
# One connection, background thread. on_tick(t, yb, ya, src, age) fires
# only when the best YES bid or YES ask actually changes.
# Kalshi books hold BIDS only: YES ask = 100 - best NO bid.
# ============================================================
def _levels(m, side):
    for key in (f"{side}_dollars_fp", f"{side}_dollars"):
        if m.get(key) is not None:
            out = {}
            for p, q in m[key] or []:
                c = _cents(p); qty = float(q)
                if c is not None and qty > 0: out[c] = out.get(c, 0.0) + qty
            return out
    out = {}
    for p, q in m.get(side) or []:
        if float(q) > 0: out[int(p)] = out.get(int(p), 0.0) + float(q)
    return out


class PriceFeed:
    def __init__(self, k):
        self.k = k
        self.lock = threading.Lock()
        self.books, self.top, self.tick = {}, {}, {}
        self.tracked = set()
        self.sids, self.cmd_channel, self.seq = {}, {}, {}
        self.sub_sent = False
        self.ws = None
        self.url_i = 0
        self.url = WS_URLS[0]
        self.ready = threading.Event()
        self.stop_flag = threading.Event()
        self._next_id = 1
        self.connect_count = 0
        self.auth_failed = False
        self.resync = False
        self.n_msgs = 0
        self.lags = collections.deque(maxlen=2000)
        self.on_tick = None

    def _cmd_id(self):
        with self.lock:
            i = self._next_id; self._next_id += 1
        return i

    def _headers(self):
        ts = str(int(time.time() * 1000))
        return [f"KALSHI-ACCESS-KEY: {KEY_ID}",
                f"KALSHI-ACCESS-SIGNATURE: {_sign(self.k, ts, 'GET', WS_PATH)}",
                f"KALSHI-ACCESS-TIMESTAMP: {ts}"]

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        threading.Thread(target=self._pinger, daemon=True).start()

    def stop(self):
        self.stop_flag.set()
        self._close()

    def _close(self):
        ws, self.ws = self.ws, None
        try:
            if ws: ws.close()
        except Exception:
            pass

    def _pinger(self):
        while not self.stop_flag.wait(WS_PING_SEC):
            ws = self.ws
            if ws is not None and self.ready.is_set():
                try: ws.ping()
                except Exception: pass

    def _run(self):
        backoff = 1
        auth_msg_shown = False
        while not self.stop_flag.is_set():
            self.url = WS_URLS[self.url_i % len(WS_URLS)]
            try:
                self.ready.clear()
                ws = websocket.create_connection(
                    self.url, header=self._headers(), timeout=WS_RECV_TIMEOUT_SEC,
                    enable_multithread=True)
                with self.lock:
                    self.books.clear(); self.top.clear()
                    self.sids.clear(); self.cmd_channel.clear(); self.seq.clear()
                    self.sub_sent = False
                    want = set(self.tracked)
                self.ws = ws
                self.connect_count += 1
                backoff = 1; self.auth_failed = False; self.resync = False
                if want:
                    self._subscribe(want)
                print(f"[ws] connected #{self.connect_count} {self.url.split('/')[2]}"
                      f"{f', {len(want)} markets' if want else ''}")
                self.ready.set()
                while not self.stop_flag.is_set():
                    raw = ws.recv()
                    if raw:
                        self._on_message(raw)
            except Exception as e:
                if self.stop_flag.is_set(): break
                self.ready.clear()
                self._close()
                status = getattr(e, "status_code", None)
                if status in (401, 403):
                    self.auth_failed = True
                    if not auth_msg_shown:
                        print(f"[ws] AUTH REJECTED (HTTP {status}) — key ID "
                              f"{KEY_ID[:8] or '<empty>'}... from {KEY_SRC or 'nowhere'}. "
                              "Key ID and .pem must be the same key pair. "
                              "Retrying every 30s.")
                        auth_msg_shown = True
                    time.sleep(30)
                elif self.resync:
                    pass  # order-book gap: reconnect immediately, same host
                else:
                    self.url_i += 1
                    print(f"[ws] disconnected ({str(e)[:160]}) — retry in {backoff}s "
                          f"via {WS_URLS[self.url_i % len(WS_URLS)].split('/')[2]}")
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 15)

    def _send(self, obj):
        ws = self.ws
        if ws is not None:
            ws.send(json.dumps(obj))

    def _subscribe(self, tickers):
        with self.lock:
            self.sub_sent = True
        for ch in ("orderbook_delta", "ticker"):
            cid = self._cmd_id()
            with self.lock:
                self.cmd_channel[cid] = ch
            params = {"channels": [ch], "market_tickers": sorted(tickers)}
            if ch == "ticker":
                params["send_initial_snapshot"] = True
            self._send({"id": cid, "cmd": "subscribe", "params": params})

    def _on_message(self, raw):
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return
        self.n_msgs += 1
        typ = msg.get("type")
        m = msg.get("msg") or {}

        if typ in ("orderbook_snapshot", "orderbook_delta"):
            sid, seq = msg.get("sid"), msg.get("seq")
            if sid is not None and seq is not None:
                last = self.seq.get(sid)
                if last is not None and seq != last + 1:
                    print(f"[ws] order-book sequence gap ({last}->{seq}) — resyncing")
                    self.resync = True
                    self.ready.clear()
                    self._close()
                    return
                self.seq[sid] = seq
            t = m.get("market_ticker")
            if not t: return
            t_rx = time.time()
            with self.lock:
                if typ == "orderbook_snapshot":
                    b = {"yes": _levels(m, "yes"), "no": _levels(m, "no")}
                    self.books[t] = b
                else:
                    b = self.books.get(t)
                    side = m.get("side")
                    if b is None or side not in ("yes", "no"): return
                    px = _cents(m.get("price_dollars"), m.get("price"))
                    try:
                        dq = float(m["delta_fp"] if m.get("delta_fp") is not None else m.get("delta"))
                    except (TypeError, ValueError, KeyError):
                        return
                    if px is None: return
                    q = b[side].get(px, 0.0) + dq
                    if q > 1e-9: b[side][px] = q
                    else: b[side].pop(px, None)
                yb = max(b["yes"]) if b["yes"] else None
                ya = 100 - max(b["no"]) if b["no"] else None
                ts_ms = m.get("ts_ms")
                lag = t_rx * 1000 - ts_ms if isinstance(ts_ms, (int, float)) else None
                if lag is not None: self.lags.append(lag)
                prev = self.top.get(t)
                self.top[t] = (yb, ya, t_rx, lag)
                changed = prev is None or prev[0] != yb or prev[1] != ya
            cb = self.on_tick
            if changed and cb and yb is not None and ya is not None and ya > yb:
                try: cb(t, yb, ya, "book", 0.0)
                except Exception as e: print(f"[on-tick-error] {e}")

        elif typ == "ticker":
            t = m.get("market_ticker")
            yb = _cents(m.get("yes_bid_dollars"), m.get("yes_bid"))
            ya = _cents(m.get("yes_ask_dollars"), m.get("yes_ask"))
            if not t or yb is None or ya is None: return
            with self.lock:
                self.tick[t] = (yb, ya, time.time())
                top = self.top.get(t)
                use = top is None or top[0] is None or top[1] is None
            cb = self.on_tick
            if use and cb and ya > yb:
                try: cb(t, yb, ya, "ticker", 0.0)
                except Exception as e: print(f"[on-tick-error] {e}")

        elif typ == "subscribed":
            ch = m.get("channel") or self.cmd_channel.get(msg.get("id"))
            if ch:
                with self.lock:
                    self.sids[ch] = m.get("sid")
        elif typ == "error":
            print(f"[ws-error] {m}")

    def track(self, tickers):
        """Call every loop with the markets to follow; sends only the delta."""
        tickers = set(tickers)
        if not self.ready.is_set():
            with self.lock:
                self.tracked = tickers  # subscribed on (re)connect
            return
        with self.lock:
            add, remove = tickers - self.tracked, self.tracked - tickers
            sent, sids = self.sub_sent, dict(self.sids)
        if not sent:
            if tickers:
                with self.lock:
                    self.tracked = tickers
                self._subscribe(tickers)
            return
        if not (add or remove): return
        if len(sids) < 2: return  # subscribe acks pending — retry next loop
        for sid in sids.values():
            if add:
                self._send({"id": self._cmd_id(), "cmd": "update_subscription",
                            "params": {"sid": sid, "market_tickers": sorted(add),
                                       "action": "add_markets"}})
            if remove:
                self._send({"id": self._cmd_id(), "cmd": "update_subscription",
                            "params": {"sid": sid, "market_tickers": sorted(remove),
                                       "action": "delete_markets"}})
        with self.lock:
            self.tracked = tickers
            for t in remove:
                self.books.pop(t, None); self.top.pop(t, None); self.tick.pop(t, None)

    def get(self, t):
        """(yes_bid, yes_ask, yes_mid, age_s, src) or None. The order book
        is valid for as long as the connection is up (no age limit — a
        quiet book is still correct); ticker data only if fresh."""
        if not self.ready.is_set(): return None
        with self.lock:
            top = self.top.get(t)
            tk = self.tick.get(t)
        if top and top[0] is not None and top[1] is not None and top[1] > top[0]:
            return top[0], top[1], (top[0] + top[1]) // 2, time.time() - top[2], "book"
        if tk and time.time() - tk[2] <= WS_MAX_AGE_SEC and tk[1] > tk[0]:
            return tk[0], tk[1], (tk[0] + tk[1]) // 2, time.time() - tk[2], "ticker"
        return None

    def bids(self, t, side):
        """Best-first [(px, qty)] bids for 'yes' or 'no', or None."""
        if not self.ready.is_set(): return None
        with self.lock:
            b = self.books.get(t)
            if not b: return None
            return sorted(b[side].items(), reverse=True)

    def lag(self, t):
        with self.lock:
            top = self.top.get(t)
        return top[3] if top else None

    def n_books(self):
        with self.lock:
            return sum(1 for v in self.top.values() if v[0] is not None and v[1] is not None)

    def lag_stats(self):
        with self.lock:
            xs = list(self.lags)
        if not xs: return None
        xs.sort()
        return xs[len(xs) // 2], xs[int(len(xs) * 0.95) - 1 if len(xs) > 20 else -1]


FEED = None
SPOT = None

def start_feed(k, wait=10):
    """(Re)starts the websocket feed. Safe to re-run any time."""
    global FEED
    if FEED is not None:
        FEED.stop()
    FEED = PriceFeed(k)
    FEED.start()
    if FEED.ready.wait(timeout=wait):
        print("[ws] feed is up")
    elif FEED.auth_failed:
        print("[ws] feed REJECTED by Kalshi (auth) — see message above")
    else:
        print("[ws] still connecting — entries stay paused until it's up")
    return FEED


# ============================================================
# SPOT PRICE FEED (Coinbase, no auth) — tick-log data only.
# ============================================================
SPOT_WS_URL = "wss://ws-feed.exchange.coinbase.com"
SPOT_ASSET_MAP = {"BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD",
                  "XRP": "XRP-USD", "DOGE": "DOGE-USD"}

class SpotFeed:
    def __init__(self, product_ids):
        self.product_ids = list(product_ids)
        self.prices = {}
        self.lock = threading.Lock()
        self.stop_flag = threading.Event()
        self.ws = None

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self.stop_flag.set()
        try:
            if self.ws: self.ws.close()
        except Exception: pass

    def _run(self):
        backoff = 1
        while not self.stop_flag.is_set():
            try:
                self.ws = websocket.create_connection(SPOT_WS_URL, timeout=15)
                backoff = 1
                self.ws.send(json.dumps({"type": "subscribe", "product_ids": self.product_ids,
                                         "channels": ["ticker"]}))
                while not self.stop_flag.is_set():
                    raw = self.ws.recv()
                    if not raw: continue
                    try: msg = json.loads(raw)
                    except (json.JSONDecodeError, TypeError): continue
                    if msg.get("type") != "ticker": continue
                    try: px = float(msg.get("price"))
                    except (TypeError, ValueError): continue
                    with self.lock:
                        self.prices[msg.get("product_id")] = (px, time.time())
            except Exception:
                if self.stop_flag.is_set(): break
                time.sleep(backoff); backoff = min(backoff * 2, 30)

    def get(self, asset_code, max_age=5.0):
        pid = SPOT_ASSET_MAP.get(asset_code)
        with self.lock:
            d = self.prices.get(pid) if pid else None
        if not d or time.time() - d[1] > max_age: return None
        return d[0], time.time() - d[1]


# ============================================================
# BACKGROUND CSV WRITER — keeps Drive I/O off every hot path.
# ============================================================
class CsvWriter:
    def __init__(self, path, cols):
        new = not (os.path.exists(path) and os.path.getsize(path) > 0)
        self.fh = open(path, "a", newline="")
        self.w = csv.writer(self.fh)
        if new:
            self.w.writerow(cols); self.fh.flush()
        self.q = queue.Queue()
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    def write(self, row):
        self.q.put(row)

    def _run(self):
        done = False
        while not done:
            rows = [self.q.get()]
            while True:
                try: rows.append(self.q.get_nowait())
                except queue.Empty: break
            for r in rows:
                if r is None: done = True
                else: self.w.writerow(r)
            self.fh.flush()

    def close(self):
        self.q.put(None)
        self.t.join(timeout=15)
        self.fh.close()


# ============================================================
# SHARD CASH (LIVE) — local balance estimate, re-synced from the real
# balance every BALANCE_RESYNC_SEC in the main loop. Stops never wait on
# it; entries check it locally.
# ============================================================
class ShardCash:
    def __init__(self, sess, k, dest=DEST_SHARD):
        self.sess, self.k, self.dest = sess, k, dest
        self.lock = threading.Lock()
        self.bal = balance_by_shard(sess, k).get(dest, 0)
        self.last_sync = time.time()

    def available(self):
        with self.lock: return self.bal

    def adjust(self, delta_c):
        with self.lock: self.bal += delta_c

    def resync(self, target_c=6000):
        if time.time() - self.last_sync < BALANCE_RESYNC_SEC: return
        bal = balance_by_shard(self.sess, self.k).get(self.dest, 0)
        if bal < target_c:
            move_funds_to_shard(self.sess, self.k, self.dest, quiet=True)
            bal = balance_by_shard(self.sess, self.k).get(self.dest, 0)
        with self.lock:
            self.bal = bal
            self.last_sync = time.time()


# ============================================================
# PREFLIGHT — run before main(). Proves auth, websocket and book feed
# are healthy and measures latency. main() won't start blind either.
# ============================================================
def preflight(listen_sec=8):
    print("Commod15min V26 preflight")
    k = init_auth()
    print(f"  [ok]   key ID {KEY_ID[:8]}... from {KEY_SRC}")
    print(f"  [ok]   private key {PEM_PATH}")
    sess = make_session()
    code, body = rest_auth_status(sess, k)
    if code != 200:
        print(f"  [FAIL] REST auth HTTP {code}: {body}")
        print("         key ID and .pem must be the same key pair (Kalshi > Account > API Keys)")
        return False
    print("  [ok]   REST auth (portfolio/balance -> 200)")
    rtts = []
    for _ in range(5):
        t0 = time.perf_counter()
        try: sess.get(BASE + "/exchange/status", timeout=5)
        except requests.RequestException: continue
        rtts.append((time.perf_counter() - t0) * 1000)
    if rtts:
        print(f"  [info] REST round trip: median {statistics.median(rtts):.0f}ms, "
              f"best {min(rtts):.0f}ms  (an order costs about one of these)")
    tickers = [m["ticker"] for m in fetch_open(sess, k) if m.get("ticker")]
    print(f"  [info] {len(tickers)} open markets across {len(SERIES)} series")
    feed = start_feed(k)
    if not feed.ready.is_set():
        print("  [FAIL] websocket did not connect"
              + (" — Kalshi rejected the key (401)" if feed.auth_failed else ""))
        return False
    feed.track(tickers)
    time.sleep(listen_sec)
    nb = feed.n_books()
    ls = feed.lag_stats()
    print(f"  [{'ok' if nb else 'FAIL'}]   websocket {feed.url.split('/')[2]}: "
          f"{nb}/{len(tickers)} order books live, {feed.n_msgs} messages in {listen_sec}s")
    if ls:
        print(f"  [info] exchange->bot feed lag: median {ls[0]:.0f}ms, p95 {ls[1]:.0f}ms "
              "(includes any Colab clock offset)")
    return nb > 0


# ============================================================
# MAIN
# ============================================================
def main():
    global FEED, SPOT
    print("Commod15min V26 — starting")
    print(f"markets: {', '.join(asset(s) for s in SERIES)}")
    k = init_auth()
    sess = make_session()
    require_rest_auth(sess, k)
    fetch_open(sess, k)

    if FEED is None or FEED.stop_flag.is_set() or FEED.auth_failed:
        start_feed(k)
    if FEED.auth_failed:
        raise SystemExit("websocket auth rejected — fix the key, then re-run")
    if SPOT is None:
        SPOT = SpotFeed(SPOT_ASSET_MAP.values()); SPOT.start()

    if LIVE:
        cancel_all_resting(sess, k)
        move_funds_to_shard(sess, k)
    mode = "LIVE" if LIVE else "PAPER"
    bal = get_balance(sess, k)
    print(f"mode={mode}  balance={'$%.2f' % (bal / 100) if bal is not None else '—'}")
    print(f"rules: armed last {TRIGGER_MIN:.0f}m · buy the favorite when its ask is "
          f"{ENTRY_C}-{ENTRY_CAP_C}c · {CONTRACTS} contracts FOK · if its bid <= {STOP_C}c: "
          f"reverse (2x opposite side, 1st try limit bid-{STOP_SLIPPAGE_C}c, then market exit) "
          f"· reversal held to settlement · no stops in last {STOP_DEADBAND_SEC:.0f}s "
          f"· entries need the websocket: {REQUIRE_WS_FOR_ENTRY}")

    order_executor = ThreadPoolExecutor(max_workers=max(4, len(SERIES)))
    state_lock = threading.RLock()
    shard_cash = ShardCash(sess, k) if LIVE else None
    trades = CsvWriter(OUT_CSV, COLS)
    ticks = CsvWriter(TICK_CSV, TICK_COLS)

    state, close_at, strike_at = {}, {}, {}
    open_assets = set()
    run_net, n_trades = 0.0, 0
    day = now().date()
    halt = False
    last_ws_warn = 0.0
    health_window = None

    def s2c_of(t):
        ct = close_at.get(t)
        return None if ct is None else (ct - now()).total_seconds()

    def record(t, reason, p, exit_px, trigger_px=None, seen_px=None,
               src="", lag=None, ms=None):
        nonlocal run_net, n_trades
        lots, entry_px = p["lots"], p["entry_px"]
        entry_fee = fee_est(entry_px) * lots
        exit_fee = fee_est(exit_px) * lots if reason == "stop" else 0
        net = (exit_px - entry_px) * lots - entry_fee - exit_fee
        s2c = s2c_of(t)
        with state_lock:
            run_net += net; n_trades += 1
            run = run_net
        trades.write([t, mode, "YES" if p["yes"] else "NO", p.get("entry_iso", ""),
                      round(entry_px, 1), now_iso(), round(exit_px, 1), reason,
                      round(net, 1), round(run, 1), p.get("entry_s2c"),
                      round(s2c, 1) if s2c is not None else None,
                      trigger_px, seen_px, src,
                      round(lag) if lag is not None else None,
                      round(ms) if ms is not None else None])
        print(f"[{now():%H:%M:%S}] {reason.upper()} {lots}x {'YES' if p['yes'] else 'NO'} "
              f"entry={entry_px:.1f} exit={exit_px:.1f} net={net:+.0f}c  "
              f"{asset(t)} {t[-7:]}  run={run:+.0f}c")

    def set_done(t):
        with state_lock:
            state[t] = {"phase": "done"}
            open_assets.discard(asset(t))

    def do_entry(t, lead_yes, ask_seen, src, t_trig, s2c):
        side = "YES" if lead_yes else "NO"
        lag = FEED.lag(t) if FEED else None

        def release(why):
            with state_lock:
                if state.get(t, {}).get("phase") == "entering":
                    state[t] = {"phase": "armed", "retry_after": time.time() + ORDER_RETRY_SEC}
                open_assets.discard(asset(t))
            print(f"    (entry {side} {asset(t)} {t[-7:]} not filled — {why}; will retry)")

        rtt = None
        if LIVE:
            need = ENTRY_CAP_C * CONTRACTS
            if shard_cash.available() < need:
                print(f"[skip-bal] {asset(t)} {t[-7:]} need ~{need}c, have "
                      f"{shard_cash.available()}c on shard {DEST_SHARD}")
                release("low shard balance")
                return
            ok, avgpx, _, rtt = place_order(sess, k, t, "buy", lead_yes, CONTRACTS,
                                            limit_c=ENTRY_CAP_C)
            if not ok:
                release(f"book moved past {ENTRY_CAP_C}c or too thin")
                return
            fill = (avgpx if lead_yes else 100 - avgpx) if avgpx is not None else ask_seen
            shard_cash.adjust(-int(fill * CONTRACTS))
        else:
            asks = None
            opp = FEED.bids(t, "no" if lead_yes else "yes") if FEED else None
            if opp:
                asks = [(100 - px, q) for px, q in opp]
            if asks:
                px = book_take(asks, CONTRACTS, worst=ENTRY_CAP_C, buy=True)
                if px is None:
                    release(f"not {CONTRACTS} contracts at <= {ENTRY_CAP_C}c")
                    return
                fill = avg(px)
            else:
                fill = ask_seen
        with state_lock:
            state[t] = {"phase": "long", "yes": lead_yes, "entry_px": fill,
                        "lots": CONTRACTS, "entry_iso": now_iso(),
                        "entry_s2c": round(s2c, 1)}
        print(f"[{now():%H:%M:%S}] BUY {CONTRACTS}x {side} @ {fill:.1f}c  {asset(t)} {t[-7:]} "
              f"{s2c:.0f}s to close  run={run_net:+.0f}c  (saw ask {ask_seen}c via {src}, "
              f"feed lag {fmt_ms(lag)}, slip {fill - ask_seen:+.1f}c, "
              f"trigger->fill {(time.time() - t_trig) * 1000:.0f}ms"
              f"{f', order rtt {rtt:.0f}ms' if rtt is not None else ''})")

    def do_stop(t, p, held_bid0, src0, t_trig):
        held_yes, lots, rev_yes = p["yes"], p["lots"], not p["yes"]
        held_bid, src = held_bid0, src0
        fresh = FEED.get(t) if FEED else None
        if fresh is not None:
            fyb, fya, _, _, src = fresh
            held_bid = fyb if held_yes else 100 - fya
        lag = FEED.lag(t) if FEED else None

        def back_to_long(why, retry=0.0):
            with state_lock:
                if state.get(t, {}).get("phase") == "stopping":
                    state[t] = dict(p, phase="long", retry_after=time.time() + retry)
            print(f"    ({why} — {asset(t)} {t[-7:]})")

        s2c = s2c_of(t)
        if s2c is None or s2c <= STOP_DEADBAND_SEC:
            back_to_long("inside close deadband at execution — ride to settlement")
            return
        if held_bid > STOP_C:
            back_to_long(f"bid back to {held_bid}c before execution — hold")
            return

        floor1 = max(1, held_bid - STOP_SLIPPAGE_C)
        rtts = []
        combo = False
        if LIVE:
            ok, a, _, rtt = place_order(sess, k, t, "buy", rev_yes, 2 * lots,
                                        limit_c=100 - floor1)
            rtts.append(rtt)
            if ok:
                combo = True
                rev_fill = (a if rev_yes else 100 - a) if a is not None else 100 - held_bid
                exit_px = 100 - rev_fill
                shard_cash.adjust(-int(rev_fill * 2 * lots) + 100 * lots)
            else:
                ok, a, _, rtt = place_order(sess, k, t, "sell", held_yes, lots, limit_c=None)
                rtts.append(rtt)
                if not ok:
                    back_to_long("stop exit got no fill (empty book?) — retrying", ORDER_RETRY_SEC)
                    return
                exit_px = (a if held_yes else 100 - a) if a is not None else held_bid
                shard_cash.adjust(int(exit_px * lots))
        else:
            bids = FEED.bids(t, "yes" if held_yes else "no") if FEED else None
            if bids:
                px = book_take(bids, 2 * lots, worst=floor1)
                if px is not None:
                    combo = True
                    exit_px, rev_fill = avg(px[:lots]), 100 - avg(px[lots:])
                else:
                    px = book_take(bids, lots) or [held_bid] * lots
                    exit_px = avg(px)
            else:
                combo = True
                exit_px, rev_fill = held_bid, 100 - held_bid
        ms = (time.time() - t_trig) * 1000

        record(t, "stop", p, exit_px, trigger_px=STOP_C, seen_px=held_bid, src=src,
               lag=lag, ms=ms)
        print(f"    (stop {STOP_C}c, saw bid {held_bid}c via {src}, feed lag {fmt_ms(lag)}, "
              f"exit {exit_px:.1f}c [{exit_px - STOP_C:+.1f}c vs stop], "
              f"trigger->fill {ms:.0f}ms"
              f"{', order rtt ' + '+'.join(f'{r:.0f}' for r in rtts) + 'ms' if rtts else ''}"
              f", {'combined order' if combo else 'market exit + separate reversal'})")

        if not combo:
            limit = min(99, round(100 - exit_px) + REVERSAL_SLIPPAGE_C)
            if LIVE:
                ok, a, _, rtt = place_order(sess, k, t, "buy", rev_yes, lots, limit_c=limit)
                if not ok:
                    print(f"[reverse-fail] {asset(t)} {t[-7:]} — flat, no reversal")
                    set_done(t)
                    return
                rev_fill = (a if rev_yes else 100 - a) if a is not None else 100 - exit_px
                shard_cash.adjust(-int(rev_fill * lots))
            else:
                # buying the reversal side hits the held side's bids — the
                # ones left after the exit above already took the best `lots`
                opp = FEED.bids(t, "yes" if held_yes else "no") if FEED else None
                if opp:
                    opp = book_skip(opp, lots)
                px = book_take([(100 - b, q) for b, q in opp], lots, worst=limit, buy=True) \
                    if opp else None
                if opp and px is None:
                    print(f"[reverse-fail] {asset(t)} {t[-7:]} — no {lots} at <= {limit}c, flat")
                    set_done(t)
                    return
                rev_fill = avg(px) if px else 100 - exit_px

        s2c = s2c_of(t)
        with state_lock:
            state[t] = {"phase": "reversal", "yes": rev_yes, "entry_px": rev_fill,
                        "lots": lots, "entry_iso": now_iso(),
                        "entry_s2c": round(s2c, 1) if s2c is not None else None}
        print(f"[{now():%H:%M:%S}] REVERSE {lots}x {'YES' if rev_yes else 'NO'} "
              f"@ {rev_fill:.1f}c  {asset(t)} {t[-7:]}")

    def try_entry(t, p, yb, ya, src):
        """Called with state_lock held. Claims the market and returns
        do_entry args, or None."""
        if halt: return None
        if REQUIRE_WS_FOR_ENTRY and src == "rest": return None
        if time.time() < p.get("retry_after", 0): return None
        s2c = s2c_of(t)
        if s2c is None or s2c <= 0 or s2c > TRIGGER_MIN * 60: return None
        if asset(t) in open_assets: return None
        lead_yes = (yb + ya) >= 100
        ask = ya if lead_yes else 100 - yb
        if ask < ENTRY_C or ask > ENTRY_CAP_C: return None
        state[t] = {"phase": "entering"}
        open_assets.add(asset(t))
        return (t, lead_yes, ask, src, time.time(), s2c)

    def handle_tick(t, yb, ya, src="book", src_age=0.0):
        """Runs on the WS thread the instant the best bid/ask changes (and
        once per REST loop as a backstop). Cheap: decide, claim, hand off."""
        t_rx = time.time()
        with state_lock:
            p = state.get(t)
            if not p: return
            ph = p.get("phase")
            if ph in ("watch", "armed"):
                args = try_entry(t, p, yb, ya, src)
                if args is None: return
                job = ("entry", args)
            elif ph == "long":
                if t_rx < p.get("retry_after", 0): return
                s2c = s2c_of(t)
                if s2c is None or s2c <= STOP_DEADBAND_SEC or s2c > STOP_ACTIVATE_SEC:
                    return
                held_bid = yb if p["yes"] else 100 - ya
                if held_bid > STOP_C: return
                job = ("stop", (t, dict(p), held_bid, src, t_rx))
                state[t] = dict(p, phase="stopping")
            else:
                return
        fn = do_entry if job[0] == "entry" else do_stop
        if LIVE: order_executor.submit(fn, *job[1])
        else:    fn(*job[1])

    FEED.on_tick = handle_tick

    try:
        while True:
            try:
                t0 = time.time()
                if now().date() != day:
                    day = now().date(); n_trades = 0; halt = False
                    if LIVE: run_net = 0.0
                if LIVE and not halt and (run_net <= -DAILY_LOSS_LIMIT_C or n_trades >= MAX_TRADES_DAY):
                    halt = True
                    print(f"[{now():%H:%M:%S}] HALT — net={run_net:+.0f}c trades={n_trades}")

                live = {m["ticker"]: m for m in fetch_open(sess, k) if m.get("ticker")}
                fetch_ts = time.time()
                FEED.track(live.keys())

                if not FEED.ready.is_set() and time.time() - last_ws_warn > 30:
                    last_ws_warn = time.time()
                    print(f"[{now():%H:%M:%S}] [ws] DOWN — new entries paused; "
                          "open positions' stops running on REST backstop (slower)")

                for t, m in live.items():
                    if close_at.get(t) is None:
                        close_at[t] = parse_iso(m.get("close_time"))
                    strike_at.setdefault(t, m.get("floor_strike"))
                    state.setdefault(t, {"phase": "watch"})

                for t, p in list(state.items()):
                    if p.get("phase") not in ("long", "reversal") or t in live: continue
                    mk = fetch_market(sess, k, t)
                    if mk.get("status") in ("finalized", "settled") and mk.get("result"):
                        won = (mk["result"] == "yes") == p["yes"]
                        rev = p["phase"] == "reversal"
                        reason = ("rev_settle_win" if won else "rev_settle_loss") if rev \
                            else ("settle_win" if won else "settle_loss")
                        record(t, reason, p, 100 if won else 0)
                        if LIVE and won: shard_cash.adjust(100 * p["lots"])
                        set_done(t)

                for t, m in live.items():
                    s2c = s2c_of(t)
                    if s2c is None or s2c <= 0 or s2c > TRIGGER_MIN * 60: continue
                    q = FEED.get(t)
                    if q is not None:
                        yb, ya, ymid, src_age, src = q
                    else:
                        r = rest_prices(m)
                        if r is None: continue
                        yb, ya, ymid = r
                        src, src_age = "rest", time.time() - fetch_ts

                    sp = SPOT.get(asset(t)) if SPOT else None
                    spot_px, spot_age = sp if sp else (None, None)
                    strike = strike_at.get(t)
                    pct = round((spot_px - strike) / strike * 100, 4) \
                        if spot_px is not None and strike else None
                    ph = state[t].get("phase")
                    ticks.write([now_iso(), t, asset(t), ph, src, yb, ya, ymid, round(s2c, 1),
                                 spot_px, strike, pct,
                                 round(spot_age, 2) if spot_age is not None else None])
                    if ph == "done": continue

                    with state_lock:
                        if state[t].get("phase") == "watch":
                            state[t] = {"phase": "armed"}
                            wk = window_key(t)
                            if wk != health_window:
                                health_window = wk
                                ls = FEED.lag_stats()
                                print(f"[{now():%H:%M:%S}] window {wk}: ws "
                                      f"{'UP' if FEED.ready.is_set() else 'DOWN'} "
                                      f"({FEED.url.split('/')[2]}, connects={FEED.connect_count}) "
                                      f"books {FEED.n_books()}/{len(live)}"
                                      + (f", feed lag median {ls[0]:.0f}ms p95 {ls[1]:.0f}ms"
                                         if ls else ""))
                            print(f"[armed] {asset(t)} {t[-7:]} fav={max(ymid, 100 - ymid)} "
                                  f"{s2c:.0f}s to close")

                    handle_tick(t, yb, ya, src, src_age)

                if LIVE:
                    shard_cash.resync()

                el = time.time() - t0
                if el < POLL_SEC: time.sleep(POLL_SEC - el)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                print(f"[loop-error] {e}"); time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        FEED.on_tick = None
        print("[stopped] waiting for in-flight orders...")
        order_executor.shutdown(wait=True)
        trades.close(); ticks.close()
        print(f"[stopped] records={n_trades} net={run_net:+.0f}c")
