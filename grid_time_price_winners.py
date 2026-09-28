"""
Grid search: Window-start × Floor × Ceiling → total profit (winners only).

Sweeps three variables across every recorded 15-min contract window:
  1. window_start  – minutes before close when the bot begins watching.
                     Range 7.0 → ~0.17 (10 sec before close), step 10 sec.
  2. floor         – minimum favorite price to trigger entry (51¢ → 85¢, step 1¢).
  3. ceiling       – maximum favorite price to trigger entry
                     (floor+5 → 95¢, step 5¢).

The bot enters at the FIRST tick where the favorite is within [floor, ceiling].
If it's at 55¢ first, it enters at 55¢ (big payout).  If the market never
dips below 72¢, it enters at 72¢ (smaller payout, but still a trade).

Losses are IGNORED ($0) per the task spec — only winning settlements count.
"""

import pandas as pd
import numpy as np
import time

# ── paths (Colab / Drive) ────────────────────────────────────────────
TICK_CSV = "/content/drive/MyDrive/commod15min_ticks.csv"
OUT_OVERALL = "/content/drive/MyDrive/grid_fc_overall.csv"
OUT_BY_ASSET = "/content/drive/MyDrive/grid_fc_by_asset.csv"
OUT_TOP_OVERALL = "/content/drive/MyDrive/grid_fc_top200.csv"
OUT_TOP_ASSET = "/content/drive/MyDrive/grid_fc_top20_per_asset.csv"

# ── parameters ────────────────────────────────────────────────────────
LOTS = 10
SETTLE_WINDOW_SEC = 30.0

WINDOW_STEP_SEC = 10.0
window_grid = np.arange(
    WINDOW_STEP_SEC / 60.0,
    7.0 + WINDOW_STEP_SEC / 60.0,
    WINDOW_STEP_SEC / 60.0,
)[::-1]  # 7.0, 6.83, ... 0.17  (minutes before close)

floor_grid = np.arange(51.0, 86.0, 1.0)   # 51, 52, ..., 85
ceiling_step = 5.0
max_ceiling = 95.0

print(f"window steps : {len(window_grid)}  ({window_grid[0]:.2f} -> {window_grid[-1]:.2f} min before close)")
print(f"floor steps  : {len(floor_grid)}  ({floor_grid[0]:.0f}c -> {floor_grid[-1]:.0f}c)")
print(f"ceiling step : {ceiling_step:.0f}c  (from floor+5 up to {max_ceiling:.0f}c)")


def determine_settlement(minute, ymid):
    sec_to_close = (15.0 - minute) * 60.0
    final_mask = sec_to_close <= SETTLE_WINDOW_SEC
    if final_mask.any():
        return ymid[final_mask].mean() > 50.0
    return ymid[-1] > 50.0


def process_window(minute, ymid, yes_won):
    """For every (window_start, floor, ceiling) combo, find the first tick
    where the favorite is within [floor, ceiling].
    Returns dict  (ws, floor, ceiling) -> (won: bool, profit_c: float).
    """
    n = len(minute)
    if n == 0:
        return {}

    fav_price = np.maximum(ymid, 100.0 - ymid)
    fav_is_yes = ymid >= 50.0

    results = {}

    for ws in window_grid:
        open_minute = 15.0 - ws
        valid = minute >= open_minute
        if not valid.any():
            continue
        valid_idx = np.where(valid)[0]
        valid_fav = fav_price[valid_idx]

        for fl in floor_grid:
            ceilings = np.arange(fl + ceiling_step, max_ceiling + 0.01, ceiling_step)
            for ceil_val in ceilings:
                hits = (valid_fav >= fl) & (valid_fav <= ceil_val)
                if not hits.any():
                    continue
                first_hit = np.argmax(hits)
                entry_i = valid_idx[first_hit]
                actual_px = fav_price[entry_i]
                is_yes = fav_is_yes[entry_i]
                bot_won = (is_yes and yes_won) or (not is_yes and not yes_won)
                profit = (100.0 - actual_px) * LOTS if bot_won else 0.0
                results[(round(float(ws), 4), float(fl), float(ceil_val))] = (bot_won, profit)

    return results


