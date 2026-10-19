# V38b V3 tests: (1) dry run end to end, (2) LIVE path against a fake Kalshi (nothing real is reached),
# (3) round-based daily stop, (4) switch checks, (5) fill parsing.
import os, sys, time, threading, datetime as dt, io, contextlib, _thread, glob, shutil, json, re
from cryptography.hazmat.primitives.asymmetric import rsa
SP = os.path.dirname(os.path.abspath(__file__))
SRC = os.environ.get("V38B3_SRC", f"{SP}/commod15min_v38b_v3.py")
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)      # throwaway key: real signing, no account

def load(out, **over):
    shutil.rmtree(out, ignore_errors=True); os.makedirs(out, exist_ok=True)
    code = open(SRC).read().replace("/content/drive/MyDrive/", out + "/")
    g = {"__name__": "botmod"}; exec(compile(code, "v38b3", "exec"), g)
    g.update(MARKETS_REFRESH_SEC=0.3, LOOP_SEC=0.1, SETTLE_RETRY_SEC=0.2, STATUS_EVERY_SEC=2, LATENCY_PROBE_SEC=0.5)
    g.update(over)                         # what the SWITCHES cell does: set the names, then run main()
    return g

class Feed:
    def __init__(s): s.p = {}; s.on_tick = None; s.ready = threading.Event(); s.ready.set()
    def get(s, t): return s.p.get(t)
    def track(s, ts): pass
    def stop(s): pass

class Resp:
    def __init__(s, code, js): s.status_code, s._js, s.text = code, js, json.dumps(js)
    def json(s): return s._js

def wire(g, M, secs, result, api_calls, extra_get=None):
    REAL = g["now"]; clock = {"off": 0.0}
    g["now"] = lambda: REAL() + dt.timedelta(seconds=clock["off"])
    T0 = REAL()
    close = {t: T0 + dt.timedelta(seconds=v) for t, v in secs.items()}
    feed = Feed()
    g["make_feed"] = lambda k: feed; g["load_key"] = lambda: KEY
    g["fetch_open"] = lambda sess, k: [{"ticker": t, "close_time": close[t].isoformat()} for t in M.values()
                                       if (close[t] - g["now"]()).total_seconds() > -60]
    g["fetch_market"] = lambda sess, k, t: {"result": result[t] if (close[t] - g["now"]()).total_seconds() < 0 else ""}
    def api_get(sess, k, path, params=None):
        api_calls.append((path, params))
        if extra_get:
            r = extra_get(path, params)
            if r is not None: return r
        if path == "/exchange/status": return {"trading_active": True}
        if path == "/portfolio/balance": return {"balance": 50000, "balance_breakdown": [{"exchange_index": 2, "balance": "500.00"}]}
        if path in ("/portfolio/orders", "/portfolio/positions"): return {"orders": [], "market_positions": []}
        return None
    g["api_get"] = api_get
    return clock, feed

def run_bot(g, script):
    buf = io.StringIO()
    threading.Thread(target=script, daemon=True).start()
    with contextlib.redirect_stdout(buf):
        try: g["main"]()
        except KeyboardInterrupt: pass
    return buf.getvalue()

def tick(feed, t, yb, ya): feed.p[t] = (yb, ya); feed.on_tick(t, yb, ya); time.sleep(0.05)

# ---------------- 1) DRY RUN (default switches + one skipped market) ----------------
out = f"{SP}/run_v38b3_dry"
g = load(out, SKIP_MARKETS=["sol"], MAX_WRITES_PER_SEC=1)
assert g["DRY_RUN"] is True and g["TRADE_CRYPTO"] and g["TRADE_COMMODITIES"] and g["DAILY_LOSS_LIMIT_USD"] == 50
M = {"B": "KXBTC15M-26OCT011215-15", "E": "KXETH15M-26OCT011215-15", "S": "KXSOL15M-26OCT011215-15",
     "G": "KXGOLD15M-26OCT011215-15", "X": "KXXRP15M-26OCT011215-15"}
