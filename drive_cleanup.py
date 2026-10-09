# %% [markdown]
# # Drive cleanup — shrink the bot logs without losing any data
#
# For every finished tick / prints / book file:
#   1. keeps only the rows of your 11 markets (the V34-V36 bots also logged every other Kalshi
#      market by mistake — those rows are dropped),
#   2. writes them compressed as <name>.csv.gz,
#   3. re-reads the compressed file and checks the row count matches,
#   4. only then deletes the original .csv.
# Trades, settlements and analysis results are left untouched (they're small).
# Files changed in the last 2 hours (a bot may still be writing them) and the V37 folder are skipped.
# Deleted files go to Drive's Trash: empty it to get the space back (it also empties itself after 30 days).
# The grid search reads the .csv.gz files directly.
#
# Set DRY_RUN = True to only list what would happen. Runtime -> Run all.

# %%
try:
    from google.colab import drive
    drive.mount('/content/drive')
    D = "/content/drive/MyDrive/"
except ImportError:                      # local test run
    import os
    D = os.environ["CLEANUP_DIR"]

# %%
import os, re, csv, gzip, glob, time
DRY_RUN = False
MIN_AGE_HOURS = 2
OURS = re.compile(r"^KX(BTC|ETH|SOL|XRP|DOGE|GOLD|SILVER|WTI|COPPER|PLATINUM|PALLADIUM)15M-")
PATTERNS = ["*_ticks.csv", "*_prints.csv", "*_book.csv", "commod15min_ticks_recent.csv"]

files = sorted({p for pat in PATTERNS for p in glob.glob(D + pat)})
plan = []
for p in files:
    age_h = (time.time() - os.path.getmtime(p)) / 3600
    if os.path.exists(p + ".gz"):
        print(f"skip (already has a .gz): {os.path.basename(p)}"); continue
    if age_h < MIN_AGE_HOURS:
        print(f"skip (changed {age_h:.1f}h ago — may still be written): {os.path.basename(p)}"); continue
    plan.append(p)
total = sum(os.path.getsize(p) for p in plan)
print(f"\n{len(plan)} files to shrink, {total / 1e6:,.0f} MB now:")
for p in plan: print(f"  {os.path.basename(p):45} {os.path.getsize(p) / 1e6:9,.1f} MB")

# %%
def ticker_of(row):
    for f in row[:4]:
        if "-" in f and f.startswith("KX"): return f
    return ""

log = []
for p in plan:
    t0, size0 = time.time(), os.path.getsize(p)
    if DRY_RUN:
        print(f"[dry run] would shrink {os.path.basename(p)}"); continue
    tmp = p + ".gz.tmp"
    kept = dropped = 0
    with open(p, newline="", errors="replace") as fin, gzip.open(tmp, "wt", newline="") as fout:
        rd, w = csv.reader(fin), csv.writer(fout)
        header = next(rd, None)
        if header is None:
            print(f"empty, left as is: {os.path.basename(p)}"); continue
        w.writerow(header)
        for row in rd:
            if OURS.match(ticker_of(row)):
                w.writerow(row); kept += 1
            else:
                dropped += 1
    # verify: the compressed file reads back completely with the same header and row count
    with gzip.open(tmp, "rt", newline="") as fchk:
        rd = csv.reader(fchk)
        ok_header = next(rd, None) == header
        n_back = sum(1 for _ in rd)
    if not ok_header or n_back != kept:
        print(f"!! check FAILED for {os.path.basename(p)} (header ok: {ok_header}, rows {n_back} vs {kept}) — original kept")
        os.remove(tmp); continue
    os.replace(tmp, p + ".gz")
    os.remove(p)
    size1 = os.path.getsize(p + ".gz")
    log.append((os.path.basename(p), size0, size1, kept, dropped))
    print(f"{os.path.basename(p):45} {size0 / 1e6:8,.1f} MB -> {size1 / 1e6:6,.1f} MB  "
          f"({kept:,} rows kept, {dropped:,} other-market rows dropped, {time.time() - t0:.0f}s)")

# %%
if log:
    before, after = sum(x[1] for x in log), sum(x[2] for x in log)
    print(f"\nDone: {before / 1e6:,.0f} MB -> {after / 1e6:,.0f} MB ({(1 - after / before) * 100:.0f}% smaller).")
    print("Empty Google Drive's Trash to get the space back.")
    with open(D + "drive_cleanup_log.csv", "a", newline="") as fh:
        w = csv.writer(fh)
        if fh.tell() == 0: w.writerow(["file", "bytes_before", "bytes_after", "rows_kept", "rows_dropped"])
        w.writerows(log)
