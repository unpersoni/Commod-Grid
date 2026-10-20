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
_fm, boom = g["fetch_market"], {"n": 0}
def fm(sess, k, t):                                  # one bad answer while settling: the bot must keep going
    if boom["n"] == 0: boom["n"] = 1; raise ValueError("bad json")
    return _fm(sess, k, t)
g["fetch_market"] = fm
sent = []
g["requests"].Session.post = lambda *a, **k: sent.append(("POST", a, k))      # nothing may be sent in a dry run
def script1():
    while feed.on_tick is None: time.sleep(0.05)
    time.sleep(0.8)
    tick(feed, M["B"], 58, 59)          # BTC YES ask 59 -> filled
    tick(feed, M["S"], 60, 61)          # SOL is switched off -> still followed in dry run
    tick(feed, M["E"], 20, 22)          # ETH NO ask 80 -> waits for the 2/s limit...
    tick(feed, M["G"], 84, 85)          # GOLD
    feed.p[M["E"]] = (15, 16)           # ... ETH NO ask jumps to 85 > 82 -> killed, retried at 85 -> filled
    time.sleep(2.5)
    tick(feed, M["X"], 52, 60)          # spread 8c -> no buy
    open(f"{out}/STOP_V38B.txt", "w").write("stop")
    time.sleep(6.0)
    tick(feed, M["X"], 70, 71)          # kill switch only stops live buys: dry run keeps following
    time.sleep(1.0)
    clock["off"] = 900; time.sleep(3.5)
    _thread.interrupt_main()
txt = run_bot(g, script1); print(txt[-3000:])
fee = g["taker_fee_c"]; usd = g["_usd"]
assert not sent, f"dry run SENT something: {sent[:1]}"
assert "DRY RUN" in txt and "🟢 LIVE" not in txt and "\x1b[" not in txt
assert "crypto: ON · commodities: ON · skipped: SOL" in txt
assert "⚪ DRY RUN: all markets · switched on (would be live with DRY_RUN = False): BTC, ETH, XRP, DOGE, GOLD, SILVER, WTI, COPPER, PLATINUM, PALLADIUM" in txt
assert "⚪ DRY RUN  BUY  10x YES @ 61c  SOL (crypto)" in txt
assert "ETH buy 10 NO · ask seen 80c, limit 82c · killed (simulated: ask 85c > limit 82c)" in txt
assert "ETH buy 10 NO · ask seen 85c, limit 87c · FILLED (simulated: not sent) @ 85c" in txt
assert "BUY  10x YES @ 59c  BTC (crypto)" in txt and "BUY  10x YES @ 85c  GOLD (commodity)" in txt
assert "BUY  10x YES @ 71c  XRP (crypto)" in txt and "KILL SWITCH" in txt
assert "XRP buy 10 YES · ask seen 60c" not in txt                       # the 8c spread: no buy
assert "[account] markets already held: 0" in txt
b = 10 * (0 - 59) - fee(10, 59); e = 10 * (0 - 85) - fee(10, 85); gd = 10 * (100 - 85) - fee(10, 85)
so = 10 * (100 - 61) - fee(10, 61); x = 10 * (100 - 71) - fee(10, 71)
last = txt[txt.rindex("[status]"):]
assert f"  ⚪ DRY RUN  CRYPTO      4 markets,   2 won (50%), avg buy 69.0c, net {usd(b + e + so + x)}" in last, last
assert f"  ⚪ DRY RUN  COMMODITY   1 markets,   1 won (100%), avg buy 85.0c, net {usd(gd)}" in last
assert "🟢 LIVE" not in last
assert re.search(r"BTC\s+1 markets,\s+0 won \(\s*0%\), net\s+-\$6\.07 · streak L1\s+· last 10: L\n", last), last
assert re.search(r"SOL\s+1 markets,\s+1 won \(100%\), net\s+\+\$3\.73 · streak W1\s+· last 10: W\n", last), last
assert re.search(r"DOGE\s+0 markets,\s+0 won \(  - \), net\s+\+\$0\.00 · streak -\s+· last 10: -\n", last), last
assert "PALLADIUM" in last and "bought 56-65c:   2 markets,   1 won (50%)" in last
assert "quick orders built, not sent: 6 · filled 5, killed 1" in last, last
assert f"today (UTC) net of the switched-on markets {usd(b + e + gd + x)}, lowest " in last          # SOL is switched off
assert f"all markets {usd(b + e + gd + x + so)}" in last
assert "DAILY LOSS STOP" not in txt                         # -$13.84 is well above -$50
assert sorted(os.listdir(out)) == ["STOP_V38B.txt"], os.listdir(out)              # no files written by the bot
assert not any(p == "/portfolio/intra_exchange_instance_transfer" for p, _ in api)
assert "[error] ValueError: bad json — the bot keeps running (error #1)" in txt and boom["n"] == 1
print("DRY RUN CHECKS PASSED")