result = {M["B"]: "no", M["E"]: "yes", M["S"]: "yes", M["G"]: "yes", M["X"]: "yes"}
api = []
clock, feed = wire(g, M, {t: 885 for t in M.values()}, result, api)
sent = []
g["requests"].Session.post = lambda *a, **k: sent.append(("POST", a, k))      # nothing may be sent in a dry run
def script1():
    while feed.on_tick is None: time.sleep(0.05)
    time.sleep(0.8)
    tick(feed, M["B"], 58, 59)          # BTC YES ask 59 -> filled
    tick(feed, M["S"], 60, 61)          # SOL is skipped -> nothing
    tick(feed, M["E"], 20, 22)          # ETH NO ask 80 -> waits for the 2/s limit...
    tick(feed, M["G"], 84, 85)          # GOLD
    feed.p[M["E"]] = (15, 16)           # ... ETH NO ask jumps to 85 > 82 -> killed, retried at 85 -> filled
    time.sleep(2.5)
    tick(feed, M["X"], 52, 60)          # spread 8c -> no buy
    open(f"{out}/STOP_V38B.txt", "w").write("stop")
    time.sleep(6.0)
    tick(feed, M["X"], 70, 71)          # kill switch on -> no buy
    time.sleep(1.0)
    clock["off"] = 900; time.sleep(3.5)
    _thread.interrupt_main()
txt = run_bot(g, script1); print(txt[-3000:])
fee = g["taker_fee_c"]; usd = g["_usd"]
assert not sent, f"dry run SENT something: {sent[:1]}"
assert "DRY RUN" in txt and "LIVE" not in txt.split("[status]")[0].replace("LIVE trading", "")
assert "crypto: ON · commodities: ON · skipped: SOL" in txt
assert "trading: BTC, ETH, XRP, DOGE, GOLD, SILVER, WTI, COPPER, PLATINUM, PALLADIUM" in txt
assert "SOL buy" not in txt and "SOL (crypto)" not in txt
assert "ETH buy 10 NO · ask seen 80c, limit 82c · killed (simulated: ask 85c > limit 82c)" in txt
assert "ETH buy 10 NO · ask seen 85c, limit 87c · FILLED (simulated: not sent) @ 85c" in txt
assert "BUY  10x YES @ 59c  BTC (crypto)" in txt and "BUY  10x YES @ 85c  GOLD (commodity)" in txt
assert "XRP (crypto)" not in txt and "KILL SWITCH" in txt
assert "[account] markets already held: 0" in txt
b = 10 * (0 - 59) - fee(10, 59); e = 10 * (0 - 85) - fee(10, 85); gd = 10 * (100 - 85) - fee(10, 85)
last = txt[txt.rindex("[status]"):]
assert f"crypto   :   2 markets,   0 won (0%), avg buy 72.0c, net {usd(b + e)}" in last, last
assert f"commodity:   1 markets,   1 won (100%), avg buy 85.0c, net {usd(gd)}" in last
assert "quick orders built, not sent: 4 · filled 3, killed 1" in last, last
assert f"today (UTC) net {usd(b + e + gd)}, lowest " in last
assert "DAILY LOSS STOP" not in txt                         # -$13.84 is well above -$50
assert sorted(os.listdir(out)) == ["STOP_V38B.txt"], os.listdir(out)              # no files written by the bot
assert not any(p == "/portfolio/intra_exchange_instance_transfer" for p, _ in api)
print("DRY RUN CHECKS PASSED")

# ---------------- 2) LIVE path against a fake Kalshi at the production address ----------------
out2 = f"{SP}/run_v38b3_live"
g2 = load(out2, DRY_RUN=False, KEY_ID="test-key-id", LIVE_COUNTDOWN_SEC=0, DAILY_LOSS_LIMIT_USD=5)
M2 = {"B": "KXBTC15M-26OCT011215-15", "E": "KXETH15M-26OCT011215-15", "S": "KXSOL15M-26OCT011215-15",
      "X": "KXXRP15M-26OCT011215-15", "G": "KXGOLD15M-26OCT011215-15", "D": "KXDOGE15M-26OCT011215-15",
      "V": "KXSILVER15M-26OCT011215-15", "B2": "KXBTC15M-26OCT011230-30"}
