"""
comps.py
Trading comparables ("comps"): how the market prices similar companies today.

Run (from the finance-projects folder):
  uv run comps/comps.py AAPL MSFT GOOGL META             target first, then its peers
  uv run comps/comps.py SNDK MU WDC STX --core MU        also print a second median for a core group
  add --export (or y) to any of the above to save outputs/<TARGET>_comps.xlsx

What it does:
1. Pulls today's valuation multiples for the target and each peer from yfinance
   (yfinance covers Korean and Japanese listings, which OpenBB's free sources do not).
2. Prints the peer table and the peer median for each multiple.
   With --core, it prints a second median using only the core peers (for example, memory
   chip makers only, leaving out hard drive makers that are shown for reference).
3. Applies each peer median to the target's own numbers to get an implied share price.
   Example: peer median EV/EBITDA x target EBITDA = implied enterprise value,
   then minus debt, plus cash, divided by shares.

Multiples are ratios, so peers in won or yen compare directly with peers in dollars.
"""

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

# yfinance field -> column name
FIELDS = {
    "marketCap": "Mkt cap (bn, local)",
    "trailingPE": "P/E LTM",
    "forwardPE": "P/E NTM",
    "enterpriseToEbitda": "EV/EBITDA LTM",
    "enterpriseToRevenue": "EV/Sales LTM",
    "priceToBook": "P/B",
    "grossMargins": "Gross margin",
    "operatingMargins": "Op margin",
    "returnOnEquity": "ROE",
    "revenueGrowth": "Rev growth YoY",
}
MULTIPLES = ["P/E LTM", "P/E NTM", "EV/EBITDA LTM", "EV/Sales LTM", "P/B"]
PERCENTS = ["Gross margin", "Op margin", "ROE", "Rev growth YoY"]

USAGE = """Usage:
  uv run comps/comps.py TARGET PEER1 PEER2 ...       (any tickers, target first)
  uv run comps/comps.py TARGET PEERS... --core P1,P2 (second median for a core group)
  add --export (or y) to save outputs/<TARGET>_comps.xlsx

Example (Sandisk vs memory chip makers, with hard drive makers WDC and STX shown for reference):
  uv run comps/comps.py SNDK MU 000660.KS 005930.KS 285A.T WDC STX --core MU,000660.KS,005930.KS,285A.T"""


def parse_args(argv):
    """Read the command line. Returns target, peers, core peers, export flag."""
    export = False
    core = []
    tickers = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.lower() in ("y", "yes", "--export"):
            export = True
        elif a in ("-h", "--help"):
            print(USAGE)
            sys.exit(0)
        elif a == "--core":
            if i + 1 >= len(argv):
                sys.exit("--core needs a list of peers, for example: --core MU,WDC")
            core = [t.strip().upper() for t in argv[i + 1].split(",") if t.strip()]
            i += 1
        else:
            tickers.append(a.upper())
        i += 1

    if not tickers:
        sys.exit("Error: you must give a target ticker, then at least one peer.\n\n" + USAGE)
    if len(tickers) < 2:
        sys.exit("Error: you must give at least one peer after the target.\n\n" + USAGE)
    target, peers = tickers[0], tickers[1:]
    missing = [c for c in core if c not in peers]
    if missing:
        sys.exit(f"--core lists tickers that are not in the peer list: {', '.join(missing)}")
    return target, peers, core, export


def pull(ticker):
    info = yf.Ticker(ticker).info
    row = {col: info.get(field) for field, col in FIELDS.items()}
    if row["Mkt cap (bn, local)"]:
        row["Mkt cap (bn, local)"] = row["Mkt cap (bn, local)"] / 1e9
    name = info.get("shortName") or info.get("longName") or ticker
    return row, info, name


def peer_median(df):
    # A negative multiple (losses) carries no information, so it is left out of the median
    return df[MULTIPLES].where(df[MULTIPLES] > 0).median()


