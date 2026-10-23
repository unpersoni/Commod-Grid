# LIP Maker V2 tests against a fake Kalshi and a fake price stream (nothing real is reached):
# (1) dry run: picking many markets with the per-state cap, quotes at the reference price, the queue simulation
#     (waiting behind the line, cancels ahead, trading through), the net limit, the stream-down pull, the stop;
# (2) live path: exact requests, fills from one read of resting orders, an executed order looked up, moving an order,
#     positions check, loss limit, everything cancelled at the stop; (3) switch checks.
import os, sys, time, threading, datetime as dt, io, contextlib, _thread, shutil, json, re, collections
from cryptography.hazmat.primitives.asymmetric import rsa
SP = os.path.dirname(os.path.abspath(__file__))
SRC = os.environ.get("LIPMM2_SRC", f"{SP}/lip_maker_v2.py")
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

def load(out, **over):
    shutil.rmtree(out, ignore_errors=True); os.makedirs(out, exist_ok=True)
    code = open(SRC).read().replace("/content/drive/MyDrive/", out + "/")
    g = {"__name__": "botmod"}; exec(compile(code, "lipmm2", "exec"), g)
    g.update(LOOP_SEC=0.1, STATUS_EVERY_SEC=1.5, LIVE_COUNTDOWN_SEC=0, REQUOTE_HOLD_SEC=0.3, FAST_MOVE_SEC=2, STABLE_SEC=0.5,
             COOL_OFF_SEC=1.0, FILL_CHECK_SEC=0.2, RECONCILE_SEC=0.3, FEED_LOST_SEC=0.5)
    g.update(over)
    g["load_key"] = lambda: KEY
    return g

class Resp:
    def __init__(s, code, js): s.status_code, s._js, s.text = code, js, json.dumps(js)
    def json(s): return s._js

def iso(d): return d.strftime("%Y-%m-%dT%H:%M:%SZ")

class FakeFeed:
    def __init__(s, K): s.K, s.ready, s.down_since, s.on_trade, s.tracked = K, threading.Event(), None, None, set(); s.ready.set()
    def levels(s, t):
        if not s.ready.is_set() or t not in s.tracked: return None
        with s.K.lock:
            y, n = dict(s.K.books[t][0]), dict(s.K.books[t][1])
            for o in s.K.orders.values():               # live: our own resting orders are in Kalshi's book too
                if o["ticker"] != t or o["status"] != "resting": continue
                p, q = round(float(o["price"]) * 100), float(o["remaining_count_fp"])
                if o["side"] == "bid": y[p] = y.get(p, 0) + q
                else: n[100 - p] = n.get(100 - p, 0) + q
            return {"yes": sorted(y.items(), reverse=True), "no": sorted(n.items(), reverse=True)}
    def track(s, ts): s.tracked = set(ts)
    def stop(s): pass
    def trade(s, t, yp, n, taker): s.on_trade(t, yp, n, taker); time.sleep(0.05)

