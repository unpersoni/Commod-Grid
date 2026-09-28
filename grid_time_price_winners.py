"""
Grid search: Window-start × Favorite-entry-price-BAND → total profit (winners only).

Sweeps two variables across every recorded 15-min contract window:
  1. window_start  – minutes before close when the bot begins watching.
                     Range 7.0 → ~0.17 (10 sec before close), step 10 sec.
  2. entry_price   – favorite-side price BAND to trigger entry.
                     Range 51¢ → 90¢, step 1¢.
                     Band width = 5¢, so threshold=54 means enter only when
                     the favorite is priced between 54¢ and 59¢.

For each combo, for each historical window+ticker:
  - From window_start onward, find the first tick where the favorite
    (whichever side has mid > 50) is priced within [threshold, threshold+5].
  - If found, the bot enters at the actual market mid price at that tick.
  - Settlement: avg yes_mid over the final 30s of ticks.
    If > 50 → yes won (settle 100); else no won (settle 0).
  - If the bot's side won: profit = (100 − actual_entry_price) × LOTS.
  - Losses are IGNORED ($0) per the task spec.

Run in Colab with Drive mounted, or locally with the CSV path adjusted.
"""

import pandas as pd
import numpy as np
import time

# ── paths (Colab / Drive) ────────────────────────────────────────────
TICK_CSV = "/content/drive/MyDrive/commod15min_ticks.csv"
OUT_OVERALL = "/content/drive/MyDrive/grid_tp_overall.csv"
OUT_BY_ASSET = "/content/drive/MyDrive/grid_tp_by_asset.csv"
OUT_TOP_OVERALL = "/content/drive/MyDrive/grid_tp_top100.csv"
OUT_TOP_ASSET = "/content/drive/MyDrive/grid_tp_top20_per_asset.csv"

# ── parameters ────────────────────────────────────────────────────────
LOTS = 10
SETTLE_WINDOW_SEC = 30.0
BAND_WIDTH_C = 5.0  # entry band = [threshold, threshold + 5¢]

WINDOW_STEP_SEC = 10.0
window_grid = np.arange(
    WINDOW_STEP_SEC / 60.0,
    7.0 + WINDOW_STEP_SEC / 60.0,
    WINDOW_STEP_SEC / 60.0,
)[::-1]  # 7.0, 6.83, ... 0.17  (minutes before close)

price_grid = np.arange(51.0, 91.0, 1.0)  # 51, 52, ..., 90 cents

print(f"window steps : {len(window_grid)}  ({window_grid[0]:.2f} -> {window_grid[-1]:.2f} min before close)")
print(f"price steps  : {len(price_grid)}  ({price_grid[0]:.0f}c -> {price_grid[-1]:.0f}c, band width {BAND_WIDTH_C:.0f}c)")
print(f"total combos : {len(window_grid) * len(price_grid)}")


def determine_settlement(minute, ymid):
    sec_to_close = (15.0 - minute) * 60.0
    final_mask = sec_to_close <= SETTLE_WINDOW_SEC
    if final_mask.any():
        return ymid[final_mask].mean() > 50.0
    return ymid[-1] > 50.0


def process_window(minute, ymid, yes_won):
    """For every (window_start, entry_price) combo, find the first tick
    where the favorite is within the price band [threshold, threshold+5].
    Returns dict  (ws, ep) -> (won: bool, profit_c: float).
    Only combos where an entry was triggered are included.
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

        for ep in price_grid:
            band_top = ep + BAND_WIDTH_C
            hits = (valid_fav >= ep) & (valid_fav <= band_top)
            if not hits.any():
                continue
            first_hit = np.argmax(hits)
            entry_i = valid_idx[first_hit]
            actual_px = fav_price[entry_i]
            is_yes = fav_is_yes[entry_i]
            bot_won = (is_yes and yes_won) or (not is_yes and not yes_won)
            profit = (100.0 - actual_px) * LOTS if bot_won else 0.0
            results[(round(float(ws), 4), float(ep))] = (bot_won, profit)

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

    # agg[(ws, ep, asset)] = [n_entries, n_wins, total_profit_c]
    agg = {}
    report_every = max(1, n_windows // 20)

    for wi, (asset, minute, ymid, yes_won) in enumerate(windows):
        res = process_window(minute, ymid, yes_won)
        for (ws, ep), (won, profit) in res.items():
            key = (ws, ep, asset)
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
    for (ws, ep, asset), (n_ent, n_win, prof) in agg.items():
        rows.append({
            "window_min_before_close": ws,
            "entry_band_low_c": ep,
            "entry_band_high_c": ep + BAND_WIDTH_C,
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
        .groupby(["window_min_before_close", "entry_band_low_c", "entry_band_high_c"])
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
    overall.head(100).to_csv(OUT_TOP_OVERALL, index=False)
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

    print(f"\n{'='*70}")
    print("TOP 15 COMBOS BY TOTAL PROFIT (all assets, winners only)")
    print(f"  entry band = [low, low + {BAND_WIDTH_C:.0f}c]")
    print("="*70)
    cols = ["window_min_before_close", "entry_band_low_c", "entry_band_high_c",
            "n_entries", "n_wins", "win_rate",
            "total_profit_c", "avg_profit_per_entry_c"]
    print(overall[cols].head(15).to_string(index=False))

    assets = sorted(detail["asset"].unique())
    print(f"\n{'='*70}")
    print(f"TOP 5 PER ASSET  ({len(assets)} assets)")
    print("="*70)
    for a in assets:
        sub = (detail[detail["asset"] == a]
               .sort_values("total_profit_c", ascending=False))
        if sub.empty:
            continue
        print(f"\n  {a}:")
        print(sub[cols].head(5).to_string(index=False))

    return overall, detail


if __name__ == "__main__":
    run()
