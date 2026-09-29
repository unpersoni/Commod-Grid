"""
Favorite at 90c Analysis
========================
For each 15-minute contract in each market: does the favorite reach 90c?
If yes, does it win at settlement?

Strategy: buy favorite at 90c across all 11 markets.
  - Profit per win: (100 - 90) × 10 lots = $1.00
  - Loss per loss: 90 × 10 lots = $9.00 (before stop-loss)
  - Goal: $1 × 11 markets × 4 cycles/hr = $44/hr
"""

import pandas as pd
import numpy as np
import time

TICK_CSV = "/content/drive/MyDrive/commod15min_ticks.csv"
OUT_CSV  = "/content/drive/MyDrive/fav90_analysis.csv"

SETTLE_WINDOW_SEC = 30.0
LOTS = 10

WINDOWS = [420, 360, 300, 240, 180, 120, 90, 60, 50]
THRESHOLDS = [85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95]


def determine_settlement(minute, ymid):
    sec_to_close = (15.0 - minute) * 60.0
    mask = sec_to_close <= SETTLE_WINDOW_SEC
    if mask.any():
        return ymid[mask].mean() > 50.0
    return ymid[-1] > 50.0


if __name__ == "__main__":
    t0 = time.time()
    print(f"loading {TICK_CSV} ...")
    df = pd.read_csv(TICK_CSV, usecols=[
        "ticker", "asset", "yes_mid", "sec_to_close",
    ])
    df = df.dropna(subset=["yes_mid", "sec_to_close"])
    df["minute_in_cycle"] = 15.0 - df["sec_to_close"] / 60.0
    df = df[(df["minute_in_cycle"] >= 0) & (df["minute_in_cycle"] <= 15)]

    windows = []
    for ticker, g in df.groupby("ticker", sort=False):
        g = g.sort_values("minute_in_cycle")
        minute = g["minute_in_cycle"].to_numpy()
        ymid   = g["yes_mid"].to_numpy(dtype=float)
        stc    = g["sec_to_close"].to_numpy(dtype=float)
        asset  = g["asset"].iloc[0]
        yes_won = determine_settlement(minute, ymid)
        windows.append((asset, minute, ymid, stc, yes_won))

    print(f"loaded {len(windows)} contracts in {time.time()-t0:.1f}s")

    rows = []
    for asset_name in sorted(set(a for a, _, _, _, _ in windows)):
        asset_windows = [(m, y, s, w) for a, m, y, s, w in windows if a == asset_name]
        n_contracts = len(asset_windows)

        for win_sec in WINDOWS:
            for thresh in THRESHOLDS:
                contracts_with_hit = 0
                fav_wins = 0
                fav_losses = 0
                entry_prices = []

                for minute, ymid, stc, yes_won in asset_windows:
                    mask = stc <= win_sec
                    if not mask.any():
                        continue

                    w_ymid = ymid[mask]
                    fav_price = np.maximum(w_ymid, 100.0 - w_ymid)
                    fav_is_yes = w_ymid >= 50.0

                    hits = fav_price >= thresh
                    if not hits.any():
                        continue

                    contracts_with_hit += 1
                    fi = int(np.argmax(hits))
                    px = float(fav_price[fi])
                    iy = bool(fav_is_yes[fi])
                    won = (iy and yes_won) or (not iy and not yes_won)

                    entry_prices.append(px)
                    if won:
                        fav_wins += 1
                    else:
                        fav_losses += 1

                if contracts_with_hit == 0:
                    continue

                avg_entry = np.mean(entry_prices)
                rows.append({
                    'asset': asset_name,
                    'window_sec': win_sec,
                    'threshold_c': thresh,
                    'total_contracts': n_contracts,
                    'contracts_with_hit': contracts_with_hit,
                    'hit_rate': round(contracts_with_hit / n_contracts, 4),
                    'fav_wins': fav_wins,
                    'fav_losses': fav_losses,
                    'win_rate': round(fav_wins / contracts_with_hit, 4),
                    'avg_entry_price_c': round(avg_entry, 2),
                    'profit_per_win_c': round((100.0 - avg_entry) * LOTS, 1),
                    'loss_per_loss_c': round(avg_entry * LOTS, 1),
                })

    result = pd.DataFrame(rows)
    result.to_csv(OUT_CSV, index=False)
    print(f"wrote {OUT_CSV} ({len(result)} rows)")
    print(f"done in {time.time()-t0:.1f}s")