class FakeKalshi:
    def __init__(s):
        n = dt.datetime.now(dt.timezone.utc)
        s.lock = threading.RLock()
        s.books, s.close, s.programs = {}, {}, []
        def add(t, yes, no, usd=150, hours=24, close_h=8):
            s.books[t] = (dict(yes), dict(no)); s.close[t] = n + dt.timedelta(hours=close_h)
            s.programs.append({"market_ticker": t, "period_reward": int(usd * 10000), "start_date": iso(n - dt.timedelta(hours=1)),
                               "end_date": iso(n + dt.timedelta(hours=hours - 1)), "target_size_fp": "1000.00", "discount_factor_bps": 5000})
        # 4 states x 3 strikes; WI has the thinnest books (pays us most), then NC, NY, LA
        for st, depth in (("WI", 200), ("NC", 300), ("NY", 400), ("LA", 500)):
            for i, (yb, nb) in enumerate(((38, 52), (30, 60), (20, 70))):
                add(f"KXAAAGASD{st}-26OCT22-4.{1500 + 5 * i}", [(yb, depth), (yb - 1, 200)], [(nb, depth), (nb - 1, 200)])
        add("KXAAAGASDMD-26OCT22-4.0450", [(88, 300)], [(2, 300)])                      # nearly decided
        add("KXAAAGASDOK-26OCT22-3.9400", [(33, 300)], [(57, 300)], close_h=0.8)       # closes too soon
        add("KXTEMPMIAH-26OCT2112-T81.99", [(50, 10)], [(40, 10)])                     # another series
        s.orders, s.positions, s.posts, s.deletes = {}, {}, [], []
    def api_get(s, sess, k, path, params=None):
        with s.lock:
            params = params or {}
            if path == "/incentive_programs": return {"incentive_programs": s.programs, "next_cursor": ""}
            if path == "/portfolio/balance": return {"balance": 50000, "balance_breakdown": [{"exchange_index": 0, "balance": "500.00"}]}
            if path == "/portfolio/positions": return {"market_positions": [{"ticker": t, "position_fp": f"{v:.2f}"} for t, v in s.positions.items()]}
            if path == "/portfolio/orders" and params.get("status") == "resting":
                return {"orders": [dict(o) for o in s.orders.values() if o["status"] == "resting"], "cursor": ""}
            if path.startswith("/portfolio/orders/"):
                o = s.orders.get(path.split("/")[-1]); return {"order": dict(o)} if o else None
            if path == "/markets" and "event_ticker" in params:
                ev = params["event_ticker"]
                return {"markets": [{"ticker": t, "status": "active", "close_time": iso(c)} for t, c in s.close.items() if t.startswith(ev + "-")]}
            m = re.match(r"/markets/([^/]+)$", path)
            if m: return {"market": {"ticker": m.group(1), "status": "active", "close_time": iso(s.close[m.group(1)])}}
            return None
    def post(s, sess, url, headers=None, json=None, timeout=None):
        with s.lock:
            s.posts.append(json)
            if url.endswith("/order_groups/create"): return Resp(201, {"order_group_id": "grp-1"})
            if "expiration_time" in json and not isinstance(json["expiration_time"], int):   # as Kalshi answered live
                return Resp(400, {"error": {"code": "bad_request", "details": "json: cannot unmarshal string into Go struct field .expiration_time of type int64"}})
            oid = f"oid-{len(s.posts)}"
            s.orders[oid] = {"order_id": oid, "ticker": json["ticker"], "side": json["side"], "price": json["price"], "status": "resting",
                             "initial_count_fp": json["count"], "remaining_count_fp": json["count"], "fill_count_fp": "0.00"}
            return Resp(201, {"order": {"order_id": oid}})
    def delete(s, sess, url, headers=None, params=None, timeout=None):
        with s.lock:
            oid = url.split("/")[-1]; s.deletes.append(oid)
            if oid in s.orders and s.orders[oid]["status"] == "resting": s.orders[oid]["status"] = "canceled"
            return Resp(200, {})
    def fill(s, oid, n):
        with s.lock:
            o = s.orders[oid]
            f = float(o["fill_count_fp"]) + n
            o["fill_count_fp"] = f"{f:.2f}"; o["remaining_count_fp"] = f"{float(o['initial_count_fp']) - f:.2f}"
            if float(o["remaining_count_fp"]) <= 0: o["status"] = "executed"
            s.positions[o["ticker"]] = s.positions.get(o["ticker"], 0) + (n if o["side"] == "bid" else -n)
    def resting(s, t, side):
        with s.lock: return [o for o, v in s.orders.items() if v["status"] == "resting" and v["ticker"] == t and v["side"] == side]

def run(g, script):
    buf = io.StringIO()
    def safe():
        try: script(buf)
        except Exception as e:
            import traceback; traceback.print_exc(file=sys.stderr)
            print(f"SCRIPT FAILED: {type(e).__name__}: {e}", file=sys.stderr); _thread.interrupt_main()
    threading.Thread(target=safe, daemon=True).start()
    with contextlib.redirect_stdout(buf):
        try: g["main"]()
        except KeyboardInterrupt: pass
    return buf.getvalue()

