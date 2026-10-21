# LIP Maker V1 tests against a fake Kalshi (nothing real is reached): (1) dry run: market picking, quotes at the
# reference price, simulated fills, the net limit, the fast-move pull, the stop; (2) live path: exact requests,
# fills from Kalshi's order state, moving an order, an unconfirmed cancel, positions check, loss limit; (3) switches.
import os, sys, time, threading, datetime as dt, io, contextlib, _thread, shutil, json, re, collections
from cryptography.hazmat.primitives.asymmetric import rsa
SP = os.path.dirname(os.path.abspath(__file__))
SRC = os.environ.get("LIPMM_SRC", f"{SP}/lip_maker_v1.py")
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

def load(out, **over):
    shutil.rmtree(out, ignore_errors=True); os.makedirs(out, exist_ok=True)
    code = open(SRC).read().replace("/content/drive/MyDrive/", out + "/")
    g = {"__name__": "botmod"}; exec(compile(code, "lipmm", "exec"), g)
    g.update(LOOP_SEC=0.1, STATUS_EVERY_SEC=1.5, LIVE_COUNTDOWN_SEC=0, REQUOTE_HOLD_SEC=0.3, FAST_MOVE_SEC=2, COOL_OFF_SEC=1.0)
    g.update(over)
    g["load_key"] = lambda: KEY
    return g

class Resp:
    def __init__(s, code, js): s.status_code, s._js, s.text = code, js, json.dumps(js)
    def json(s): return s._js

def iso(d): return d.strftime("%Y-%m-%dT%H:%M:%SZ")

