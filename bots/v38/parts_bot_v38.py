# ---------------- the bot ----------------
FEAT_COLS = ["minute_in_market", "spread_c", "side_mid_c", "dir30_c", "dir60_c", "range60_c", "ticks60",
             "open_side_mid_c", "open_seen_s2c", "open_lean_c",
             "spot_px", "strike", "spot_dist_pct", "spot_chg30_pct", "spot_chg60_pct", "btc_chg60_pct",
             "open_positions", "cycle_bought_before", "prev_result_same_asset",
             "book_bid_qty", "book_ask_qty", "book_bid_qty3", "book_ask_qty3",
             "guard_share", "guard_markets", "guard_on", "guard_share_other_class", "z_spot"]
CRYPTO = {"BTC", "ETH", "SOL", "XRP", "DOGE"}
def cls(a): return "crypto" if a in CRYPTO else "commodity"

class Position:
    def __init__(self, strat, yes, px, s2c, n, feat):
        self.strat, self.yes, self.px, self.s2c, self.n, self.feat = strat, yes, px, s2c, n, feat
        self.iso = now_iso()
        self.low = px          # lowest bid seen while held

class MarketState:
    def __init__(self):
        self.pos = {}                     # strategy -> open Position
        self.done = set()                 # strategies that already traded this market
        self.hist = collections.deque()   # (time, yes_bid, yes_ask), last ~2 min
        self.first = None                 # (s2c, yes_bid, yes_ask) of the first price seen
        self.prev = None                  # (yes_bid, yes_ask) at the previous evaluation
        self.rb = None                    # rebound watch after an early-fav buy

class HourlyCSV:
    """Appends rows to <folder>/<prefix>_YYYY-MM-DD_HH.csv (UTC), starting a new file every hour,
    so each file is closed (and saved by Drive) within the hour and a problem loses one hour at most."""
    def __init__(self, folder, prefix, cols):
        self.folder, self.prefix, self.cols = folder, prefix, cols
        os.makedirs(folder, exist_ok=True)
        self.key, self.fh, self.w = None, None, None
    def _roll(self):
        key = now().strftime("%Y-%m-%d_%H")
        if key == self.key: return
        if self.fh:
            self.fh.close()
            done = os.path.join(self.folder, f"{self.prefix}_{self.key}.csv")
            threading.Thread(target=compress_file, args=(done,), daemon=True).start()
        path = os.path.join(self.folder, f"{self.prefix}_{key}.csv")
        new = not os.path.exists(path)
        self.fh = open(path, "a", newline=""); self.w = csv.writer(self.fh); self.key = key
        if new: self.w.writerow(self.cols)
    def writerow(self, row):
        self._roll(); self.w.writerow(row)
    def flush(self):
        if self.fh: self.fh.flush()
    def close(self):
        if self.fh: self.fh.close(); self.fh = None

def drive_free_mb(folder):
    try:
        import shutil
        return shutil.disk_usage(folder).free / 1e6
    except Exception:
        return None

def compress_file(path):
    """Gzip a finished hourly file (about 10x smaller), check it reads back with the same number
    of lines, then remove the .csv. Always compressed: a full Drive is what compression helps."""
    try:
        import gzip, shutil
        tmp = path[:-4] + ".tmp.gz"
        with open(path, "rb") as fi, gzip.open(tmp, "wb") as fo: shutil.copyfileobj(fi, fo)
        with open(path, "rb") as fi: n0 = sum(1 for _ in fi)
        with gzip.open(tmp, "rb") as fz: n1 = sum(1 for _ in fz)
        if n0 != n1:
            os.remove(tmp); print(f"[log] compress check failed, kept {os.path.basename(path)}"); return
        os.replace(tmp, path + ".gz"); os.remove(path)
    except Exception as e:
        print(f"[log] could not compress {os.path.basename(path)}: {e}")

