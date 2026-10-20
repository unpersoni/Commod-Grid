# ===================== SWITCHES — change these, then run this cell (it starts the bot) =====================
# To change something while the bot runs: stop this cell (the ■ button), edit below, run this cell again.
# Only this cell needs re-running. (After a Colab restart, run all cells from the top.)

DRY_RUN = True              # True  = practice: nothing is sent to Kalshi (fills simulated)
                            # False = LIVE, REAL MONEY (a 10-second countdown lets you stop it)

TRADE_CRYPTO      = True    # BTC, ETH, SOL, XRP, DOGE                        False = not live (dry run only)
TRADE_COMMODITIES = False   # GOLD, SILVER, WTI, COPPER, PLATINUM, PALLADIUM  False = not live (dry run only)

INVERSE_MARKETS = ["WTI"]   # these markets buy the UNDERDOG instead of the favourite (dry run experiment)
SKIP_MARKETS = []           # take single markets off live trading by name, e.g. ["WTI"] or ["DOGE", "PALLADIUM"]
                            # names: BTC ETH SOL XRP DOGE GOLD SILVER WTI COPPER PLATINUM PALLADIUM
                            # Markets that are off are still followed in DRY RUN, so the log shows every market.

CONTRACTS = 10              # contracts per buy
DAILY_LOSS_LIMIT_USD = 50   # once a 15-minute round has settled, if today's (UTC) live net is below -$50:
                            # no new live buys until midnight UTC (no limit on gains)

main()
