# V38b V2 tests: (1) dry run end to end, (2) demo path with a fake Kalshi, (3) mode guards.
import os, sys, time, threading, datetime as dt, io, contextlib, _thread, glob, shutil, json, re
from cryptography.hazmat.primitives.asymmetric import rsa
SP = os.path.dirname(os.path.abspath(__file__))
SRC = os.environ.get("V38B2_SRC", f"{SP}/commod15min_v38b_v2.py")
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)      # throwaway key: real signing, no account

def load(out, **over):
    shutil.rmtree(out, ignore_errors=True); os.makedirs(out, exist_ok=True)
    code = open(SRC).read().replace("/content/drive/MyDrive/", out + "/")
    g = {"__name__": "botmod"}; exec(compile(code, "v38b2", "exec"), g)
    g.update(MARKETS_REFRESH_SEC=0.3, LOOP_SEC=0.1, SETTLE_RETRY_SEC=0.2, STATUS_EVERY_SEC=2, LATENCY_PROBE_SEC=0.5)
    g.update(over)
    return g

class Feed:
    def __init__(s): s.p = {}; s.on_tick = s.on_trade = None; s.ready = threading.Event(); s.ready.set()
    def get(s, t): return s.p.get(t)
    def track(s, ts): pass
    def stop(s): pass

def wire(g, M, secs, result, api_calls):
    REAL = g["now"]; clock = {"off": 0.0}
    g["now"] = lambda: REAL() + dt.timedelta(seconds=clock["off"])
    T0 = REAL()
    close = {t: T0 + dt.timedelta(seconds=v) for t, v in secs.items()}
    feed = Feed()
    g["make_feed"] = lambda k: feed; g["load_key"] = lambda: KEY
    g["fetch_open"] = lambda sess, k: [{"ticker": t, "close_time": close[t].isoformat()} for t in M.values()]
    g["fetch_market"] = lambda sess, k, t: {"result": result[t] if (close[t] - g["now"]()).total_seconds() < 0 else ""}
    def api_get(sess, k, path, params=None):
        api_calls.append(path)
        if path == "/exchange/status": return {"trading_active": True}
        if path == "/portfolio/balance": return {"balance": 50000, "balance_breakdown": [{"exchange_index": 2, "balance": "500.00"}]}
        if path == "/portfolio/orders": return {"orders": []}
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

# ---------------- 1) DRY RUN ----------------
out = f"{SP}/run_v38b2_dry"
g = load(out, MAX_WRITES_PER_SEC=1, DAILY_LOSS_LIMIT_USD=1)
M = {"B": "KXBTC15M-26OCT011215-15", "E": "KXETH15M-26OCT011215-15", "S": "KXSOL15M-26OCT011215-15",
     "G": "KXGOLD15M-26OCT011215-15", "X": "KXXRP15M-26OCT011215-15"}
secs = {t: 885 for t in M.values()}
result = {M["B"]: "no", M["E"]: "yes", M["S"]: "yes", M["G"]: "yes", M["X"]: "yes"}
api = []
clock, feed = wire(g, M, secs, result, api)
sent = []
g["requests"].Session.post = lambda *a, **k: sent.append(("POST", a, k))      # nothing may be sent in a dry run
def tick(t, yb, ya): feed.p[t] = (yb, ya); feed.on_tick(t, yb, ya); time.sleep(0.05)
def script1():
    while feed.on_tick is None: time.sleep(0.05)
    time.sleep(0.8)
    tick(M["B"], 58, 59)          # BTC YES ask 59 -> quick buy (takes the 1 action this second)
    tick(M["E"], 20, 22)          # ETH NO ask 80 -> quick buy, waits ~1 s for the rate limit ...
    feed.p[M["E"]] = (15, 16)     # ... meanwhile NO ask jumps to 85 > limit 82 -> killed
    time.sleep(2.6)               # killed, then retried at once at the new ask (85c, still in the band) -> filled at 85
    tick(M["S"], 60, 61)          # SOL YES ask 61 -> waits for the limit; ask moves to 62 -> filled at 62 (+1c)
    feed.p[M["S"]] = (61, 62)
    time.sleep(1.6)
    tick(M["G"], 84, 85)          # GOLD YES ask 85 -> filled at 85
    time.sleep(1.5)
    tick(M["X"], 52, 60)          # XRP spread 8c -> no buy
    open(f"{out}/STOP_V38B.txt", "w").write("stop")
    time.sleep(6.0)
    tick(M["X"], 70, 71)          # kill switch on -> no buy
    time.sleep(2.5)
    clock["off"] = 900            # everything closed -> settle
    time.sleep(3.0)
    _thread.interrupt_main()
txt = run_bot(g, script1); print(txt[-3500:])
fee = g["taker_fee_c"]; usd = g["_usd"]
assert not sent, f"dry run SENT something: {sent[:1]}"
assert "/exchange/status" in api and "/portfolio/balance" in api and "/portfolio/orders" in api
assert "[account] balance $500.00 (shard 2: $500.00)" in txt
reqs = [json.loads(m) for m in re.findall(r"request built \(NOT sent\): POST /portfolio/events/orders (\{.*\})", txt)]
assert len(reqs) == 5, reqs
btc = [b for b in reqs if b["ticker"] == M["B"]][0]
assert btc == {**btc, "side": "bid", "price": "0.6100", "count": "10.00", "time_in_force": "fill_or_kill",
               "self_trade_prevention_type": "taker_at_cross", "exchange_index": -1} and "post_only" not in btc
