"""
Per-Second × Per-Cent Grid Search (Tiered Entry)
=================================================
Sweeps every second (50s → 420s) and every cent (55c → 90c max entry price)
per market. Tiered entry: safe zone (last 50s) first, fallback to wider
observation window only when no safe-zone tick qualifies.

The 'max_entry_c' parameter is the ceiling: the most the bot will pay
for the favorite position.  Floor is fixed at 51c (any favorite).

Output CSVs are written to Google Drive.
"""

import pandas as pd
import numpy as np
import time

# ── paths ──────────────────────────────────────────────────────
TICK_CSV       = "/content/drive/MyDrive/commod15min_ticks.csv"
OUT_OVERALL    = "/content/drive/MyDrive/sp_grid_overall.csv"
OUT_BY_ASSET   = "/content/drive/MyDrive/sp_grid_by_asset.csv"
OUT_BEST_ASSET = "/content/drive/MyDrive/sp_grid_best_per_asset.csv"

# ── constants ──────────────────────────────────────────────────
LOTS              = 10
SETTLE_WINDOW_SEC = 30.0
SAFE_SEC          = 50.0
SAFE_MINUTE       = 15.0 - SAFE_SEC / 60.0   # 14.1667
ENTRY_FLOOR       = 51.0

# ── grids ──────────────────────────────────────────────────────
max_entry_grid = np.arange(55.0, 91.0, 1.0)   # 55c → 90c, step 1c
obs_grid_sec   = np.arange(50.0, 421.0, 1.0)  # 50s → 420s, step 1s


def determine_settlement(minute, ymid):
    sec_to_close = (15.0 - minute) * 60.0
    mask = sec_to_close <= SETTLE_WINDOW_SEC
    if mask.any():
        return ymid[mask].mean() > 50.0
    return ymid[-1] > 50.0
