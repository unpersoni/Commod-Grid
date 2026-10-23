# ===================== SWITCHES — change these, then run this cell (it starts the bot) =====================
# To change something while the bot runs: stop this cell (the ■ button), edit below, run this cell again.
# Stopping the cell cancels every resting order. (After a Colab restart, run all cells from the top.)

DRY_RUN = True              # True  = practice: orders built but NOT sent, fills simulated (each order waits its turn in line)
                            # False = LIVE, REAL MONEY (a 10-second countdown lets you stop it)

SERIES = ["ALL"]            # ["ALL"] = every 15-minute series with a reward program; or a list like ["KXBRENT15M", "KX2YRRATE15M"]
SKIP_SERIES = []            # series never to quote, e.g. ["KXCRYPTOLEAD15M"]
CONTRACTS = 10              # contracts resting on EACH side (YES bid and NO bid) of each market
MAX_MARKETS = 25            # most markets per 15-minute window
MAX_PER_STATE = 5           # most markets per series per window (the crypto-lead series has 5 coins)
BUDGET_USD = 250            # most money tied up at once: resting orders + contracts held, all markets together

MAX_NET = 20                # per market: at most 20 more contracts on one side than the other
GROUP_MAX_NET = 30          # per group that moves together (crypto, rates, FX, metals, energy, crypto-lead):
                            # unpaired contracts added up across its markets; at the limit only pairing sides are quoted
MAX_LOSS_USD = 60           # if fills are down $60: everything cancelled, no new quotes this run

main()