res2 = {M2["B"]: "yes", M2["E"]: "yes", M2["S"]: "yes", M2["X"]: "yes", M2["G"]: "yes", M2["D"]: "no", M2["V"]: "yes", M2["B2"]: "yes"}
secs2 = {t: 885 for t in M2.values()}; secs2[M2["B2"]] = 1785
posted, orders_on_kalshi, bal = [], {}, {"s0": 9000, "s2": 1000}
def get2(path, params):
    if path == "/portfolio/balance":
        return {"balance": bal["s0"] + bal["s2"], "balance_breakdown": [{"exchange_index": 0, "balance": f"{bal['s0'] / 100:.2f}"},
                                                                       {"exchange_index": 2, "balance": f"{bal['s2'] / 100:.2f}"}]}
    if path == "/portfolio/positions":
        return {"market_positions": [{"ticker": M2["V"], "position_fp": "10.00"}, {"ticker": "KXOLD-1", "position_fp": "0.00"}]}
    if path == "/portfolio/orders" and params and params.get("ticker"):
        return {"orders": [o for o in orders_on_kalshi.values() if o["ticker"] == params["ticker"]]}
api2 = []
clock2, feed2 = wire(g2, M2, secs2, res2, api2, get2)
doge_calls = []
def fake_post(self, url, headers=None, json=None, timeout=None):
    posted.append((url, json, headers))
    if url.endswith("/portfolio/intra_exchange_instance_transfer"):
        bal["s2"] += json["amount"] // 100; bal["s0"] -= json["amount"] // 100
        return Resp(200, {})
    t = json["ticker"]
    if t == M2["B"]: return Resp(201, {"order": {"order_id": "o1", "fill_count": "10.00", "average_fill_price": "0.5900", "average_fee_paid": "0.0170"}})
    if t == M2["E"]: return Resp(201, {"order": {"order_id": "o2", "fill_count": "10.00", "average_fill_price": "0.2000"}})
    if t == M2["S"]:                                        # placed, but the answer is lost: must be found, not re-sent
        orders_on_kalshi[json["client_order_id"]] = {"ticker": t, "client_order_id": json["client_order_id"], "status": "executed",
                                                     "fill_count_fp": "10.00", "average_fill_price_dollars": "0.6100"}
        raise g2["requests"].ConnectionError("read timed out")
    if t == M2["X"]: raise g2["requests"].ConnectionError("read timed out")          # never reached Kalshi
    if t == M2["G"]: return Resp(400, {"error": {"code": "insufficient_balance", "message": "insufficient balance"}})
    if t == M2["D"]:
        doge_calls.append(json)
        if len(doge_calls) == 1: return Resp(201, {"order": {"order_id": "o5", "status": "canceled", "fill_count": "0.00"}})
        return Resp(201, {"order": {"order_id": "o6", "status": "executed", "fill_count": "10.00", "average_fill_price": "0.3700"}})
    if t == M2["B2"]: raise AssertionError("bought after the daily loss stop")
    raise AssertionError(f"unexpected order {json}")
g2["requests"].Session.post = fake_post
def script2():
    while feed2.on_tick is None: time.sleep(0.05)
    time.sleep(0.8)
    tick(feed2, M2["B"], 58, 59)          # YES 59
    tick(feed2, M2["E"], 19, 20)          # NO ask 81 -> fills at YES 20 = NO 80
    tick(feed2, M2["S"], 60, 61)          # timeout -> looked up -> filled 61
    tick(feed2, M2["X"], 60, 61)          # timeout, not on Kalshi -> UNKNOWN, market skipped
    tick(feed2, M2["G"], 70, 71)          # rejected -> market skipped
    tick(feed2, M2["V"], 70, 71)          # already held on the account -> never bought
    tick(feed2, M2["D"], 62, 64)          # NO ask 38? no: YES ask 64 -> killed, then filled (YES 0.37? read as NO 63? -> 63/37 rule)
    time.sleep(4.0)
    tick(feed2, M2["X"], 60, 62)          # no retries in an UNKNOWN market
    tick(feed2, M2["G"], 70, 72)
    tick(feed2, M2["D"], 62, 64)          # filled already
    time.sleep(1.0)
    clock2["off"] = 900; time.sleep(4.0)  # round 1 settles: BTC/ETH(no side)/SOL/DOGE -> net below -$5 -> stop
    tick(feed2, M2["B2"], 60, 61)         # round 2 opening: no buy after the stop
    time.sleep(1.0)
    _thread.interrupt_main()
