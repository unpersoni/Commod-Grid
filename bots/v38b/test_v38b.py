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
    tick(M["B"], 58, 59)     # BTC YES ask 59 -> BUY 10 YES @59, reversal at 20c
    tick(M["B"], 30, 31)     # bid 30: not yet
    tick(M["B"], 19, 20)     # bid 19 <= 20 -> REVERSAL 20 NO @81
    tick(M["B"], 10, 11)     # no second reversal
    tick(M["E"], 20, 22)     # ETH NO ask 80 -> BUY 10 NO @80, reversal at 40c
    tick(M["E"], 58, 60)     # NO bid = 40 -> REVERSAL 20 YES @60
    tick(M["S"], 70, 71)     # SOL 700 s left: past the first 60 s -> no buy
    tick(M["X"], 52, 53)     # XRP: YES ask 53, NO ask 48 -> nothing at 56c+
    tick(M["D"], 60, 66)     # DOGE spread 6c -> no buy ...
    tick(M["D"], 63, 64)     # ... then 64c with a 1c spread, still in the first 60 s -> BUY YES @64 (reversal at 20c)
    tick(M["G"], 84, 85)     # GOLD YES ask 85 -> BUY YES @85, reversal at 47c
    tick(M["G"], 48, 49)     # bid 48: above 47 -> no reversal
    time.sleep(2.5)          # a status line
    clock["off"] = 855       # DOGE: 25 s left
    tick(M["D"], 10, 11)     # bid 10 <= 20 but under 30 s left -> no reversal
    clock["off"] = 900       # everything closed -> settle
    time.sleep(3.0)
    _thread.interrupt_main()
threading.Thread(target=script, daemon=True).start()
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    try: g["main"]()
    except KeyboardInterrupt: pass
txt = buf.getvalue(); print(txt)
fee = g["taker_fee_c"]
b_open = 10 * (0 - 59) - fee(10, 59); b_rev = 20 * (100 - 81) - fee(20, 81)
e_open = 10 * (0 - 80) - fee(10, 80); e_rev = 20 * (100 - 60) - fee(20, 60)
g_open = 10 * (100 - 85) - fee(10, 85); d_open = 10 * (0 - 64) - fee(10, 64)
usd = g["_usd"]
assert "BUY  10x YES @ 59c  BTC (crypto)" in txt and "reversal if its bid falls to 20c" in txt
assert "REVERSAL 20x NO @ 81c  BTC" in txt and txt.count("REVERSAL 20x") == 2
assert "BUY  10x NO @ 80c  ETH" in txt and "REVERSAL 20x YES @ 60c  ETH" in txt and "trigger 40c" in txt
assert "SOL (crypto)" not in txt.split("[status]")[0] and "XRP (crypto)" not in txt.split("[status]")[0]
assert "BUY  10x YES @ 64c  DOGE" in txt and "REVERSAL 20x NO" not in txt.split("DOGE")[-1]
assert "BUY  10x YES @ 85c  GOLD (commodity)" in txt and "reversal if its bid falls to 47c" in txt
assert f"BTC (crypto) 1215-15: opening YES @ 59c lost {usd(b_open)} + reversal 20x @ 81c WON {usd(b_rev)}  = {usd(b_open + b_rev)}" in txt
assert f"ETH (crypto) 1215-15: opening NO @ 80c lost {usd(e_open)} + reversal 20x @ 60c WON {usd(e_rev)}" in txt
assert f"GOLD (commodity) 1215-15: opening YES @ 85c WON {usd(g_open)}  = {usd(g_open)}" in txt
last = txt[txt.rindex("[status]"):]
tot_c = b_open + b_rev + e_open + e_rev + d_open
assert f"crypto   :   3 markets, opening won   0" in last and f"reversals   2, won   2" in last and f"TOTAL {usd(tot_c)}" in last, last
assert f"commodity:   1 markets, opening won   1" in last and f"TOTAL {usd(g_open)}" in last
assert "bought 56-65c (reversal at 20c):   2 markets" in last and "bought 71-80c (reversal at 40c):   1 markets" in last
assert "bought 81-95c (reversal at 47c):   1 markets" in last
print("ALL CHECKS PASSED")
