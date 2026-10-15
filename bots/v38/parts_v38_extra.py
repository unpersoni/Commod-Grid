
# ---------------- V38: crypto spot only (Pyth needs a paid key) ----------------
class CryptoSpots:
    def __init__(self): self.cb = SpotFeed()
    def start(self): self.cb.start()
    def stop(self): self.cb.stop()
    def price(self, a): return self.cb.price(a) if a in SPOT_ASSET_MAP else None
    def change_pct(self, a, secs): return self.cb.change_pct(a, secs) if a in SPOT_ASSET_MAP else None

def make_spot():
    s = CryptoSpots(); s.start(); return s

# ---------------- V38: reversal-guard history at start-up ----------------
def _cents(v):
    """A candle field: {'close': 56} or {'close_dollars': '0.56'} -> 56."""
    if not isinstance(v, dict): return None
    for key in ("close_dollars", "close"):
        x = v.get(key)
        if x is None: continue
        try: f = float(x)
        except (TypeError, ValueError): continue
        return round(f * 100) if (key.endswith("_dollars") or (isinstance(x, str) and "." in x)) else round(f)
    return None

def minute3_leader(sess, k, ser, ticker, open_ts):
    """True if YES led 3 minutes into the market, False if NO led, None if unknown/tied."""
    d = api_get(sess, k, f"/series/{ser}/markets/{ticker}/candlesticks",
                {"start_ts": open_ts, "end_ts": open_ts + 240, "period_interval": 1}) or {}
    best = None
    for c in d.get("candlesticks", []) or []:
        try: end = int(c.get("end_period_ts"))
        except (TypeError, ValueError): continue
        if end <= open_ts + 180 and (best is None or end > best[0]): best = (end, c)
    if not best: return None
    c = best[1]
    yb, ya = _cents(c.get("yes_bid")), _cents(c.get("yes_ask"))
    mid = (yb + ya) / 2 if yb is not None and ya is not None else _cents(c.get("price"))
    if mid is None or mid == 50: return None
    return mid > 50

def load_guard_history(sess, k, hist):
    """Fill the guard with the last hour of settled markets so it is ready at start-up."""
    try:
        t1 = int(time.time()); t0 = t1 - GUARD_LOOKBACK_MIN * 60 - 120
        jobs = []
        for ser in SERIES:
            d = api_get(sess, k, "/markets", {"series_ticker": ser, "min_close_ts": t0, "max_close_ts": t1, "limit": 20})
            ms = (d or {}).get("markets", []) or []
            for m in ms:
                ct = parse_iso(m.get("close_time"))
                if m.get("result") in ("yes", "no") and ct:
                    jobs.append((ser, m["ticker"], ct, m["result"]))
        def one(j):
            ser, t, ct, res = j
            led = minute3_leader(sess, k, ser, t, int(ct.timestamp()) - 900)
            return None if led is None else (ct, cls(asset(t)), int(led != (res == "yes")))
        with ThreadPoolExecutor(max_workers=8) as ex:
            got = [x for x in ex.map(one, jobs) if x]
        for x in sorted(got): hist.append(x)
        msg = []
        for c in ("crypto", "commodity"):
            xs = [r for _, cc, r in got if cc == c]
            msg.append(f"{c} {sum(xs)}/{len(xs)} reversed" if xs else f"{c} none found")
        print(f"[guard] start-up history from Kalshi: {len(jobs)} settled markets in the last hour, "
              f"{len(got)} with a minute-3 leader ({'; '.join(msg)})")
        if len(got) < GUARD_MIN_MARKETS:
            print("[guard] not enough history yet — the guard fills in from live markets over the next hour")
    except Exception as e:
        print(f"[guard] start-up history not available ({e}) — the guard fills in from live markets over the next hour")
