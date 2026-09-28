"""
Tiered Entry Grid Search
========================
Two-tier entry: the bot tries the SAFE zone (last 50 seconds) first.
Only when no qualifying tick exists there does it fall back to the
wider observation window.  This captures the strong late-window entries
AND adds extra trades from the wider observation window — without
degrading safe entries by entering too early.

Sweeps: obs_window × floor × ceiling
Outputs: tiered_overall.csv, tiered_by_asset.csv,
         tiered_top200.csv, tiered_top20_per_asset.csv
"""

import pandas as pd
import numpy as np
import time

# ── paths ──────────────────────────────────────────────────────
TICK_CSV      = "/content/drive/MyDrive/commod15min_ticks.csv"
OUT_OVERALL   = "/content/drive/MyDrive/tiered_overall.csv"
OUT_BY_ASSET  = "/content/drive/MyDrive/tiered_by_asset.csv"
OUT_TOP200    = "/content/drive/MyDrive/tiered_top200.csv"
OUT_TOP_ASSET = "/content/drive/MyDrive/tiered_top20_per_asset.csv"

# ── constants ──────────────────────────────────────────────────
LOTS              = 10
SETTLE_WINDOW_SEC = 30.0
SAFE_WINDOW_SEC   = 50.0
SAFE_MINUTE       = 15.0 - SAFE_WINDOW_SEC / 60.0   # 14.1667

# ── grids ──────────────────────────────────────────────────────
obs_grid_sec = np.array([
    50, 60, 70, 80, 90, 100, 110, 120,
    150, 180, 240, 300, 420,
], dtype=float)

floor_grid   = np.arange(51.0, 86.0, 1.0)    # 51c → 85c
ceiling_step = 5.0
max_ceiling  = 95.0


def determine_settlement(minute, ymid):
    sec_to_close = (15.0 - minute) * 60.0
    mask = sec_to_close <= SETTLE_WINDOW_SEC
    if mask.any():
        return ymid[mask].mean() > 50.0
    return ymid[-1] > 50.0


def process_window(minute, ymid, yes_won):
    """Return {(obs_sec, floor, ceil): (entry_type, won, profit)} for one contract."""
    if len(minute) == 0:
        return {}

    fav_price  = np.maximum(ymid, 100.0 - ymid)
    fav_is_yes = ymid >= 50.0

    # ── safe zone (last 50 s) — constant across obs windows ───
    s_mask = minute >= SAFE_MINUTE
    s_idx  = np.where(s_mask)[0]
    s_fav  = fav_price[s_idx]  if len(s_idx) else np.array([])
    s_yes  = fav_is_yes[s_idx] if len(s_idx) else np.array([], dtype=bool)

    safe_cache = {}
    for fl in floor_grid:
        for cv in np.arange(fl + ceiling_step, max_ceiling + 0.01, ceiling_step):
            cv = float(cv)
            if len(s_fav) == 0:
                continue
            hits = (s_fav >= fl) & (s_fav <= cv)
            if not hits.any():
                continue
            fi = int(np.argmax(hits))
            px = float(s_fav[fi])
            iy = bool(s_yes[fi])
            won = (iy and yes_won) or (not iy and not yes_won)
            pr  = (100.0 - px) * LOTS if won else 0.0
            safe_cache[(fl, cv)] = (won, pr)

    # ── sweep obs windows ─────────────────────────────────────
    results = {}
    for obs_sec in obs_grid_sec:
        obs_min = 15.0 - obs_sec / 60.0

        if obs_sec > SAFE_WINDOW_SEC:
            fb_mask = (minute >= obs_min) & (minute < SAFE_MINUTE)
            fb_idx  = np.where(fb_mask)[0]
            fb_fav  = fav_price[fb_idx]
            fb_yes  = fav_is_yes[fb_idx]
            has_fb  = len(fb_idx) > 0
        else:
            has_fb = False

        for fl in floor_grid:
            for cv in np.arange(fl + ceiling_step, max_ceiling + 0.01, ceiling_step):
                cv = float(cv)
                key = (float(obs_sec), fl, cv)

                if (fl, cv) in safe_cache:
                    won, pr = safe_cache[(fl, cv)]
                    results[key] = ('S', won, pr)
                    continue

                if has_fb:
                    hits = (fb_fav >= fl) & (fb_fav <= cv)
                    if hits.any():
                        fi = int(np.argmax(hits))
                        px = float(fb_fav[fi])
                        iy = bool(fb_yes[fi])
                        won = (iy and yes_won) or (not iy and not yes_won)
                        pr  = (100.0 - px) * LOTS if won else 0.0
                        results[key] = ('F', won, pr)

    return results


