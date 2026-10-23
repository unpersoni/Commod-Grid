# ============================================================
# LIP Maker V1 — a two-sided maker paid by Kalshi's Liquidity Incentive Program · DRY RUN by default
#
# WHAT IT DOES: Kalshi pays a reward pool per market for resting orders near the best price, scored every second as
#   size x discount^(cents below the reference price), whether or not they fill. The bot picks the best-paying markets
#   in the series you choose (default: AAA daily gas prices by state, KXAAAGASD) and keeps one YES bid and one NO bid
#   resting at each market's reference price, both sides at once. A YES + NO pair always pays $1.00, so fills on both
#   sides lock in the gap between the two bids; the reward is paid on top.
# RISK CONTROLS: post-only (never takes) · at most MAX_NET contracts held one side minus the other per market (that
#   side stops quoting) · orders pulled PULL_BEFORE_CLOSE_MIN before the close and outside the reward period · a fast
#   price move (FAST_MOVE_C cents within FAST_MOVE_SEC) pulls both sides for COOL_OFF_SEC · no quotes in nearly decided
#   markets (best bid outside MIN_PX-MAX_PX) · MAX_LOSS_USD: once fills are down this much (marked at the best bids),
#   every order is cancelled and nothing new is quoted in this run · kill switch: create STOP_LIPMM.txt on Drive ·
#   live: Kalshi-side expiry on every order, an order group (Kalshi cancels everything if ORDER_GROUP_LIMIT contracts
#   fill within 15 s), leftover orders in the chosen series cancelled at start, 10-second countdown.
# REWARD: the log shows the bot's own ESTIMATE from the books (each side scored against half the pool); Kalshi's
#   actual payout is on the Rewards page / Account -> Activity -> Credits. That payout is the real verdict.
# DRY RUN: the exact orders are built and signed but NOT sent; fills are simulated from Kalshi's trade prints
#   (a print at or through our price fills us, ahead of anyone else: the worst case).
# LOGS (Drive): lip_maker_v1_fills.csv, lip_maker_v1_markets.csv, lip_maker_v1_data/orders_ (hourly, gzipped).
# ============================================================

import os, sys, collections, csv, json, time, math, base64, threading, uuid, datetime as dt
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

# ---------------- settings ----------------
BOT_NAME = "LIP Maker V1"
# The SWITCHES cell (the cell that starts the bot) sets these; the values here are used only when it has not been run.
_sw = globals()
DRY_RUN       = _sw.get("DRY_RUN", True)            # True = nothing is sent.  False = LIVE, REAL MONEY
SERIES        = _sw.get("SERIES", ["KXAAAGASD"])    # markets whose ticker starts with one of these
CONTRACTS     = _sw.get("CONTRACTS", 10)            # resting on EACH side of each market
MAX_MARKETS   = _sw.get("MAX_MARKETS", 3)
BUDGET_USD    = _sw.get("BUDGET_USD", 60)           # money tied up in resting orders + contracts held, all markets
MAX_NET       = _sw.get("MAX_NET", 20)              # per market: contracts held on one side minus the other
MAX_LOSS_USD  = _sw.get("MAX_LOSS_USD", 15)         # fills down this much: everything cancelled, no new quotes

PULL_BEFORE_CLOSE_MIN = 60    # no orders in a market's last 60 minutes
MIN_PX, MAX_PX = 5, 95        # quote a side only while its best bid is 5-95c (nearly decided markets are skipped)
FAST_MOVE_C    = 4            # the YES mid moving 4c+ ...
FAST_MOVE_SEC  = 60           # ... within 60 s pulls both sides ...
COOL_OFF_SEC   = 300          # ... for 5 minutes
REQUOTE_HOLD_SEC = 5          # an order is moved once its price has been off target this long
PROGRAMS_REFRESH_SEC = 600    # re-read Kalshi's reward programs and re-pick markets every 10 minutes
LOOP_SEC       = 3            # one pass over the chosen markets (order books, orders, fills) every 3 s
STATUS_EVERY_SEC = 900
SETTLE_RETRY_SEC = 60
RECONCILE_SEC  = 120          # live: compare our count of contracts held with Kalshi's every 2 minutes
REWARD_UNIT    = 10000        # Kalshi's period_reward is in 1/100 cents (1,000,000 = $100)