def _r(x, nd=2):
    return "" if x is None else round(x, nd)

def main():
    print(f"{BOT_NAME} — PAPER ONLY (no orders are sent)")
    print(f"markets: {', '.join(asset(s) for s in SERIES)}")
    for c, rl in RULES.items():
        print(f"rule ({c}): BUY {CONTRACTS} of a side whose ask is {rl['buy_min']}-{rl['buy_max']}c in the first "
              f"{(900 - rl['until_sec']) / 60:g} minutes (at the ask) · HOLD to settlement · "
              f"{MAX_TRADES_PER_MARKET} trade per market · no buy if spread > {MAX_SPREAD_C}c · Kalshi taker fees")
    if REBOUND_ON:
        print(f"rule (rebound, {' + '.join(sorted(REBOUND_CLASSES))}): after an early-fav buy, when that side's bid falls {REBOUND_DIP_C}c below the buy price, "
              f"BUY {CONTRACTS} more at the ask once the bid is {REBOUND_TURN_C}c above its low (>= {REBOUND_MIN_LEFT_SEC}s left) · HOLD")
    if CROSS_ON:
        print(f"rule (spot-confirmed cross, crypto): when a side's ask crosses {CROSS_LEVEL_C}c (to {CROSS_LEVEL_C}-{CROSS_MAX_C}c) while "
              f"Coinbase spot is >= {CROSS_MIN_Z} normal moves past the strike on that side · BUY {CONTRACTS} · HOLD "
              f"(needs ~5 min of spot history after start)")
    print(f"reversal guard: share of the last {GUARD_LOOKBACK_MIN} min of markets whose minute-3 leader lost; "
          f"ON at {GUARD_ON_SHARE:.0%}+ (trades are still taken and labelled)")
    fm = drive_free_mb(os.path.dirname(TRADES_CSV))
    print(f"Drive free space: {'unknown' if fm is None else f'{fm:,.0f} MB'}")
    k = load_key()
    sess = requests.Session()
    _ad = requests.adapters.HTTPAdapter(pool_maxsize=len(SERIES) + 10)
    sess.mount("https://", _ad)
    lock = threading.RLock()
    markets, rest_px, state = {}, {}, {}
    settle_due, settled = {}, set()
    STRATS = [("fav", c) for c in ("crypto", "commodity")] + [("rebound", c) for c in ("crypto", "commodity")] + [("cross", "crypto")]
    run = {k: {"net": 0, "closed": 0, "won": 0} for k in STRATS}
    run_off = {k: {"net": 0, "closed": 0, "won": 0} for k in STRATS}   # guard OFF at buy
    lead3 = {}                       # ticker -> True (YES led) / False (NO led) / None, at 3 min in
    guard_hist = collections.deque() # (close time, class, reversed 0/1) of settled markets
    flow = {}                        # (ticker, bucket start) -> [n_yes, ct_yes, n_no, ct_no, sum_px_ct, last_px]
    last_result = {}                 # asset -> "win"/"loss" of its last closed position
    cycle_buys = collections.defaultdict(set)   # market close time -> assets bought

    def open_csv(path, cols):
        if os.path.exists(path):                       # a file from an older version with other columns: set it aside
            with open(path, newline="") as fh0:
                head = next(csv.reader(fh0), None)
            if head is not None and head != cols:
                old = path[:-4] + f"_old_{now():%Y%m%d_%H%M%S}.csv"
                os.replace(path, old); print(f"[log] {os.path.basename(path)} had different columns — moved to {os.path.basename(old)}")
        new = not os.path.exists(path)
        fh = open(path, "a", newline="")
        w = csv.writer(fh)
        if new: w.writerow(cols); fh.flush()
        return fh, w
    tfh, tw = open_csv(TRADES_CSV, TRADE_COLS + FEAT_COLS)
    kfh = kw = HourlyCSV(DATA_DIR, "ticks", TICK_COLS)
    ffh = fw = HourlyCSV(DATA_DIR, "flow", FLOW_COLS)
    sfh, sw = open_csv(SETTLE_CSV, SETTLE_COLS)
    strike_due = {}                  # ticker -> next time to ask Kalshi for its strike
    last_exp = {}                    # asset -> (close time, final price) of its last settled market
    io_lock = threading.Lock()

    def s2c_of(t):
        m = markets.get(t)
        return (m["close"] - now()).total_seconds() if m else None

    def say(msg): print(f"[{now():%H:%M:%S}] {msg}")

    def prices(t):
        return feed.get(t) or rest_px.get(t)

    def guard(c):
        """(share of reversed minute-3 leaders, markets counted, on?) for class c over the look-back."""
        cutoff = now() - dt.timedelta(minutes=GUARD_LOOKBACK_MIN)
        xs = [r for ct, cc, r in guard_hist if cc == c and ct > cutoff]
        if len(xs) < GUARD_MIN_MARKETS: return None, len(xs), None
        sh = sum(xs) / len(xs)
        return sh, len(xs), int(sh >= GUARD_ON_SHARE)

    def add_guard(t, close_t, res):
        led = lead3.pop(t, None)
        if led is None or res not in ("yes", "no"): return
        guard_hist.append((close_t, cls(asset(t)), int(led != (res == "yes"))))
        while guard_hist and guard_hist[0][0] < now() - dt.timedelta(hours=3): guard_hist.popleft()

    def features(t, st, yes, yb, ya, s2c):
        """Everything known at the moment of the buy, oriented toward the side bought."""
        side_mid = lambda b, a: (b + a) / 2 if yes else 100 - (b + a) / 2
        now_t = time.time()
        h = list(st.hist)
        def mid_ago(secs):
            old = [(b, a) for ts, b, a in h if ts <= now_t - secs]
            return side_mid(*old[-1]) if old else None
        cur = side_mid(yb, ya)
        last60 = [side_mid(b, a) for ts, b, a in h if ts >= now_t - 60]
        m30, m60 = mid_ago(30), mid_ago(60)
        f = {"minute_in_market": int((900 - s2c) // 60), "spread_c": ya - yb, "side_mid_c": cur,
             "dir30_c": None if m30 is None else cur - m30, "dir60_c": None if m60 is None else cur - m60,
             "range60_c": (max(last60) - min(last60)) if last60 else None, "ticks60": len(last60)}
        if st.first:
            fs, fb, fa = st.first
            f.update(open_side_mid_c=side_mid(fb, fa), open_seen_s2c=round(fs, 1), open_lean_c=abs((fb + fa) / 2 - 50))
        a = asset(t)
        strike = markets.get(t, {}).get("strike")
        sp = spot.price(a) if spot else None
        sign = 1 if yes else -1        # YES pays if the price finishes above the strike
        if sp is not None and strike:
            f.update(spot_px=sp, strike=strike, spot_dist_pct=sign * (sp - strike) / strike * 100)
        for secs in (30, 60):
            c = spot.change_pct(a, secs) if spot else None
            f[f"spot_chg{secs}_pct"] = None if c is None else sign * c
        f["btc_chg60_pct"] = spot.change_pct("BTC", 60) if spot else None
        f["open_positions"] = sum(1 for x, y in state.items() if y.pos and cls(asset(x)) == cls(a))
        key = markets.get(t, {}).get("close")
        f["cycle_bought_before"] = len({x for x in cycle_buys[key] - {a} if cls(x) == cls(a)})
        tb = book.top(t) if book else None
        if tb:
            ybids, nbids = tb
            own, opp = (ybids, nbids) if yes else (nbids, ybids)   # our side's bids / the asks we buy from
            f["book_bid_qty"] = own[0][1] if own else 0
            f["book_ask_qty"] = opp[0][1] if opp else 0
            f["book_bid_qty3"] = sum(q for _, q in own)
            f["book_ask_qty3"] = sum(q for _, q in opp)
        f["prev_result_same_asset"] = last_result.get(a, "none yet")
        return f

    def record(t, p, sell_px, sell_s2c, result, reason):
        fees = taker_fee_c(p.n, p.px) + (taker_fee_c(p.n, sell_px) if reason == "sold" else 0)
        net = (sell_px - p.px) * p.n - fees
        key = (p.strat, cls(asset(t)))
        r = run[key]; r["net"] += net; r["closed"] += 1; r["won"] += int(net > 0)
        if p.feat.get("guard_on") == 0:
            ro = run_off[key]; ro["net"] += net; ro["closed"] += 1; ro["won"] += int(net > 0)
        if p.strat == "fav": last_result[asset(t)] = "win" if net > 0 else "loss"
        feat = [p.feat.get(c) for c in FEAT_COLS]
        feat = [_r(v, 4) if isinstance(v, float) else ("" if v is None else v) for v in feat]
        with io_lock:
            tw.writerow([t, asset(t), cls(asset(t)), p.strat, "PAPER", "YES" if p.yes else "NO", p.iso,
                         round(p.s2c, 1), p.px, "", "", sell_px, result,
                         reason, p.low, fees, net, r["net"]] + feat)
            tfh.flush()
        return net

    def spot_z(t, yes, s2c):
        """How many 'normal moves' spot is past the strike on this side (None if unknown)."""
        a = asset(t); strike = markets.get(t, {}).get("strike")
        sp = spot.price(a) if spot else None
        v = spot.var_per_sec(a) if spot and hasattr(spot, "var_per_sec") else None
        if not sp or not strike or not v or s2c <= 0: return None
        z = math.log(sp / strike) / math.sqrt(v * s2c)
        return z if yes else -z

    def buy(t, st, strat, yes, ask, yb, ya, s2c, note=""):
        c = cls(asset(t))
        try:
            feat = features(t, st, yes, yb, ya, s2c)
        except Exception as e:
            print(f"[features] {e}"); feat = {}
        gs, gn, gon = guard(c)
        other = guard("commodity" if c == "crypto" else "crypto")[0]
        z = spot_z(t, yes, s2c) if c == "crypto" else None
        feat.update(guard_share=None if gs is None else round(gs, 3), guard_markets=gn,
                    guard_on="" if gon is None else gon, guard_share_other_class=None if other is None else round(other, 3),
                    z_spot=None if z is None else round(z, 3))
        st.pos[strat] = Position(strat, yes, ask, s2c, CONTRACTS, feat)
        st.done.add(strat)
        if strat == "fav": cycle_buys[markets[t]["close"]].add(asset(t))
        gtxt = "guard warming up" if gs is None else f"guard {'ON' if gon else 'off'} ({gs:.0%} of {gn} reversed)"
        say(f"BUY [{STRAT_NAMES[strat]}] {CONTRACTS}x {'YES' if yes else 'NO'} @ {ask}c  {asset(t)} ({c}) {t[-7:]}  "
            f"{s2c:.0f}s left{note}  [{gtxt}]")

    def evaluate(t):
        px = prices(t)
        s2c = s2c_of(t)
        if px is None or s2c is None or s2c <= DEADBAND_SEC: return
        yb, ya = px
        if not (0 <= yb < ya <= 100): return
        with lock:
            st = state.setdefault(t, MarketState())
            prev, st.prev = st.prev, (yb, ya)
            for p in st.pos.values():                        # holding: track the lowest bid
                p.low = min(p.low, yb if p.yes else 100 - ya)
            c = cls(asset(t))
            if c not in TRADE_CLASSES: return                  # recorded only, never traded
            spread_ok = ya - yb <= MAX_SPREAD_C
            # 1) early favs
            rl = RULES[c]
            if "fav" not in st.done and spread_ok and rl["until_sec"] <= s2c <= rl["from_sec"]:
                cands = [(ask, yes) for yes, ask in ((True, ya), (False, 100 - yb)) if rl["buy_min"] <= ask <= rl["buy_max"]]
                if cands and not (len(cands) == 2 and cands[0][0] == cands[1][0]):
                    ask, yes = min(cands)
                    buy(t, st, "fav", yes, ask, yb, ya, s2c)
                    if REBOUND_ON and c in REBOUND_CLASSES:
                        st.rb = {"yes": yes, "sig": ask, "low": 999, "armed": False}
                    return
            # 2) rebound: after an early-fav buy, the side dips REBOUND_DIP_C below the buy price, then turns up
            rb = st.rb
            if rb and "rebound" not in st.done:
                hb = yb if rb["yes"] else 100 - ya
                rb["low"] = min(rb["low"], hb)
                if not rb["armed"] and hb <= rb["sig"] - REBOUND_DIP_C: rb["armed"] = True
                if rb["armed"] and hb >= rb["low"] + REBOUND_TURN_C and s2c >= REBOUND_MIN_LEFT_SEC and spread_ok:
                    ask = ya if rb["yes"] else 100 - yb
                    buy(t, st, "rebound", rb["yes"], ask, yb, ya, s2c, f"  (dipped to {rb['low']}c, now {hb}c)")
            # 3) spot-confirmed 90c cross
            if CROSS_ON and c == "crypto" and "cross" not in st.done and prev and spread_ok and s2c > CROSS_MIN_LEFT_SEC:
                pyb, pya = prev
                for yes, now_a, prev_a in ((True, ya, pya), (False, 100 - yb, 100 - pyb)):
                    if prev_a < CROSS_LEVEL_C <= now_a <= CROSS_MAX_C:
                        z = spot_z(t, yes, s2c)
                        if z is not None and z >= CROSS_MIN_Z:
                            buy(t, st, "cross", yes, now_a, yb, ya, s2c, f"  (spot {z:.2f} normal moves past the strike)")
                        break

    def on_tick(t, yb, ya):
        if t not in markets: return                  # only our markets
        s2c = s2c_of(t)
        now_t = time.time()
        with lock:
            st = state.setdefault(t, MarketState())
            if st.first is None and s2c is not None: st.first = (s2c, yb, ya)
            if t not in lead3 and s2c is not None and s2c <= 720 and st.first and st.first[0] >= 840:
                m = (yb + ya) / 2
                lead3[t] = None if m == 50 else (m > 50)
            st.hist.append((now_t, yb, ya))
            while st.hist and st.hist[0][0] < now_t - 130: st.hist.popleft()
        sp = spot.price(asset(t)) if spot else None
        yq = nq = ""
        if book:
            try:
                tb = book.top(t, 1)
                if tb:
                    yq = tb[0][0][1] if tb[0] else 0
                    nq = tb[1][0][1] if tb[1] else 0
            except Exception: pass
        with io_lock:
            kw.writerow([now_iso(), t, asset(t), yb, ya, round(s2c, 1) if s2c is not None else "",
                         "" if sp is None else sp, yq, nq])
        evaluate(t)

    def on_trade(t, yes_price, count, taker_side):
        if t not in markets: return
        b = int(time.time() // FLOW_BUCKET_SEC) * FLOW_BUCKET_SEC
        with io_lock:
            f = flow.setdefault((t, b), [0, 0, 0, 0, 0, None])
            i = 0 if taker_side == "yes" else 2
            f[i] += 1; f[i + 1] += count; f[4] += yes_price * count; f[5] = yes_price

    def finish(t, result):
        with lock:
            st = state.get(t)
            if not st or not st.pos: return
            ps = list(st.pos.values()); st.pos = {}
        for p in ps:
            won = (result == "yes") == p.yes
            net = record(t, p, 100 if won else 0, None, result, "settled")
            r = run[(p.strat, cls(asset(t)))]
            say(f"SETTLED {'WIN ' if won else 'LOSS'} [{STRAT_NAMES[p.strat]}] {p.n}x {'YES' if p.yes else 'NO'} bought {p.px}c  "
                f"net {net:+.0f}c  {asset(t)} {t[-7:]}  run {r['net']:+.0f}c ({r['closed']} positions, {r['won']} won)")

    global FEED, SPOT, BOOK
    spot = SPOT = make_spot()
    try: book = BOOK = make_book(k)
    except Exception as e: print(f"[book] not started ({e})"); book = None
    feed = FEED = make_feed(k)
    feed.on_tick = on_tick
    feed.on_trade = on_trade
    feed.ready.wait(timeout=10)

    last_refresh, last_flush, last_status = 0, time.time(), 0
    last_bookchk, last_vs, book_stale = time.time(), -1, 0
    load_guard_history(sess, k, guard_hist)
    try:
        while True:
            if time.time() - last_refresh >= MARKETS_REFRESH_SEC:
                last_refresh = time.time()
                try:
                    ms = fetch_open(sess, k)
                except Exception as e:
                    print(f"[rest] {e}"); ms = None
                if ms is not None:
                    for m in ms:
                        t, ct = m.get("ticker"), parse_iso(m.get("close_time"))
                        if not t or not ct or t in settled: continue
                        try: strike = float(m.get("floor_strike"))
                        except (TypeError, ValueError): strike = None
                        old = markets.get(t)
                        if strike is None and old: strike = old.get("strike")
                        if strike is None:
                            prev = last_exp.get(asset(t))     # previous market closed when this one opened
                            if prev and abs((ct - prev[0]).total_seconds() - 900) < 5: strike = prev[1]
                        if strike is None and t not in strike_due: strike_due[t] = time.time()
                        markets[t] = {"close": ct, "strike": strike}
                        yb, ya = px_c(m, "yes_bid"), px_c(m, "yes_ask")
                        if yb is not None and ya is not None: rest_px[t] = (yb, ya)
                    live = {t for t in markets if 0 < (s2c_of(t) or 0) <= 15 * 60 + 60}
                    for t in live:                      # strike of each asset's current market (Pyth check)
                        if markets[t].get("strike") and s2c_of(t) <= 900: STRIKES[asset(t)] = markets[t]["strike"]
                    feed.track(live)
                    if book: book.track(live)
            for t in list(markets):
                s2c = s2c_of(t)
                if s2c is None: continue
                if s2c > -5:
                    evaluate(t)
                elif t not in settle_due:
                    settle_due[t] = [time.time(), time.time() + SETTLE_GIVEUP_SEC]
            asked = 0
            for t, due in list(strike_due.items()):              # missing strikes: ask Kalshi (2 per loop)
                if t not in markets or markets[t].get("strike") is not None:
                    strike_due.pop(t, None); continue
                if time.time() < due or asked >= 2: continue
                asked += 1
                try: k_ = float(fetch_market(sess, k, t).get("floor_strike"))
                except (TypeError, ValueError): k_ = None
                if k_: markets[t]["strike"] = k_; strike_due.pop(t, None)
                else: strike_due[t] = time.time() + 30
            checked = 0
            for t, due in list(settle_due.items()):
                if checked >= 4: break
                if time.time() < due[0]: continue
                checked += 1
                m = fetch_market(sess, k, t)
                res = m.get("result")
                if res in ("yes", "no"):
                    try: last_exp[asset(t)] = (parse_iso(m.get("close_time")) or markets[t]["close"],
                                               float(m.get("expiration_value")))
                    except (TypeError, ValueError, KeyError): pass
                    with io_lock:
                        sw.writerow([now_iso(), t, asset(t), m.get("close_time"), res,
                                     m.get("floor_strike"), m.get("expiration_value"), m.get("status")])
                        sfh.flush()
                    finish(t, res)
                    add_guard(t, markets[t]["close"], res)
                    settled.add(t)
                    del settle_due[t]; markets.pop(t, None); rest_px.pop(t, None)
                    with lock: state.pop(t, None)
                elif time.time() > due[1]:
                    lead3.pop(t, None)
                    settled.add(t)
                    del settle_due[t]; markets.pop(t, None)
                else:
                    due[0] = time.time() + SETTLE_RETRY_SEC
            if time.time() - last_flush > 2:
                last_flush = time.time()
                cur = int(time.time() // FLOW_BUCKET_SEC) * FLOW_BUCKET_SEC
                with io_lock:
                    for key in sorted(kk for kk in flow if kk[1] < cur):
                        t, b0 = key; f = flow.pop(key)
                        ct = f[1] + f[3]
                        m = markets.get(t)
                        s2c = (m["close"] - dt.datetime.fromtimestamp(b0 + FLOW_BUCKET_SEC, dt.timezone.utc)).total_seconds() if m else ""
                        fw.writerow([dt.datetime.fromtimestamp(b0, dt.timezone.utc).isoformat(), t, asset(t),
                                     round(s2c, 1) if s2c != "" else "", f[0], f[1], f[2], f[3],
                                     round(f[4] / ct, 2) if ct else "", f[5]])
                    kfh.flush(); ffh.flush()
            if time.time() - last_status >= STATUS_EVERY_SEC:
                last_status = time.time()
                gparts = []
                for c in ("crypto", "commodity"):
                    gs, gn, gon = guard(c)
                    gtxt = "warming up" if gs is None else ("ON" if gon else "off") + f" {gs:.0%} of {gn}"
                    gparts.append(f"{c} {gtxt}")
                fm = drive_free_mb(os.path.dirname(TRADES_CSV))
                say("[status] guard: " + ", ".join(gparts) + ("" if fm is None else f" | Drive free {fm:,.0f} MB"))
                for key in STRATS:
                    r, ro = run[key], run_off[key]
                    print(f"           {STRAT_NAMES[key[0]]:13} {key[1]:9}: {r['closed']:3} closed, {r['won']:3} won, net {r['net']:+6.0f}c"
                          f"   (guard off: {ro['closed']} closed, {ro['net']:+.0f}c)")
                if fm is not None and fm < LOW_SPACE_MB:
                    say(f"!!! DRIVE NEARLY FULL ({fm:,.0f} MB free) — logs may stop saving. Free up space now. !!!")
            if book and time.time() - last_bookchk >= 60:          # order-book watchdog
                last_bookchk = time.time()
                vs = sum(book.version.values()) if book.version else 0
                live_now = {t for t in markets if 0 < (s2c_of(t) or 0) <= 900}
                book_stale = book_stale + 60 if (live_now and vs == last_vs) else 0
                last_vs = vs
                if book_stale >= 300:
                    say("[book] no order-book updates for 5 min — restarting the book feed")
                    try: book.stop()
                    except Exception: pass
                    try: book = BOOK = make_book(k); book.track(live_now)
                    except Exception as e: print(f"[book] restart failed ({e})"); book = None
                    book_stale, last_vs = 0, -1
            time.sleep(LOOP_SEC)
    except KeyboardInterrupt:
        for key in STRATS:
            r, ro = run[key], run_off[key]
            print(f"[stopped] {STRAT_NAMES[key[0]]} {key[1]}: net {r['net']:+.0f}c over {r['closed']} closed positions "
                  f"({r['won']} won) · guard-off trades only: {ro['net']:+.0f}c over {ro['closed']}")
    finally:
        feed.stop(); spot.stop()
        if book: book.stop()
        with io_lock:
            for fh in (tfh, kfh, ffh, sfh): fh.close()

FEED = None
SPOT = None
BOOK = None
