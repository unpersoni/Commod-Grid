# MM15Min V4 tests: (1) dry run end to end, (2) demo path with a fake Kalshi, (3) mode guards (from V2),
# (4) the lean strategy, switches and per-market status, (5) switch checks.
import os, sys, time, threading, datetime as dt, csv, io, contextlib, _thread, glob, shutil, json, gzip
from cryptography.hazmat.primitives.asymmetric import rsa
SP = os.path.dirname(os.path.abspath(__file__))
SRC = os.environ.get("MM4_SRC", f"{SP}/mm15min_v4.py")
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)      # a throwaway key: real signing, no account

def load(out, **over):
    shutil.rmtree(out, ignore_errors=True); os.makedirs(out, exist_ok=True)
    code = open(SRC).read().replace("/content/drive/MyDrive/", out + "/")
    g = {"__name__": "botmod"}; exec(compile(code, "mm15v4", "exec"), g)
    g.update(MARKETS_REFRESH_SEC=0.3, LOOP_SEC=0.1, SETTLE_RETRY_SEC=0.2, STATUS_EVERY_SEC=2, LATENCY_PROBE_SEC=0.5)
    g.update(over)
    return g

class Book:
    def __init__(s): s.books = {}; s.version = {}; s.ready = threading.Event(); s.ready.set()
    def top(s, t, n=3):
        b = s.books.get(t)
        if b is None: return None
        return (sorted(b["yes"].items(), reverse=True)[:n], sorted(b["no"].items(), reverse=True)[:n])
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
        if path == "/portfolio/orders": return {"orders": [{"order_id": "old-1", "ticker": "KXGOLD15M-26OCT011200-00"},
                                                          {"order_id": "keep-1", "ticker": "KXSAVEACT-27-JAN04"}]}
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

def read_csvs(pattern):
    """All rows of the files matching pattern; tolerant of the bot gzipping a file at the hour boundary."""
    for _ in range(10):
        try:
            rows = []
            for f in sorted(glob.glob(pattern)):
                if f.endswith(".csv") and os.path.exists(f + ".gz"): continue      # being gzipped: read the .gz copy
                with (gzip.open(f, "rt") if f.endswith(".gz") else open(f)) as fh: rows += list(csv.DictReader(fh))
            return rows
        except (FileNotFoundError, EOFError, OSError): time.sleep(0.5)
    raise AssertionError(f"could not read {pattern}")

def orders_rows(out, name, kind="orders_[0-9]"):
    return read_csvs(f"{out}/{name}/{kind}*")

# ---------------- 1) DRY RUN ----------------
out = f"{SP}/run_mm4_dry"
g = load(out, MAX_WRITES_PER_SEC=1, MAX_OPEN_ORDERS=1, DAILY_LOSS_LIMIT_USD=1)
M = {"G": "KXGOLD15M-26OCT011215-15", "B": "KXBTC15M-26OCT011215-15", "E": "KXETH15M-26OCT011215-15", "W": "KXWTI15M-26OCT011215-15"}
secs = {M["G"]: 800, M["B"]: 700, M["E"]: 880, M["W"]: 650}   # ETH: crypto fav bids in the first 5 min only
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
    tick(M["G"], 88, 89)          # fav YES @88 (NO @11 is no strategy's price any more) -> 1 create
    time.sleep(1.2)               # request built -> the order joins the line
    trade(M["G"], 88, 30, "no"); trade(M["G"], 88, 25, "no")      # 50 ahead used up, then 5 fill
    tick(M["G"], 89, 90)          # someone bids 89: the half-filled YES order stays
    feed.p[M["G"]] = (87, 88); feed.on_tick(M["G"], 87, 88); time.sleep(0.3)   # YES ask came down to 88: the other 5 fill
    # ETH: favourite NO @95, then a taker sweep fills it -> ETH settles YES: a loss (daily loss limit $1)
    book.books[M["E"]] = {"yes": {4: 10.0}, "no": {95: 70.0}}
    tick(M["E"], 4, 5); time.sleep(1.5)
    trade(M["E"], 5, 80, "yes")   # takers bought YES at 5c = sold NO at 95c: 70 ahead, then 10 for us
    # BTC lean NO @78 (crypto, first 5 min) takes the one open-order slot -> WTI's fav YES @90 is blocked
    book.books[M["B"]] = {"yes": {20: 500.0}, "no": {78: 100.0}}
    book.books[M["W"]] = {"yes": {90: 10.0}, "no": {9: 10.0}}
    tick(M["B"], 20, 22); time.sleep(1.2); tick(M["W"], 90, 91)
    book.books[M["B"]]["no"][79] = 5.0
    tick(M["B"], 20, 21)          # NO best bid now 79 > 78: lean NO cancelled + re-placed at 79 (waits for its 1/s limit)
    time.sleep(3.0)
    open(f"{out}/STOP_MM15MIN.txt", "w").write("stop")        # kill switch
    time.sleep(6.0)
    clock["off"] = 900            # all closed -> settle
    time.sleep(3.0)
    _thread.interrupt_main()
