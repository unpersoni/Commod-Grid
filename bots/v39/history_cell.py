# ===================== HISTORY — run this cell to see how the last cycles went (nothing is traded) =====================
# V39's rule rebuilt from Kalshi's market history: what it would have bought in each market's first minute and how
# it settled. Per class, per price band and per market, with streaks. Run it before starting the bot (Colab runs one
# cell at a time, so stop the bot first if it is running). Needs the BOT CODE cell above to have been run once.

HISTORY_CYCLES = 5          # how many of the last 15-minute cycles to show (1-20)

show_history(HISTORY_CYCLES)