def wait_for(cond, t=8):
    end = time.time() + t
    while time.time() < end:
        if cond(): return True
        time.sleep(0.05)
    raise AssertionError("timed out")

WI = "KXAAAGASDWI-26OCT22-4.1500"
# ---------------- 1) DRY RUN ----------------
out = f"{SP}/run_lip2_dry"
g = load(out, MAX_MARKETS=6, MAX_PER_STATE=2, BUDGET_USD=150, ORDER_RENEW_MIN=0.04, PROGRAMS_REFRESH_SEC=1.0)
assert g["DRY_RUN"] is True and g["CONTRACTS"] == 10 and g["MAX_MARKETS"] == 6
K = FakeKalshi(); F = FakeFeed(K)
K.books[WI] = ({39: 150, 38: 200, 37: 200}, {52: 200, 51: 200})   # reference 38c (150 + 200 reach 1/5 of 1000); 200 ahead of us
for t_ in ("KXAAAGASDWI-26OCT22-4.1505", "KXAAAGASDWI-26OCT22-4.1510"):   # WI's other strikes: deeper (pay us less than NC)
    y_, n_ = K.books[t_]; K.books[t_] = ({p: (350 if q == 200 and p == max(y_) else q) for p, q in y_.items()},
                                         {p: (350 if q == 200 and p == max(n_) else q) for p, q in n_.items()})
g["api_get"] = K.api_get; g["make_feed"] = lambda k: F
sent = []
g["requests"].Session.post = lambda *a, **k: sent.append(a)
g["requests"].Session.delete = lambda *a, **k: sent.append(a)
def script1(buf):
    wait_for(lambda: "PICKED" in buf.getvalue())
    time.sleep(0.5)
    F.trade(WI, 38, 100, "no")             # 100 sold into the 38c YES bids: 200 were ahead of us -> 100 left ahead, no fill
    assert "FILL" not in buf.getvalue()
    with K.lock: K.books[WI][0][38] = 50   # 150 cancelled at 38c: 50 ahead of us now (the reference stays 38c: 150 + 50)
    time.sleep(0.4)
    F.trade(WI, 38, 54, "no")              # 50 ahead, then 4 to us
    time.sleep(0.3)
    F.trade(WI, 37, 30, "no")              # traded through 38c: the 6 left fill
    time.sleep(0.6)
    F.trade(WI, 37, 30, "no")              # the new YES order (another 10) -> held 20 YES: net limit
    time.sleep(0.6)
    NY0 = "KXAAAGASDNY-26OCT22-4.1500"              # a quote vanishing for a moment (cancel / re-post) is not a move
    with K.lock: saved = K.books[NY0]; K.books[NY0] = ({20: 400}, {70: 400})
    time.sleep(0.12)
    with K.lock: K.books[NY0] = saved
    time.sleep(2.5)
    NC0, NC1 = "KXAAAGASDNC-26OCT22-4.1500", "KXAAAGASDNC-26OCT22-4.1505"
    F.trade(NC0, 30, 0.001, "no")                  # a dust-sized print below our price: no fill logged
    with K.lock: s0 = K.books[NC0]; K.books[NC0] = ({}, s0[1])   # one side empty across one pick: kept
    time.sleep(1.2)
    with K.lock: K.books[NC0] = s0
    with K.lock: s1 = K.books[NC1]; K.books[NC1] = ({}, s1[1])   # empty across several picks: replaced
    wait_for(lambda: f"{NC1}: no longer among the best" in buf.getvalue())
    with K.lock: K.books[NC1] = s1
    F.ready.clear(); F.down_since = time.time()   # the price stream drops
    time.sleep(1.0)
    F.ready.set(); F.down_since = None
    time.sleep(1.0)
    _thread.interrupt_main()