PEM_PATH  = '/content/drive/MyDrive/intraday key.pem'
KEY_ID    = "__KEY_ID__"
DRIVE     = '/content/drive/MyDrive/'
FILLS_CSV   = DRIVE + 'lip_maker_v1_fills.csv'
MARKETS_CSV = DRIVE + 'lip_maker_v1_markets.csv'
DATA_DIR    = DRIVE + 'lip_maker_v1_data/'
KILL_FILE   = DRIVE + 'STOP_LIPMM.txt'

MAX_WRITES_PER_SEC = 10       # Kalshi Basic API tier
ORDER_GROUP_LIMIT  = 200      # live: Kalshi cancels all our orders if this many contracts fill within 15 s
LIVE_COUNTDOWN_SEC = 10
ORDER_CREATE_PATH = "/portfolio/events/orders"
ORDER_CANCEL_PATH = "/portfolio/events/orders/{order_id}"
ORDERS_LIST_PATH  = "/portfolio/orders"
ORDER_GROUP_PATH  = "/portfolio/order_groups/create"

BASE = "https://api.elections.kalshi.com/trade-api/v2"
PREF = "/trade-api/v2"

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

# ---------------- the bot ----------------
def check_switches():
    if not isinstance(DRY_RUN, bool): sys.exit(f"DRY_RUN must be True or False without quotes (it is {DRY_RUN!r})")
    if not (isinstance(SERIES, (list, tuple)) and SERIES and all(isinstance(s, str) and s.startswith("KX") for s in SERIES)):
        sys.exit(f'SERIES must be a list of ticker beginnings like ["KXAAAGASD"] (it is {SERIES!r})')
    for name, lo, hi in (("CONTRACTS", 1, 500), ("MAX_MARKETS", 1, 20), ("MAX_NET", 1, 1000)):
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