def run(tick_csv=TICK_CSV):
    t0 = time.time()
    print(f"loading {tick_csv} ...")
    df = pd.read_csv(tick_csv, usecols=[
        "ticker", "asset", "yes_bid", "yes_ask", "yes_mid", "sec_to_close"
    ])
    df = df.dropna(subset=["yes_mid", "sec_to_close"])
    df["minute_in_cycle"] = 15.0 - df["sec_to_close"] / 60.0
    df = df[(df["minute_in_cycle"] >= 0) & (df["minute_in_cycle"] <= 15)]

    windows = []
    for ticker, g in df.groupby("ticker", sort=False):
        g = g.sort_values("minute_in_cycle")
        minute = g["minute_in_cycle"].to_numpy()
        ymid = g["yes_mid"].to_numpy(dtype=float)
        asset = g["asset"].iloc[0]
        yes_won = determine_settlement(minute, ymid)
        windows.append((asset, minute, ymid, yes_won))

    n_windows = len(windows)
    print(f"loaded {n_windows} windows in {time.time()-t0:.1f}s")

    # count combos for estimate
    n_combos = 0
    for fl in floor_grid:
        n_combos += len(np.arange(fl + ceiling_step, max_ceiling + 0.01, ceiling_step))
    n_combos *= len(window_grid)
    print(f"total combos per window: {n_combos}")

    # agg[(ws, floor, ceiling, asset)] = [n_entries, n_wins, total_profit_c]
    agg = {}
    report_every = max(1, n_windows // 20)

    for wi, (asset, minute, ymid, yes_won) in enumerate(windows):
        res = process_window(minute, ymid, yes_won)
        for (ws, fl, ceil_val), (won, profit) in res.items():
            key = (ws, fl, ceil_val, asset)
            if key not in agg:
                agg[key] = [0, 0, 0.0]
            agg[key][0] += 1
            if won:
                agg[key][1] += 1
                agg[key][2] += profit
        if (wi + 1) % report_every == 0:
            pct = 100 * (wi + 1) / n_windows
            print(f"  {wi+1}/{n_windows} ({pct:.0f}%) -- {time.time()-t0:.0f}s elapsed")

    # ── per-asset detail ──────────────────────────────────────────────
    rows = []
    for (ws, fl, ceil_val, asset), (n_ent, n_win, prof) in agg.items():
        rows.append({
            "window_min_before_close": ws,
            "entry_floor_c": fl,
            "entry_ceiling_c": ceil_val,
            "band_width_c": ceil_val - fl,
            "asset": asset,
            "n_entries": n_ent,
            "n_wins": n_win,
            "win_rate": round(n_win / n_ent, 4) if n_ent else 0,
            "total_profit_c": round(prof, 1),
            "avg_profit_per_entry_c": round(prof / n_ent, 2) if n_ent else 0,
        })
    detail = pd.DataFrame(rows)
    detail.to_csv(OUT_BY_ASSET, index=False)
    print(f"\nwrote {OUT_BY_ASSET} ({len(detail)} rows)")

    # ── overall (all assets combined) ─────────────────────────────────
    overall = (
        detail
        .groupby(["window_min_before_close", "entry_floor_c", "entry_ceiling_c", "band_width_c"])
        .agg(
            n_entries=("n_entries", "sum"),
            n_wins=("n_wins", "sum"),
            total_profit_c=("total_profit_c", "sum"),
        )
        .reset_index()
    )
    overall["win_rate"] = (overall["n_wins"] / overall["n_entries"]).round(4)
    overall["avg_profit_per_entry_c"] = (
        overall["total_profit_c"] / overall["n_entries"]
    ).round(2)
    overall = overall.sort_values("total_profit_c", ascending=False)
    overall.to_csv(OUT_OVERALL, index=False)
    print(f"wrote {OUT_OVERALL} ({len(overall)} rows)")

    # ── compact top-N files ───────────────────────────────────────────
    overall.head(200).to_csv(OUT_TOP_OVERALL, index=False)
    print(f"wrote {OUT_TOP_OVERALL}")

    top_asset = (
        detail
        .sort_values("total_profit_c", ascending=False)
        .groupby("asset", group_keys=False)
        .head(20)
        .sort_values(["asset", "total_profit_c"], ascending=[True, False])
    )
    top_asset.to_csv(OUT_TOP_ASSET, index=False)
    print(f"wrote {OUT_TOP_ASSET} ({len(top_asset)} rows)")

    elapsed = time.time() - t0
    print(f"\ndone in {elapsed / 60:.1f} min")

    print(f"\n{'='*80}")
    print("TOP 20 COMBOS BY TOTAL PROFIT (all assets, winners only)")
    print("="*80)
    cols = ["window_min_before_close", "entry_floor_c", "entry_ceiling_c",
            "band_width_c", "n_entries", "n_wins", "win_rate",
            "total_profit_c", "avg_profit_per_entry_c"]
    print(overall[cols].head(20).to_string(index=False))

    assets = sorted(detail["asset"].unique())
    print(f"\n{'='*80}")
    print(f"TOP 3 PER ASSET  ({len(assets)} assets)")
    print("="*80)
    for a in assets:
        sub = (detail[detail["asset"] == a]
               .sort_values("total_profit_c", ascending=False))
        if sub.empty:
            continue
        print(f"\n  {a}:")
        print(sub[cols].head(3).to_string(index=False))

    return overall, detail


if __name__ == "__main__":
    run()
