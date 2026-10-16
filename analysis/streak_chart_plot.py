import json, sys, numpy as np, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.lines import Line2D
D = json.load(open("streak_chart.json"))
SURF, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
WIN, LOSS = "#2a78d6", "#eb6834"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "text.color": INK, "axes.labelcolor": INK2,
                     "xtick.color": MUTED, "ytick.color": INK2, "axes.edgecolor": AXIS, "figure.facecolor": SURF, "axes.facecolor": SURF})
fig = plt.figure(figsize=(17, 25), dpi=110)
gs = fig.add_gridspec(9, 2, height_ratios=[0.22, 1.6] + [1.15] * 7, hspace=0.62, wspace=0.42, left=0.11, right=0.96, top=0.965, bottom=0.03)
T = D["totals"]
fig.text(0.04, 0.985, "V37 winning stretches vs losing stretches: the 13 conditions at the moment of each buy", fontsize=19, weight="bold", va="top")
fig.text(0.04, 0.9715, "V37 real trades, Fri Oct 9 - Thu Oct 15 2026 (rule: buy a crypto side at 58-67c in the first 3 minutes, sell at 90c, else hold). "
         "A new stretch starts whenever the running total turns by $20+.", fontsize=11, color=INK2, va="top")
# legend row
ax0 = fig.add_subplot(gs[0, :]); ax0.axis("off")
h = [Patch(color=WIN, label=f"Winning stretches  ·  28 stretches, {T['WIN']['trades']} trades, +${T['WIN']['usd']:,.0f}, {T['WIN']['won']:.1f}% of trades won"),
     Patch(color=LOSS, label=f"Losing stretches  ·  25 stretches, {T['LOSS']['trades']} trades, -${-T['LOSS']['usd']:,.0f}, {T['LOSS']['won']:.1f}% of trades won")]
ax0.legend(handles=h, loc="center left", ncol=2, frameon=False, fontsize=12, handlelength=1.6, handleheight=1.1, borderaxespad=0)
# running P&L
ax = fig.add_subplot(gs[1, :])
cum = np.array(D["curve"]["cum"]); dirs = D["curve"]["dir"]; days = D["curve"]["day"]; x = np.arange(1, len(cum) + 1)
i = 0
while i < len(dirs):
    j = i
    while j + 1 < len(dirs) and dirs[j + 1] == dirs[i]: j += 1
    if dirs[i] in ("WIN", "LOSS"):
        ax.axvspan(i + 0.5, j + 1.5, color=WIN if dirs[i] == "WIN" else LOSS, alpha=0.16, lw=0)
    i = j + 1