class Market:
    def __init__(self, ticker):
        self.t = ticker
        self.prog = None                  # the reward program: per_hour ($), target, disc, end
        self.close = None
        self.quoting = False              # picked now (False: only followed until settled)
        self.orders = {"yes": None, "no": None}     # {"oid", "side", "px", "left", "filled", "since_off"}
        self.held = {"yes": 0.0, "no": 0.0}
        self.cost = {"yes": 0.0, "no": 0.0}         # cents
        self.best = {"yes": None, "no": None}
        self.mids = collections.deque()             # (time, YES mid)
        self.cool_until = 0
        self.reward_c = 0.0                         # estimated reward, cents
        self.share = {"yes": 0.0, "no": 0.0}
        self.adj = 0.0                              # live: Kalshi's net position minus ours (missed fills)
        self.last_trade_ts = int(time.time())
        self.seen_trades = set()
        self.done = False
    def net(self): return self.held["yes"] - self.held["no"] + self.adj
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
        print(f"{BOT_NAME} — DRY RUN (exact orders built and signed — NOTHING is sent; fills simulated from Kalshi's trades)")
    print(f"markets: Kalshi liquidity-reward programs in {', '.join(SERIES)} · best {MAX_MARKETS} by estimated reward · "
          f"{CONTRACTS} contracts resting on EACH side · budget ${BUDGET_USD}")
    print(f"limits: at most {MAX_NET} contracts net per market · fills down ${MAX_LOSS_USD}: everything cancelled, no new quotes · "
          f"no orders in the last {PULL_BEFORE_CLOSE_MIN} min · {FAST_MOVE_C}c move in {FAST_MOVE_SEC}s: both sides pulled for "
          f"{COOL_OFF_SEC // 60} min · sides quoted only at {MIN_PX}-{MAX_PX}c · kill switch: create {KILL_FILE}")
    print("reward shown here is the bot's ESTIMATE; Kalshi's real payout: Rewards page / Account -> Activity -> Credits")
    if live and LIVE_COUNTDOWN_SEC > 0:
        for i in range(int(LIVE_COUNTDOWN_SEC), 0, -1):
            print(f"LIVE trading starts in {i} s — stop the cell now to cancel", flush=True); time.sleep(1)
    k = load_key()
    sess = requests.Session()
    def say(msg): print(f"[{now():%H:%M:%S}] {msg}", flush=True)
    gw = Gateway(live, k, sess, say)
    held0 = gw.startup()
    mk = {}                                            # ticker -> Market
    for t, pos in held0.items():
        m = mk.setdefault(t, Market(t))
        m.held["yes" if pos > 0 else "no"] = abs(pos)
        say(f"[account] already holding {abs(pos):.0f} {'YES' if pos > 0 else 'NO'} in {t} — counted in the net limit "
            f"(its cost is taken at today's price)")
    flags = {"halted": False, "kill": False}
    os.makedirs(DRIVE, exist_ok=True)
    def open_csv(path, cols):
        new = not os.path.exists(path)
        fh = open(path, "a", newline=""); w = csv.writer(fh)
        if new: w.writerow(cols); fh.flush()
        return fh, w
    fills_fh, fills_w = open_csv(FILLS_CSV, FILL_COLS)
    mkts_fh, mkts_w = open_csv(MARKETS_CSV, MARKET_COLS)
    totals = {"settled_pnl": 0.0, "settled_reward": 0.0, "fills": 0, "markets_settled": 0}

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

    def estimate(pr, lv):
        """$/h our CONTRACTS per side would earn now, the capital they tie up (cents), (ref yes, ref no)."""
        refs, est = {}, 0.0
        for s in ("yes", "no"):
            ref = reference(lv[s], pr["target"])
            if ref is None or not (MIN_PX <= lv[s][0][0] <= MAX_PX): return None, None, None
            refs[s] = ref
            est += pr["per_hour"] / 2 * CONTRACTS / (score(lv[s], ref, pr["disc"]) + CONTRACTS)
        return est, CONTRACTS * (refs["yes"] + refs["no"]), refs

    def pick():
        progs = fetch_programs()
        if progs is None: say("[programs] Kalshi's reward programs could not be read — keeping the current markets"); return
        cands = []
        for t, pr in progs.items():
            md = (api_get(sess, k, f"/markets/{t}") or {}).get("market") or {}
            close = parse_iso(md.get("close_time"))
            if md.get("status") not in (None, "open", "active") or not close: continue
            if (close - now()).total_seconds() < (PULL_BEFORE_CLOSE_MIN + 30) * 60: continue
            if pr["start"] > now(): continue
            lv = book_levels(api_get(sess, k, f"/markets/{t}/orderbook"))
            m = mk.get(t)
            if m: lv = {s: minus_own(lv[s], own_of(m, s)) for s in ("yes", "no")}
            est, cap, _ = estimate(pr, lv)
            if est: cands.append((est, cap, t, pr, close))
            time.sleep(0.05)
        cands.sort(key=lambda c: -c[0])
        chosen, used = [], held_cost()
        for est, cap, t, pr, close in cands:
            if len(chosen) >= MAX_MARKETS: break
            if used + cap > BUDGET_USD * 100: continue
            chosen.append((est, cap, t, pr, close)); used += cap
        names = {c[2] for c in chosen}
        for t, m in mk.items():
            if m.quoting and t not in names:
                m.quoting = False; pull(m, "no longer picked")
                say(f"[programs] {t}: no longer among the best — orders pulled (any contracts held are followed to the close)")
        for est, cap, t, pr, close in chosen:
            m = mk.setdefault(t, Market(t))
            if not m.quoting: say(f"[programs] PICKED {t}: pool ${pr['per_hour']:.2f}/h, estimated ${est:.2f}/h for us, "
                                  f"${cap / 100:.0f} in resting orders, reward period ends {pr['end']:%b %d %H:%M} UTC, "
                                  f"market closes {close:%b %d %H:%M} UTC")
            m.prog, m.close, m.quoting = pr, close, True
        say(f"[programs] {len(progs)} reward programs in {', '.join(SERIES)}; {len(cands)} quotable now; quoting {len(chosen)} "
            f"(${used / 100:,.0f} of the ${BUDGET_USD} budget)" + ("" if chosen else " — nothing suitable right now, checking again in 10 min"))

    def own_of(m, s):
        o = m.orders[s]
        return {o["px"]: o["left"]} if (o and live) else {}   # dry run: our orders are not in Kalshi's book

    def held_cost():
        return sum(m.cost["yes"] + m.cost["no"] for m in mk.values() if not m.done)

    def drop(m, s, reason):
        """Cancel our order on this side. It is forgotten only once the cancel is confirmed (else retried next pass)."""
        o = m.orders[s]
        if not o: return True
        ok = gw.cancel(m.t, o, reason)
        if live: check_order(m, s, final=True)
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

    def check_order(m, s, final=False):
        """Live: new fills on our order (from Kalshi), and whether it still rests."""
        o = m.orders[s]
        if not o: return
        filled, resting = gw.order_state(o["oid"])
        if filled is None: return
        if filled > o["filled"]:
            n = filled - o["filled"]; o["filled"] = filled; o["left"] = max(0, o["left"] - n)
            record_fill(m, s, n, o["px"], "Kalshi")
        if not resting and not final: m.orders[s] = None

    def sim_fills(m):
        """Dry run: a trade print at or through our price fills us first (the worst case for being picked off)."""
        d = api_get(sess, k, "/markets/trades", {"ticker": m.t, "min_ts": m.last_trade_ts - 5, "limit": 200})
        for tr in (d or {}).get("trades", []) or []:
            tid = tr.get("trade_id") or json.dumps(tr, sort_keys=True)
            if tid in m.seen_trades: continue
            m.seen_trades.add(tid)
            yp, n, taker = field(tr, "yes_price"), field(tr, "count"), tr.get("taker_side")
            if yp is None or not n: continue
            ct = parse_iso(tr.get("created_time"))
            if ct: m.last_trade_ts = max(m.last_trade_ts, int(ct.timestamp()))
            for s, hit in (("yes", taker == "no" and m.orders["yes"] and yp <= m.orders["yes"]["px"]),
                           ("no", taker == "yes" and m.orders["no"] and 100 - yp <= m.orders["no"]["px"])):
                o = m.orders[s]
                if hit and o["left"] > 0:
                    x = min(n, o["left"]); o["left"] -= x; o["filled"] += x
                    record_fill(m, s, x, o["px"], "simulated")
                    if o["left"] <= 0: m.orders[s] = None
        if len(m.seen_trades) > 5000: m.seen_trades = set(list(m.seen_trades)[-2000:])

    def work(m, dt_s):
        lv_all = book_levels(api_get(sess, k, f"/markets/{m.t}/orderbook"))
        lv = {s: minus_own(lv_all[s], own_of(m, s)) for s in ("yes", "no")}
        for s in ("yes", "no"): m.best[s] = lv_all[s][0][0] if lv_all[s] else None
        if live:
            for s in ("yes", "no"): check_order(m, s)
        else:
            sim_fills(m)
        t_now = time.time()
        if m.best["yes"] is not None and m.best["no"] is not None:
            mid = (m.best["yes"] + 100 - m.best["no"]) / 2
            m.mids.append((t_now, mid))
            while m.mids and t_now - m.mids[0][0] > FAST_MOVE_SEC: m.mids.popleft()
            if max(x for _, x in m.mids) - min(x for _, x in m.mids) >= FAST_MOVE_C and t_now >= m.cool_until:
                m.cool_until = t_now + COOL_OFF_SEC
                m.mids.clear(); m.mids.append((t_now, mid))         # measure the next move from here
                pull(m, "fast move")
                say(f"[safety] {m.t}: price moved {FAST_MOVE_C}c+ within {FAST_MOVE_SEC}s — both sides pulled for {COOL_OFF_SEC / 60:g} min")
        pr = m.prog
        # the reward estimate: our resting size against everyone's, per side, half the pool each
        if pr and m.quoting and now() < pr["end"]:
            for s in ("yes", "no"):
                o = m.orders[s]
                ref = reference(lv_all[s] if live else sorted(lv_all[s] + ([(o["px"], o["left"])] if o else []), reverse=True),
                                pr["target"])
                if not o or ref is None: m.share[s] = 0.0; continue
                mine = o["left"] * pr["disc"] ** max(0, ref - o["px"])
                total = score(lv[s], ref, pr["disc"]) + mine
                m.share[s] = mine / total if total > 0 else 0.0
                m.reward_c += 100 * pr["per_hour"] / 2 * m.share[s] * dt_s / 3600
        # what should rest now
        stop = (not m.quoting or flags["halted"] or flags["kill"] or t_now < m.cool_until or not pr
                or now() >= pr["end"] or (m.close and (m.close - now()).total_seconds() < PULL_BEFORE_CLOSE_MIN * 60))
        for s in ("yes", "no"):
            o = m.orders[s]
            want_n = min(CONTRACTS, MAX_NET - m.net()) if s == "yes" else min(CONTRACTS, MAX_NET + m.net())
            ref = reference(lv[s], pr["target"]) if pr else None
            best = lv[s][0][0] if lv[s] else None
            ok = not stop and want_n >= 1 and ref is not None and best is not None and MIN_PX <= best <= MAX_PX
            if ok:                                                  # never cross the other side's best bid
                other = m.best["no" if s == "yes" else "yes"]
                if other is not None and ref + other >= 100: ok = False
            if not ok:
                drop(m, s, "pulled")
                continue
            if o and (o["px"] != ref or o["left"] > want_n):
                o["since_off"] = o.get("since_off") or t_now
                if t_now - o["since_off"] < REQUOTE_HOLD_SEC and o["left"] <= want_n: continue
                if not drop(m, s, f"moved {o['px']}c -> {ref}c"): continue
                o = None
            elif o:
                o["since_off"] = None
            if not o:
                expire = min(pr["end"], m.close - dt.timedelta(minutes=PULL_BEFORE_CLOSE_MIN)) if m.close else pr["end"]
                oid = gw.create(m.t, s, ref, int(want_n), expire)
                if oid: m.orders[s] = {"oid": oid, "side": s, "px": ref, "left": float(int(want_n)), "filled": 0.0, "since_off": None}

    def settle(m):
        md = (api_get(sess, k, f"/markets/{m.t}") or {}).get("market") or {}
        res = md.get("result")
        if res not in ("yes", "no"): return False
        pay = 100 * m.held[res]
        pnl = pay - m.cost["yes"] - m.cost["no"]
        totals["settled_pnl"] += pnl; totals["settled_reward"] += m.reward_c; totals["markets_settled"] += 1
        mkts_w.writerow([now_iso(), m.t, res, m.held["yes"], m.cost["yes"], m.held["no"], m.cost["no"], pnl,
                         round(m.reward_c, 1), int(live)]); mkts_fh.flush()
        say(f"SETTLED {res.upper()}  {m.t}: held YES {m.held['yes']:g} / NO {m.held['no']:g} -> fills {_usd(pnl)} · "
            f"estimated reward {_usd(m.reward_c)}")
        m.done = True
        return True

    def status():
        open_ = [m for m in mk.values() if not m.done]
        fills_pnl = sum(m.pnl_c() for m in open_) + totals["settled_pnl"]
        est = sum(m.reward_c for m in open_) + totals["settled_reward"]
        say(f"[status] {'LIVE' if live else 'DRY RUN'} · quoting {sum(m.quoting for m in open_)} market(s) · fills so far "
            f"{totals['fills']} · fills P&L {_usd(fills_pnl)} (open positions at the best bids) · estimated reward {_usd(est)} · "
            f"together {_usd(fills_pnl + est)}" + ("  — STOPPED: loss limit" if flags["halted"] else "")
            + ("  — KILL SWITCH" if flags["kill"] else ""))
        for m in open_:
            qs = " · ".join(f"{s.upper()} {o['left']:g} @ {o['px']}c" for s, o in m.orders.items() if o) or "no orders"
            print(f"           {m.t:34} {qs:28} · held YES {m.held['yes']:g} / NO {m.held['no']:g} · {_usd(m.pnl_c())} · "
                  f"share now YES {100 * m.share['yes']:.0f}% NO {100 * m.share['no']:.0f}% · est reward {_usd(m.reward_c)}"
                  + ("" if m.quoting else "  (not quoting: followed to the close)"))
        print(f"           order actions: {gw.n['creates']} creates, {gw.n['cancels']} cancels"
              + (f", {gw.n['rejects']} rejected" if live else " (built, not sent)"))

    def reconcile():
        """Live: Kalshi's net position per market vs ours; a gap (a fill we missed) is counted in the net limit."""
        d = api_get(sess, k, "/portfolio/positions", {"limit": 200})
        if d is None: return
        pos = {p.get("ticker"): field(p, "position") or 0 for p in d.get("market_positions", []) or []}
        for m in mk.values():
            if m.done: continue
            gap = pos.get(m.t, 0) - (m.held["yes"] - m.held["no"])
            if abs(gap - m.adj) >= 1:
                say(f"[account] {m.t}: Kalshi shows net {pos.get(m.t, 0):+g}, the bot counted {m.held['yes'] - m.held['no']:+g} "
                    f"— the difference ({gap:+g}) now counts in the net limit")
                m.adj = gap

    pick()
    last_pick, last_status, last_settle, last, last_rec = time.time(), time.time(), 0, time.time(), time.time()
    errors = 0
    try:
        while True:
            try:
                t_now = time.time(); dt_s = t_now - last; last = t_now
                if not flags["kill"] and os.path.exists(KILL_FILE):
                    flags["kill"] = True
                    say(f"!!! KILL SWITCH ({KILL_FILE} found): every order cancelled, nothing new. Delete the file and restart to resume. !!!")
                    for m in mk.values(): pull(m, "kill switch")
                if t_now - last_pick >= PROGRAMS_REFRESH_SEC and not flags["kill"]:
                    last_pick = t_now; pick()
                for m in list(mk.values()):
                    if m.done: continue
                    if m.close and now() >= m.close:
                        if m.orders["yes"] or m.orders["no"]: pull(m, "closed")
                        m.quoting = False
                        if not (m.held["yes"] or m.held["no"]):
                            m.done = True; continue
                        if t_now - last_settle >= SETTLE_RETRY_SEC:
                            settle(m)
                        continue
                    work(m, dt_s)
                if mk and t_now - last_settle >= SETTLE_RETRY_SEC: last_settle = t_now
                loss = sum(m.pnl_c() for m in mk.values() if not m.done) + totals["settled_pnl"]
                if not flags["halted"] and loss <= -MAX_LOSS_USD * 100:
                    flags["halted"] = True
                    for m in mk.values(): pull(m, "loss limit")
                    say(f"!!! LOSS LIMIT: fills are {_usd(loss)} (limit -${MAX_LOSS_USD}) — every order cancelled, no new quotes "
                        f"in this run; contracts held are followed to the close !!!")
                if live and t_now - last_rec >= RECONCILE_SEC:
                    last_rec = t_now; reconcile()
                if t_now - last_status >= STATUS_EVERY_SEC:
                    last_status = t_now; status()
            except Exception as e:
                errors += 1
                if errors <= 20 or errors % 100 == 0:
                    say(f"[error] {type(e).__name__}: {e} — the bot keeps running (error #{errors})")
            time.sleep(LOOP_SEC)
    except KeyboardInterrupt:
        for m in mk.values(): pull(m, "stopped")
        print("[stopped] every order cancelled. Contracts already held stay on Kalshi and settle there. Totals so far:")
        status()
    finally:
        for fh in (fills_fh, mkts_fh):
            try: fh.close()
            except Exception: pass
        gw.log.close()
