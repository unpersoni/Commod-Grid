
# ---------------- Kalshi order book depth (logging only, its own connection) ----------------
class BookFeed(PriceFeed):
    """Resting orders per price level from the orderbook_delta channel. Runs on a
    separate websocket so a problem here can never stop the price feed or trading."""
    CHANNELS = ("orderbook_delta",)

    def __init__(self, k):
        super().__init__(k)
        self.books = {}        # ticker -> {"yes": {price_c: qty}, "no": {price_c: qty}}
        self.version = {}      # ticker -> change counter
        self.last_seq = None
        self.check_seq, self.gaps = True, 0

    def _subscribe(self, tickers):
        with self.lock:
            self.books.clear(); self.last_seq = None    # fresh snapshots follow
        super()._subscribe(tickers)

    @staticmethod
    def _levels(m, side):
        for key in (side + "_dollars_fp", side + "_dollars", side):
            lv = m.get(key)
            if lv is None: continue
            out = {}
            for p, q in lv:
                try:
                    pc = round(float(p) * 100) if "dollars" in key else round(float(p))
                    q = float(q)
                except (TypeError, ValueError): continue
                if q > 0: out[pc] = q
            return out
        return {}

    def _on_message(self, raw):
        try: msg = json.loads(raw)
        except (json.JSONDecodeError, TypeError): return
        mtype = msg.get("type")
        if mtype not in ("orderbook_snapshot", "orderbook_delta"):
            return super()._on_message(raw)
        seq = msg.get("seq")
        if self.check_seq and seq is not None and self.last_seq is not None and seq != self.last_seq + 1:
            now_g = time.time()
            self.gap_times = [x for x in getattr(self, "gap_times", []) if x > now_g - 3600] + [now_g]
            if len(self.gap_times) <= 5:            # at most 5 resubscribes per hour
                print(f"[book] sequence gap ({self.last_seq} -> {seq}) — resubscribing for fresh snapshots")
                try: self.ws.close()       # the run loop reconnects and resubscribes
                except Exception: pass
                return
            elif len(self.gap_times) == 6:
                print("[book] many sequence gaps this hour — not resubscribing again until the hour passes")
        if seq is not None: self.last_seq = seq
        m = msg.get("msg") or {}
        t = m.get("market_ticker")
        if not t or t not in self.tracked: return    # only our markets
        with self.lock:
            if mtype == "orderbook_snapshot":
                self.books[t] = {"yes": self._levels(m, "yes"), "no": self._levels(m, "no")}
            else:
                b = self.books.get(t)
                side = m.get("side")
                p = px_c(m, "price")
                try: d = float(m.get("delta_fp", m.get("delta")))
                except (TypeError, ValueError): return
                if b is None or side not in ("yes", "no") or p is None: return
                q = b[side].get(p, 0) + d
                if q > 0: b[side][p] = q
                else: b[side].pop(p, None)
            self.version[t] = self.version.get(t, 0) + 1

    def top(self, t, n=3):
        """([(price, qty)] best YES bids, [(price, qty)] best NO bids), best first."""
        if not self.ready.is_set(): return None
        with self.lock:
            b = self.books.get(t)
            if b is None: return None
            return (sorted(b["yes"].items(), reverse=True)[:n], sorted(b["no"].items(), reverse=True)[:n])

def make_book(k):
    f = BookFeed(k); f.start(); return f
