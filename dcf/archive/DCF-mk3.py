#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue May 27 13:59:20 2025

@author: peterdu
"""

import yfinance as yf
import sys


#%% SETUP

symbol = "nvda"
ticker = yf.Ticker(symbol)
info = ticker.info

terminal_growth = 0.015     # 1.5%
growth_rate = 0.02          # 2% annual FCF growth (ASSUMPTION)
projection_years = 5
risk_free = 0.04543         # 10-year Treasury
market_return = 0.09043     # Expected market return (ASSUMPTION)
tax_rate = 0.165            # Effective tax rate (ASSUMPTION)
cost_of_debt = 0.05         # Pre-tax cost of debt
beta = (info.get("beta") 
        or info.get("beta3Year") 
        or info.get("beta5Year") 
        or 1.20)
beta = max(0.3, min(beta, 3.0))
ev_info = info.get("enterpriseValue")
ebitda_info = info.get("ebitda")

stock = ticker
info = stock.info
currency = info.get("financialCurrency", "USD")
ebitda_ttm = info.get("ebitda")

# Fallback FCF calculation from quarterly cash flow statement
cash_flow = ticker.quarterly_cashflow

try:
    ocf = cash_flow.loc["Total Cash From Operating Activities"].head(4).sum()
    capex = cash_flow.loc["Capital Expenditures"].head(4).sum()
    fcf_ttm_raw = ocf + capex  # CapEx is typically negative
    print(f"✅ Fallback FCF (TTM): {fcf_ttm_raw}")
except Exception as e:
    print("⚠️ Could not compute FCF from cashflow statement:", e)
    fcf_ttm_raw = info.get("freeCashflow", info.get("marketCap", 0) * 0.05)


#%% Currency Conversion (TO USD)

currency_conversion = {
"USD": 1,
"TWD": 0.031,
"CAD": 0.73,
"EUR": 1.08,
"GBP": 1.27,
"JPY": 0.0064,
}

conversion_rate = currency_conversion.get(currency, 1)
ee_multiple = (ev_info / ebitda_info) if (ev_info and ebitda_info and \
                                          ebitda_info > 0) else None


fcf_ttm = fcf_ttm_raw * conversion_rate

# Quick sanity filter
if fcf_ttm_raw is None or fcf_ttm_raw <= 0:
    print("⚠️ FCF Year 0 is negative or unavailable. Manual assumptions \
          required.")
    sys.exit()

total_cash = info.get("totalCash", 0) * conversion_rate          # FIX: needed for net debt
interest_expense = info.get("interestExpense", 0) or 0           # FIX: GAAP interest is in CFO
if interest_expense < 0:
    interest_expense = -interest_expense                         # make it positive amount


#%% Try to safely get data, with fallbacks
try:
    fcff_ttm_raw = fcf_ttm_raw + interest_expense * (1 - tax_rate)   # FIX: Equity-ish -> FCFF
    fcf_ttm = fcff_ttm_raw * conversion_rate
    ebitda_ttm_raw = info.get("ebitda", None)
    ebitda_ttm = ebitda_ttm_raw * conversion_rate if ebitda_ttm_raw else None
    shares_outstanding = info["sharesOutstanding"]
    debt = info.get("totalDebt", 0) * conversion_rate
    market_cap = info["marketCap"] * conversion_rate
    current_price = stock.info['regularMarketPrice']
    print(f"Using scraped values for {symbol}...")
except:
    raise ValueError("Could not fetch complete data from yfinance.")
    
if fcf_ttm is None or fcf_ttm <= 0:
    print("⚠️ FCFF Year 0 is negative or unavailable. Manual assumptions \
          required.")
    sys.exit()

#%% FCFF PROJECTIONS

fcfs = []
fcf = fcf_ttm
for _ in range(projection_years):
    fcf *= (1 + growth_rate)
    fcfs.append(fcf)

#%% WACC

equity = market_cap
value = equity + debt

#%% Cost of equity by CAPM

cost_of_equity = risk_free + beta * (market_return - risk_free)

#%% WACC ACTUAL

wacc = (equity / value) * cost_of_equity + (debt / value) * cost_of_debt * \
    (1 - tax_rate)
    
# Guardrail: ensure (wacc - g) stays safely > 0
if wacc <= terminal_growth:
    new_g = max(0.0, wacc - 0.005)  # keep at least ~50bps spread; clamp g >= 0
    print(f"⚠️ Adjusting terminal growth from {terminal_growth:.3%} to {new_g:.3%} because WACC={wacc:.3%}.")
    terminal_growth = new_g

#%% TV CALCULATIONS

tv_perp = fcfs[-1] * (1 + terminal_growth) / (wacc - terminal_growth)  # OK for FCFF

#%% EXIT MULTIPLE METHOD

if ebitda_ttm and ee_multiple:
    ebitda_proj = ebitda_ttm * (1 + growth_rate) ** projection_years
    tv_exit = ebitda_proj * ee_multiple
else:
    tv_exit = tv_perp

#%% TV AVERAGED OUT

tv_avg = (tv_perp + tv_exit) / 2 if (ebitda_ttm and ee_multiple) else tv_perp

#%% DISCOUNTING

discounted_fcfs = [fcfs[i] / ((1 + wacc) ** (i + 1)) for i in \
                   range(projection_years)]
discounted_tv = tv_avg / ((1 + wacc) ** projection_years)

#%% VALUATIONS FINAL

enterprise_value = sum(discounted_fcfs) + discounted_tv
net_debt = debt - total_cash                                      # FIX: net debt
equity_value = enterprise_value - net_debt                        # FIX: add back cash via net debt
share_price = equity_value / shares_outstanding

#%% Split Sanity Guard

try:
    mcap_local = info["marketCap"]                 # local currency
    price_local = info["regularMarketPrice"]       # local currency
    implied_shares = mcap_local / price_local
    ratio = implied_shares / shares_outstanding
    if ratio < 0.9 or ratio > 1.1:
        print(f"⚠️ Share-count looks off (mcap/price vs yfin shares): ratio={ratio:.2f} — pre/post-split mismatch?")
except KeyError:
    print("⚠️ Could not run split sanity check (marketCap/regularMarketPrice missing).")

#%% Final warning checks

def dcf_audit(info, current_price, fcfs, wacc, discounted_tv, \
              enterprise_value, equity_value, share_price):
    import math
    def safe(v): 
        return v if isinstance(v, (int, float)) and math.isfinite(v) else None

    mcap = safe(info.get("marketCap"))
    debt_i = safe(info.get("totalDebt"))
    cash_i = safe(info.get("totalCash"))
    so = safe(info.get("sharesOutstanding"))

    print("\n=== DCF AUDIT ===")
    if mcap and current_price:
        implied = mcap / current_price
        if so:
            ratio = implied / so
            print(f"Implied shares (mcap/price): {implied:,.0f} vs yfin: \
                  {so:,.0f} (ratio={ratio:.2f})")
            if 9 < ratio < 11:
                print("⚠️ ~10× gap → split mismatch somewhere.")
    if equity_value and so:
        implied_price = equity_value / so
        print(f"Per-share check: {implied_price:.2f} vs reported: \
              {share_price:.2f}")
    if discounted_tv and enterprise_value:
        tv_share = discounted_tv / enterprise_value
        print(f"Discounted TV / EV: {tv_share:.2%}")
        if tv_share > 0.85:
            print("⚠️ TV dominates >85% — check g vs WACC and TV timing.")
    if mcap and debt_i is not None and cash_i is not None:
        ev_bridge = mcap + debt_i - cash_i
        ev_info = safe(info.get("enterpriseValue"))
        if ev_info:
            d = abs(ev_bridge - ev_info) / ev_info
            if d > 0.1:
                print("⚠️ EV(info) vs (mcap+debt-cash) differ >10% — \
                      units/staleness?")

dcf_audit(info, current_price, fcfs, wacc, discounted_tv, enterprise_value, \
          equity_value, share_price)

#%% OUTPUT RESULTS

print("--- WACC CALCULATION ---")
print(f"Cost of Equity (CAPM): {cost_of_equity * 100:.2f}%")
print(f"Cost of Debt (after-tax): {cost_of_debt * (1 - tax_rate) * 100:.2f}%")
print(f"WACC: {wacc * 100:.2f}%")

print("\n--- Technicals Check ---")
print(f"EV/EBITDA Multiple: {ee_multiple:.2f}" if ee_multiple else "EV/EBITDA \
      Multiple: N/A")

print("\n--- DCF VALUATION RESULTS ---")
print(f"Ticker: {ticker.ticker}")
print(f"Company Name: {info.get('longName', 'N/A')}")
print(f"Financials reported in: {currency}")
print(f"Projected FCFs: {[round(f/1e6, 2) for f in fcfs]} (in millions USD)")
print(f"Terminal Value (avg): ${tv_avg:,.0f}")
print(f"Discounted TV: ${discounted_tv:,.0f}")
print(f"Enterprise Value: ${enterprise_value:,.0f}")
print(f"Equity Value: ${equity_value:,.0f}")
print(f"Current Share Price: ${current_price:.2f}")
print(f"Intrinsic Share Price: ${share_price:.2f}")