class FakeKalshi:
    def __init__(s):
        n = dt.datetime.now(dt.timezone.utc)
        s.close = {"KXAAAGASDWI-26OCT22-4.1500": n + dt.timedelta(hours=8), "KXAAAGASDNC-26OCT22-3.5500": n + dt.timedelta(hours=8),
                   "KXAAAGASDNY-26OCT22-4.4000": n + dt.timedelta(hours=8), "KXAAAGASDMD-26OCT22-4.0450": n + dt.timedelta(hours=8),
                   "KXAAAGASDOK-26OCT22-3.9400": n + dt.timedelta(minutes=50),            # too close to its close
                   "KXTEMPMIAH-26OCT2112-T81.99": n + dt.timedelta(hours=1)}               # another series: never picked
        prog = lambda t, usd, h: {"market_ticker": t, "period_reward": int(usd * 10000), "start_date": iso(n - dt.timedelta(hours=1)),
                                  "end_date": iso(n + dt.timedelta(hours=h - 1)), "target_size_fp": "1000.00", "discount_factor_bps": 5000}
        s.programs = [prog("KXAAAGASDWI-26OCT22-4.1500", 150, 24), prog("KXAAAGASDNC-26OCT22-3.5500", 150, 24),
                      prog("KXAAAGASDNY-26OCT22-4.4000", 75, 24), prog("KXAAAGASDMD-26OCT22-4.0450", 150, 24),
                      prog("KXAAAGASDOK-26OCT22-3.9400", 150, 24), prog("KXTEMPMIAH-26OCT2112-T81.99", 100, 1)]
        # books (YES bids, NO bids) in cents; WI and NC: 38/52 with 300 + 100 at the best; MD nearly decided (88c / 2c)
        s.books = {"KXAAAGASDWI-26OCT22-4.1500": ([(38, 300), (37, 200)], [(52, 300), (51, 200)]),
                   "KXAAAGASDNC-26OCT22-3.5500": ([(56, 300), (55, 200)], [(34, 300), (33, 200)]),
                   "KXAAAGASDNY-26OCT22-4.4000": ([(78, 300)], [(12, 300)]),
                   "KXAAAGASDMD-26OCT22-4.0450": ([(88, 300)], [(2, 300)]),
                   "KXAAAGASDOK-26OCT22-3.9400": ([(33, 300)], [(57, 300)]),
                   "KXTEMPMIAH-26OCT2112-T81.99": ([(86, 300)], [(10, 300)])}
        s.trades = collections.defaultdict(list)
        s.orders = {}                     # live: oid -> order dict
        s.positions = {}
        s.posts, s.deletes = [], []
        s.refuse_cancel = set()
        s.lock = threading.Lock()
    def api_get(s, sess, k, path, params=None):
        with s.lock:
            if path == "/incentive_programs": return {"incentive_programs": s.programs, "next_cursor": ""}
            if path == "/portfolio/balance": return {"balance": 50000, "balance_breakdown": [{"exchange_index": 0, "balance": "500.00"}]}
            if path == "/portfolio/positions": return {"market_positions": [{"ticker": t, "position": v} for t, v in s.positions.items()]}
            if path == "/portfolio/orders" and (params or {}).get("status") == "resting":
                return {"orders": [o for o in s.orders.values() if o["status"] == "resting"]}
            if path.startswith("/portfolio/orders/"):
                o = s.orders.get(path.split("/")[-1]); return {"order": dict(o)} if o else None
            if path == "/markets/trades":
                return {"trades": [t for t in s.trades[params["ticker"]]]}
            m = re.match(r"/markets/([^/]+)(/orderbook)?$", path)
            if m:
                t = m.group(1)
                if m.group(2):
                    y, n = s.books[t]
                    return {"orderbook_fp": {"yes_dollars": [[f"{p / 100:.4f}", f"{q:.2f}"] for p, q in sorted(y)],
                                             "no_dollars": [[f"{p / 100:.4f}", f"{q:.2f}"] for p, q in sorted(n)]}}
                return {"market": {"ticker": t, "status": "active", "close_time": iso(s.close[t])}}
            return None
    def post(s, sess, url, headers=None, json=None, timeout=None):
        with s.lock:
            s.posts.append(json)
            if url.endswith("/order_groups/create"): return Resp(201, {"order_group_id": "grp-1"})
            oid = f"oid-{len(s.posts)}"
            s.orders[oid] = {"order_id": oid, "ticker": json["ticker"], "client_order_id": json["client_order_id"], "status": "resting",
                             "side": json["side"],
                             "initial_count_fp": json["count"], "remaining_count_fp": json["count"], "fill_count_fp": "0.00"}
            return Resp(201, {"order": {"order_id": oid}})
    def delete(s, sess, url, headers=None, params=None, timeout=None):
        with s.lock:
            oid = url.split("/")[-1]; s.deletes.append(oid)
            if oid in s.refuse_cancel: return Resp(500, {"error": "busy"})
            if oid in s.orders: s.orders[oid]["status"] = "canceled"
            return Resp(200, {})
    def fill(s, oid, n):
        with s.lock:
            o = s.orders[oid]
            f = float(o["fill_count_fp"]) + n
            o["fill_count_fp"] = f"{f:.2f}"; o["remaining_count_fp"] = f"{float(o['initial_count_fp']) - f:.2f}"
            if float(o["remaining_count_fp"]) <= 0: o["status"] = "executed"

def run(g, script):
    buf = io.StringIO()
    def safe():
        try: script()
        except Exception as e:
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

# ---------------- 1) DRY RUN ----------------
out = f"{SP}/run_lip_dry"
g = load(out)                                   # default switches: gas, 10 per side, 3 markets, $60, net 20, loss $15
assert g["DRY_RUN"] is True and g["SERIES"] == ["KXAAAGASD"] and g["CONTRACTS"] == 10 and g["MAX_NET"] == 20
K = FakeKalshi(); g["api_get"] = K.api_get
sent = []
g["requests"].Session.post = lambda *a, **k: sent.append(a)
g["requests"].Session.delete = lambda *a, **k: sent.append(a)
WI, NC, NY = "KXAAAGASDWI-26OCT22-4.1500", "KXAAAGASDNC-26OCT22-3.5500", "KXAAAGASDNY-26OCT22-4.4000"
def script1():
    wait_for(lambda: "PICKED" in buf1.getvalue())
    time.sleep(0.6)
    # a seller hits the YES bids at 38c: our 10 YES in WI fill (simulated, worst case: ahead of everyone)
    with K.lock: K.trades[WI].append({"trade_id": "t1", "yes_price": 38, "count": 25, "taker_side": "no",
                                      "created_time": iso(dt.datetime.now(dt.timezone.utc))})
    time.sleep(0.6)
    with K.lock: K.trades[WI].append({"trade_id": "t2", "yes_price_dollars": "0.3700", "count_fp": "40.00", "taker_side": "no",
                                      "created_time": iso(dt.datetime.now(dt.timezone.utc))})
    time.sleep(0.6)
    # NC's price jumps 6c: both sides pulled, then back after the cool-off
    with K.lock: K.books[NC] = ([(62, 300), (61, 200)], [(28, 300), (27, 200)])
    time.sleep(2.5)
    _thread.interrupt_main()
