import os, sys, time, threading, datetime as dt, io, contextlib, _thread, math
SP = os.path.dirname(os.path.abspath(__file__))
code = open(f"{SP}/commod15min_v38b.py").read()
g = {"__name__": "botmod"}; exec(compile(code, "v38b", "exec"), g)
g.update(MARKETS_REFRESH_SEC=0.3, LOOP_SEC=0.1, SETTLE_RETRY_SEC=0.2, STATUS_EVERY_SEC=2)
REAL = g["now"]; clock = {"off": 0.0}
g["now"] = lambda: REAL() + dt.timedelta(seconds=clock["off"])
T0 = REAL()
M = {"B": "KXBTC15M-26OCT011215-15", "E": "KXETH15M-26OCT011215-15", "S": "KXSOL15M-26OCT011215-15",
     "X": "KXXRP15M-26OCT011215-15", "D": "KXDOGE15M-26OCT011215-15", "G": "KXGOLD15M-26OCT011215-15"}
secs = {M["B"]: 880, M["E"]: 870, M["S"]: 700, M["X"]: 880, M["D"]: 880, M["G"]: 890}
close = {t: T0 + dt.timedelta(seconds=v) for t, v in secs.items()}
result = {M["B"]: "no", M["E"]: "yes", M["S"]: "yes", M["X"]: "yes", M["D"]: "no", M["G"]: "yes"}
class Feed:
    def __init__(s): s.p = {}; s.on_tick = s.on_trade = None; s.ready = threading.Event(); s.ready.set()
    def get(s, t): return s.p.get(t)
    def track(s, ts): pass
    def stop(s): pass
feed = Feed()
g["make_feed"] = lambda k: feed; g["load_key"] = lambda: None
g["fetch_open"] = lambda sess, k: [{"ticker": t, "close_time": close[t].isoformat()} for t in M.values()]
g["fetch_market"] = lambda sess, k, t: {"result": result[t] if (close[t] - g["now"]()).total_seconds() < 0 else ""}
def tick(t, yb, ya): feed.p[t] = (yb, ya); feed.on_tick(t, yb, ya); time.sleep(0.05)
def script():
    while feed.on_tick is None: time.sleep(0.05)
    time.sleep(0.8)
    tick(M["B"], 58, 59)     # BTC YES ask 59 -> BUY 10 YES @59
    tick(M["B"], 19, 20)     # dips to 19c: nothing happens (no reversals), held
    tick(M["E"], 20, 22)     # ETH NO ask 80 -> BUY 10 NO @80
    tick(M["E"], 58, 60)     # dips: held
    tick(M["S"], 70, 71)     # SOL 700 s left: past the first 60 s -> no buy
    tick(M["X"], 52, 53)     # XRP: YES ask 53, NO ask 48 -> nothing at 56c+
    tick(M["D"], 60, 66)     # DOGE spread 6c -> no buy ...
    tick(M["D"], 63, 64)     # ... then 64c with a 1c spread, still in the first 60 s -> BUY YES @64
    tick(M["G"], 84, 85)     # GOLD YES ask 85 -> BUY YES @85
    tick(M["G"], 96, 97)     # no second buy in the same market
    time.sleep(2.5)          # a status line
    clock["off"] = 900       # everything closed -> settle
    time.sleep(3.0)
    _thread.interrupt_main()
threading.Thread(target=script, daemon=True).start()
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    try: g["main"]()
    except KeyboardInterrupt: pass
txt = buf.getvalue(); print(txt)
fee = g["taker_fee_c"]; usd = g["_usd"]
b = 10 * (0 - 59) - fee(10, 59); e = 10 * (0 - 80) - fee(10, 80); d = 10 * (0 - 64) - fee(10, 64); gd = 10 * (100 - 85) - fee(10, 85)
assert "BUY  10x YES @ 59c  BTC (crypto)" in txt and "BUY  10x NO @ 80c  ETH" in txt and "BUY  10x YES @ 64c  DOGE" in txt
assert "BUY  10x YES @ 85c  GOLD (commodity)" in txt and txt.count("BUY  ") == 4
assert "REVERSAL" not in txt and "reversal" not in txt
assert "SOL (crypto)" not in txt and "XRP (crypto)" not in txt
assert f"BTC (crypto) 1215-15: YES @ 59c lost {usd(b)}" in txt and f"GOLD (commodity) 1215-15: YES @ 85c WON {usd(gd)}" in txt
last = txt[txt.rindex("[status]"):]
assert f"crypto   :   3 markets,   0 won (0%), avg buy 67.7c, net {usd(b + e + d)}" in last, last
assert f"commodity:   1 markets,   1 won (100%), avg buy 85.0c, net {usd(gd)}" in last
assert "bought 56-65c:   2 markets,   0 won (0%)" in last and "bought 71-80c:   1 markets" in last and "bought 81-95c:   1 markets,   1 won" in last
print("ALL CHECKS PASSED")
