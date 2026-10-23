# LIP Maker 15M tests against a fake Kalshi and a fake price stream (nothing real is reached), with short windows:
# (1) dry run: picking only 15-minute series (SKIP_SERIES honoured, other series ignored), the group limit (crypto),
#     the pull before the close, each window's line and totals (reward kept for markets that close with nothing held),
#     settlement, moving to the next window, the programs log; (2) live path: leftover orders cancelled only in 15-min
#     series, post-only orders with a Kalshi-side expiry (Unix seconds) before the pull, fills; (3) switch checks.
import os, sys, time, threading, datetime as dt, io, contextlib, _thread, shutil, json, re, csv
from cryptography.hazmat.primitives.asymmetric import rsa
SP = os.path.dirname(os.path.abspath(__file__))
SRC = os.environ.get("LIP15_SRC", f"{SP}/lip_maker_15m.py")
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

def load(out, **over):
    shutil.rmtree(out, ignore_errors=True); os.makedirs(out, exist_ok=True)
    code = open(SRC).read().replace("/content/drive/MyDrive/", out + "/")
    g = {"__name__": "botmod"}; exec(compile(code, "lip15", "exec"), g)
    g.update(LOOP_SEC=0.1, STATUS_EVERY_SEC=1.5, LIVE_COUNTDOWN_SEC=0, REQUOTE_HOLD_SEC=0.2, FAST_MOVE_SEC=2, STABLE_SEC=0.5,
             COOL_OFF_SEC=1.0, FILL_CHECK_SEC=0.2, RECONCILE_SEC=0.5, FEED_LOST_SEC=0.5, PROGRAMS_REFRESH_SEC=1.0,
             PULL_BEFORE_CLOSE_SEC=3, MIN_LEFT_SEC=2, SETTLE_RETRY_SEC=0.5, GROUP_MAX_NET=15, SKIP_SERIES=["KXUSDJPY15M"])
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
        if not s.ready.is_set() or t not in s.tracked or t not in s.K.books: return None
        with s.K.lock:
            y, n = dict(s.K.books[t][0]), dict(s.K.books[t][1])
            for o in s.K.orders.values():
                if o["ticker"] != t or o["status"] != "resting": continue
                p, q = round(float(o["price"]) * 100), float(o["remaining_count_fp"])
                if o["side"] == "bid": y[p] = y.get(p, 0) + q
                else: n[100 - p] = n.get(100 - p, 0) + q
            return {"yes": sorted(y.items(), reverse=True), "no": sorted(n.items(), reverse=True)}
    def track(s, ts): s.tracked = set(ts)
    def stop(s): pass
    def trade(s, t, yp, n, taker): s.on_trade(t, yp, n, taker); time.sleep(0.05)

class FakeKalshi:
    def __init__(s, close_a=16):
        s.lock = threading.RLock()
        s.t0 = dt.datetime.now(dt.timezone.utc)
        s.books, s.close, s.programs, s.result = {}, {}, [], {}
        s.A = s.t0 + dt.timedelta(seconds=close_a)
        for t in ("KXBTC15M-26OCT231245-45", "KXETH15M-26OCT231245-45", "KXCRYPTOLEAD15M-26OCT231245-BTC",
                  "KXCRYPTOLEAD15M-26OCT231245-ETH", "KXBRENT15M-26OCT231245-45", "KXGOLD15M-26OCT231245-45",
                  "KXUSDJPY15M-26OCT231245-30", "KXAAAGASDWI-26OCT24-4.3000"):
            s.add(t, s.t0 - dt.timedelta(minutes=1), s.A, usd=50 if "BRENT" in t else 20)
        s.orders, s.positions, s.posts, s.deletes = {}, {}, [], []
    def add(s, t, start, close, usd=20, yes=((48, 100), (47, 200)), no=((50, 100), (49, 200))):
        s.books[t] = (dict(yes), dict(no)); s.close[t] = close
        s.programs.append({"market_ticker": t, "period_reward": int(usd * 10000), "start_date": iso(start), "end_date": iso(close),
                           "target_size_fp": "300.00", "discount_factor_bps": 5000})
    def api_get(s, sess, k, path, params=None):
        with s.lock:
            params = params or {}
            if path == "/incentive_programs": return {"incentive_programs": list(s.programs), "next_cursor": ""}
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
            if m:
                t = m.group(1); done = dt.datetime.now(dt.timezone.utc) >= s.close[t]
                return {"market": {"ticker": t, "status": "finalized" if done else "active", "close_time": iso(s.close[t]),
                                   "result": s.result.get(t, "no") if done else ""}}
            return None
    def post(s, sess, url, headers=None, json=None, timeout=None):
        with s.lock:
            s.posts.append(dict(json))
            if url.endswith("/order_groups/create"): return Resp(201, {"order_group_id": "grp-1"})
            if "expiration_time" in json and not isinstance(json["expiration_time"], int):
                return Resp(400, {"error": {"code": "bad_request", "details": "cannot unmarshal string into .expiration_time of type int64"}})
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

