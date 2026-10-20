# ===================== SWITCHES — change these, then run this cell (it starts the bot) =====================
# To change something while the bot runs: stop this cell (the ■ button), edit below, run this cell again.
# Only this cell needs re-running. (After a Colab restart, run all cells from the top.)

DRY_RUN = True              # True  = practice: nothing is sent to Kalshi (fills simulated)
                            # False = LIVE, REAL MONEY for the trade types switched on below (10-second countdown first);
                            #         the ones switched off keep running in dry run

# True = this trade type is LIVE when DRY_RUN = False (★ON in the log while in dry run); False = dry run only.
# Every trade type is always followed, so the log shows all four side by side.
FAV_CRYPTO        = True    # favourite: bids on a side at 85-98c, crypto in the first 5 minutes only
FAV_COMMODITIES   = True    #            commodities whole market
LEAN_CRYPTO       = False   # leader: bids on the leading side at 75-84c, crypto in the first 5 minutes only
LEAN_COMMODITIES  = False   #         commodities whole market

SKIP_MARKETS = []           # markets never live, by name, e.g. ["WTI"] or ["DOGE", "PALLADIUM"]
                            # names: BTC ETH SOL XRP DOGE GOLD SILVER WTI COPPER PLATINUM PALLADIUM

CONTRACTS = 10              # contracts per order

main()
