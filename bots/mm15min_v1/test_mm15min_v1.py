import os, sys, time, threading, datetime as dt, csv, io, contextlib, _thread, glob, shutil
SP = os.path.dirname(os.path.abspath(__file__)); out = f"{SP}/run_mm"
shutil.rmtree(out, ignore_errors=True); os.makedirs(out, exist_ok=True)
code = open(f"{SP}/mm15min_v1.py").read().replace("/content/drive/MyDrive/", out + "/")
g = {"__name__": "botmod"}; exec(compile(code, "mm15", "exec"), g)
g.update(MARKETS_REFRESH_SEC=0.3, LOOP_SEC=0.1, SETTLE_RETRY_SEC=0.2, STATUS_EVERY_SEC=2)
REAL = g["now"]; clock = {"off": 0.0}
g["now"] = lambda: REAL() + dt.timedelta(seconds=clock["off"])
T0 = REAL()
M = {"G": "KXGOLD15M-26OCT011215-15", "B": "KXBTC15M-26OCT011215-15", "W": "KXWTI15M-26OCT011215-15",
     "E": "KXETH15M-26OCT011215-15"}
secs = {M["G"]: 800, M["B"]: 700, M["W"]: 25, M["E"]: 600}
close = {t: T0 + dt.timedelta(seconds=v) for t, v in secs.items()}
result = {M["G"]: "yes", M["B"]: "no", M["W"]: "yes", M["E"]: "yes"}
class Spot:
    def price(s, a): return {"BTC": 60100.0}.get(a)
    def stop(s): pass
g["make_spot"] = lambda: Spot()
class Book:
    def __init__(s):
        s.books = {}; s.version = {}; s.ready = threading.Event(); s.ready.set()
    def level_qty(s, t, side, p):
        b = s.books.get(t)
        return None if b is None else b[side].get(p, 0.0)
    def track(s, ts): pass
    def stop(s): pass
book = Book()
g["make_book"] = lambda k: book
class Feed:
    def __init__(s): s.p = {}; s.on_tick = s.on_trade = None; s.ready = threading.Event(); s.ready.set()
    def get(s, t): return s.p.get(t)
    def track(s, ts): pass
    def stop(s): pass
feed = Feed()
g["make_feed"] = lambda k: feed; g["load_key"] = lambda: None
g["fetch_open"] = lambda sess, k: [{"ticker": t, "close_time": close[t].isoformat()} for t in M.values()]
g["fetch_market"] = lambda sess, k, t: {"result": result[t] if (close[t] - g["now"]()).total_seconds() < 0 else "",
                                        "close_time": close[t].isoformat(), "status": "finalized", "expiration_value": 1, "floor_strike": 1}
def tick(t, yb, ya): feed.p[t] = (yb, ya); feed.on_tick(t, yb, ya); time.sleep(0.05)
def trade(t, yp, n, side): feed.on_trade(t, yp, n, side); time.sleep(0.02)
open(f"{out}/mm15min_v1_fills.csv", "w").write("window,asset,old\n")     # a file with other columns is set aside
def script():
    while feed.on_tick is None: time.sleep(0.05)
    time.sleep(0.8)                                  # markets loaded
    # --- GOLD (commodity): favourite YES at 88 (fav + mm), mm NO at 11 ---
    book.books[M["G"]] = {"yes": {88: 50.0, 87: 40.0}, "no": {11: 30.0, 12: 8.0}}
    tick(M["G"], 88, 89)
    trade(M["G"], 88, 30, "no")      # 30 sold into 88: line ahead 50 -> 20
    trade(M["G"], 88, 25, "no")      # 25 more: 20 ahead used up, 5 fill (queue)
    trade(M["G"], 87, 3, "no")       # sold at 87, below our 88: 3 more fill (sold through)
    trade(M["G"], 11, 4, "yes")      # a taker bought YES at 11c?? no: yes_price 11 -> NO price 89, above our NO bid 11: nothing
    book.books[M["G"]]["yes"][88] = 0.0
    tick(M["G"], 87, 88)             # the YES ask came down to 88 = our bid: last 2 fill; NO bid now 12 > 11: mm NO moves up to 12
    # --- BTC (crypto): mm both sides; NO side gets 3 fills then the 30-contract cap stops it ---
    book.books[M["B"]] = {"yes": {20: 500.0}, "no": {78: 100.0}}
    tick(M["B"], 20, 22)             # mm YES @20, mm NO @78 (no favourite: nothing at 85-98)
    for _ in range(4):
        trade(M["B"], 22, 1000, "yes")   # takers buy YES at 22 = sell into NO bids at 78: 900 past the line, fill 10
        tick(M["B"], 20, 22)             # re-place the NO bid (until the cap)
    # --- ETH: favourite NO at 95; outbid to 96 before any fill -> moves up; 99 is outside the band ---
    book.books[M["E"]] = {"yes": {4: 10.0}, "no": {95: 70.0, 96: 5.0, 99: 9.0}}
    tick(M["E"], 4, 5)               # NO bid 95 -> fav NO @95 (mm NO @95 too, mm YES @4)
    tick(M["E"], 3, 4)               # NO bid 96: both NO orders move up to 96; YES ask 4 = mm YES bid 4 -> 10 fill
    trade(M["E"], 4, 5, "yes")       # NO price 96: 5 ahead -> 0, nothing filled
    trade(M["E"], 4, 4, "yes")       # 4 fill at 96 (queue) for fav and mm
    # --- WTI: 25 s left -> no orders ---
    book.books[M["W"]] = {"yes": {90: 10.0}, "no": {9: 10.0}}
    tick(M["W"], 90, 91)
    time.sleep(2.5)                  # a status line
    clock["off"] = 820               # all markets closed -> settle
    time.sleep(3.0)
    _thread.interrupt_main()
