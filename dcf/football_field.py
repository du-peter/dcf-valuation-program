"""
football_field.py
Draws a football field chart: one horizontal bar per valuation method showing its range of
value per share, with vertical lines at the current price and the probability-weighted DCF value.
This is the standard summary chart in a banking valuation.

Called by report.py when you export. It saves outputs/<TICKER>_football_field.png.
If outputs/<TICKER>_comps.xlsx exists (from uv run comps/comps.py <TICKER> ... y), the comps
ranges are added as extra bars, labelled with the comps run date and source.
"""

import math
from datetime import date, datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")                     # draw to a file, no window
import matplotlib.pyplot as plt           # noqa: E402
import pandas as pd                       # noqa: E402

DCF_COLOR = "#1f4e79"
COMPS_COLOR = "#7f7f7f"


def _ok(x):
    return isinstance(x, (int, float)) and math.isfinite(x) and x > 0


def _dcf_rows(ctx):
    """(label, low, high, markers) for each DCF bar. markers = [(value, text)]."""
    rows = []
    res = {r["name"]: r for r in ctx["results"]}
    scen = [(n, r["value"]["per_share"]) for n, r in res.items() if _ok(r["value"]["per_share"])]
    if scen:
        vals = [v for _, v in scen]
        lo, hi = min(vals), max(vals)
        rows.append(("DCF scenarios\n(bear to bull)", lo, hi,
                     [(v, n.capitalize()) for n, v in scen if lo < v < hi]))
    if "base" in res:
        v = res["base"]["value"]
        g, e = v["gordon"]["per_share"], v["exit"]["per_share"]
        if _ok(g) and _ok(e):
            lo_lab, hi_lab = (f"Exit {e:,.0f}", f"Gordon {g:,.0f}") if e <= g else (f"Gordon {g:,.0f}", f"Exit {e:,.0f}")
            rows.append(("DCF base case\n(Gordon vs exit multiple)", min(g, e), max(g, e), [], (lo_lab, hi_lab)))
    waccs, gs, grid = ctx["sensitivity"]
    cells = [x for row in grid for x in row if _ok(x)]
    if cells:
        dw = (max(waccs) - min(waccs)) / 2
        dg = (max(gs) - min(gs)) / 2
        rows.append((f"DCF base sensitivity\n(WACC ±{dw:.1%}, g ±{dg:.1%})", min(cells), max(cells), []))
    return rows


def _comps_rows(comps_path):
    """(label, low, high, markers) for each peer group in a comps export, plus its date."""
    if comps_path is None or not Path(comps_path).exists():
        return [], None
    implied = pd.read_excel(comps_path, sheet_name="Implied price", index_col=0)
    rows = []
    for group in implied.columns:
        vals = [float(v) for v in implied[group] if _ok(v)]
        if len(vals) >= 2:
            med = float(pd.Series(vals).median())
            rows.append((f"Comps: {group.lower()}\n({len(vals)} multiples)", min(vals), max(vals),
                         [(med, "Median")]))
    run_date = datetime.fromtimestamp(Path(comps_path).stat().st_mtime).date()
    return rows, run_date


def make_chart(ctx, out_path, comps_path=None):
    ticker = ctx["cfg"]["ticker"]
    price, weighted = ctx["price"], ctx["weighted"]
    dcf_rows = _dcf_rows(ctx)
    comps_rows, comps_date = _comps_rows(comps_path)
    rows = dcf_rows + comps_rows
    if not rows:
        print("Football field skipped: no valid values to plot.")
        return None

    fig, ax = plt.subplots(figsize=(11, 1.2 + 0.85 * len(rows)))
    allv = [v for r in rows for v in (r[1], r[2])] + [x for x in (price, weighted) if _ok(x)]
    span = max(allv) - min(allv) or max(allv)
    ax.set_xlim(max(0, min(allv) - 0.12 * span), max(allv) + 0.10 * span)
    min_w = 0.006 * span                      # keep near-equal ranges visible as a thin bar
    for i, row in enumerate(rows):
        label, lo, hi, markers = row[:4]
        lo_lab, hi_lab = row[4] if len(row) > 4 else (f"{lo:,.0f}", f"{hi:,.0f}")
        if hi - lo < min_w:
            mid = (lo + hi) / 2
            lo_d, hi_d = mid - min_w / 2, mid + min_w / 2
        else:
            lo_d, hi_d = lo, hi
        y = len(rows) - 1 - i                 # first row at the top
        color = DCF_COLOR if i < len(dcf_rows) else COMPS_COLOR
        ax.barh(y, hi_d - lo_d, left=lo_d, height=0.5, color=color, alpha=0.85)
        ax.text(lo_d, y, f"{lo_lab}  ", va="center", ha="right", fontsize=9)
        ax.text(hi_d, y, f"  {hi_lab}", va="center", ha="left", fontsize=9)
        for v, txt in markers:
            if hi_d - lo_d > 1.5 * min_w:
                ax.plot([v, v], [y - 0.25, y + 0.25], color="white", linewidth=1.5)
            ax.text(v, y + 0.3, txt, ha="center", va="bottom", fontsize=7, color="#404040")

    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r[0] for r in reversed(rows)], fontsize=9)
    if _ok(price):
        ax.axvline(price, color="#c00000", linewidth=1.5, label=f"Current price {price:,.2f}")
    if _ok(weighted):
        ax.axvline(weighted, color=DCF_COLOR, linestyle="--", linewidth=1.2,
                   label=f"Probability-weighted DCF {weighted:,.2f}")
    ax.set_xlabel("Value per share (trading currency)")
    ax.grid(axis="x", alpha=0.3)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_ylim(-0.7, len(rows) - 0.2)
    ax.legend(loc="lower right", fontsize=8, frameon=False, bbox_to_anchor=(1.0, 1.0), ncol=2)
    ax.set_title(f"{ticker}: valuation summary", loc="left", fontsize=13, fontweight="bold", pad=22)

    footer = (f"Illustrative model output, not a recommendation.  DCF run {date.today()}, "
              f"WACC {ctx['coc']['wacc']:.1%}; data: OpenBB SEC filings, price {ctx['price_source']}.")
    if comps_date:
        footer += f"  Comps run {comps_date}; data: yfinance."
    fig.text(0.01, 0.01, footer, fontsize=7, color="#606060")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(out_path, dpi=200)
    plt.close(fig)
    return out_path