buf1 = io.StringIO()
threading.Thread(target=script1, daemon=True).start()
with contextlib.redirect_stdout(buf1):
    try: g["main"]()
    except KeyboardInterrupt: pass
txt = buf1.getvalue(); print(txt[-3500:])
assert not sent, "dry run sent something"
assert "LIP Maker V1 — DRY RUN" in txt
assert f"PICKED {WI}" in txt and f"PICKED {NC}" in txt and f"PICKED {NY}" in txt
assert "KXAAAGASDMD" not in txt and "KXAAAGASDOK" not in txt and "KXTEMPMIAH" not in txt   # decided / closing / other series
orders = open([f"{out}/lip_maker_v1_data/{f}" for f in os.listdir(f"{out}/lip_maker_v1_data")][0]).read()
# reference price: 1/5 of target 1000 = 200 contracts -> the best level (300) -> 38c YES, 52c NO in WI
assert re.search(rf"create,{WI},yes,38,10,", orders) and re.search(rf"create,{WI},no,52,10,", orders), orders[:1500]
import csv as _csv
reqs = [json.loads(r[-1]) for r in _csv.reader(io.StringIO(orders)) if r and r[2] == "create"]
assert all(r["post_only"] is True for r in reqs)
assert any(r["ticker"] == WI and r["side"] == "ask" and r["price"] == "0.4800" for r in reqs)   # NO 52c = YES ask 48c
assert f"FILL  {WI}: bought 10 YES @ 38c (simulated)" in txt
assert "held YES 20 / NO 0 · net +20" in txt                   # the 2nd trade fills the next 10: net limit reached
assert re.search(rf"cancel \(pulled\),{WI},yes", orders) or "YES 10 @ 38c" not in txt.split("[stopped]")[-1]
assert txt.count(f"[safety] {NC}: price moved 4c+") == 1
assert "estimated reward +$" in txt and "share now YES" in txt
assert "[stopped] every order cancelled" in txt
fills = open(f"{out}/lip_maker_v1_fills.csv").read()
assert fills.count("simulated") == 2, fills
print("DRY RUN CHECKS PASSED")

# ---------------- 2) LIVE path against the fake Kalshi ----------------
out2 = f"{SP}/run_lip_live"
g2 = load(out2, DRY_RUN=False, KEY_ID="k", MAX_MARKETS=1, CONTRACTS=10, MAX_LOSS_USD=3, RECONCILE_SEC=0.3)
K2 = FakeKalshi(); g2["api_get"] = K2.api_get
K2.orders["old-1"] = {"order_id": "old-1", "ticker": "KXAAAGASDNY-26OCT22-4.4000", "status": "resting",
                      "initial_count_fp": "5.00", "remaining_count_fp": "5.00", "fill_count_fp": "0.00"}
K2.orders["other-1"] = {"order_id": "other-1", "ticker": "KXBTC15M-26OCT221200-00", "status": "resting",
                        "initial_count_fp": "5.00", "remaining_count_fp": "5.00", "fill_count_fp": "0.00"}
g2["requests"].Session.post = lambda self, url, **kw: K2.post(self, url, **kw)
g2["requests"].Session.delete = lambda self, url, **kw: K2.delete(self, url, **kw)
def yes_oid():
    with K2.lock: return [o for o, v in K2.orders.items() if v["status"] == "resting" and v.get("side") == "bid" and v["ticker"] == WI]