txt2 = run_bot(g2, script2); print(txt2[-4500:])
orders = [p for p in posted if p[0].endswith("/portfolio/events/orders")]
assert all(u == "https://api.elections.kalshi.com/trade-api/v2/portfolio/events/orders" for u, _, _ in orders)
assert all(h["KALSHI-ACCESS-KEY"] == "test-key-id" and h["KALSHI-ACCESS-SIGNATURE"] for _, _, h in posted)
assert "LIVE: REAL ORDERS WITH REAL MONEY" in txt2
tk = [b["ticker"] for _, b, _ in orders]
assert tk.count(M2["S"]) == 1 and tk.count(M2["X"]) == 1 and tk.count(M2["G"]) == 1, tk     # never re-sent
assert tk.count(M2["D"]) == 2 and M2["V"] not in tk and M2["B2"] not in tk, tk
tr = [b for u, b, _ in posted if u.endswith("intra_exchange_instance_transfer")]
assert tr == [{"source": "event_contract", "destination": "event_contract", "amount": 900000,
               "source_exchange_shard": 0, "destination_exchange_shard": 2}], tr
assert "[funds] moved $90.00 shard 0 -> shard 2: HTTP 200, shard 2 now $100.00" in txt2
assert "[account] markets already held: 1 (KXSILVER15M-26OCT011215-15) — not bought again" in txt2
assert "Kalshi's raw answer HTTP 201" in txt2
assert "BUY  10x YES @ 59c  BTC" in txt2 and "BUY  10x NO @ 80c  ETH" in txt2 and "BUY  10x YES @ 61c  SOL" in txt2
assert "SOL no clear answer (HTTP 0) — looking the order up by its id" in txt2
assert "XRP buy 10 YES · ask seen 61c, limit 63c · UNKNOWN (not found on Kalshi)" in txt2
assert "GOLD buy 10 YES · ask seen 71c, limit 73c · rejected HTTP 400" in txt2
assert "DOGE buy 10 YES · ask seen 64c, limit 66c · killed (not enough at or below the limit)" in txt2
assert "BUY  10x YES @ 63c  DOGE" in txt2                       # 0.37 read as 37 or 63: only 63 fits a YES buy near 64
fee = g2["taker_fee_c"]; usd = g2["_usd"]
net = (10 * 41 - fee(10, 59)) + (-800 - fee(10, 80)) + (10 * 39 - fee(10, 61)) + (-630 - fee(10, 63))
assert f"today (UTC) net {usd(net)}" in txt2 and net < -500
stop_at = txt2.index("!!! DAILY LOSS STOP (round closing")
assert stop_at > txt2.rindex("SETTLED ")                          # checked once the whole round has settled
assert "DOGE" in txt2[:stop_at] and txt2[:stop_at].count("SETTLED ") == 4
assert "DAILY LOSS STOP HIT, no new buys today" in txt2[txt2.rindex("[status]"):]
assert "unknown 1" in txt2 and "rejected 1" in txt2
print("LIVE PATH CHECKS PASSED")

# ---------------- 3) round-based stop: a dip inside a round that ends above the limit does not stop ----------------
out3 = f"{SP}/run_v38b3_round"
g3 = load(out3, DAILY_LOSS_LIMIT_USD=5)
M3 = {"B": "KXBTC15M-26OCT011215-15", "E": "KXETH15M-26OCT011215-15", "G": "KXGOLD15M-26OCT011215-15"}
res3 = {M3["B"]: "no", M3["E"]: "no", M3["G"]: "no"}          # BTC YES loses -$5.9, ETH NO wins, GOLD NO wins
clock3, feed3 = wire(g3, M3, {t: 885 for t in M3.values()}, res3, [])
g3["requests"].Session.post = lambda *a, **k: (_ for _ in ()).throw(AssertionError("sent"))
def script3():
    while feed3.on_tick is None: time.sleep(0.05)
    time.sleep(0.8)
    tick(feed3, M3["B"], 58, 59); tick(feed3, M3["E"], 40, 41); tick(feed3, M3["G"], 40, 41)
    time.sleep(1.0)
    clock3["off"] = 900; time.sleep(3.0)
    _thread.interrupt_main()
