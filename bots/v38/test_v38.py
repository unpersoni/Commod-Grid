import os, sys, time, threading, datetime as dt, csv, io, contextlib, _thread, glob, math, gzip, shutil
SP = os.path.dirname(os.path.abspath(__file__)); out = f"{SP}/run38"
shutil.rmtree(out, ignore_errors=True); os.makedirs(out, exist_ok=True)
code = open(f"{SP}/v38_src.py").read().replace("/content/drive/MyDrive/", out + "/")
g = {"__name__": "botmod"}; exec(compile(code, "v38", "exec"), g)
g.update(MARKETS_REFRESH_SEC=0.3, LOOP_SEC=0.1, SETTLE_RETRY_SEC=0.2, FLOW_BUCKET_SEC=1, STATUS_EVERY_SEC=2)
REAL = g["now"]; clock = {"off": 0.0}
g["now"] = lambda: REAL() + dt.timedelta(seconds=clock["off"])
T0 = REAL()
M = {"A": "KXBTC15M-26OCT011215-15", "B": "KXETH15M-26OCT011215-15", "C": "KXSOL15M-26OCT011215-15",
     "D": "KXXRP15M-26OCT011215-15", "E": "KXDOGE15M-26OCT011215-15",
     "G": "KXGOLD15M-26OCT011215-15", "S": "KXSILVER15M-26OCT011215-15", "W": "KXWTI15M-26OCT011215-15"}
secs = {M["A"]: 880, M["B"]: 760, M["C"]: 650, M["D"]: 870, M["E"]: 860, M["G"]: 880, M["S"]: 450, M["W"]: 400}
close = {t: T0 + dt.timedelta(seconds=v) for t, v in secs.items()}
result = {M["A"]: "yes", M["B"]: "no", M["C"]: "yes", M["D"]: "yes", M["E"]: "no", M["G"]: "yes", M["S"]: "no", M["W"]: "yes"}
class Spot:
    def price(s, a): return {"BTC": 60100, "ETH": 3000}.get(a)
    def change_pct(s, a, secs): return 0.0
    def stop(s): pass
g["make_spot"] = lambda: Spot()
g["make_book"] = lambda k: None
class Feed:
    def __init__(s): s.p = {}; s.on_tick = s.on_trade = None; s.ready = threading.Event(); s.ready.set()
    def get(s, t): return s.p.get(t)
    def track(s, ts): pass
    def stop(s): pass
feed = Feed()
g["make_feed"] = lambda k: feed; g["load_key"] = lambda: None
g["fetch_open"] = lambda sess, k: [{"ticker": t, "close_time": close[t].isoformat(), "floor_strike": 1} for t in M.values()]
g["fetch_market"] = lambda sess, k, t: {"result": result[t] if (close[t] - g["now"]()).total_seconds() < 0 else "",
                                        "close_time": close[t].isoformat(), "status": "finalized", "expiration_value": 1, "floor_strike": 1}
# mocked Kalshi history for the guard: 15 crypto markets in the last hour, YES led at minute 3, 7 ended "no" (reversed)
hist_calls = []
def api_get(sess, k, path, params=None):
    hist_calls.append(path)
    if path == "/markets":
        ser = params["series_ticker"]
        if ser not in ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M"): return {"markets": []}
        base = int(time.time()) // 900 * 900
        out_ = []
        for j in range(3):
            ct = base - 900 * j
            res = "no" if (ser in ("KXBTC15M", "KXETH15M") or (ser == "KXSOL15M" and j < 1)) and j < 3 else "yes"
            out_.append({"ticker": f"{ser}-HIST{j}", "close_time": dt.datetime.fromtimestamp(ct, dt.timezone.utc).isoformat(), "result": res})
        return {"markets": out_}
    if "/candlesticks" in path:
        o = params["start_ts"]
        return {"candlesticks": [{"end_period_ts": o + 60 * m, "yes_bid": {"close": 60}, "yes_ask": {"close_dollars": "0.62"}} for m in (1, 2, 3, 4)]}
    return None