def wait_for(cond, t=10):
    end = time.time() + t
    while time.time() < end:
        if cond(): return True
        time.sleep(0.05)
    raise AssertionError("timed out")

def orders_csv(out):
    d = f"{out}/lip15_data"
    rows = []
    for f in sorted(os.listdir(d)):
        rows += list(csv.DictReader(open(f"{d}/{f}")))
    return rows

BTC, ETH, BRENT = "KXBTC15M-26OCT231245-45", "KXETH15M-26OCT231245-45", "KXBRENT15M-26OCT231245-45"
BTC2 = "KXBTC15M-26OCT231300-00"
# ---------------- 1) DRY RUN ----------------
out = f"{SP}/run_lip15_dry"
g = load(out)
assert g["DRY_RUN"] is True and g["SERIES"] == ["ALL"]
K = FakeKalshi(); F = FakeFeed(K)
K.result[BTC] = "yes"
K.add(BTC2, K.A, K.A + dt.timedelta(seconds=40))               # the next window: its program starts when window A closes
g["api_get"] = K.api_get; g["make_feed"] = lambda k: F
sent = []
g["requests"].Session.post = lambda *a, **k: sent.append(a)
g["requests"].Session.delete = lambda *a, **k: sent.append(a)
def script1(buf):
    wait_for(lambda: "PICKED" in buf.getvalue())
    time.sleep(0.6)
    F.trade(BTC, 45, 20, "no")              # traded through our 48c YES bid: 10 YES held in BTC (crypto group 10 of 15)
    time.sleep(1.2)                         # ETH may now hold only 5 a side; BTC's YES only 5 more
    assert "unpaired" in buf.getvalue()
    wait_for(lambda: "[window" in buf.getvalue(), t=25)      # window A: pulled 3 s before the close, closed, settled
    wait_for(lambda: f"PICKED 1: {BTC2}" in buf.getvalue(), t=8)
    time.sleep(1.6)
    _thread.interrupt_main()
txt = run(g, script1); print(txt[-3500:])
assert not sent, "dry run sent something"
first = re.search(r"PICKED (\d+): (.*)", txt)
assert first and first.group(1) == "6", first.group(0)
names = first.group(2)
for t in (BTC, ETH, BRENT, "KXGOLD15M", "KXCRYPTOLEAD15M-26OCT231245-BTC", "KXCRYPTOLEAD15M-26OCT231245-ETH"): assert t in names, t
assert "USDJPY" not in names and "AAAGAS" not in names and BTC2 not in names
rows = orders_csv(out)
c_eth = [r for r in rows if r["action"] == "create" and r["ticker"] == ETH]
assert any(r["count"] == "5" for r in c_eth), "the crypto group limit did not shrink ETH's orders"
c_btc_yes = [r for r in rows if r["action"] == "create" and r["ticker"] == BTC and r["side"] == "yes"]
assert c_btc_yes[-1]["count"] == "5"
assert not [r for r in rows if r["action"] == "create" and r["ticker"] == BRENT and r["count"] != "10"]   # other groups untouched
assert "crypto 10/15" in txt
creates_a = [r for r in rows if r["action"] == "create" and r["ticker"] != BTC2]
assert all(dt.datetime.fromisoformat(r["ts_iso"]) <= K.A - dt.timedelta(seconds=3) for r in creates_a)   # nothing new in the last 3 s
assert f"SETTLED YES  {BTC}: held YES 10 / NO 0 -> fills +$5.20" in txt
win = re.search(r"\[window (\d\d:\d\d) UTC\] (\d+) market\(s\) · estimated reward \+\$([\d.]+) · 1 fill\(s\), settled \+\$5\.20", txt)
assert win and win.group(2) == "6" and float(win.group(3)) > 0, win
wcsv = list(csv.DictReader(open(f"{out}/lip15_windows.csv")))
assert len(wcsv) == 6 and sum(float(r["est_reward_c"]) for r in wcsv) > 0
pcsv = list(csv.DictReader(open(f"{out}/lip15_programs.csv")))
assert {r["ticker"] for r in pcsv} == {BTC, ETH, BRENT, BTC2, "KXGOLD15M-26OCT231245-45", "KXCRYPTOLEAD15M-26OCT231245-BTC",
                                       "KXCRYPTOLEAD15M-26OCT231245-ETH"}, pcsv