txt3 = run_bot(g3, script3); print(txt3[-1200:])
fee = g3["taker_fee_c"]
assert "today -$6.0" in txt3 or "today -$5.9" in txt3            # the day dipped below -$5 inside the round ...
assert "DAILY LOSS STOP" not in txt3                              # ... but the round ended above it: no stop
assert "lowest -$" in txt3
print("ROUND STOP CHECKS PASSED")

# ---------------- 4) switch checks ----------------
for over, msg in (({"DRY_RUN": "False"}, "DRY_RUN must be True or False without quotes"),
                  ({"TRADE_CRYPTO": "no"}, "TRADE_CRYPTO must be True or False"),
                  ({"SKIP_MARKETS": ["GLD"]}, "SKIP_MARKETS has unknown names ['GLD']"),
                  ({"SKIP_MARKETS": "WTI"}, "SKIP_MARKETS must be a list"),
                  ({"TRADE_CRYPTO": False, "TRADE_COMMODITIES": False}, "nothing to trade"),
                  ({"DRY_RUN": False}, "live trading needs KEY_ID"),
                  ({"CONTRACTS": 0}, "CONTRACTS must be"), ({"DAILY_LOSS_LIMIT_USD": -5}, "DAILY_LOSS_LIMIT_USD must be")):
    g4 = load(f"{SP}/run_v38b3_guard", **over)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf): g4["main"]()
        raise AssertionError(f"did not stop: {over}")
    except SystemExit as e: assert msg in str(e), (over, e)
g4 = load(f"{SP}/run_v38b3_guard", TRADE_CRYPTO=False)
assert g4["trading_markets"]() == ["GOLD", "SILVER", "WTI", "COPPER", "PLATINUM", "PALLADIUM"]
g4 = load(f"{SP}/run_v38b3_guard", TRADE_COMMODITIES=False, SKIP_MARKETS=["DOGE", "xrp"])
assert g4["trading_markets"]() == ["BTC", "ETH", "SOL"]
g5 = {"__name__": "x", "DRY_RUN": False, "SKIP_MARKETS": ["WTI"]}                 # a SWITCHES cell run before the code cell
exec(compile(open(SRC).read(), "v38b3", "exec"), g5)
assert g5["DRY_RUN"] is False and g5["SKIP_MARKETS"] == ["WTI"] and g5["TRADE_CRYPTO"] is True
g6 = {"__name__": "x"}; exec(compile(open(SRC).read(), "v38b3", "exec"), g6)
assert g6["DRY_RUN"] is True                                                      # no SWITCHES cell: dry run
print("SWITCH CHECKS PASSED")

# ---------------- 5) fill parsing ----------------
rf = g6["read_fill"]
assert rf({"fill_count": "10.00", "average_fill_price": "0.5900"}, True, 61, 59) == (10.0, 59)
assert rf({"fill_count": "10.00", "average_fill_price": "0.2000"}, False, 83, 81) == (10.0, 80)
assert rf({"fill_count": "10.00", "average_fill_price": "0.8000"}, False, 83, 81) == (10.0, 80)   # if Kalshi reports our side
assert rf({"fill_count_fp": "10.00", "average_fill_price_dollars": "0.6100"}, True, 63, 61) == (10.0, 61)
assert rf({"fill_count": 10, "taker_fill_cost": 610}, True, 63, 61) == (10.0, 61)
assert rf({"fill_count": 10, "taker_fill_cost_dollars": "6.10"}, True, 63, 61) == (10.0, 61)
assert rf({"status": "canceled", "fill_count": "0.00"}, True, 61, 59) == (0.0, None)
assert rf({}, True, 61, 59) == (0.0, None)
assert rf({"fill_count": "10.00", "average_fill_price": "0.4900"}, False, 52, 50) == (10.0, 51)   # tie: NO = 100 - YES
b = g6["fok_body"]("T", False, 82, 10, "id")
assert b["side"] == "ask" and b["price"] == "0.1800" and b["time_in_force"] == "fill_or_kill" and b["count"] == "10.00"
print("FILL PARSING CHECKS PASSED")
for d in glob.glob(f"{SP}/run_v38b3_*"): shutil.rmtree(d, ignore_errors=True)
print("ALL CHECKS PASSED")