# ---------------- 2) LIVE path against a fake Kalshi at the production address ----------------
out2 = f"{SP}/run_v38b3_live"
g2 = load(out2, DRY_RUN=False, KEY_ID="test-key-id", LIVE_COUNTDOWN_SEC=0, DAILY_LOSS_LIMIT_USD=5, SKIP_MARKETS=["WTI"])
M2 = {"B": "KXBTC15M-26OCT011215-15", "E": "KXETH15M-26OCT011215-15", "S": "KXSOL15M-26OCT011215-15",
      "X": "KXXRP15M-26OCT011215-15", "G": "KXGOLD15M-26OCT011215-15", "D": "KXDOGE15M-26OCT011215-15",
      "V": "KXSILVER15M-26OCT011215-15", "B2": "KXBTC15M-26OCT011230-30", "W": "KXWTI15M-26OCT011215-15"}
res2 = {M2["B"]: "yes", M2["E"]: "yes", M2["S"]: "yes", M2["X"]: "yes", M2["G"]: "yes", M2["D"]: "no", M2["V"]: "yes", M2["B2"]: "yes", M2["W"]: "no"}
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
    tick(feed2, M2["W"], 70, 71)          # WTI switched off -> dry run only, never sent
    tick(feed2, M2["D"], 62, 64)          # NO ask 38? no: YES ask 64 -> killed, then filled (YES 0.37? read as NO 63? -> 63/37 rule)
    time.sleep(4.0)
    tick(feed2, M2["X"], 60, 62)          # no retries in an UNKNOWN market
    tick(feed2, M2["G"], 70, 72)
    tick(feed2, M2["D"], 62, 64)          # filled already
    time.sleep(1.0)
    clock2["off"] = 900; time.sleep(4.0)  # round 1 settles: BTC/ETH(no side)/SOL/DOGE -> net below -$5 -> stop
    tick(feed2, M2["B2"], 60, 61)         # round 2 opening: no live buy after the stop (dry run only)
    time.sleep(1.0)
    _thread.interrupt_main()
