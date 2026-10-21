# ===================== SWITCHES — change these, then run this cell (it starts the bot) =====================
# To change something while the bot runs: stop this cell (the ■ button), edit below, run this cell again.
# Stopping the cell cancels every resting order. (After a Colab restart, run all cells from the top.)

DRY_RUN = True              # True  = practice: orders built but NOT sent, fills simulated from Kalshi's trades
                            # False = LIVE, REAL MONEY (a 10-second countdown lets you stop it)

SERIES = ["KXAAAGASD"]      # which reward markets: tickers starting with these (KXAAAGASD = AAA daily gas price by state)
CONTRACTS = 10              # contracts resting on EACH side (YES bid and NO bid) of each market
MAX_MARKETS = 3             # how many markets at once (the best-paying ones by the bot's estimate)
BUDGET_USD = 60             # most money tied up at once: resting orders + contracts held, all markets together

MAX_NET = 20                # per market: at most 20 more contracts on one side than the other (that side stops quoting)
MAX_LOSS_USD = 15           # if fills are down $15 (marked at the best bids): everything cancelled, no new quotes this run

main()