txt = run_bot(g, script1); print(txt[-4000:])
rows = orders_rows(out, "mm15min_v4_data")
creates = [r for r in rows if r["action"] == "create"]; cancels = [r for r in rows if r["action"].startswith("cancel")]
print(len(creates), "creates,", len(cancels), "cancels")
assert not sent, f"dry run SENT something: {sent[:2]}"
assert "/exchange/status" in api and "/portfolio/balance" in api and "/portfolio/orders" in api
assert "[account] balance $1,234.56 (shard 0: $1,000.00; shard 2: $234.56)" in txt and "resting orders on the account now: 2 (1 in the 15-minute markets)" in txt
assert "dry run: would create an order group (limit 300 contracts / 15 s) and cancel the 1 resting order(s) in the 15-minute markets" in txt
c0 = [json.loads(r["request"]) for r in creates if r["ticker"] == M["G"]]
yes88 = [b for b in c0 if b["side"] == "bid" and b["price"] == "0.8800"]
assert len(yes88) == 1 and len(c0) == 1, c0                                             # fav only; no two-sided mm
assert not any(r["strategy"] == "mm" for r in rows)
b = yes88[0]
assert b["count"] == "10.00" and b["time_in_force"] == "good_till_canceled" and b["post_only"] is True
assert b["self_trade_prevention_type"] == "taker_at_cross" and b["ticker"] == M["G"] and len(b["client_order_id"]) == 36
exp = (close[M["G"]] - dt.timedelta(seconds=25)).strftime("%Y-%m-%dT%H:%M:%SZ")
assert b["expiration_time"] == exp, (b["expiration_time"], exp)
assert all(r["result"] == "built, NOT sent (dry run)" for r in rows)
assert any(r["action"] == "cancel (outbid)" and r["ticker"] == M["B"] and r["strategy"] == "lean" and r["side"] == "no" and r["request"].startswith("DELETE /portfolio/events/orders/dry-") for r in cancels)
assert any(json.loads(r["request"])["price"] == "0.2100" and json.loads(r["request"])["side"] == "ask" for r in creates if r["ticker"] == M["B"])   # re-placed NO @79 = YES ask @21
assert any(r["action"] == "cancel (kill switch)" for r in cancels) and "KILL SWITCH" in txt
assert "blocked by safety limits: max open orders" in txt or "max open orders" in txt
assert float(max(float(r["rate_wait_ms"]) for r in rows)) > 50, "rate limit never waited"
for grp in ("fav", "lean"):               # fav is switched on (Kalshi's limit); lean is off (its own limit, as if live alone)
    ts = sorted(dt.datetime.fromisoformat(r["ts_iso"]).timestamp() for r in rows if r["strategy"] == grp)
    assert all(ts[i + 1] - ts[i] >= 0.95 for i in range(len(ts) - 1)), f"more than 1 {grp} action within one second"