def implied_prices(t, med, price, shares, ev, debt, cash):
    out = {}
    for m in ("P/E LTM", "P/E NTM", "P/B"):
        if pd.notna(t[m]) and t[m] > 0 and pd.notna(med[m]):
            out[m] = price / t[m] * med[m]                 # target EPS (or book/share) x peer multiple
    for m in ("EV/EBITDA LTM", "EV/Sales LTM"):
        if ev and pd.notna(t[m]) and t[m] > 0 and pd.notna(med[m]):
            metric = ev / t[m]                             # target EBITDA (or sales)
            out[m] = (metric * med[m] - debt + cash) / shares
    return out


def main(argv):
    target, peers, core, export = parse_args(argv)

    rows, target_info, core_rows = {}, None, []
    for ticker in [target] + peers:
        try:
            row, info, name = pull(ticker)
        except Exception as err:
            print(f"Skipped {ticker}: {err}")
            continue
        if not info or all(v is None for v in row.values()):
            print(f"Skipped {ticker}: yfinance returned no data (check the ticker spelling)")
            continue
        label = f"{name} [{ticker}]" + (" (target)" if ticker == target else "")
        rows[label] = row
        if ticker == target:
            target_info = info
            target_label = label
        elif ticker in core:
            core_rows.append(label)

    if target_info is None:
        sys.exit(f"No data for the target {target}. Check the ticker and try again.")
    if len(rows) < 2:
        sys.exit("No peer data came back, so there is nothing to compare against.")

    table = pd.DataFrame(rows).T.apply(pd.to_numeric, errors="coerce")
    table = table.reindex(columns=list(FIELDS.values()))
    peers_only = table.drop(index=target_label)

    medians = {"All peers": peer_median(peers_only)}
    if core_rows:
        medians["Core peers"] = peer_median(peers_only.loc[core_rows])

    show = table.copy().astype(object)
    for c in PERCENTS:
        show[c] = table[c].map(lambda x: f"{x:.0%}" if pd.notna(x) else "n/a")
    for c in MULTIPLES + ["Mkt cap (bn, local)"]:
        show[c] = table[c].map(lambda x: f"{x:,.1f}" if pd.notna(x) else "n/a")
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 20)
    print(f"\nTrading comps for {target}, {date.today()} (source: yfinance)\n")
    print(show.to_string())
    for group, med in medians.items():
        cells = "  |  ".join(f"{m} {med[m]:.1f}x" if pd.notna(med[m]) else f"{m} n/a" for m in MULTIPLES)
        print(f"\n{group + ' median:':<20}" + cells)

    # ---- implied target price from each peer median ----
    price = target_info.get("currentPrice") or target_info.get("regularMarketPrice")
    shares = target_info.get("sharesOutstanding")
    debt = target_info.get("totalDebt") or 0.0
    cash = target_info.get("totalCash") or 0.0
    ev = target_info.get("enterpriseValue")
    t = table.loc[target_label]

    implied = {}
    if price and shares:
        implied = {g: implied_prices(t, med, price, shares, ev, debt, cash) for g, med in medians.items()}
        print(f"\nImplied {target} price at the peer median (current price {price:,.2f}):")
        print(f"  {'':<15}" + "".join(f"{g:>24}" for g in implied))
        for m in MULTIPLES:
            cells = ""
            for g in implied:
                v = implied[g].get(m)
                cells += f"{'n/a':>24}" if v is None else f"{f'{v:,.0f} ({v / price - 1:+.0%})':>24}"
            print(f"  {m:<15}" + cells)
        print("\nRead with care: when a whole industry is at a cycle peak, LTM and NTM earnings are peak "
              "earnings for every company in the table. Comps then tell you whether the target is cheap "
              "or expensive against its peers, not whether the whole group is.")
    else:
        print(f"\nNo price or share count for {target}, so implied prices were skipped.")

    if export:
        out = Path(__file__).resolve().parent.parent / "outputs"
        out.mkdir(exist_ok=True)
        path = out / f"{target.replace('.', '_')}_comps.xlsx"   # no date: each export replaces the last
        with pd.ExcelWriter(path) as xw:
            table.to_excel(xw, sheet_name="Comps")
            pd.DataFrame(medians).to_excel(xw, sheet_name="Medians")
            pd.DataFrame(implied).to_excel(xw, sheet_name="Implied price")
        print(f"Exported: {path}")


if __name__ == "__main__":
    main(sys.argv[1:])
