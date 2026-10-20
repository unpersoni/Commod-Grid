# ===================== HISTORY — run this cell to see how the last cycles went (nothing is traded) =====================
# Per trade type, from the bot's own results files on Drive (mm15min_v4_markets.csv, then V3's): markets, contracts,
# % won, net, c per contract, streak and the results market by market. Run it while the bot is stopped (Colab runs one
# cell at a time). Needs the BOT CODE cell above to have been run once.

HISTORY_CYCLES = 5          # how many of the last 15-minute cycles to show (1-96; 96 = a whole day)

show_history(HISTORY_CYCLES)