assert "switched-on strategies (Kalshi's limit, 1/s): busiest second" in txt and "switched-off strategies (each on its own limit): waited" in txt
for r in rows: assert r["build_ms"] and float(r["build_ms"]) < 200
fills = list(csv.DictReader(open(f"{out}/mm15min_v4_fills.csv")))
g_fav = [f for f in fills if f["asset"] == "GOLD" and f["strategy"] == "fav"]
assert sum(float(f["contracts"]) for f in g_fav if f["how"] == "queue") == 5, g_fav          # 50 ahead used up, then 5
assert sum(float(f["contracts"]) for f in g_fav if f["how"] == "ask reached our bid") == 5, g_fav   # YES ask came down to 88
assert sum(float(f["contracts"]) for f in fills if f["asset"] == "ETH" and f["side"] == "NO") == 10, [f for f in fills if f["asset"] == "ETH"]
assert not any(f["strategy"] == "mm" for f in fills)
assert "DAILY LOSS" not in txt and "no daily loss stop" in txt                         # V4: no daily loss stop
last = txt[txt.rindex("[status]"):]
assert "order actions built, not sent:" in last and "busiest second" in last and "decision -> request ready" in last and "Kalshi round trip" in last
def data_rows(prefix):
    return read_csvs(f"{out}/mm15min_v4_data/{prefix}_*")
tk = data_rows("ticks"); fl = data_rows("flow")
assert tk and {r["asset"] for r in tk} >= {"GOLD", "BTC", "ETH", "WTI"} and tk[0]["yes_bid_qty"] != "", tk[:2]
assert any(r["ticker"] == M["G"] and r["yes_bid"] == "88" and r["yes_bid_qty"] == "5.0" and r["no_bid_qty"] == "30.0" for r in tk), [r for r in tk if r["ticker"] == M["G"]][:3]      # top book level (89: 5) and NO top (11: 30), as V38 logged
g_fl = [r for r in fl if r["ticker"] == M["G"]]
assert sum(int(r["ct_no_taker"]) for r in g_fl) == 55, g_fl        # the two GOLD trades (30 + 25) where takers bought NO
e_fl = [r for r in fl if r["ticker"] == M["E"]]
assert sum(int(r["ct_yes_taker"]) for r in e_fl) == 80 and e_fl[0]["last_yes_px"] == "5"
print("DRY RUN CHECKS PASSED")

# ---------------- 2) DEMO path against a fake Kalshi ----------------
out2 = f"{SP}/run_mm4_demo"
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
assert len(posts) == 1 and all(c[1].startswith("https://external-api.demo.kalshi.co/trade-api/v2/") for c in posts)
assert all(c[2]["order_group_id"] == "grp-1" for c in posts)
assert all(c[3]["KALSHI-ACCESS-KEY"] == "demo-key" and c[3]["KALSHI-ACCESS-SIGNATURE"] for c in posts)
dels = [c for c in calls if c[0] == "DELETE"]
assert any(c[1].endswith("/portfolio/events/orders/old-1") for c in dels)          # leftover order cancelled at start
assert not any(c[1].endswith("/portfolio/events/orders/keep-1") for c in dels)       # other markets are never touched
assert any(c[1].endswith("/portfolio/events/orders/oid-1") for c in dels)          # the rest of fav's order cancelled at the close
fills2 = list(csv.DictReader(open(f"{out2}/mm15min_v4_fills.csv")))
assert len(fills2) == 1 and fills2[0]["how"] == "exchange fill" and float(fills2[0]["contracts"]) == 4 and fills2[0]["px"] == "88"
assert glob.glob(f"{out2}/mm15min_v4_data/flow_*"), "demo mode must log trade flow too"
print("DEMO PATH CHECKS PASSED")

# ---------------- 3) mode guards ----------------
for over, msg in (({"MODE": "live"}, "MODE = 'live' is not used"), ({"MODE": "demo"}, "demo mode needs DEMO_KEY_ID"), ({"MODE": "xyz"}, "MODE must be")):
    g3 = load(f"{SP}/run_mm4_guard", **over)
    try: g3["main"](); raise AssertionError("did not stop")
    except SystemExit as e: assert msg in str(e), e
print("MODE GUARDS PASSED")

