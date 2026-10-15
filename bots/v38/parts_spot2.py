
# ---------------- spot prices (logging only: if a feed is down, its columns are blank) ----------------
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

# Pyth (what Kalshi settles the commodity markets on). Pyth has many similar feeds
# (spot, futures months, indexes), so the bot picks, for each commodity, the feed whose
# live price is closest to the strike of Kalshi's current market (the strike is set at
# the market's opening price). It re-checks every hour (futures roll) and prints its
# choice. Paste an ID into PYTH_IDS to force a feed.
PYTH_URL = "https://hermes.pyth.network"
PYTH_POLL_SEC = 2
PYTH_RESELECT_SEC = 3600
PYTH_MAX_DIST_PCT = 1.0     # a feed must be within 1% of Kalshi's strike to be used
PYTH_IDS = {}               # e.g. {"GOLD": "<64-hex feed id>"}
PYTH_WORDS = {"GOLD": ["XAU", "GOLD"], "SILVER": ["XAG", "SILVER"],
              "PLATINUM": ["XPT", "PLATINUM"], "PALLADIUM": ["XPD", "PALLADIUM"],
              "COPPER": ["XCU", "COPPER"], "WTI": ["WTI", "CRUDE", "USOIL"]}
PYTH_EXCLUDE = {"WTI": ["BRENT", "UKOIL"]}
STRIKES = {}                # asset -> strike of its current Kalshi market (filled by main)

def _fid(x): return str(x).lower().removeprefix("0x")

def pyth_candidates(feeds, a):
    """Commodity/metal USD feeds whose symbol or description names asset `a` (no stocks)."""
    out = []
    for f in feeds:
        at = f.get("attributes") or {}
        if str(at.get("asset_type", "")).lower() not in ("metal", "commodities"): continue
        if at.get("quote_currency", "USD") != "USD": continue
        txt = (str(at.get("symbol", "")) + " " + str(at.get("description", ""))).upper()
        if any(w in txt for w in PYTH_WORDS.get(a, [])) and not any(w in txt for w in PYTH_EXCLUDE.get(a, [])):
            out.append(f)
    return out

def pyth_choose(cands, prices, strike):
    """(feed, dist_pct) of the candidate whose price is closest to the strike, or (None, ranked list)."""
    ranked = sorted(((abs(prices[_fid(f["id"])] / strike - 1) * 100, f) for f in cands
                     if prices.get(_fid(f["id"]))), key=lambda x: x[0])
    if ranked and ranked[0][0] <= PYTH_MAX_DIST_PCT: return ranked[0][1], ranked[0][0]
    return None, ranked

def _sym(f): return (f.get("attributes") or {}).get("symbol")

class PythFeed(_History):
    def __init__(self, assets):
        super().__init__(assets)
        self.ids = {}            # feed id (lowercase, no 0x) -> asset
        self.chosen = {}         # asset -> symbol
        self.sess = requests.Session()

    def stop(self): self.stop_flag.set()

    def _get_latest(self, ids):
        """{id: price} for fresh prices. Asks for all ids at once; if Pyth rejects the
        batch (one bad id fails it), asks one id at a time."""
        out = {}
        def one(batch):
            r = self.sess.get(f"{PYTH_URL}/v2/updates/price/latest", timeout=10,
                              params=[("ids[]", i) for i in batch] + [("parsed", "true")])
            if r.status_code != 200: raise RuntimeError(f"HTTP {r.status_code}: {r.text[:120]}")
            for q in r.json().get("parsed") or []:
                pr = q.get("price") or {}
                try:
                    px = int(pr["price"]) * 10 ** int(pr["expo"])
                    age = time.time() - int(pr.get("publish_time", time.time()))
                except (KeyError, TypeError, ValueError): continue
                if px > 0 and age <= 60: out[_fid(q.get("id"))] = px     # stale (market closed): skip
        try: one(list(ids))
        except Exception as e:
            self.last_err = str(e)
            for i in ids:
                try: one([i])
                except Exception: pass
        return out

    def _select(self):
        feeds = self.sess.get(f"{PYTH_URL}/v2/price_feeds", timeout=20).json()
        cands = {a: pyth_candidates(feeds, a) for a in self.hist}
        all_ids = list({_fid(f["id"]) for c in cands.values() for f in c})
        prices = {}
        for i in range(0, len(all_ids), 20):
            prices.update(self._get_latest(all_ids[i:i + 20]))
        ids = {}
        for a in self.hist:
            if PYTH_IDS.get(a):
                ids[_fid(PYTH_IDS[a])] = a
                if self.chosen.get(a) != "PYTH_IDS": print(f"[pyth] {a}: id from PYTH_IDS")
                self.chosen[a] = "PYTH_IDS"; continue
            k = STRIKES.get(a)
            if not k:
                if a not in self.chosen: print(f"[pyth] {a}: no Kalshi strike yet — will retry")
                continue
            f, d = pyth_choose(cands[a], prices, k)
            if f is None:
                near = ", ".join(f"{_sym(g)} {dd:.2f}% off" for dd, g in d[:3]) or "no live candidates"
                if self.chosen.get(a) != "":
                    print(f"[pyth] {a}: nothing within {PYTH_MAX_DIST_PCT}% of strike {k:g} "
                          f"({near}) — its spot columns will be blank")
                self.chosen[a] = ""; continue
            ids[_fid(f["id"])] = a
            if self.chosen.get(a) != _sym(f):
                print(f"[pyth] {a}: {_sym(f)} = {prices[_fid(f['id'])]:g} vs Kalshi strike {k:g} ({d:.2f}% off)")
            self.chosen[a] = _sym(f)
        self.ids = ids

    def _run(self):
        last_sel, fails = 0, 0
        while not self.stop_flag.is_set():
            missing = [a for a in self.hist if a not in self.ids.values()]
            due = time.time() - last_sel >= (PYTH_RESELECT_SEC if not missing else 300)
            if due:
                try:
                    self._select(); last_sel = time.time()
                except Exception as e:
                    print(f"[pyth] feed lookup failed ({e}) — retrying in 5 min"); last_sel = time.time()
            if self.ids:
                got = self._get_latest(list(self.ids))
                for i, px in got.items():
                    if i in self.ids: self._add(self.ids[i], px)
                if got: fails = 0
                else:
                    fails += 1
                    if fails in (1, 10) or fails % 300 == 0:
                        print(f"[pyth] no prices x{fails} ({getattr(self, 'last_err', 'all stale or closed')})")
            self.stop_flag.wait(PYTH_POLL_SEC)

class Spots:
    """Coinbase for crypto, Pyth for commodities, one interface."""
    def __init__(self, commodities):
        self.cb = SpotFeed()
        self.py = PythFeed(commodities)
    def start(self): self.cb.start(); self.py.start()
    def stop(self): self.cb.stop(); self.py.stop()
    def _f(self, a): return self.cb if a in SPOT_ASSET_MAP else self.py
    def price(self, a): return self._f(a).price(a)
    def change_pct(self, a, secs): return self._f(a).change_pct(a, secs)

def make_spot():
    s = Spots([asset(x) for x in SERIES if asset(x) not in SPOT_ASSET_MAP]); s.start(); return s
