# MM15Min V2 tests: (1) dry run end to end, (2) demo path with a fake Kalshi, (3) mode guards.
import os, sys, time, threading, datetime as dt, csv, io, contextlib, _thread, glob, shutil, json, gzip
from cryptography.hazmat.primitives.asymmetric import rsa
SP = os.path.dirname(os.path.abspath(__file__))
SRC = os.environ.get("MM2_SRC", f"{SP}/mm15min_v2.py")
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)      # a throwaway key: real signing, no account

def load(out, **over):
    shutil.rmtree(out, ignore_errors=True); os.makedirs(out, exist_ok=True)
    code = open(SRC).read().replace("/content/drive/MyDrive/", out + "/")
    g = {"__name__": "botmod"}; exec(compile(code, "mm15v2", "exec"), g)
    g.update(MARKETS_REFRESH_SEC=0.3, LOOP_SEC=0.1, SETTLE_RETRY_SEC=0.2, STATUS_EVERY_SEC=2, LATENCY_PROBE_SEC=0.5)
    g.update(over)
    return g

class Book:
    def __init__(s): s.books = {}; s.version = {}; s.ready = threading.Event(); s.ready.set()
    def level_qty(s, t, side, p):
        b = s.books.get(t)
        return None if b is None else b[side].get(p, 0.0)
    def track(s, ts): pass
    def stop(s): pass
class Feed:
    def __init__(s): s.p = {}; s.on_tick = s.on_trade = s.on_fill = None; s.ready = threading.Event(); s.ready.set()
    def get(s, t): return s.p.get(t)
    def track(s, ts): pass
    def stop(s): pass
class Spot:
    def price(s, a): return None
    def stop(s): pass

def wire(g, M, secs, result, api_calls):
    REAL = g["now"]; clock = {"off": 0.0}
    g["now"] = lambda: REAL() + dt.timedelta(seconds=clock["off"])
    T0 = REAL()
    close = {t: T0 + dt.timedelta(seconds=v) for t, v in secs.items()}
    book, feed = Book(), Feed()
    g["make_spot"] = lambda: Spot(); g["make_book"] = lambda k: book
    g["make_feed"] = lambda k: feed; g["make_private_feed"] = lambda k: feed
    g["load_key"] = lambda: KEY
    g["fetch_open"] = lambda sess, k: [{"ticker": t, "close_time": close[t].isoformat()} for t in M.values()]
    g["fetch_market"] = lambda sess, k, t: {"result": result[t] if (close[t] - g["now"]()).total_seconds() < 0 else ""}
    def api_get(sess, k, path, params=None):
        api_calls.append(path)
        if path == "/exchange/status": return {"trading_active": True}
        if path == "/portfolio/balance": return {"balance": 123456, "balance_breakdown": [{"exchange_index": 0, "balance": "1000.00"}, {"exchange_index": 2, "balance": "234.56"}]}
        if path == "/portfolio/orders": return {"orders": [{"order_id": "old-1"}]}
        return None
    g["api_get"] = api_get
    return clock, close, book, feed

def run_bot(g, script):
    buf = io.StringIO()
    threading.Thread(target=script, daemon=True).start()
    with contextlib.redirect_stdout(buf):
        try: g["main"]()
        except KeyboardInterrupt: pass
    return buf.getvalue()

def orders_rows(out, name):
    rows = []
    for f in sorted(glob.glob(f"{out}/{name}/orders_*")):
        fh = gzip.open(f, "rt") if f.endswith(".gz") else open(f)
        rows += list(csv.DictReader(fh))
    return rows

