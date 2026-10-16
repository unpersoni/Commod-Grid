import os
SP = os.path.dirname(os.path.abspath(__file__))
rd = lambda f: open(f"{SP}/{f}").read()
imports = rd("parts_imports.py").replace("import os, sys,", "import os, sys, collections,")
src = rd("head_v38.py") + "\n" + imports + rd("settings_v38.py") + rd("parts_helpers_v37.py") + rd("parts_spot2.py") \
      + rd("parts_v38_extra.py") + rd("parts_book_v38.py") + rd("parts_bot_v38.py")
for must in ("RULES = {", '"buy_min": 88', '"buy_min": 85', '"until_sec": 720', '"until_sec": 420', "SELL_AT_C     = None",
             "load_guard_history", "class CryptoSpots", "FLOW_COLS", "yes_bid_qty", "gap_times", "commod15min_v38_data/",
             "if t not in self.tracked: return", "strike_due", "LOW_SPACE_MB", "REBOUND_DIP_C        = 5", "CROSS_MIN_Z        = 1.5", "def var_per_sec", "\"strategy\"", "REBOUND_CLASSES"):
    assert must in src, must
for bad in ("commod15min_v37", "prints_", '"book", BOOK_COLS', "BUY_MIN_C", "min_free_mb"):
    assert bad not in src, bad
compile(src, "v38", "exec")
open(f"{SP}/v38_src.py", "w").write(src); print("v38", len(src))
