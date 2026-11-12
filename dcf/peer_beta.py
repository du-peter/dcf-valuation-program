"""
peer_beta.py
Peer beta for a company whose own beta is unreliable (for example, under 2 years of
trading history, or a business that changed recently).

Run:  uv run dcf/peer_beta.py TARGET PEER1 PEER2 ...
      e.g. uv run dcf/peer_beta.py SNDK MU WDC STX 000660.KS 005930.KS 285A.T

Method (each step prints in the table):
1. Raw beta of each peer = Cov(peer, market) / Var(market), 2 years of weekly returns,
   measured against the peer's home index (chosen from the ticker's exchange suffix).
2. Unlever each peer:   unlevered beta = raw beta / (1 + (1 - tax) x Debt / Equity)
   This removes the effect of the peer's own debt, leaving business risk only.
3. Take the median unlevered beta of the peers.
4. Relever for the target: beta = unlevered x (1 + (1 - tax) x target Debt / Equity)
5. Blume adjustment: 2/3 x beta + 1/3 x 1.0, because measured betas drift toward 1 over time.
   Copy the result into the target's settings file as beta.

Every number in this script comes from yfinance (one source for the whole calculation),
because OpenBB's free sources do not cover non-US listings.
"""

import sys

import numpy as np
import pandas as pd
import yfinance as yf

YEARS = 2
TAX = 0.21          # assumption: one marginal tax rate for all peers, used only to unlever

# Exchange suffix -> home market index. No suffix = US listing, measured against SPY.
HOME_INDEX = {
    ".KS": "^KS11", ".KQ": "^KQ11",        # Korea
    ".T": "^N225",                         # Japan
    ".TO": "^GSPTSE", ".V": "^GSPTSE",     # Canada
    ".L": "^FTSE",                         # United Kingdom
    ".PA": "^FCHI",                        # France
    ".DE": "^GDAXI",                       # Germany
    ".AS": "^AEX",                         # Netherlands
    ".SW": "^SSMI",                        # Switzerland
    ".HK": "^HSI",                         # Hong Kong
    ".TW": "^TWII",                        # Taiwan
    ".AX": "^AXJO",                        # Australia
}

USAGE = "Usage: uv run dcf/peer_beta.py TARGET PEER1 PEER2 ...   (target first, at least one peer)"


def home_index(ticker):
    for suffix, index in HOME_INDEX.items():
        if ticker.upper().endswith(suffix.upper()):
            return index
    return "SPY"


def weekly_returns(ticker):
    """Weekly % change in the adjusted close over the last YEARS years."""
    px = yf.Ticker(ticker).history(period=f"{YEARS}y", auto_adjust=True)["Close"]
    px.index = px.index.tz_localize(None)
    return px.resample("W-FRI").last().pct_change().dropna()


def raw_beta(ticker, index):
    both = pd.concat([weekly_returns(ticker), weekly_returns(index)], axis=1, keys=["s", "m"], sort=True).dropna()
    beta = np.cov(both["s"], both["m"])[0, 1] / np.var(both["m"], ddof=1)
    return float(beta), len(both)


def debt_to_equity(ticker):
    """Total debt / market cap, both in the company's trading currency."""
    info = yf.Ticker(ticker).info
    debt = info.get("totalDebt") or 0.0
    mcap = info.get("marketCap") or float("nan")
    return debt / mcap, info.get("shortName") or ticker


def main(argv):
    tickers = [a.upper() for a in argv if not a.startswith("-")]
    if len(tickers) < 2:
        sys.exit("Error: you must give a target ticker, then at least one peer.\n" + USAGE)
    target, peers = tickers[0], tickers[1:]

    rows = []
    for ticker in peers:
        index = home_index(ticker)
        try:
            beta, weeks = raw_beta(ticker, index)
            de, name = debt_to_equity(ticker)
            unlevered = beta / (1 + (1 - TAX) * de)
            rows.append([name, ticker, index, weeks, beta, de, unlevered])
        except Exception as err:                  # one bad ticker should not stop the rest
            print(f"Skipped {ticker}: {err}")
    if not rows:
        sys.exit("No peer data came back. Check the tickers.")

    table = pd.DataFrame(rows, columns=["Peer", "Ticker", "Index", "Weeks", "Raw beta", "D/E", "Unlevered beta"])
    print(f"\nPeer betas for {target}: {YEARS} years of weekly returns, tax {TAX:.0%} for unlevering (source: yfinance)\n")
    print(table.to_string(index=False, float_format=lambda x: f"{x:.2f}"))

    median_u = table["Unlevered beta"].median()
    mean_u = table["Unlevered beta"].mean()
    target_de, _ = debt_to_equity(target)
    relevered = median_u * (1 + (1 - TAX) * target_de)

    print(f"\nMedian unlevered beta {median_u:.2f}   (mean {mean_u:.2f})")
    print(f"{target} D/E {target_de:.3f}  ->  relevered peer beta {relevered:.2f}")
    try:
        own_beta, own_weeks = raw_beta(target, home_index(target))
        print(f"{target} own raw beta vs {home_index(target)} {own_beta:.2f}  ({own_weeks} weeks)")
    except Exception as err:
        print(f"{target} own beta not available: {err}")
    print(f"Blume-adjusted peer beta (2/3 x beta + 1/3 x 1.0): {2/3 * relevered + 1/3:.2f}")


if __name__ == "__main__":
    main(sys.argv[1:])
