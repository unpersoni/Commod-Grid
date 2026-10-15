
# ---------------- settings ----------------
BOT_NAME  = "Commod15min V38"
PEM_PATH  = '/content/drive/MyDrive/intraday key.pem'
KEY_ID    = "__KEY_ID__"
DRIVE     = '/content/drive/MyDrive/'
TRADES_CSV = DRIVE + 'commod15min_v38_trades.csv'
SETTLE_CSV = DRIVE + 'commod15min_v38_settlements.csv'
DATA_DIR   = DRIVE + 'commod15min_v38_data/'      # ticks_ and flow_YYYY-MM-DD_HH.csv(.gz), one file per hour (UTC)

SERIES = ["KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M",
          "KXGOLD15M", "KXSILVER15M", "KXWTI15M", "KXCOPPER15M",
          "KXPLATINUM15M", "KXPALLADIUM15M"]
TRADE_CLASSES = {"crypto", "commodity"}

# "early favs": buy the side that is already a strong favourite early in the market, hold to settlement
RULES = {
    "crypto":    {"buy_min": 88, "buy_max": 95, "from_sec": 900, "until_sec": 720},   # first 3 minutes
    "commodity": {"buy_min": 85, "buy_max": 95, "from_sec": 900, "until_sec": 420},   # first 8 minutes
}
SELL_AT_C     = None   # no sell target: every position is held to settlement
MAX_TRADES_PER_MARKET = 1   # per strategy

# "rebound" (crypto): a second buy after an early-fav buy, if that side dips and turns back up
REBOUND_ON           = True
REBOUND_DIP_C        = 5    # the side's bid must fall at least 5c below the early-fav buy price ...
REBOUND_TURN_C       = 2    # ... then buy when the bid is 2c above its lowest point since the buy
REBOUND_MIN_LEFT_SEC = 30   # not in the last 30 seconds

# "spot-confirmed cross" (crypto): a side's ask crosses 90c while Coinbase spot is already well past the strike
CROSS_ON           = True
CROSS_LEVEL_C      = 90     # the ask moves from below 90c ...
CROSS_MAX_C        = 95     # ... to 90-95c
CROSS_MIN_Z        = 1.5    # spot past the strike by >= 1.5 normal moves (15-min volatility x time left)
CROSS_MIN_LEFT_SEC = 5

STRAT_NAMES = {"fav": "early fav", "rebound": "rebound", "cross": "spot cross"}
CONTRACTS     = 10
MAX_SPREAD_C  = 5      # skip a buy when ask - bid is wider than this
DEADBAND_SEC  = 2      # never act this close to the close

# reversal guard: share of recent markets (same class) whose minute-3 leader ended up losing
GUARD_LOOKBACK_MIN = 60
GUARD_ON_SHARE     = 0.35
GUARD_MIN_MARKETS  = 10

FLOW_BUCKET_SEC  = 15     # trade flow is summarised per market every 15 seconds
STATUS_EVERY_SEC = 900    # status line every 15 minutes
LOW_SPACE_MB     = 200    # loud warning when Drive has less free space than this

MARKETS_REFRESH_SEC = 5
LOOP_SEC      = 0.5
SETTLE_RETRY_SEC  = 10
SETTLE_GIVEUP_SEC = 1800

BASE    = "https://api.elections.kalshi.com/trade-api/v2"
PREF    = "/trade-api/v2"
WS_URL  = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
WS_PATH = "/trade-api/ws/v2"

TRADE_COLS = ["window", "asset", "class", "strategy", "mode", "side", "buy_iso", "buy_s2c", "buy_px",
              "sell_iso", "sell_s2c", "sell_px", "result", "reason", "lowest_bid_held",
              "fees_c", "net_c", "run_net_c"]
TICK_COLS   = ["ts_iso", "ticker", "asset", "yes_bid", "yes_ask", "sec_to_close", "spot_px",
               "yes_bid_qty", "no_bid_qty"]
FLOW_COLS   = ["bucket_iso", "ticker", "asset", "sec_to_close_end", "n_yes_taker", "ct_yes_taker",
               "n_no_taker", "ct_no_taker", "avg_yes_px", "last_yes_px"]
SETTLE_COLS = ["logged_iso", "ticker", "asset", "close_time", "result", "floor_strike",
               "expiration_value", "status"]