ax.plot(x, cum, color=INK, lw=1.6)
ax.axhline(0, color=AXIS, lw=1)
ax.set_xlim(1, len(cum)); ax.set_ylabel("running total ($, 10 contracts)", color=INK2)
starts = [k for k in range(len(days)) if k == 0 or days[k] != days[k - 1]]
ax.set_xticks([k + 1 for k in starts]); ax.set_xticklabels([days[k] for k in starts])
for k in starts[1:]: ax.axvline(k + 0.5, color=GRID, lw=1, zorder=0)
ax.set_xlabel("trade number (day labels in ET)", color=INK2)
ax.grid(axis="y", color=GRID, lw=0.8); ax.set_axisbelow(True)
for sp in ["top", "right"]: ax.spines[sp].set_visible(False)
ax.set_title("Running total, trade by trade - shaded blue during winning stretches, orange during losing stretches", loc="left", fontsize=13, weight="bold", pad=8)
ax.annotate(f"final  -${-cum[-1]:,.0f}", (x[-1], cum[-1]), xytext=(-8, 14), textcoords="offset points", ha="right", color=INK2, fontsize=10)
# condition panels in numeric order
items = sorted([(k, "n") for k in D["numeric"]] + [(k, "t") for k in D["tally"]])
for idx, (name, kind) in enumerate(items):
    ax = fig.add_subplot(gs[2 + idx // 2, idx % 2])
    for sp in ["top", "right", "left"]: ax.spines[sp].set_visible(False)
    num, title = name.split(" ", 1)
    ax.set_title(f"{int(num)}. {title}", loc="left", fontsize=12, weight="bold", pad=8)
    if kind == "n":
        v = D["numeric"][name]
        lo = min(v["WIN"]["p10"], v["LOSS"]["p10"]); hi = max(v["WIN"]["p90"], v["LOSS"]["p90"]); pad = (hi - lo) * 0.22 or 1
        dec = 3 if hi - lo < 0.5 else (2 if hi - lo < 2 else 1)
        f = lambda z: f"{z:+.{dec}f}" if lo < 0 or "%" in title else f"{z:.{dec}f}"
        for row, (dname, col, lab) in enumerate([("WIN", WIN, "Winning"), ("LOSS", LOSS, "Losing")]):
            y = 1 - row; s = v[dname]
            ax.plot([s["p10"], s["p90"]], [y, y], color=col, lw=11, solid_capstyle="round", alpha=0.9)
            ax.plot([s["med"]], [y], "o", ms=11, mfc=SURF, mec=col, mew=2.6, zorder=5)
            ax.text(s["p10"], y - 0.36, f(s["p10"]), ha="center", va="top", color=INK2, fontsize=9)
            ax.text(s["p90"], y - 0.36, f(s["p90"]), ha="center", va="top", color=INK2, fontsize=9)
            ax.text(s["med"], y + 0.24, f"median {f(s['med'])}", ha="center", va="bottom", color=INK, fontsize=9.5)
            ax.text(lo - pad * 0.98, y, f"{lab}\n(n={s['n']})", ha="right", va="center", color=INK2, fontsize=9.5)
        ax.set_xlim(lo - pad, hi + pad); ax.set_ylim(-0.75, 1.75); ax.set_yticks([])
        if lo < 0 < hi: ax.axvline(0, color=AXIS, lw=1, ls=(0, (3, 3)), zorder=0)
        ax.tick_params(axis="x", labelsize=8.5); ax.xaxis.set_major_locator(plt.MaxNLocator(5))
        ax.text(1.0, -0.36, "bar = middle 80% of trades, dot = median", transform=ax.transAxes, ha="right", fontsize=8.5, color=MUTED)
    else:
        v = D["tally"][name]; order = v["order"]; n = len(order)
        H = 0.36
        for k, lev in enumerate(order):
            y0 = n - 1 - k
            for dname, col, off in [("WIN", WIN, H / 2 + 0.02), ("LOSS", LOSS, -H / 2 - 0.02)]:
                c = v[dname][lev]
                ax.barh(y0 + off, c["pct"], height=H, color=col, edgecolor=SURF, linewidth=1)
                wr = "-" if c["won"] is None else f"{c['won']:.0f}% won"
                ax.text(c["pct"] + 1.2, y0 + off, f"{c['n']}  ·  {c['pct']:.0f}%  ·  {wr}", va="center", fontsize=9, color=INK)
        ax.set_yticks(range(n)); ax.set_yticklabels(list(reversed(order)), fontsize=9.5)
        ax.tick_params(axis="y", length=0)
        ax.set_xlim(0, 135); ax.set_xticks([0, 25, 50, 75, 100]); ax.set_xticklabels(["0%", "25%", "50%", "75%", "100%"], fontsize=8.5)
        ax.grid(axis="x", color=GRID, lw=0.8); ax.set_axisbelow(True); ax.set_ylim(-0.6, n - 0.4)
        ax.text(1.0, -0.30, "count · share of that stretch type's trades · how often those trades won", transform=ax.transAxes, ha="right", fontsize=8.5, color=MUTED)
# notes panel
axn = fig.add_subplot(gs[8, 1]); axn.axis("off")
import textwrap
notes_raw = ("How to read this\n"
 "• Ranges: the bar covers the middle 80% of trades (10th to 90th percentile); the dot is the median.\n"
 "  Blue = winning stretches, orange = losing stretches. Overlapping bars = the condition doesn't separate them.\n"
 "• Tallies: count, then share of that stretch type's trades, then how often those trades won.\n"
 "  Trades in winning stretches mostly won by definition, so compare the shares, not the win rates.\n"
 "• #3: V37 usually bought ~35 s into a market, so only ~230 trades had 30 s of price history.\n"
 "  #4 uses the move since the market opened instead (60 s history existed for only 6% of trades).\n"
 "• Spot/BTC values are % of price; + means moving toward / already past the strike on our side.\n"
 "• Sources: V37 trades file + V37 ticks/book (FINDINGS_log.md, 'V37 winning vs losing phases').")
notes = "\n".join(textwrap.fill(l, 86, subsequent_indent="  ") if l.startswith("•") else l for l in notes_raw.replace("\n  ", " ").split("\n"))
axn.text(0, 1.05, notes, va="top", fontsize=9.0, color=INK2, linespacing=1.38)
fig.savefig(sys.argv[1], dpi=110, facecolor=SURF)
print("saved", sys.argv[1])