# ---------------- 4) lean strategy, switches, per-market status ----------------
out4 = f"{SP}/run_mm4_lean"
g4 = load(out4, LEAN_CRYPTO=True, SKIP_MARKETS=["sol"])
assert g4["FAV_CRYPTO"] and g4["FAV_COMMODITIES"] and not g4["LEAN_COMMODITIES"] and set(g4["STRATEGIES"]) == {"fav", "lean"}
M4 = {"B": "KXBTC15M-26OCT011215-15", "S": "KXSOL15M-26OCT011215-15", "V": "KXSILVER15M-26OCT011215-15",
      "E": "KXETH15M-26OCT011215-15", "G": "KXGOLD15M-26OCT011215-15"}
secs4 = {M4["B"]: 880, M4["S"]: 880, M4["V"]: 880, M4["E"]: 500, M4["G"]: 880}
res4 = {M4["B"]: "yes", M4["S"]: "yes", M4["V"]: "no", M4["E"]: "yes", M4["G"]: "yes"}
clock4, close4, book4, feed4 = wire(g4, M4, secs4, res4, [])
g4["requests"].Session.post = lambda *a, **k: (_ for _ in ()).throw(AssertionError("sent"))
g4["requests"].Session.delete = lambda *a, **k: (_ for _ in ()).throw(AssertionError("sent"))
def t4(t, yb, ya): feed4.p[t] = (yb, ya); feed4.on_tick(t, yb, ya); time.sleep(0.05)
def script4():
    while feed4.on_tick is None: time.sleep(0.05)
    time.sleep(0.8)
    for k_ in ("B", "S", "V", "E"):
        book4.books[M4[k_]] = {"yes": {80: 10.0}, "no": {19: 10.0}}
        t4(M4[k_], 80, 81)        # YES bid 80: lean (crypto in the first 5 min / commodity 75-84)
    book4.books[M4["G"]] = {"yes": {86: 10.0}, "no": {13: 10.0}}
    t4(M4["G"], 86, 87)           # GOLD YES 86: fav only (lean commodity stops at 84)
    time.sleep(2.0)               # requests built -> orders on the book
    for k_ in ("B", "S", "V", "E"): t4(M4[k_], 79, 80)     # YES ask came down to 80: the YES bids at 80 fill
    t4(M4["G"], 85, 86)           # YES ask 86: fav YES @86 fills
    time.sleep(0.5)
    clock4["off"] = 900; time.sleep(4.0)
    _thread.interrupt_main()
txt4 = run_bot(g4, script4); print(txt4[-5000:])
rows4 = orders_rows(out4, "mm15min_v4_data")
cr4 = [r for r in rows4 if r["action"] == "create"]
lean = {(r["ticker"], json.loads(r["request"])["price"]) for r in cr4 if r["strategy"] == "lean"}
assert lean == {(M4["B"], "0.8000"), (M4["S"], "0.8000"), (M4["V"], "0.8000")}, lean     # not ETH (5 min gone), not GOLD (86 > 84)
assert not any(r["ticker"] == M4["E"] for r in cr4)                                          # ETH at 500 s: no strategy bids
assert {r["ticker"] for r in cr4 if r["strategy"] == "fav"} == {M4["G"]}
fills4 = list(csv.DictReader(open(f"{out4}/mm15min_v4_fills.csv")))
on = {(f["asset"], f["strategy"]): f["switched_on"] for f in fills4}
assert on == {("BTC", "lean"): "1", ("SOL", "lean"): "0", ("SILVER", "lean"): "0", ("GOLD", "fav"): "1"}, on
assert "strategies and switches" in txt4 and "never live: SOL" in txt4
assert "lean leader     crypto 75-84c first 5 min       ★ON    commodity 75-84c whole market      off" in txt4
assert "fav  favourite  crypto 85-98c first 5 min       ★ON    commodity 85-98c whole market      ★ON" in txt4
assert "SETTLED [lean ★ON] BTC (crypto)" in txt4 and "SETTLED [lean] SOL (crypto)" in txt4 and "Lean Crypto streak W1" in txt4
assert "Lean Crypto streak W2" in txt4 and "Lean Commodities streak L1" in txt4
last4 = txt4[txt4.rindex("[status]"):]
import re
lines4 = [l for l in last4.split("\n") if re.match(r"  (★ON|off)  ", l)]
assert [l[7:28].strip() for l in lines4] == ["Favorite Crypto", "Favorite Commodities", "Lean Crypto", "Lean Commodities"], lines4
assert re.search(r"  ★ON  Lean Crypto\s+75-84c first 5 min\s+2 markets,\s+2 fills,\s+20 contracts, 100\.0% won, net\s+\+\$4\.00 "
                 r"\(\+20\.00c/contract\) · if maker fees \+\$3\.94 · streak W2\s+· last 10 markets: WW$", lines4[2]), lines4[2]
