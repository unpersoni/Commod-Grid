# ===================== SWITCHES — change these, then run this cell (it starts the bot) =====================
# To change something while the bot runs: stop this cell (the ■ button), edit below, run this cell again.
# Only this cell needs re-running. (After a Colab restart, run all cells from the top.)

DRY_RUN = True              # must stay True in MM15Min V3 (live trading for resting orders is not built yet)

# True = this strategy would trade this class LIVE (★ON in the log); False = dry run only.
# Every strategy is always followed in dry run, so the log shows all six side by side.
FAV_CRYPTO        = True    # favourite: bids on any side at 85-98c, whole market
FAV_COMMODITIES   = True
LEAN_CRYPTO       = False   # leader: crypto 75-90c in the first 5 minutes only
LEAN_COMMODITIES  = False   #         commodities 75-84c, whole market
MM_CRYPTO         = False   # two-sided: both sides at 3-97c
MM_COMMODITIES    = False

SKIP_MARKETS = []           # markets never live, by name, e.g. ["WTI"] or ["DOGE", "PALLADIUM"]
                            # names: BTC ETH SOL XRP DOGE GOLD SILVER WTI COPPER PLATINUM PALLADIUM

CONTRACTS = 10              # contracts per order
DAILY_LOSS_LIMIT_USD = 20   # once today's (UTC) net of the switched-on strategies is below -$20: no new orders
                            # (no limit on gains; in dry run it is only reported)

main()
