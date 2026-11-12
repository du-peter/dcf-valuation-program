"""
data_openbb.py
Pulls the inputs DCF_mk8.3 needs from OpenBB v5, using free sources that need no API key.

- Financial statements: SEC EDGAR filings (US-listed companies)   -> obb.sec.*
- Price and market cap: Nasdaq quote, Cboe as backup               -> obb.nasdaq / obb.cboe
- Beta: calculated here from 2 years of weekly returns vs SPY      -> obb.nasdaq.equity.historical

OpenBB v5 has no Yahoo Finance source, so there is no ready-made "beta" field.
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd
from openbb import obb


# ---------- small helpers ----------

def _to_df(result):
    """OpenBB result -> DataFrame, one row per period, newest period first."""
    df = result.to_df()
    if "period_ending" in df.columns:
        df = df.set_index("period_ending")
    df.index = pd.to_datetime(df.index)
    return df.sort_index(ascending=False)


def _latest(df, col, default=0.0):
    """Most recent non-empty value of a column (default if missing)."""
    if df is None or col not in df.columns:
        return default
    s = pd.to_numeric(df[col], errors="coerce").dropna()
    return float(s.iloc[0]) if len(s) else default


def _sum_latest(df, cols):
    """Add up the most recent value of several columns (missing ones count as 0)."""
    return sum(_latest(df, c, 0.0) for c in cols)


# ---------- statements ----------

def get_statements(symbol):
    """Income, balance sheet and cash flow from SEC filings.
    Keys: income_annual, income_quarterly, income_ttm, balance_annual,
          balance_quarterly, cash_annual, cash_quarterly, cash_ttm."""
    calls = {
        "income": obb.sec.income_statement,
        "balance": obb.sec.balance_sheet,
        "cash": obb.sec.cash_flow,
    }
    out = {}
    for name, fn in calls.items():
        for period in ("annual", "quarterly", "ttm"):
            if name == "balance" and period == "ttm":
                continue  # a balance sheet is a point in time, so no TTM version
            key = f"{name}_{period}"
            try:
                out[key] = _to_df(fn(symbol=symbol, period=period, limit=8))
            except Exception as e:
                print(f"⚠️ OpenBB {key} failed: {e}")
                out[key] = None
    return out


# ---------- price, market cap, beta ----------

def get_quote(symbol):
    """Try Nasdaq first (has market cap), then Cboe (price only)."""
    for source, fn in (("nasdaq", obb.nasdaq.equity.quote), ("cboe", obb.cboe.equity.quote)):
        try:
            q = fn(symbol=symbol).to_df().iloc[0]
        except Exception as e:
            print(f"ℹ️ {source} quote unavailable for {symbol}, trying next source. ({str(e).strip()[:80]})")
            continue
        return {
            "source": source,
            "name": q.get("name"),
            "price": q.get("last_price") or q.get("close") or q.get("prev_close"),
            "market_cap": q.get("market_cap"),  # Cboe has no market cap, so this is None there
        }
    raise RuntimeError(f"No quote source returned data for {symbol}.")


def _weekly_closes(symbol, start):
    """Weekly closing prices. Tries Nasdaq, then Cboe."""
    for fn in (obb.nasdaq.equity.historical, obb.cboe.equity.historical, obb.nasdaq.etf.historical):
        try:
            df = fn(symbol=symbol, start_date=start).to_df()
            break
        except Exception:
            continue
    else:
        raise RuntimeError(f"No price history source returned data for {symbol}.")
    df.index = pd.to_datetime(df.index)
    return df["close"].resample("W-FRI").last()


def calc_beta(symbol, benchmark="SPY", years=2):
    """Beta = Cov(stock, market) / Var(market), on weekly returns."""
    start = (date.today() - timedelta(days=365 * years)).isoformat()
    stock = _weekly_closes(symbol, start).pct_change()
    market = _weekly_closes(benchmark, start).pct_change()
    both = pd.concat([stock, market], axis=1, keys=["s", "m"], sort=True).dropna()
    if len(both) < 26:  # under ~6 months of data is too little to trust
        return None
    return float(np.cov(both["s"], both["m"])[0, 1] / np.var(both["m"], ddof=1))


# ---------- one call that returns everything mk8.3 uses ----------

def get_dcf_inputs(symbol):
    st = get_statements(symbol)
    qt = get_quote(symbol)

    inc_a, inc_ttm = st["income_annual"], st["income_ttm"]
    bal_q = st["balance_quarterly"] if st["balance_quarterly"] is not None else st["balance_annual"]
    cf_a, cf_ttm = st["cash_annual"], st["cash_ttm"]

    price = qt["price"]
    market_cap = qt["market_cap"]
    if market_cap and price:
        shares = market_cap / price
    else:
        # No market cap from the quote source: use diluted shares from the latest SEC filing
        inc_q = st["income_quarterly"]
        shares = _latest(inc_q, "weighted_ave_diluted_shares_os", None) or _latest(inc_ttm, "weighted_ave_diluted_shares_os", None)
        market_cap = price * shares if (price and shares) else None
        print(f"ℹ️ Market cap built from price x SEC diluted shares ({qt['source']} quote has no market cap).")

    capex_raw = _latest(cf_ttm, "purchase_of_plant_property_and_equipment")

    return {
        # replaces ticker.info fields
        "longName": qt["name"],
        "financialCurrency": (bal_q["reported_currency"].iloc[0]
                              if bal_q is not None and "reported_currency" in bal_q.columns else "USD"),
        "regularMarketPrice": price,
        "marketCap": market_cap,
        "sharesOutstanding": shares,
        # Borrowed debt plus lease liabilities, to match how yfinance's totalDebt is built
        "totalDebt": _sum_latest(bal_q, ["short_term_debt", "current_portion_of_long_term_debt",
                                         "long_term_debt", "capital_lease_obligations",
                                         "finance_lease_liability_current",
                                         "operating_lease_liability_current",
                                         "operating_lease_liability_noncurrent"]),
        "totalCash": _latest(bal_q, "cash_and_equivalents"),
        "shortTermInvestments": _latest(bal_q, "short_term_investments"),
        "beta": calc_beta(symbol),

        # replaces the statement lookups (TTM = last 4 quarters, straight from SEC)
        "ocf_ttm": _latest(cf_ttm, "net_cash_from_operating_activities"),
        "capex_ttm": -abs(capex_raw),  # forced negative, same sign convention as yfinance
        "dep_ttm": _latest(cf_ttm, "depreciation_and_amortization"),
        "interest_ttm": abs(_latest(inc_ttm, "total_interest_expense")),
        "ebit_annual": _latest(inc_a, "total_operating_income"),
        "total_equity": _latest(bal_q, "total_equity"),
        "current_assets": _latest(bal_q, "total_current_assets"),
        "current_liabilities": _latest(bal_q, "total_current_liabilities"),

        # raw tables, for the avg3y mode and anything else
        "statements": st,
        "cash_annual": cf_a,
    }


# ---------- base year for the mk9 operating model ----------

def get_base_year(symbol):
    """Everything dcf_engine.py (mk9) needs, in one dict. TTM = last 4 quarters from SEC."""
    st = get_statements(symbol)
    qt = get_quote(symbol)
    inc_ttm, inc_q = st["income_ttm"], st["income_quarterly"]
    cf_ttm = st["cash_ttm"]
    bal = st["balance_quarterly"] if st["balance_quarterly"] is not None else st["balance_annual"]

    revenue = _latest(inc_ttm, "total_revenue")
    base = {
        "price": qt["price"],
        "price_source": f"live ({qt['source']})",
        "name": qt["name"],
        "revenue": revenue,
        "ebit": _latest(inc_ttm, "total_operating_income"),
        "da": _latest(cf_ttm, "depreciation_and_amortization"),
        "capex": abs(_latest(cf_ttm, "purchase_of_plant_property_and_equipment")),
        "sbc": _latest(cf_ttm, "stock_based_compensation"),
        "basic_shares": _latest(inc_q, "weighted_ave_basic_shares_os"),
        "diluted_shares": _latest(inc_q, "weighted_ave_diluted_shares_os"),
        "debt": _sum_latest(bal, ["short_term_debt", "current_portion_of_long_term_debt",
                                  "long_term_debt", "capital_lease_obligations",
                                  "finance_lease_liability_current",
                                  "operating_lease_liability_current",
                                  "operating_lease_liability_noncurrent"]),
        "cash": _latest(bal, "cash_and_equivalents"),
        "short_term_investments": _latest(bal, "short_term_investments"),
        "total_equity": _latest(bal, "total_equity"),
    }
    missing = [k for k in ("revenue", "basic_shares", "diluted_shares", "price") if not base[k]]
    if missing:
        raise RuntimeError(f"Missing {missing} for {symbol}. Fill them under [overrides] in the settings file.")
    return base