g["api_get"] = api_get
def tick(t, yb, ya): feed.p[t] = (yb, ya); feed.on_tick(t, yb, ya); time.sleep(0.05)
def script():
    while feed.on_tick is None: time.sleep(0.05)
    time.sleep(1.0)
    tick(M["A"], 89, 90)      # BTC YES ask 90, minute 0 -> BUY YES @90
    tick(M["B"], 6, 7)        # ETH NO ask 94, minute 2 -> BUY NO @94
    tick(M["C"], 89, 90)      # SOL minute 4 -> too late for crypto, no buy
    tick(M["D"], 95, 96)      # XRP YES ask 96 -> above 95, no buy
    tick(M["E"], 10, 17)      # DOGE NO ask 90 but spread 7c -> no buy
    tick(M["G"], 86, 87)      # GOLD YES ask 87, minute 0 -> BUY YES @87 (commodity 85-95)
    tick(M["S"], 90, 91)      # SILVER minute 7.5 (450 s left) -> still inside the 8-minute commodity window -> BUY YES @91
    tick(M["W"], 90, 91)      # WTI 400 s left -> past the commodity window, no buy
    feed.on_trade(M["A"], 90, 5, "yes"); feed.on_trade(M["A"], 89, 3, "no"); feed.on_trade(M["G"], 87, 2, "yes")
    time.sleep(3.0)           # flow buckets flushed, a status line printed
    clock["off"] = 170
    tick(M["A"], 92, 93)      # BTC 3 minutes in: YES leads (records the minute-3 leader; BTC settles YES = not reversed)
    clock["off"] = 900
    time.sleep(4.0)
    _thread.interrupt_main()
threading.Thread(target=script, daemon=True).start()
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    try: g["main"]()
    except KeyboardInterrupt: pass
txt = buf.getvalue(); print(txt)
fee = g["taker_fee_c"]
rows = list(csv.DictReader(open(f"{out}/commod15min_v38_trades.csv")))
by = sorted((r["asset"], r["side"], r["buy_px"], r["reason"], r["net_c"], r["guard_on"], r["guard_markets"]) for r in rows)
for b in by: print(b)
assert len(rows) == 4, by
assert ("BTC", "YES", "90", "settled", str(100 - fee(10, 90)), "1", "15") in by, by
assert ("ETH", "NO", "94", "settled", str(60 - fee(10, 94)), "1", "15") in by, by
assert ("GOLD", "YES", "87", "settled", str(130 - fee(10, 87)), "", "0") in by, by
assert ("SILVER", "YES", "91", "settled", str(-910 - fee(10, 91)), "", "0") in by, by
assert "[guard] start-up history from Kalshi: 15 settled markets" in txt and "crypto 7/15 reversed" in txt, txt
assert "guard ON (47% of 15 reversed)" in txt and "guard warming up" in txt
assert "[status] crypto:" in txt and "Drive free" in txt
assert "guard ON 44% of 16" in txt.split("[stopped]")[0].splitlines()[-1], txt.splitlines()[-4:]   # live market added to the guard
files = sorted(os.listdir(f"{out}/commod15min_v38_data")); print("data files:", files)
assert all(f.startswith(("ticks_", "flow_")) for f in files) and any(f.startswith("flow_") for f in files)
def rd(f): return list(csv.DictReader(gzip.open(f, "rt") if f.endswith(".gz") else open(f)))
fl = [r for f in files if f.startswith("flow_") for r in rd(f"{out}/commod15min_v38_data/{f}")]
print("flow rows:", fl)
a = [r for r in fl if r["ticker"] == M["A"]]
assert len(a) == 1 and a[0]["n_yes_taker"] == "1" and a[0]["ct_yes_taker"] == "5" and a[0]["ct_no_taker"] == "3", a
assert abs(float(a[0]["avg_yes_px"]) - (90*5 + 89*3)/8) < 0.01
tk = [r for f in files if f.startswith("ticks_") for r in rd(f"{out}/commod15min_v38_data/{f}")]
assert tk and "yes_bid_qty" in tk[0] and {r["asset"] for r in tk} >= {"BTC", "GOLD", "WTI"}
st = list(csv.DictReader(open(f"{out}/commod15min_v38_settlements.csv")))
assert {r["asset"] for r in st} >= {"BTC", "ETH", "SOL", "GOLD", "SILVER", "WTI"}, st
assert not glob.glob(f"{out}/**/*book*", recursive=True) and not glob.glob(f"{out}/**/*prints*", recursive=True)
assert not os.path.exists(f"{out}/commod15min_v37_trades.csv")
print("ALL CHECKS PASSED")