assert re.search(r"  off  Lean Commodities\s+75-84c whole market\s+1 markets,\s+1 fills,\s+10 contracts,\s+0\.0% won, net\s+-\$8\.00 .* streak L1\s+· last 10 markets: L$", lines4[3]), lines4[3]
assert re.search(r"  ★ON  Favorite Crypto\s+85-98c first 5 min\s+0 markets, .* streak -\s+· last 10 markets: -$", lines4[0]), lines4[0]
assert "BTC " not in last4 and "SILVER " not in last4 and "skipped" not in last4             # no per-asset rows
fav_g = 10 * (100 - 86)
on_net = 200 + fav_g                               # lean BTC + fav GOLD (switched on)
all_net = 200 + 200 - 800 + fav_g                  # + SOL lean (skipped) and SILVER lean (switched off)
assert f"today (UTC) net of the switched-on trade types {g4['_usd'](on_net)}" in last4, last4
assert f"all trade types {g4['_usd'](all_net)}" in last4, last4
print("LEAN + SWITCH CHECKS PASSED")

# ---------------- 5) switch checks ----------------
for over, msg in (({"DRY_RUN": False}, "live trading needs KEY_ID"),
                  ({"DRY_RUN": False, "KEY_ID": "k", "FAV_CRYPTO": False, "FAV_COMMODITIES": False}, "every trade type is switched off"),
                  ({"DRY_RUN": False, "KEY_ID": "k", "MODE": "demo"}, "DRY_RUN = False needs MODE = 'dry_run'"), ({"FAV_CRYPTO": "yes"}, "FAV_CRYPTO must be True or False"),
                  ({"SKIP_MARKETS": ["GLD"]}, "SKIP_MARKETS has unknown names ['GLD']"), ({"SKIP_MARKETS": "WTI"}, "must be a list"),
                  ({"CONTRACTS": 0}, "CONTRACTS must be")):
    g5 = load(f"{SP}/run_mm4_guard", **over)
    try:
        with contextlib.redirect_stdout(io.StringIO()): g5["main"]()
        raise AssertionError(f"did not stop: {over}")
    except SystemExit as e: assert msg in str(e), (over, e)