threading.Thread(target=script, daemon=True).start()
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    try: g["main"]()
    except KeyboardInterrupt: pass
txt = buf.getvalue(); print(txt)
fills = list(csv.DictReader(open(f"{out}/mm15min_v1_fills.csv")))
for r in fills: print({k: r[k] for k in ("asset", "strategy", "side", "px", "queue_ahead", "contracts", "how", "won", "pnl_c", "maker_fee_c", "spot_at_order")})
def ct(a, s, side, how=None):
    return sum(float(r["contracts"]) for r in fills if r["asset"] == a and r["strategy"] == s and r["side"] == side and (how is None or r["how"] == how))
for s in ("fav", "mm"):
    assert ct("GOLD", s, "YES", "queue") == 5 and ct("GOLD", s, "YES", "sold through") == 3 and ct("GOLD", s, "YES", "ask reached our bid") == 2, s
assert ct("GOLD", "mm", "NO") == 0
assert ct("BTC", "mm", "NO") == 30 and ct("BTC", "mm", "YES") == 0 and ct("BTC", "fav", "NO") == 0
assert ct("ETH", "fav", "NO") == 4 and ct("ETH", "mm", "NO") == 4 and all(r["px"] == "96" for r in fills if r["asset"] == "ETH" and r["side"] == "NO")
assert not [r for r in fills if r["asset"] == "WTI"]
g_fav = [r for r in fills if r["asset"] == "GOLD" and r["strategy"] == "fav"]
assert all(r["won"] == "1" and r["px"] == "88" for r in g_fav) and abs(sum(float(r["pnl_c"]) for r in g_fav) - 120) < 1e-6
assert abs(sum(float(r["pnl_c"]) for r in fills if r["asset"] == "BTC") - 30 * 22) < 1e-6           # NO won: 30 x (100-78)
assert abs(sum(float(r["pnl_c"]) for r in fills if r["asset"] == "ETH" and r["strategy"] == "fav") - (-4 * 96)) < 1e-6   # YES won: NO lost
assert [r["maker_fee_c"] for r in fills if r["asset"] == "GOLD"][0] == str(g["maker_fee_c"](5, 88)) == "1"
assert all(r["spot_at_order"] == "60100.0" for r in fills if r["asset"] == "BTC")
assert all(r["spot_at_order"] == "" for r in fills if r["asset"] == "GOLD")
mk = list(csv.DictReader(open(f"{out}/mm15min_v1_markets.csv")))
for r in mk: print({k: r[k] for k in ("asset", "strategy", "orders_placed", "orders_moved_up", "orders_filled", "yes_ct", "yes_avg_px", "no_ct", "no_avg_px", "pnl_c")})
gm = [r for r in mk if r["asset"] == "GOLD" and r["strategy"] == "mm"][0]
assert gm["orders_moved_up"] == "1" and gm["orders_filled"] == "1" and gm["yes_ct"] == "10.0" and gm["yes_avg_px"] == "88.0"
bm = [r for r in mk if r["asset"] == "BTC" and r["strategy"] == "mm"][0]
assert bm["orders_filled"] == "3" and bm["no_ct"] == "30.0" and bm["orders_placed"] == "4", bm     # 1 YES + 3 NO; the cap stopped a 4th NO
em = [r for r in mk if r["asset"] == "ETH" and r["strategy"] == "fav"][0]
assert em["orders_placed"] == "2" and em["orders_moved_up"] == "1"
assert not [r for r in mk if r["asset"] == "WTI"]
assert glob.glob(f"{out}/mm15min_v1_fills_old_*.csv"), "old fills file not set aside"
assert "FILL [fav] 5x YES @ 88c  GOLD (commodity)" in txt and "20 were ahead" not in txt and "50 were ahead" in txt
assert "SETTLED [mm] BTC (crypto)" in txt and "30 NO @ 78.0c" in txt
last = txt[txt.rindex("[status]"):]
print(last)
assert "fav favourite crypto" in last and "fav favourite commodity" in last and "mm  two-sided crypto" in last
assert ct("ETH", "mm", "YES", "ask reached our bid") == 10
assert "ALL crypto   : net +$8.52" in last, last          # BTC mm +660, ETH fav NO -384, ETH mm NO -384 + YES +960
assert "ALL commodity: net +$2.40" in last, last        # GOLD fav +120, mm +120
print("ALL CHECKS PASSED")