txt = run(g, script1); print(txt[-4000:])
assert not sent, "dry run sent something"
picked = re.search(r"PICKED (\d+): (.*)", txt)
assert picked and picked.group(1) == "6", txt
names = picked.group(2)
assert names.count("KXAAAGASDWI") == 2 and names.count("KXAAAGASDNC") == 2 and names.count("KXAAAGASDNY") == 2
assert "LA" not in names and "MD" not in names and "OK" not in names and "TEMP" not in names
assert "quoting 6 in 3 states/series" in txt
orders_csv = open([f"{out}/lip_maker_v2_data/{f}" for f in os.listdir(f"{out}/lip_maker_v2_data")][0]).read()
assert re.search(rf"create,{WI},yes,38,10,", orders_csv) and re.search(rf"create,{WI},no,52,10,", orders_csv)
assert f"FILL  {WI}: bought 4 YES @ 38c (simulated, our turn in line)" in txt
assert f"FILL  {WI}: bought 6 YES @ 38c (simulated, traded through)" in txt
assert "held YES 20 / NO 0 · net +20" in txt
assert "[safety] KXAAAGASDNY-26OCT22-4.1500" not in txt                      # the flicker did not pull it
assert "FILL  KXAAAGASDNC-26OCT22-4.1500" not in txt                          # dust print: no fill
assert "KXAAAGASDNC-26OCT22-4.1500: no longer among the best" not in txt      # a one-pick gap in its book: kept
assert "KXAAAGASDNC-26OCT22-4.1505: no longer among the best" in txt          # a lasting gap: replaced
assert "by the end of today's quoting if this held" in txt
assert "price stream down" in txt and "price stream back — quoting again" in txt
assert "cancel (renewed (expiry))" in orders_csv.replace('"', "")            # orders renewed before their Kalshi expiry
assert "[stopped] every order cancelled" in txt and "estimated reward +$" in txt
print("DRY RUN CHECKS PASSED")

# ---------------- 2) LIVE path ----------------
out2 = f"{SP}/run_lip2_live"
g2 = load(out2, DRY_RUN=False, KEY_ID="k", MAX_MARKETS=2, MAX_PER_STATE=1, MAX_LOSS_USD=2)
K2 = FakeKalshi(); F2 = FakeFeed(K2)
K2.orders["old-1"] = {"order_id": "old-1", "ticker": "KXAAAGASDNY-26OCT22-4.1500", "side": "bid", "status": "resting",
                      "initial_count_fp": "5.00", "remaining_count_fp": "5.00", "fill_count_fp": "0.00"}
K2.orders["other-1"] = {"order_id": "other-1", "ticker": "KXBTC15M-26OCT221200-00", "side": "bid", "status": "resting",
                        "initial_count_fp": "5.00", "remaining_count_fp": "5.00", "fill_count_fp": "0.00"}
g2["api_get"] = K2.api_get; g2["make_feed"] = lambda k: F2
g2["requests"].Session.post = lambda self, url, **kw: K2.post(self, url, **kw)
g2["requests"].Session.delete = lambda self, url, **kw: K2.delete(self, url, **kw)
def script2(buf):
    wait_for(lambda: len(K2.resting(WI, "bid")) == 1 and len(K2.resting(WI, "ask")) == 1)
    K2.fill(K2.resting(WI, "bid")[0], 4)                    # partly filled, still resting
    time.sleep(0.6)
    K2.fill(K2.resting(WI, "ask")[0], 10)                   # the NO order fully filled: no longer resting -> looked up
    time.sleep(0.6)
    with K2.lock: K2.books[WI] = ({39: 200, 38: 200}, {52: 200, 51: 200})   # YES reference moves to 39c
    time.sleep(1.0)
    with K2.lock: K2.positions[WI] = -3                     # Kalshi: net -3 (the bot counted 4 YES - 10 NO = -6): a missed fill
    time.sleep(0.8)
    with K2.lock: K2.books[WI] = ({5: 300}, {90: 300})      # YES collapses: the 4 YES are marked at 5c, the NO at 90c
    time.sleep(0.5)
    with K2.lock: K2.books[WI] = ({8: 300}, {90: 300})
    time.sleep(1.5)
    # then YES soars: 4 YES + 4 NO are pairs (worth $1 each); the other 6 NO (bought at 52c) are marked at 3c -> about -$2.54
    with K2.lock: K2.books[WI] = ({95: 300}, {3: 300})
    time.sleep(1.5)
    _thread.interrupt_main()