g6 = {"__name__": "x", "LEAN_CRYPTO": True, "SKIP_MARKETS": ["WTI"]}           # a SWITCHES cell run before the code cell
exec(compile(open(SRC).read(), "mm15v4", "exec"), g6)
assert g6["LEAN_CRYPTO"] is True and g6["SKIP_MARKETS"] == ["WTI"] and g6["LEAN_COMMODITIES"] is False and g6["DRY_RUN"] is True
assert "MM_CRYPTO" not in g6 and set(g6["SWITCH_NAMES"].values()) == {"FAV_CRYPTO", "FAV_COMMODITIES", "LEAN_CRYPTO", "LEAN_COMMODITIES"}
assert g6["is_on"]("lean", "crypto", "BTC") and not g6["is_on"]("fav", "commodity", "WTI") and g6["is_on"]("fav", "commodity", "GOLD")
assert g6["band_text"]("lean", "crypto") == "75-84c first 5 min" and g6["band_text"]("fav", "crypto") == "85-98c first 5 min"
assert g6["band_text"]("fav", "commodity") == "85-98c whole market" and g6["band_text"]("lean", "commodity") == "75-84c whole market"
assert g6["streak"](list("WWLLL")) == "L3"
print("SWITCH CHECKS PASSED")
# ---------------- 6) LIVE path against a fake Kalshi at the production address ----------------
out6 = f"{SP}/run_mm4_live"
g6l = load(out6, DRY_RUN=False, KEY_ID="test-key-id", LIVE_COUNTDOWN_SEC=0, FAV_CRYPTO=False, FAV_COMMODITIES=True)
M6 = {"G": "KXGOLD15M-26OCT011215-15", "V": "KXSILVER15M-26OCT011215-15", "B": "KXBTC15M-26OCT011215-15"}
api6 = []
clock6, close6, book6, feed6 = wire(g6l, M6, {M6["G"]: 800, M6["V"]: 800, M6["B"]: 880}, {t: "yes" for t in M6.values()}, api6)
base_get = g6l["api_get"]; bal = {"s0": 9000, "s2": 1000}; on_kalshi = {}
def get6(sess, k, path, params=None):
    if path == "/portfolio/balance":
        return {"balance": bal["s0"] + bal["s2"], "balance_breakdown": [{"exchange_index": 0, "balance": f"{bal['s0'] / 100:.2f}"},
                                                                       {"exchange_index": 2, "balance": f"{bal['s2'] / 100:.2f}"}]}
    if path == "/portfolio/orders" and params and params.get("ticker"):
        return {"orders": [o for o in on_kalshi.values() if o["ticker"] == params["ticker"]]}
    return base_get(sess, k, path, params)
g6l["api_get"] = get6
posted6, nlive = [], {"n": 0}
def post6(self, url, headers=None, json=None, timeout=None):
    posted6.append(("POST", url, json, headers))
    if url.endswith("/intra_exchange_instance_transfer"):
        bal["s2"] += json["amount"] // 100; bal["s0"] -= json["amount"] // 100; return Resp(200, {})
    if url.endswith("/portfolio/order_groups/create"): return Resp(201, {"order_group_id": "grp-L"})
    if url.endswith("/portfolio/events/orders"):
        if json["ticker"] == M6["V"]:                          # placed on Kalshi, but the answer is lost
            on_kalshi[json["client_order_id"]] = {"ticker": json["ticker"], "client_order_id": json["client_order_id"],
                                                  "order_id": f"oid-silver-{len(on_kalshi) + 1}", "status": "resting"}
            raise g6l["requests"].ConnectionError("read timed out")
        nlive["n"] += 1
        return Resp(201, {"order": {"order_id": f"live-{nlive['n']}", "status": "resting"}})
    raise AssertionError(f"unexpected POST {url}")
def del6(self, url, headers=None, params=None, timeout=None):
    posted6.append(("DELETE", url, None, headers)); return Resp(200, {})
g6l["requests"].Session.post = post6; g6l["requests"].Session.delete = del6
def script6():
    while feed6.on_tick is None: time.sleep(0.05)
    time.sleep(0.8)
    book6.books[M6["G"]] = {"yes": {88: 50.0}, "no": {11: 30.0}}
    feed6.p[M6["G"]] = (88, 89); feed6.on_tick(M6["G"], 88, 89)          # GOLD fav YES @88 -> a LIVE order
    book6.books[M6["V"]] = {"yes": {90: 20.0}, "no": {9: 5.0}}
    feed6.p[M6["V"]] = (90, 91); feed6.on_tick(M6["V"], 90, 91)          # SILVER fav YES @90 -> LIVE, answer lost
    book6.books[M6["B"]] = {"yes": {86: 10.0}, "no": {13: 10.0}}
    feed6.p[M6["B"]] = (86, 87); feed6.on_tick(M6["B"], 86, 87)          # BTC crypto fav is switched off -> dry run
    time.sleep(4.0)
    feed6.on_trade(M6["G"], 88, 500, "no"); time.sleep(0.2)              # market trades never fill a LIVE order
    feed6.on_fill("live-1", 4, {"order_id": "live-1", "count_fp": "4.00"}); time.sleep(0.2)
    feed6.on_fill("oid-silver-1", 10, {"order_id": "oid-silver-1", "count_fp": "10.00"}); time.sleep(0.2)
    feed6.p[M6["B"]] = (85, 86); feed6.on_tick(M6["B"], 85, 86); time.sleep(0.3)   # dry run: BTC YES ask down to 86 -> filled
    clock6["off"] = 900; time.sleep(4.0)
    _thread.interrupt_main()