txt2 = run_bot(g2, script2); print(txt2[-4500:])
orders = [p for p in posted if p[0].endswith("/portfolio/events/orders")]
assert all(u == "https://api.elections.kalshi.com/trade-api/v2/portfolio/events/orders" for u, _, _ in orders)
assert all(h["KALSHI-ACCESS-KEY"] == "test-key-id" and h["KALSHI-ACCESS-SIGNATURE"] for _, _, h in posted)
assert "LIVE: REAL ORDERS WITH REAL MONEY" in txt2
tk = [b["ticker"] for _, b, _ in orders]
assert tk.count(M2["S"]) == 1 and tk.count(M2["X"]) == 1 and tk.count(M2["G"]) == 1, tk     # never re-sent
assert tk.count(M2["D"]) == 2 and M2["V"] not in tk and M2["B2"] not in tk and M2["W"] not in tk, tk
assert "\x1b[1;32m" in txt2                                              # live lines in green
assert "🟢 LIVE    (real orders): BTC, ETH, SOL, XRP, DOGE, GOLD, SILVER, COPPER, PLATINUM, PALLADIUM" in txt2
assert "⚪ DRY RUN (followed, nothing sent): WTI" in txt2
assert "⚪ DRY RUN  BUY  10x YES @ 71c  WTI (commodity)" in txt2 and "⚪ DRY RUN [order] 1215-15 WTI buy 10 YES" in txt2
assert "🟢 LIVE  BUY  10x YES @ 59c  BTC" in txt2 and "🟢 LIVE [order] 1215-15 BTC buy 10 YES" in txt2
assert "⚪ DRY RUN  BUY  10x YES @ 61c  BTC (crypto) 1230-30" in txt2                  # after the stop: dry run
assert "⚪ DRY RUN  SETTLED NO  WTI (commodity)" in txt2
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
wti = -710 - fee(10, 71)
last2 = txt2[txt2.rindex("[status]"):]
assert f"today (UTC) live net {usd(net)}" in last2 and net < -500
assert f"today (UTC) dry-run net {usd(wti)} (not real money)" in last2                 # WTI is not in the live stop
stop_at = txt2.index("!!! DAILY LOSS STOP (round closing")
assert stop_at > txt2.rindex("🟢 LIVE  SETTLED ")                 # checked once the whole round has settled
assert "DOGE" in txt2[:stop_at] and txt2[:stop_at].count("🟢 LIVE  SETTLED ") == 4
assert "DAILY LOSS STOP HIT, no new live buys today" in last2
assert "🟢 LIVE  CRYPTO      4 markets,   2 won (50%)" in last2 and "⚪ DRY RUN  COMMODITY   1 markets,   0 won (0%)" in last2
assert "⚪ DRY RUN  CRYPTO      0 markets" in last2              # after the stop the crypto markets are followed in dry run
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
assert "today switched-on -$6.0" in txt3                         # the day dipped below -$5 inside the round ...
assert "DAILY LOSS STOP" not in txt3                              # ... but the round ended above it: no stop
assert "lowest -$" in txt3
print("ROUND STOP CHECKS PASSED")