txt2 = run(g2, script2); print(txt2[-3500:])
assert "LIVE: REAL ORDERS WITH REAL MONEY" in txt2
assert "cancelled leftover order old-1" in txt2 and "other-1" not in txt2
creates = [p for p in K2.posts if "ticker" in p]
assert all(c["post_only"] is True and c["order_group_id"] == "grp-1" and "expiration_time" in c for c in creates)
lim = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=31)
assert all(isinstance(c["expiration_time"], int) and time.time() < c["expiration_time"] and c["expiration_time"] <= lim.timestamp() for c in creates)   # 30-min life, Unix s
assert "Kalshi rejected expiration_time" not in txt2
assert f"FILL  {WI}: bought 4 YES @ 38c (Kalshi)" in txt2
assert f"FILL  {WI}: bought 10 NO @ 52c (Kalshi)" in txt2
assert any(c["ticker"] == WI and c["side"] == "bid" and c["price"] == "0.3900" for c in creates)
assert "Kalshi shows net -3, the bot counted -6" in txt2
assert "price moved 4c+" in txt2 and "LOSS LIMIT: fills are -$2.54" in txt2
assert "[stopped] every order cancelled" in txt2
assert not [o for o, v in K2.orders.items() if v["status"] == "resting" and v["ticker"].startswith("KXAAAGASD")]
print("LIVE PATH CHECKS PASSED")

# ---------------- 3) switch checks ----------------
for over, msg in (({"DRY_RUN": "False"}, "DRY_RUN must be True or False"), ({"SERIES": "KXAAAGASD"}, "SERIES must be a list"),
                  ({"CONTRACTS": 0}, "CONTRACTS must be"), ({"MAX_MARKETS": 500}, "MAX_MARKETS must be"),
                  ({"MAX_PER_STATE": 0}, "MAX_PER_STATE must be"), ({"BUDGET_USD": 0}, "BUDGET_USD must be"),
                  ({"MAX_NET": 5}, "MAX_NET (5) must be at least CONTRACTS"), ({"DRY_RUN": False}, "live trading needs KEY_ID")):
    g3 = load(f"{SP}/run_lip2_guard", **over)
    try:
        with contextlib.redirect_stdout(io.StringIO()): g3["main"]()
        raise AssertionError(f"did not stop: {over}")
    except SystemExit as e: assert msg in str(e), (over, e)
g4 = {"__name__": "x"}; exec(compile(open(SRC).read(), "lipmm2", "exec"), g4)
assert g4["DRY_RUN"] is True and g4["MAX_MARKETS"] == 15 and g4["BUDGET_USD"] == 150
fd = g4["StreamFeed"](KEY); fd.tracked = {"T"}; fd.ready.set()
fd._on_message(json.dumps({"type": "orderbook_snapshot", "seq": 1, "msg": {"market_ticker": "T",
               "yes_dollars_fp": [["0.3800", "300.00"], ["0.3700", "200.00"]], "no_dollars_fp": [["0.5200", "100.00"]]}}))
fd._on_message(json.dumps({"type": "orderbook_delta", "seq": 2, "msg": {"market_ticker": "T", "side": "yes",
               "price_dollars": "0.3800", "delta_fp": "-100.00"}}))
assert fd.levels("T") == {"yes": [(38, 200.0), (37, 200.0)], "no": [(52, 100.0)]}
got = []; fd.on_trade = lambda *a: got.append(a)
fd._on_message(json.dumps({"type": "trade", "msg": {"market_ticker": "T", "yes_price_dollars": "0.3800", "count_fp": "5.00", "taker_side": "no"}}))
assert got == [("T", 38, 5.0, "no")]
print("SWITCH + FEED CHECKS PASSED")
for d in ("run_lip2_dry", "run_lip2_live", "run_lip2_guard"): shutil.rmtree(f"{SP}/{d}", ignore_errors=True)
print("ALL CHECKS PASSED")