txt6 = run_bot(g6l, script6); print(txt6[-3500:])
assert "LIVE: REAL ORDERS WITH REAL MONEY for: Favorite Commodities" in txt6
creates6 = [c for c in posted6 if c[0] == "POST" and c[1].endswith("/portfolio/events/orders")]
assert all(c[1] == "https://api.elections.kalshi.com/trade-api/v2/portfolio/events/orders" for c in creates6)
assert {c[2]["ticker"] for c in creates6} == {M6["G"], M6["V"]}, creates6                             # never BTC (dry run)
assert len({c[2]["client_order_id"] for c in creates6}) == len(creates6)                             # no order sent twice
sv = [c for c in creates6 if c[2]["ticker"] == M6["V"]]
assert len(sv) == 2 and sv[0][2]["client_order_id"] in on_kalshi       # 1st: answer lost, found; 2nd: a new order after it filled
assert all(c[2]["order_group_id"] == "grp-L" and c[2]["post_only"] is True and c[2]["count"] == "10.00" for c in creates6)
assert all(c[3]["KALSHI-ACCESS-KEY"] == "test-key-id" and c[3]["KALSHI-ACCESS-SIGNATURE"] for c in posted6)
tr6 = [c[2] for c in posted6 if c[1].endswith("intra_exchange_instance_transfer")]
assert tr6 == [{"source": "event_contract", "destination": "event_contract", "amount": 900000,
                "source_exchange_shard": 0, "destination_exchange_shard": 2}], tr6
assert "[funds] moved $90.00 shard 0 -> shard 2: HTTP 200, shard 2 now $100.00" in txt6
dels6 = [c[1] for c in posted6 if c[0] == "DELETE"]
assert any(u.endswith("/orders/old-1") for u in dels6) and not any(u.endswith("/orders/keep-1") for u in dels6)
assert any(u.endswith("/orders/live-1") for u in dels6)                       # GOLD's unfilled 6 cancelled at the close
assert not any(u.endswith("/orders/oid-silver-1") for u in dels6)            # fully filled: nothing to cancel
assert any(u.endswith("/orders/oid-silver-2") for u in dels6)                # the 2nd SILVER order, unfilled, cancelled at the close
assert "create had no clear answer (HTTP 0) — found on Kalshi as oid-silver-1" in txt6
assert "Kalshi's raw answer to a create (HTTP 201)" in txt6
f6 = list(csv.DictReader(open(f"{out6}/mm15min_v4_fills.csv")))
by6 = {(f["asset"], f["live"]): f for f in f6}
assert sum(float(f["contracts"]) for f in f6 if f["asset"] == "GOLD") == 4, f6         # only Kalshi's fill counts
assert by6[("GOLD", "1")]["how"] == "exchange fill" and by6[("SILVER", "1")]["how"] == "exchange fill"
assert float(by6[("SILVER", "1")]["contracts"]) == 10 and by6[("BTC", "0")]["how"] == "ask reached our bid"
lv_rows = orders_rows(out6, "mm15min_v4_data", "orders_live_"); dry_rows = orders_rows(out6, "mm15min_v4_data")
assert {r["ticker"] for r in lv_rows if r["action"] == "create"} == {M6["G"], M6["V"]}
assert {r["ticker"] for r in dry_rows if r["action"] == "create"} == {M6["B"]}
assert all(r["result"] == "built, NOT sent (dry run)" for r in dry_rows)
assert "🟢 LIVE FILL [fav] 4x YES @ 88c  GOLD" in txt6 and "🟢 LIVE SETTLED [fav] GOLD (commodity)" in txt6
last6 = txt6[txt6.rindex("[status]"):]
assert re.search(r"🟢\s+Favorite Commodities\s+85-98c whole market\s+2 markets,\s+2 fills,\s+14 contracts, 100\.0% won, net\s+\+\$1\.48", last6), last6
assert re.search(r"off\s+Favorite Crypto\s+85-98c first 5 min\s+1 markets,\s+1 fills,\s+10 contracts", last6), last6
assert "🟢 LIVE order actions SENT LIVE: 3 creates" in last6 and "dry run: order actions built, not sent: 1 creates" in last6
print("LIVE PATH CHECKS PASSED")