def script2():
    wait_for(lambda: len([p for p in K2.posts if "ticker" in p]) >= 2)
    time.sleep(0.5)
    oy = yes_oid()[0]
    K2.fill(oy, 4)                                       # Kalshi fills 4 of our 10 YES at 38c
    time.sleep(0.6)
    with K2.lock: K2.books[WI] = ([(39, 300), (38, 200)], [(52, 300), (51, 200)])     # YES reference moves to 39c
    time.sleep(1.2)
    oy2 = yes_oid()[0]
    with K2.lock: K2.refuse_cancel.add(oy2)              # Kalshi does not answer the next cancel of it
    with K2.lock: K2.books[WI] = ([(40, 300), (39, 200)], [(52, 300), (51, 200)])
    time.sleep(1.0)
    with K2.lock: K2.refuse_cancel.discard(oy2)
    time.sleep(1.0)
    with K2.lock: K2.positions[WI] = 9                   # Kalshi shows 9 YES, the bot counted 4
    time.sleep(0.5)
    # the price falls: YES bids at 10c -> the 4 held YES are marked at 10c -> -$1.12 ... then 0c: below -$3? (no: 4 x 38c)
    with K2.lock: K2.books[WI] = ([(5, 300)], [(90, 300)])
    oy3 = yes_oid()
    if oy3: K2.fill(oy3[0], 6)
    time.sleep(1.5)
    _thread.interrupt_main()
g2["PROGRAMS_REFRESH_SEC"] = 600
txt2 = run(g2, script2); print(txt2[-3500:])
assert "LIVE: REAL ORDERS WITH REAL MONEY" in txt2
assert "cancelled leftover order old-1" in txt2 and "other-1" not in txt2           # only the chosen series is touched
assert "order group" in txt2 and "grp-1" in txt2
creates = [p for p in K2.posts if "ticker" in p]
assert creates[0]["ticker"] == WI and {c["side"] for c in creates[:2]} == {"bid", "ask"}
assert all(c["post_only"] is True and c["order_group_id"] == "grp-1" and "expiration_time" in c for c in creates)
assert f"FILL  {WI}: bought 4 YES @ 38c (Kalshi)" in txt2
assert any(c["side"] == "bid" and c["price"] == "0.3900" and c["count"] == "10.00" for c in creates)   # moved to 39c
assert "cancel NOT confirmed by Kalshi" in txt2
assert "Kalshi shows net +9, the bot counted +4" in txt2
assert "LOSS LIMIT" in txt2
assert "[stopped] every order cancelled" in txt2
with K2.lock: assert not [o for o, v in K2.orders.items() if v["status"] == "resting" and v["ticker"].startswith("KXAAAGASD")]
print("LIVE PATH CHECKS PASSED")

# ---------------- 3) switch checks ----------------
for over, msg in (({"DRY_RUN": "False"}, "DRY_RUN must be True or False"), ({"SERIES": "KXAAAGASD"}, "SERIES must be a list"),
                  ({"CONTRACTS": 0}, "CONTRACTS must be"), ({"MAX_MARKETS": 50}, "MAX_MARKETS must be"),
                  ({"BUDGET_USD": 0}, "BUDGET_USD must be"), ({"MAX_NET": 5}, "MAX_NET (5) must be at least CONTRACTS"),
                  ({"DRY_RUN": False}, "live trading needs KEY_ID")):
    g3 = load(f"{SP}/run_lip_guard", **over)
    try:
        with contextlib.redirect_stdout(io.StringIO()): g3["main"]()
        raise AssertionError(f"did not stop: {over}")
    except SystemExit as e: assert msg in str(e), (over, e)
g4 = {"__name__": "x"}; exec(compile(open(SRC).read(), "lipmm", "exec"), g4)
assert g4["DRY_RUN"] is True                       # no SWITCHES cell: dry run
lv = g4["book_levels"]({"orderbook": {"yes": [[37, 200], [38, 300]], "no": [[52, 100]]}})
assert lv == {"yes": [(38, 300.0), (37, 200.0)], "no": [(52, 100.0)]}
assert g4["reference"](lv["yes"], 1000) == 38 and g4["reference"](lv["no"], 1000) is None
assert abs(g4["score"](lv["yes"], 38, 0.5) - 400) < 1e-9
print("SWITCH CHECKS PASSED")
for d in ("run_lip_dry", "run_lip_live", "run_lip_guard"): shutil.rmtree(f"{SP}/{d}", ignore_errors=True)
print("ALL CHECKS PASSED")