assert "[stopped] every order cancelled" in txt
print("DRY RUN CHECKS PASSED")

# ---------------- 2) LIVE path ----------------
out2 = f"{SP}/run_lip15_live"
g2 = load(out2, DRY_RUN=False, KEY_ID="test-key")
K2 = FakeKalshi(close_a=14); F2 = FakeFeed(K2)
K2.orders["old-1"] = {"order_id": "old-1", "ticker": "KXETH15M-26OCT231230-30", "side": "bid", "price": "0.4000", "status": "resting",
                      "initial_count_fp": "5.00", "remaining_count_fp": "5.00", "fill_count_fp": "0.00"}
K2.orders["gas-1"] = {"order_id": "gas-1", "ticker": "KXAAAGASDWI-26OCT24-4.3000", "side": "bid", "price": "0.4000",
                      "status": "resting", "initial_count_fp": "10.00", "remaining_count_fp": "10.00", "fill_count_fp": "0.00"}
g2["api_get"] = K2.api_get; g2["make_feed"] = lambda k: F2
g2["requests"].Session.post = lambda self, url, **kw: K2.post(self, url, **kw)
g2["requests"].Session.delete = lambda self, url, **kw: K2.delete(self, url, **kw)
def script2(buf):
    wait_for(lambda: "PICKED" in buf.getvalue() and K2.resting(BRENT, "bid"))
    K2.fill(K2.resting(BRENT, "bid")[0], 4)
    wait_for(lambda: f"FILL  {BRENT}: bought 4 YES @ 48c (Kalshi)" in buf.getvalue())
    time.sleep(0.5)
    _thread.interrupt_main()
txt2 = run(g2, script2); print(txt2[-2500:])
assert "LIVE: REAL ORDERS WITH REAL MONEY" in txt2
assert "cancelled leftover order old-1" in txt2 and "gas-1" not in txt2 and "gas-1" not in K2.deletes   # the gas bot's order stays
creates = [p for p in K2.posts if "ticker" in p]
assert creates and all(c["post_only"] is True and c["order_group_id"] == "grp-1" for c in creates)
assert all(isinstance(c["expiration_time"], int) and c["expiration_time"] <= (K2.A - dt.timedelta(seconds=3)).timestamp() + 1
           for c in creates)
assert "Kalshi rejected expiration_time" not in txt2
assert not any("USDJPY" in c["ticker"] or "AAAGAS" in c["ticker"] for c in creates)
with K2.lock: assert not [o for o, v in K2.orders.items() if v["status"] == "resting" and o not in ("gas-1",)]   # all cancelled at the stop
print("LIVE PATH CHECKS PASSED")

# ---------------- 3) switch checks ----------------
def bad(**over):
    g3 = load(f"{SP}/run_lip15_sw", **over)
    try: g3["check_switches"](); return None
    except SystemExit as e: return str(e)
assert bad() is None
assert "SERIES" in bad(SERIES="ALL")
assert "GROUP_MAX_NET" in bad(GROUP_MAX_NET=0)
assert "SKIP_SERIES" in bad(SKIP_SERIES="KXBTC15M")
g4 = load(f"{SP}/run_lip15_sw")
assert [g4["risk_group"](t) for t in ("KXBTC15M-X-1", "KXCRYPTOLEAD15M-X-BTC", "KX10YRRATE15M-X-1", "KXEURUSD15M-X-1",
        "KXUSDJPY15M-X-1", "KXPALLADIUM15M-X-1", "KXNATGAS15M-X-1", "KXFOO15M-X-1")] == \
       ["crypto", "crypto-lead", "rates", "fx", "fx", "metals", "energy", "KXFOO15M"]
print("SWITCH CHECKS PASSED")
print("ALL CHECKS PASSED")