# ---------------- 7) HISTORY cell: the bot's own results files (V4 + V3) ----------------
out7 = f"{SP}/run_mm4_hist"
g7 = load(out7)
cols4 = g7["MARKET_COLS"]
def mrow(win, asset_, c, s_, result, yes_ct, yes_px, no_ct, no_px, pnl):
    return {"window": win, "asset": asset_, "class": c, "strategy": s_, "switched_on": 1, "live": 0, "result": result,
            "orders_placed": 1, "orders_moved_up": 0, "orders_filled": 1, "yes_ct": yes_ct, "yes_avg_px": yes_px,
            "no_ct": no_ct, "no_avg_px": no_px, "pnl_c": pnl, "maker_fee_c": 2, "run_net_c": 0}
with open(f"{out7}/mm15min_v3_markets.csv", "w", newline="") as fh:            # V3's file: no "live" column
    w = csv.DictWriter(fh, [c_ for c_ in cols4 if c_ != "live"], extrasaction="ignore"); w.writeheader()
    w.writerow(mrow("KXGOLD15M-26OCT201300-00", "GOLD", "commodity", "fav", "no", 10, 88, 0, "", -880))
with open(f"{out7}/mm15min_v4_markets.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, cols4); w.writeheader()
    for hhmm in ("1315-15", "1330-30", "1345-45"):
        w.writerow(mrow(f"KXGOLD15M-26OCT20{hhmm}", "GOLD", "commodity", "fav", "yes", 10, 88, 0, "", 120))
    w.writerow(mrow("KXBTC15M-26OCT201345-45", "BTC", "crypto", "lean", "no", 10, 80, 0, "", -800))
buf = io.StringIO()
with contextlib.redirect_stdout(buf): g7["show_history"](3)
h = buf.getvalue(); print(h)
assert "THE LAST 3 CYCLES in mm15min_v4_markets.csv, mm15min_v3_markets.csv (closing Oct 20 13:15-13:45 ET)" in h
assert re.search(r"Favorite Commodities\s+85-98c whole market\s+3 markets,\s+30 contracts, 100\.0% won, net\s+\+\$3\.60 \(\+12\.00c/contract\) · if maker fees \+\$3\.54 · streak W3\s+· markets: WWW", h), h
assert re.search(r"Lean Crypto\s+75-84c first 5 min\s+1 markets,\s+10 contracts,\s+0\.0% won, net\s+-\$8\.00 .* streak L1\s+· markets: L", h), h
assert re.search(r"Favorite Crypto\s+85-98c first 5 min\s+0 markets", h), h
buf = io.StringIO()
with contextlib.redirect_stdout(buf): g7["show_history"](4)
assert re.search(r"Favorite Commodities\s+85-98c whole market\s+4 markets,\s+40 contracts,\s+75\.0% won, net\s+-\$5\.20 .* streak W3\s+· markets: LWWW", buf.getvalue()), buf.getvalue()
for f in glob.glob(f"{out7}/mm15min_v*_markets.csv"): os.remove(f)
buf = io.StringIO()
with contextlib.redirect_stdout(buf): g7["show_history"](5)
assert "no results yet" in buf.getvalue()
try: g7["show_history"](0); raise AssertionError("did not stop")
except SystemExit as e: assert "HISTORY_CYCLES must be" in str(e)
compile(open(f"{SP}/history_cell.py").read(), "history_cell", "exec") if os.path.exists(f"{SP}/history_cell.py") else None
print("HISTORY CHECKS PASSED")
for d in glob.glob(f"{SP}/run_mm4_*"): shutil.rmtree(d, ignore_errors=True)
print("ALL CHECKS PASSED")