# ---------------- 1) DRY RUN ----------------
out = f"{SP}/run_mm2_dry"
g = load(out, MAX_WRITES_PER_SEC=4, MAX_OPEN_ORDERS=7, DAILY_LOSS_LIMIT_USD=1)
M = {"G": "KXGOLD15M-26OCT011215-15", "B": "KXBTC15M-26OCT011215-15", "E": "KXETH15M-26OCT011215-15", "W": "KXWTI15M-26OCT011215-15"}
secs = {M["G"]: 800, M["B"]: 700, M["E"]: 600, M["W"]: 650}
result = {M["G"]: "yes", M["B"]: "no", M["E"]: "yes", M["W"]: "no"}
api = []
clock, close, book, feed = wire(g, M, secs, result, api)
sent = []
g["requests"].Session.post = lambda *a, **k: sent.append(("POST", a, k))      # nothing may be sent in a dry run
g["requests"].Session.delete = lambda *a, **k: sent.append(("DELETE", a, k))
def tick(t, yb, ya): feed.p[t] = (yb, ya); feed.on_tick(t, yb, ya); time.sleep(0.05)
def trade(t, yp, n, side): feed.on_trade(t, yp, n, side); time.sleep(0.02)
def script1():
    while feed.on_tick is None: time.sleep(0.05)
    time.sleep(0.8)
    book.books[M["G"]] = {"yes": {88: 50.0, 89: 5.0}, "no": {11: 30.0, 10: 9.0}}
    tick(M["G"], 88, 89)          # fav YES @88, mm YES @88, mm NO @11 -> 3 creates through the order path
    time.sleep(1.2)               # requests built (rate limit 4/s) -> orders join the line
    trade(M["G"], 88, 30, "no"); trade(M["G"], 88, 25, "no")      # 50 ahead used up, 5 fill each (fav + mm YES)
    tick(M["G"], 89, 90)          # someone bids 89: the half-filled YES orders stay; NO ask is now 11 = mm NO bid -> filled
    book.books[M["G"]]["no"][12] = 7.0
    feed.p[M["G"]] = (87, 88); feed.on_tick(M["G"], 87, 88); time.sleep(0.3)   # NO best bid now 12 > 11: mm NO cancelled + re-placed
    # ETH: favourite NO @95 (fav + mm) and mm YES @4, then a taker sweep fills the NO bids -> ETH settles YES: a loss
    book.books[M["E"]] = {"yes": {4: 10.0}, "no": {95: 70.0}}
    tick(M["E"], 4, 5); time.sleep(1.5)
    trade(M["E"], 5, 80, "yes")   # takers bought YES at 5c = sold NO at 95c: 70 ahead, then 10 for us (both strategies)
    # burst: BTC + WTI at once -> the open-order cap (7) blocks some
    book.books[M["B"]] = {"yes": {20: 500.0}, "no": {78: 100.0}}
    book.books[M["W"]] = {"yes": {50: 10.0}, "no": {49: 10.0}}
    tick(M["B"], 20, 22); tick(M["W"], 50, 51)
    time.sleep(3.0)
    open(f"{out}/STOP_MM15MIN.txt", "w").write("stop")        # kill switch
    time.sleep(6.0)
    clock["off"] = 820            # all closed -> settle
    time.sleep(3.0)
    _thread.interrupt_main()
txt = run_bot(g, script1); print(txt[-4000:])
rows = orders_rows(out, "mm15min_v2_data")
creates = [r for r in rows if r["action"] == "create"]; cancels = [r for r in rows if r["action"].startswith("cancel")]
print(len(creates), "creates,", len(cancels), "cancels")
assert not sent, f"dry run SENT something: {sent[:2]}"
assert "/exchange/status" in api and "/portfolio/balance" in api and "/portfolio/orders" in api
assert "[account] balance $1,234.56 (shard 0: $1,000.00; shard 2: $234.56)" in txt and "resting orders on the account now: 1" in txt
assert "dry run: would create an order group (limit 300 contracts / 15 s) and cancel the 1 resting order(s) above" in txt
c0 = [json.loads(r["request"]) for r in creates if r["ticker"] == M["G"]]
yes88 = [b for b in c0 if b["side"] == "bid" and b["price"] == "0.8800"]
no11 = [b for b in c0 if b["side"] == "ask" and b["price"] == "0.8900"]                 # buy NO @11 = YES ask @89
assert len(yes88) == 2 and len(no11) == 1, c0
b = yes88[0]
assert b["count"] == "10.00" and b["time_in_force"] == "good_till_canceled" and b["post_only"] is True
assert b["self_trade_prevention_type"] == "taker_at_cross" and b["ticker"] == M["G"] and len(b["client_order_id"]) == 36
exp = (close[M["G"]] - dt.timedelta(seconds=25)).strftime("%Y-%m-%dT%H:%M:%SZ")
assert b["expiration_time"] == exp, (b["expiration_time"], exp)
assert all(r["result"] == "built, NOT sent (dry run)" for r in rows)
assert any(r["action"] == "cancel (outbid)" and r["ticker"] == M["G"] and r["side"] == "no" and r["request"].startswith("DELETE /portfolio/events/orders/dry-") for r in cancels)
assert any(json.loads(r["request"])["price"] == "0.8800" and json.loads(r["request"])["side"] == "ask" for r in creates if r["ticker"] == M["G"])   # re-placed NO @12
assert any(r["action"] == "cancel (kill switch)" for r in cancels) and "KILL SWITCH" in txt
assert "blocked by safety limits: max open orders" in txt or "max open orders" in txt
assert float(max(float(r["rate_wait_ms"]) for r in rows)) > 50, "rate limit never waited"
ts = sorted(dt.datetime.fromisoformat(r["ts_iso"]).timestamp() for r in rows)
assert all(ts[i + 4] - ts[i] >= 0.95 for i in range(len(ts) - 4)), "more than 4 actions within one second"
for r in rows: assert r["build_ms"] and float(r["build_ms"]) < 200
fills = list(csv.DictReader(open(f"{out}/mm15min_v2_fills.csv")))
g_fav = [f for f in fills if f["asset"] == "GOLD" and f["strategy"] == "fav"]
assert sum(float(f["contracts"]) for f in g_fav if f["how"] == "queue") == 5, g_fav          # 50 ahead used up, then 5
assert sum(float(f["contracts"]) for f in g_fav if f["how"] == "ask reached our bid") == 5, g_fav   # YES ask came down to 88
assert sum(float(f["contracts"]) for f in fills if f["asset"] == "ETH" and f["side"] == "NO") == 20, [f for f in fills if f["asset"] == "ETH"]
assert "[safety] DAILY LOSS LIMIT would stop new orders now" in txt and "dry run keeps going" in txt
assert "no new orders until tomorrow" not in txt and "dry run: the limit would have stopped new orders at" in txt
last = txt[txt.rindex("[status]"):]
assert "order actions built, not sent:" in last and "busiest second" in last and "decision -> request ready" in last and "Kalshi round trip" in last
print("DRY RUN CHECKS PASSED")

