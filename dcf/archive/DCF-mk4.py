#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DCF mk4 — NVDA-ready FCFF model with manual beta (IB/PE style)
- FCFF = (CFO - Capex) + Interest*(1 - tax)
- WACC using CAPM CoE (manual beta by default)
- EV -> Equity via NET debt
- Optional EV/EBITDA exit TV (guarded)
"""

import sys
import math
import numpy as np
import pandas as pd
import yfinance as yf

# -----------------------------
# CONFIG
# -----------------------------
symbol = "tsla"                      # Ticker (case-insensitive for yfinance)
projection_years = 5                 # Explicit forecast horizon
growth_rate = 0.02                   # Constant FCFF growth during forecast
terminal_growth = 0.015              # Gordon growth (long-run, real-ish)
risk_free = 0.04543                  # 10Y UST (as decimal)
market_return = 0.09043              # Long-run expected market return
tax_rate = 0.165                     # Effective tax
cost_of_debt_pre_tax = 0.05          # Pre-tax cost of debt
use_midyear = False                  # Mid-year discounting toggle

# Manual beta settings (IB/PE style)
beta_market_index = "^GSPC"          # Benchmark; you can switch to ^NDX or ^IXIC
beta_lookback = "5y"                 # Lookback window
beta_interval = "1wk"                # Return frequency
beta_clip = (0.6, 1.6)               # Clamp mega-cap beta to a sensible band
use_manual_beta = True               # Use the computed beta by default
target_de_override = None            # If not None, use this target D/E when relevering (e.g., 0.10)

# Currency conversion (to USD)
currency_conversion = {
    "USD": 1,
    "TWD": 0.031,
    "CAD": 0.73,
    "EUR": 1.08,
    "GBP": 1.27,
    "JPY": 0.0064,
}

# -----------------------------
# DATA FETCH
# -----------------------------
ticker = yf.Ticker(symbol)
info = ticker.info  # vendor snapshot; treat with caution

currency = info.get("financialCurrency", "USD")
conversion_rate = currency_conversion.get(currency, 1.0)

# Raw vendor multiples (guarded)
ev_info = info.get("enterpriseValue")
ebitda_info = info.get("ebitda")
ee_multiple = (ev_info / ebitda_info) if (ev_info and ebitda_info and ebitda_info > 0) else None

# Core balance sheet / shares (converted to USD where it matters)
shares_outstanding = info.get("sharesOutstanding")
if not shares_outstanding:
    raise ValueError("Missing sharesOutstanding from yfinance.")

market_cap_local = info.get("marketCap")
price_local = info.get("regularMarketPrice")

if not (market_cap_local and price_local):
    raise ValueError("Missing marketCap and/or regularMarketPrice from yfinance.")

market_cap = market_cap_local * conversion_rate
current_price = price_local  # local currency; use conversion_rate if you want USD display later

debt = (info.get("totalDebt") or 0) * conversion_rate
total_cash = (info.get("totalCash") or 0) * conversion_rate

# -----------------------------
# STATEMENT-BASED FCF (TTM) → FCFF
# -----------------------------
# Helper to handle label differences
def grab(df, keys):
    for k in keys:
        if k in df.index:
            return df.loc[k]
    raise KeyError(str(keys))

# Try quarterly cash flow for TTM OCF and Capex
fcf_ttm_raw = None
try:
    qcf = ticker.quarterly_cashflow
    ocf = grab(qcf, ["Total Cash From Operating Activities", "Operating Cash Flow"]).head(4).sum()
    capex = grab(qcf, ["Capital Expenditures", "Capital Expenditure"]).head(4).sum()
    # US GAAP: Capex is typically negative; OCF includes interest
    fcf_ttm_raw = ocf + capex  # this is closer to FCFE (equity-ish)
    print(f"✅ Fallback FCF (CFO - Capex) TTM: {fcf_ttm_raw:,.0f} ({currency})")
except Exception as e:
    print("⚠️ Could not compute FCF (TTM) from cashflow statement:", e)

if fcf_ttm_raw is None:
    # Last-resort vendor field (not ideal)
    fcf_ttm_raw = info.get("freeCashflow", market_cap_local * 0.05)
    print(f"⚠️ Using vendor freeCashflow fallback: {fcf_ttm_raw:,.0f} ({currency})")

# Interest expense add-back to convert equity-ish FCF to FCFF
interest_expense = info.get("interestExpense", 0) or 0
# Normalize sign: make it a positive "expense" amount
if interest_expense < 0:
    interest_expense = -interest_expense

fcff_ttm_local = fcf_ttm_raw + interest_expense * (1 - tax_rate)
fcff_ttm_usd = fcff_ttm_local * conversion_rate

if fcff_ttm_usd <= 0 or not math.isfinite(fcff_ttm_usd):
    print("⚠️ FCFF Year 0 is negative or unavailable. Manual assumptions required.")
    sys.exit(1)

print(f"OCF: {('N/A' if 'ocf' not in locals() else f'{ocf:,.0f}')}, "
      f"Capex: {('N/A' if 'capex' not in locals() else f'{capex:,.0f}')}, "
      f"IntExp: {interest_expense:,.0f} ({currency})")
print(f"FCF (CFO-Capex): {fcf_ttm_raw:,.0f} → FCFF addback: {(interest_expense*(1-tax_rate)):,.0f} "
      f"→ FCFF TTM (USD): {fcff_ttm_usd:,.0f}")

# -----------------------------
# MANUAL BETA (IB/PE STYLE)
# -----------------------------
def compute_manual_beta(tick, benchmark, lookback="5y", interval="1wk",
                        tax=0.21, d_val=0.0, e_val=1.0, target_de=None, clip=(0.6, 1.6)):
    """
    - 5y weekly beta vs benchmark (OLS)
    - Blume adjust -> unlever -> relever -> clip
    Returns: (beta_final, details_dict)
    """

    try:
        raw = yf.download([tick, benchmark],
                          period=lookback,
                          interval=interval,
                          auto_adjust=True,
                          progress=False)

        # yfinance returns a MultiIndex when multiple tickers are requested.
        # With auto_adjust=True there is NO 'Adj Close'; use 'Close'. Fall back if needed.
        if isinstance(raw.columns, pd.MultiIndex):
            top_levels = set(raw.columns.get_level_values(0))
            if "Close" in top_levels:
                prices = raw["Close"]
            elif "Adj Close" in top_levels:
                prices = raw["Adj Close"]
            else:
                raise ValueError("No Close/Adj Close columns found in download (MultiIndex).")
        else:
            # Single-level columns (rare in multi-ticker downloads, but guard anyway)
            if "Close" in raw.columns:
                prices = raw["Close"]
            elif "Adj Close" in raw.columns:
                prices = raw["Adj Close"]
            else:
                raise ValueError("No Close/Adj Close columns found in download.")

        prices = prices[[tick, benchmark]].dropna(how="any")

        if prices.shape[0] < 52:
            raise ValueError("Not enough observations (< ~1y of weekly data).")

        # Simple weekly returns (you could use log returns; either is fine for beta)
        rets = prices.pct_change().dropna()
        mkt = rets[benchmark].values
        stk = rets[tick].values
        if np.var(mkt) == 0:
            raise ValueError("Benchmark variance is zero (unexpected).")

        beta_raw = np.cov(stk, mkt, ddof=0)[0, 1] / np.var(mkt, ddof=0)

        # Blume adjustment toward 1.0
        beta_blume = 0.67 * beta_raw + 0.33 * 1.0

        # Unlever using current D/E
        de_current = (d_val / e_val) if (e_val and e_val > 0) else 0.0
        beta_unlev = beta_blume / (1 + (1 - tax) * de_current)

        # Relever to target D/E (or current if no override)
        de_target = de_current if target_de is None else float(target_de)
        beta_relev = beta_unlev * (1 + (1 - tax) * de_target)

        # Clip for stability
        lo, hi = clip
        beta_final = float(np.clip(beta_relev, lo, hi))

        details = {
            "beta_raw": beta_raw,
            "beta_blume": beta_blume,
            "beta_unlevered": beta_unlev,
            "de_current": de_current,
            "de_target": de_target,
            "beta_final_clipped": beta_final,
            "n_obs": int(rets.shape[0]),
        }
        return beta_final, details

    except Exception as e:
        print(f"⚠️ Manual beta computation failed: {e}")
        return None, {"error": str(e)}


    except Exception as e:
        print(f"⚠️ Manual beta computation failed: {e}")
        return None, {"error": str(e)}

# Compute manual beta using USD values for D/E (ratio is currency-agnostic anyway)
beta_manual, beta_details = (compute_manual_beta(
    tick=symbol.upper(),
    benchmark=beta_market_index,
    lookback=beta_lookback,
    interval=beta_interval,
    tax=tax_rate,
    d_val=debt,                 # USD
    e_val=market_cap,           # USD
    target_de=target_de_override,
    clip=beta_clip
))

# Vendor beta as fallback / comparison
beta_vendor = (info.get("beta") or info.get("beta3Year") or info.get("beta5Year") or 1.20)
beta_vendor = float(np.clip(beta_vendor, beta_clip[0], beta_clip[1])) if math.isfinite(beta_vendor) else 1.20

beta_used = beta_manual if (use_manual_beta and beta_manual is not None) else beta_vendor

print("\n--- Beta Summary ---")
if beta_manual is not None:
    print(f"Manual beta (final, clipped): {beta_used:.3f} | raw={beta_details.get('beta_raw', float('nan')):.3f} "
          f"blume={beta_details.get('beta_blume', float('nan')):.3f} "
          f"D/E cur={beta_details.get('de_current', float('nan')):.3f} "
          f"D/E tgt={beta_details.get('de_target', float('nan')):.3f} "
          f"n={beta_details.get('n_obs', 'N/A')}")
else:
    print("Manual beta unavailable; using vendor beta.")
print(f"Vendor beta (clipped): {beta_vendor:.3f}")
print(f"Beta used in CAPM: {beta_used:.3f}")

# -----------------------------
# COST OF CAPITAL
# -----------------------------
equity_value_mkt = market_cap
firm_value_mkt = equity_value_mkt + debt

market_premium = market_return - risk_free
cost_of_equity = risk_free + beta_used * market_premium

cost_of_debt_after_tax = cost_of_debt_pre_tax * (1 - tax_rate)

wacc = (equity_value_mkt / firm_value_mkt) * cost_of_equity + \
       (debt / firm_value_mkt) * cost_of_debt_after_tax

# Guardrail for TV denominator
if wacc <= terminal_growth:
    new_g = max(0.0, wacc - 0.005)
    print(f"⚠️ Adjusting terminal growth from {terminal_growth:.3%} to {new_g:.3%} because WACC={wacc:.3%}.")
    terminal_growth = new_g

# -----------------------------
# FCFF PROJECTIONS
# -----------------------------
fcfs = []
fcf = fcff_ttm_usd
for _ in range(projection_years):
    fcf *= (1 + growth_rate)
    fcfs.append(fcf)

if not fcfs:
    sys.exit("No projected FCFFs generated.")

# -----------------------------
# TERMINAL VALUE (Perpetuity & Exit Multiple)
# -----------------------------
tv_perp = fcfs[-1] * (1 + terminal_growth) / (wacc - terminal_growth)

# EV/EBITDA exit (guarded) — compatible with FCFF/EV framework
ebitda_ttm_usd = (ebitda_info * conversion_rate) if ebitda_info else None
if ebitda_ttm_usd and ee_multiple:
    ebitda_proj = ebitda_ttm_usd * (1 + growth_rate) ** projection_years
    tv_exit = ebitda_proj * ee_multiple
else:
    tv_exit = None

tv_avg = (tv_perp + tv_exit) / 2 if tv_exit is not None else tv_perp

# -----------------------------
# DISCOUNTING
# -----------------------------
if use_midyear:
    discounted_fcfs = [fcfs[i] / ((1 + wacc) ** (i + 0.5)) for i in range(len(fcfs))]
    discounted_tv = tv_avg / ((1 + wacc) ** (len(fcfs) - 0.5))
else:
    discounted_fcfs = [fcfs[i] / ((1 + wacc) ** (i + 1)) for i in range(len(fcfs))]
    discounted_tv = tv_avg / ((1 + wacc) ** len(fcfs))

enterprise_value_dcf = sum(discounted_fcfs) + discounted_tv

# EV -> Equity bridge via NET debt
net_debt = debt - total_cash
equity_value_dcf = enterprise_value_dcf - net_debt
intrinsic_price = equity_value_dcf / shares_outstanding

# -----------------------------
# AUDIT & SANITY CHECKS
# -----------------------------
def dcf_audit():
    print("\n=== DCF AUDIT ===")
    implied_shares_local = market_cap_local / price_local
    ratio = implied_shares_local / shares_outstanding
    print(f"Implied shares (mcap/price): {implied_shares_local:,.0f} vs yfin: {shares_outstanding:,.0f} (ratio={ratio:.2f})")
    if 9 < ratio < 11:
        print("⚠️ ~10× gap → split mismatch somewhere.")
    implied_price = equity_value_dcf / shares_outstanding
    print(f"Per-share check: {implied_price:.2f} vs reported: {intrinsic_price:.2f}")
    tv_share = discounted_tv / enterprise_value_dcf
    print(f"Discounted TV / EV: {tv_share:.2%}")
    if tv_share > 0.85:
        print("⚠️ TV dominates >85% — consider longer horizon / staged growth / mid-year / validate WACC & g.")
    # EV bridge sanity (vendor snapshot)
    if ev_info and (info.get("totalDebt") is not None) and (info.get("totalCash") is not None):
        ev_bridge_vendor = market_cap_local + (info.get("totalDebt") or 0) - (info.get("totalCash") or 0)
        if ev_info:
            d = abs(ev_bridge_vendor - ev_info) / ev_info
            if d > 0.1:
                print("⚠️ EV(info) vs (mcap+debt-cash) differ >10% — vendor staleness/units?")
dcf_audit()

# -----------------------------
# OUTPUT
# -----------------------------
print("\n--- WACC CALCULATION ---")
print(f"Cost of Equity (CAPM): {cost_of_equity * 100:.2f}%")
print(f"Cost of Debt (after-tax): {cost_of_debt_after_tax * 100:.2f}%")
print(f"WACC: {wacc * 100:.2f}%")

print("\n--- Technicals Check ---")
print(f"EV/EBITDA Multiple (vendor): {ee_multiple:.2f}" if ee_multiple else "EV/EBITDA Multiple (vendor): N/A")

print("\n--- DCF VALUATION RESULTS ---")
print(f"Ticker: {ticker.ticker}")
print(f"Company Name: {info.get('longName', 'N/A')}")
print(f"Financials reported in: {currency}  (converted to USD @ {conversion_rate})")
print(f"Projected FCFFs (USD): {[round(f/1e6, 2) for f in fcfs]}  (in millions)")
print(f"Terminal Value (avg, USD): ${tv_avg:,.0f}")
print(f"Discounted TV (USD): ${discounted_tv:,.0f}")
print(f"Enterprise Value (USD): ${enterprise_value_dcf:,.0f}")
print(f"Equity Value (USD): ${equity_value_dcf:,.0f}")
print(f"Current Share Price (local): ${current_price:.2f}")
print(f"Intrinsic Share Price (USD): ${intrinsic_price:.2f}")