# ── main ───────────────────────────────────────────────────────
if __name__ == "__main__":
    t0 = time.time()
    print(f"loading {TICK_CSV} ...")
    df = pd.read_csv(TICK_CSV, usecols=[
        "ticker", "asset", "yes_bid", "yes_ask", "yes_mid", "sec_to_close",
    ])
    df = df.dropna(subset=["yes_mid", "sec_to_close"])
    df["minute_in_cycle"] = 15.0 - df["sec_to_close"] / 60.0
    df = df[(df["minute_in_cycle"] >= 0) & (df["minute_in_cycle"] <= 15)]

    windows = []
    for ticker, g in df.groupby("ticker", sort=False):
        g = g.sort_values("minute_in_cycle")
        minute = g["minute_in_cycle"].to_numpy()
        ymid   = g["yes_mid"].to_numpy(dtype=float)
        asset  = g["asset"].iloc[0]
        yes_won = determine_settlement(minute, ymid)
        windows.append((asset, minute, ymid, yes_won))

    n_windows = len(windows)
    print(f"loaded {n_windows} windows in {time.time()-t0:.1f}s")

    # ── sweep ──────────────────────────────────────────────────
    # agg[key] = [ent, wins, prof, s_ent, s_wins, s_prof, f_ent, f_wins, f_prof]
    agg = {}
    report_every = max(1, n_windows // 20)

    for wi, (asset, minute, ymid, yes_won) in enumerate(windows):
        res = process_window(minute, ymid, yes_won)
        for (obs_sec, fl, cv), (etype, won, prof) in res.items():
            k = (obs_sec, fl, cv, asset)
            if k not in agg:
                agg[k] = [0, 0, 0.0, 0, 0, 0.0, 0, 0, 0.0]
            r = agg[k]
            r[0] += 1
            if won:
                r[1] += 1; r[2] += prof
            if etype == 'S':
                r[3] += 1
                if won:
                    r[4] += 1; r[5] += prof
            else:
                r[6] += 1
                if won:
                    r[7] += 1; r[8] += prof
        if (wi + 1) % report_every == 0:
            pct = 100 * (wi + 1) / n_windows
            print(f"  {wi+1}/{n_windows} ({pct:.0f}%) -- {time.time()-t0:.0f}s elapsed")

    print(f"sweep done in {(time.time()-t0)/60:.1f} min")

    # ── build DataFrames ───────────────────────────────────────
    rows = []
    for (obs_sec, fl, cv, asset), r in agg.items():
        rows.append({
            "obs_window_sec":   int(obs_sec),
            "entry_floor_c":    fl,
            "entry_ceiling_c":  cv,
            "band_width_c":     cv - fl,
            "asset":            asset,
            "entries":          r[0],
            "wins":             r[1],
            "total_profit_c":   round(r[2], 1),
            "safe_entries":     r[3],
            "safe_wins":        r[4],
            "safe_profit_c":    round(r[5], 1),
            "fb_entries":       r[6],
            "fb_wins":          r[7],
            "fb_profit_c":      round(r[8], 1),
        })
    detail = pd.DataFrame(rows)
    detail.to_csv(OUT_BY_ASSET, index=False)
    print(f"wrote {OUT_BY_ASSET} ({len(detail)} rows)")

    overall = (
        detail
        .groupby(["obs_window_sec", "entry_floor_c", "entry_ceiling_c", "band_width_c"])
        .agg(
            entries=("entries", "sum"),
            wins=("wins", "sum"),
            total_profit_c=("total_profit_c", "sum"),
            safe_entries=("safe_entries", "sum"),
            safe_wins=("safe_wins", "sum"),
            safe_profit_c=("safe_profit_c", "sum"),
            fb_entries=("fb_entries", "sum"),
            fb_wins=("fb_wins", "sum"),
            fb_profit_c=("fb_profit_c", "sum"),
        )
        .reset_index()
    )
    overall["win_rate"] = (overall["wins"] / overall["entries"]).round(4)
    overall["fb_win_rate"] = np.where(
        overall["fb_entries"] > 0,
        (overall["fb_wins"] / overall["fb_entries"]).round(4),
        0.0,
    )
    overall["avg_profit_per_entry_c"] = (
        overall["total_profit_c"] / overall["entries"]
    ).round(2)
    overall = overall.sort_values("total_profit_c", ascending=False)
    overall.to_csv(OUT_OVERALL, index=False)
    print(f"wrote {OUT_OVERALL} ({len(overall)} rows)")

    overall.head(200).to_csv(OUT_TOP200, index=False)
    print(f"wrote {OUT_TOP200}")

    top_asset = (
        detail
        .sort_values("total_profit_c", ascending=False)
        .groupby("asset", group_keys=False)
        .head(20)
        .sort_values(["asset", "total_profit_c"], ascending=[True, False])
    )
    top_asset.to_csv(OUT_TOP_ASSET, index=False)
    print(f"wrote {OUT_TOP_ASSET} ({len(top_asset)} rows)")

    print(f"\ntotal time: {(time.time()-t0)/60:.1f} min")

    # ── summary ────────────────────────────────────────────────
    print("\nTOP 20 COMBOS BY TOTAL PROFIT")
    print("=" * 100)
    cols = [
        "obs_window_sec", "entry_floor_c", "entry_ceiling_c",
        "entries", "wins", "win_rate", "total_profit_c",
        "safe_entries", "safe_wins", "safe_profit_c",
        "fb_entries", "fb_wins", "fb_win_rate", "fb_profit_c",
    ]
    print(overall[cols].head(20).to_string(index=False))

    print("\nBEST COMBO AT EACH OBSERVATION WINDOW")
    print("=" * 100)
    best = (
        overall
        .sort_values("total_profit_c", ascending=False)
        .groupby("obs_window_sec", sort=False)
        .first()
        .reset_index()
        .sort_values("obs_window_sec")
    )
    print(best[cols].to_string(index=False))