eth = [b for b in reqs if b["ticker"] == M["E"]]
assert len(eth) == 2 and all(b["side"] == "ask" for b in eth) and eth[0]["price"] == "0.1800" and eth[1]["price"] == "0.1300", eth
assert "ETH buy 10 NO · ask seen 80c, limit 82c · killed (simulated: ask 85c > limit 82c)" in txt
assert "ETH buy 10 NO · ask seen 85c, limit 87c · FILLED (simulated: not sent) @ 85c (slippage +0c)" in txt
assert "SOL buy 10 YES · ask seen 61c, limit 63c · FILLED (simulated: not sent) @ 62c (slippage +1c)" in txt
assert "BUY  10x NO @ 85c  ETH (crypto)" in txt and "BUY  10x YES @ 62c  SOL (crypto)" in txt and "BUY  10x YES @ 85c  GOLD (commodity)" in txt
assert "XRP (crypto)" not in txt and "KILL SWITCH" in txt
assert "rate wait 9" in txt or "rate wait 1" in txt or "rate wait 8" in txt         # orders waited for the 1/s limit
b = 10 * (0 - 59) - fee(10, 59); e = 10 * (0 - 85) - fee(10, 85); s = 10 * (100 - 62) - fee(10, 62); gd = 10 * (100 - 85) - fee(10, 85)
last = txt[txt.rindex("[status]"):]
assert f"crypto   :   3 markets,   1 won (33%), avg buy 68.7c, net {usd(b + e + s)}" in last, last
assert f"commodity:   1 markets,   1 won (100%), avg buy 85.0c, net {usd(gd)}" in last
assert "quick orders built, not sent: 5 · filled 4, killed 1, rejected 0, errors 0 · avg slippage +0.25c" in last, last
assert "busiest second 1 (limit 1/s)" in last and "decision -> request ready" in last and "Kalshi round trip" in last
assert "[safety] DAILY LOSS LIMIT would stop new buys now" in txt and "dry run: the limit would have stopped new buys at" in last
assert "KILL SWITCH ON" in last
assert sorted(os.listdir(out)) == ["STOP_V38B.txt"], os.listdir(out)              # no files written by the bot
print("DRY RUN CHECKS PASSED")

# ---------------- 2) DEMO path against a fake Kalshi ----------------
out2 = f"{SP}/run_v38b2_demo"
g2 = load(out2, MODE="demo", DEMO_KEY_ID="demo-key", DEMO_PEM_PATH="/nonexistent")
M2 = {"B": "KXBTC15M-26OCT011215-15", "E": "KXETH15M-26OCT011215-15"}
api2 = []
clock2, feed2 = wire(g2, M2, {t: 885 for t in M2.values()}, {M2["B"]: "yes", M2["E"]: "no"}, api2)
calls = []
class Resp:
    def __init__(s, code, js): s.status_code, s._js, s.text = code, js, json.dumps(js)
    def json(s): return s._js
def fake_post(self, url, headers=None, json=None, timeout=None):
    calls.append((url, json, headers))
    if json["side"] == "bid": return Resp(201, {"order": {"order_id": "o1", "fill_count": "10.00", "average_fill_price": "0.5900", "average_fee_paid": "0.0170"}})
    return Resp(201, {"order": {"order_id": "o2", "fill_count": "10.00", "average_fill_price": "0.2000", "average_fee_paid": "0.0120"}})
g2["requests"].Session.post = fake_post
def script2():
    while feed2.on_tick is None: time.sleep(0.05)
    time.sleep(0.8)
    feed2.p[M2["B"]] = (58, 59); feed2.on_tick(M2["B"], 58, 59)
    feed2.p[M2["E"]] = (19, 20); feed2.on_tick(M2["E"], 19, 20)          # NO ask 81
    time.sleep(1.0)
    clock2["off"] = 900; time.sleep(3.0)
    _thread.interrupt_main()
txt2 = run_bot(g2, script2); print(txt2[-1500:])
assert len(calls) == 2 and all(c[0] == "https://external-api.demo.kalshi.co/trade-api/v2/portfolio/events/orders" for c in calls)
assert all(c[2]["KALSHI-ACCESS-KEY"] == "demo-key" and c[2]["KALSHI-ACCESS-SIGNATURE"] for c in calls)
assert "BUY  10x YES @ 59c  BTC" in txt2 and "BUY  10x NO @ 80c  ETH" in txt2                   # NO fill = 100 - YES 20c
assert "[order] raw response HTTP 201" in txt2
assert f"BTC (crypto) 1215-15: YES @ 59c WON {g2['_usd'](10 * 41 - 17)}" in txt2               # Kalshi's own fee: 1.7c x 10
assert f"ETH (crypto) 1215-15: NO @ 80c WON {g2['_usd'](10 * 20 - 12)}" in txt2
print("DEMO PATH CHECKS PASSED")

# ---------------- 3) mode guards ----------------
for over, msg in (({"MODE": "live"}, "LIVE mode is locked"), ({"MODE": "demo"}, "demo mode needs DEMO_KEY_ID"), ({"MODE": "xyz"}, "MODE must be")):
    g3 = load(f"{SP}/run_v38b2_guard", **over)
    try: g3["main"](); raise AssertionError("did not stop")
    except SystemExit as e: assert msg in str(e), e
print("MODE GUARDS PASSED")
for d in glob.glob(f"{SP}/run_v38b2_*"): shutil.rmtree(d, ignore_errors=True)
print("ALL CHECKS PASSED")