# ---------------- 4) switch checks ----------------
for over, msg in (({"DRY_RUN": "False"}, "DRY_RUN must be True or False without quotes"),
                  ({"TRADE_CRYPTO": "no"}, "TRADE_CRYPTO must be True or False"),
                  ({"SKIP_MARKETS": ["GLD"]}, "SKIP_MARKETS has unknown names ['GLD']"),
                  ({"SKIP_MARKETS": "WTI"}, "SKIP_MARKETS must be a list"),
                  ({"DRY_RUN": False, "KEY_ID": "k", "TRADE_CRYPTO": False, "TRADE_COMMODITIES": False}, "every market is switched off"),
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
g6["COLORS"] = False
assert g6["paint"]("x", True) == "x" and g6["streak"](list("WLLW")) == "W1" and g6["streak"](list("WLLL")) == "L3"
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
bn = g6["band_name"]
assert [bn(x) for x in (54, 56, 65, 66, 80, 81, 95, 96, 97)] == ["56-65c", "56-65c", "56-65c", "66-70c", "71-80c",
                                                                "81-95c", "81-95c", "81-95c", "81-95c"]
print("FILL PARSING CHECKS PASSED")
# ---------------- 6) start-up history from Kalshi (fake history) ----------------
g7 = load(f"{SP}/run_v38b3_hist")
T = int(time.time() // 900) * 900
CL = [T - 900 * i for i in range(6)][::-1]                 # 6 settled windows, oldest first: the last 5 are used
iso = lambda ts: dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
RES = {"BTC": ["no", "yes", "no", "yes", "yes", "yes"], "GOLD": ["no"] + ["yes"] * 5, "ETH": ["no"] * 6,
       "SOL": ["yes"] * 6, "XRP": ["yes"] * 6, "DOGE": ["yes"] * 6}
def candle(a_, s0):
    if a_ == "BTC": return [{"end_period_ts": s0 + 60, "yes_bid": {"open": 60, "close": 61}, "yes_ask": {"open": 62, "close": 63}}]
    if a_ == "GOLD": return [{"end_period_ts": s0 + 60, "yes_bid": {"open_dollars": "0.8400"}, "yes_ask": {"open_dollars": "0.8500"}}]
    if a_ == "ETH": return [{"end_period_ts": s0 + 60, "yes_bid": {"open": 40, "close": 55}, "yes_ask": {"open": 50, "close": 57}}]
    if a_ == "SOL": return []
    if a_ == "XRP": return [{"end_period_ts": s0 + 60, "yes_bid": {"open": 50}, "yes_ask": {"open": 51}}]
    if a_ == "DOGE": return [{"end_period_ts": s0 + 60, "odd_field": 1}]
calls7 = []
def api7(sess, k, path, params=None):
    calls7.append((path, dict(params or {})))
    if path == "/markets":
        a_ = g7["asset"](params["series_ticker"])
        if a_ not in RES: return {"markets": []}
        assert params["status"] == "settled" and params["min_close_ts"] < CL[0]
        return {"markets": [{"ticker": f"{params['series_ticker']}-W{c}", "close_time": iso(c), "open_time": iso(c - 900),
                             "result": RES[a_][i]} for i, c in enumerate(CL)]}
    if path.endswith("/candlesticks"):
        assert params["period_interval"] == 1 and params["end_ts"] - params["start_ts"] == 60
        return {"candlesticks": candle(g7["asset"](path.split("/")[2]), params["start_ts"])}
    return None
g7["api_get"] = api7
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    g7["history_report"](None, None, lambda m: print(f"[t] {m}"), {"BTC"}, 5)
h = buf.getvalue(); print(h)
fee = g7["taker_fee_c"]; usd = g7["_usd"]
assert f"THE LAST 5 CYCLES (closing {dt.datetime.fromtimestamp(CL[1], dt.timezone.utc):%H:%M}-" in h
btc = 4 * (10 * 38 - fee(10, 62)) + (-620 - fee(10, 62)); eth = 5 * (-570 - fee(10, 57)); gold = 5 * (150 - fee(10, 85))
assert f"CRYPTO     10 markets,   4 won (40%), avg buy 59.5c, net {usd(btc + eth)}" in h, h
assert f"COMMODITY   5 markets,   5 won (100%), avg buy 85.0c, net {usd(gold)}" in h
assert re.search(r"BTC\s+5 markets,\s+4 won \( 80%\), net\s+\+\$\d+\.\d\d · streak W3\s+· last 5: WLWWW   ★ switched on", h), h
assert re.search(r"ETH\s+5 markets,\s+0 won \(  0%\).* streak L5\s+· last 5: LLLLL\n", h)          # bought at 57c on the end-of-minute quote
assert "bought 81-95c:   5 markets,   5 won (100%)" in h
assert "no buy (no side at 56-95c with a spread of 5c or less in the first minute): 10 markets · no price history: 5" in h
assert "could not read the prices in Kalshi's candle" in h and h.count("could not read the prices") == 1
assert all(c[1]["start_ts"] + 900 in CL for c in calls7 if c[0].endswith("/candlesticks"))
assert not any(c[1]["start_ts"] + 900 == CL[0] for c in calls7 if c[0].endswith("/candlesticks"))   # the 6th-oldest window is not fetched
g7["api_get"] = lambda *a, **k: None
buf = io.StringIO()
with contextlib.redirect_stdout(buf): g7["history_report"](None, None, lambda m: print(m), set(), 5)
assert "history could not be read — check the key and the connection" in buf.getvalue()
# the HISTORY cell itself: show_history() with the switches as set (crypto on, commodities off, DOGE skipped)
g7["api_get"] = api7; g7["load_key"] = lambda: KEY
g7.update(TRADE_COMMODITIES=False, SKIP_MARKETS=["DOGE"])
buf = io.StringIO()
with contextlib.redirect_stdout(buf): g7["show_history"](5)
h2 = buf.getvalue()
assert "BTC " in h2 and re.search(r"BTC .*★ switched on", h2) and not re.search(r"GOLD .*★ switched on", h2), h2
try: g7["show_history"](0); raise AssertionError("did not stop")
except SystemExit as e: assert "HISTORY_CYCLES must be" in str(e)
cell = open(f"{SP}/history_cell.py").read() if os.path.exists(f"{SP}/history_cell.py") else None
if cell: compile(cell, "history_cell", "exec")
print("HISTORY CHECKS PASSED")
for d in glob.glob(f"{SP}/run_v38b3_*"): shutil.rmtree(d, ignore_errors=True)
print("ALL CHECKS PASSED")