# ---------------- 2) DEMO path against a fake Kalshi ----------------
out2 = f"{SP}/run_mm2_demo"
g2 = load(out2, MODE="demo", DEMO_KEY_ID="demo-key", DEMO_PEM_PATH="/nonexistent")
M2 = {"G": "KXGOLD15M-26OCT011215-15"}
api2 = []
clock2, close2, book2, feed2 = wire(g2, M2, {M2["G"]: 800}, {M2["G"]: "yes"}, api2)
calls = []
class Resp:
    def __init__(s, code, js): s.status_code, s._js, s.text = code, js, json.dumps(js)
    def json(s): return s._js
n_order = {"i": 0}
def fake_post(self, url, headers=None, json=None, timeout=None):
    calls.append(("POST", url, json, headers))
    if url.endswith("/portfolio/order_groups/create"): return Resp(201, {"order_group_id": "grp-1"})
    n_order["i"] += 1
    return Resp(201, {"order_id": f"oid-{n_order['i']}", "status": "resting"})
def fake_delete(self, url, headers=None, params=None, timeout=None):
    calls.append(("DELETE", url, None, headers)); return Resp(200, {})
g2["requests"].Session.post = fake_post; g2["requests"].Session.delete = fake_delete
def script2():
    while feed2.on_tick is None: time.sleep(0.05)
    time.sleep(0.8)
    book2.books[M2["G"]] = {"yes": {88: 50.0}, "no": {11: 30.0}}
    feed2.p[M2["G"]] = (88, 89); feed2.on_tick(M2["G"], 88, 89); time.sleep(1.0)
    trade_ = feed2.on_trade(M2["G"], 88, 500, "no"); time.sleep(0.2)      # demo: market trades do NOT fill us
    feed2.on_fill("oid-1", 4, {"order_id": "oid-1", "count_fp": "4.00"}); time.sleep(0.2)   # a real (fake) fill: 4 contracts
    clock2["off"] = 820; time.sleep(3.0)
    _thread.interrupt_main()
txt2 = run_bot(g2, script2); print(txt2[-2500:])
assert g2["BASE"] == "https://external-api.demo.kalshi.co/trade-api/v2" and g2["KEY_ID"] == "demo-key"
posts = [c for c in calls if c[0] == "POST" and c[1].endswith("/portfolio/events/orders")]
assert len(posts) == 3 and all(c[1].startswith("https://external-api.demo.kalshi.co/trade-api/v2/") for c in posts)
assert all(c[2]["order_group_id"] == "grp-1" for c in posts)
assert all(c[3]["KALSHI-ACCESS-KEY"] == "demo-key" and c[3]["KALSHI-ACCESS-SIGNATURE"] for c in posts)
dels = [c for c in calls if c[0] == "DELETE"]
assert any(c[1].endswith("/portfolio/events/orders/old-1") for c in dels)          # leftover order cancelled at start
assert any(c[1].endswith("/portfolio/events/orders/oid-2") or c[1].endswith("/portfolio/events/orders/oid-3") for c in dels)   # cancelled at the close / stop
fills2 = list(csv.DictReader(open(f"{out2}/mm15min_v2_fills.csv")))
assert len(fills2) == 1 and fills2[0]["how"] == "exchange fill" and float(fills2[0]["contracts"]) == 4 and fills2[0]["px"] == "88"
print("DEMO PATH CHECKS PASSED")

# ---------------- 3) mode guards ----------------
for over, msg in (({"MODE": "live"}, "LIVE mode is locked"), ({"MODE": "demo"}, "demo mode needs DEMO_KEY_ID"), ({"MODE": "xyz"}, "MODE must be")):
    g3 = load(f"{SP}/run_mm2_guard", **over)
    try: g3["main"](); raise AssertionError("did not stop")
    except SystemExit as e: assert msg in str(e), e
print("MODE GUARDS PASSED")
print("ALL CHECKS PASSED")
