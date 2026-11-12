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
beta = info.get("beta")            # Beta (ASSUMPTION)

stock = ticker
info = stock.info
currency = info.get("financialCurrency", "USD")
enterprise_value = info.get("enterpriseValue")
ebitda_ttm = info.get("ebitda")

# Optional: fallback FCF calculation from quarterly cash flow statement
cash_flow = ticker.quarterly_cashflow

try:
    ocf = cash_flow.loc["Total Cash From Operating Activities"].head(4).sum()
    capex = cash_flow.loc["Capital Expenditures"].head(4).sum()
    fcf_ttm_raw = ocf + capex  # CapEx is typically negative
    print(f"✅ Fallback FCF (TTM): {fcf_ttm_raw}")
except Exception as e:
    print("⚠️ Could not compute FCF from cashflow statement:", e)
    fcf_ttm_raw = info.get("freeCashflow", info.get("marketCap", 0) * 0.05)


#%% Currency Conversion

currency_conversion = {
"USD": 1,
"TWD": 0.031,
"CAD": 0.73,
"EUR": 1.08,
"GBP": 1.27,
"JPY": 0.0064,
}

conversion_rate = currency_conversion.get(currency, 1)
ee_multiple = info.get("enterpriseValue") / info.get("ebitda")


fcf_ttm = fcf_ttm_raw * conversion_rate

# Quick sanity filter
if fcf_ttm_raw is None or fcf_ttm_raw <= 0:
    print("⚠️ FCF Year 0 is negative or unavailable. Manual assumptions required.")
    exit()


#%% Try to safely get data, with fallbacks
try:
    fcf_ttm_raw = info.get("freeCashflow", info["marketCap"] * 0.05)
    fcf_ttm = fcf_ttm_raw * conversion_rate
    ebitda_ttm_raw = info.get("ebitda", None)
    ebitda_ttm = ebitda_ttm_raw * conversion_rate if ebitda_ttm_raw else None
    shares_outstanding = info["sharesOutstanding"]
    debt = info.get("totalDebt", 0) * conversion_rate
    market_cap = info["marketCap"] * conversion_rate
    current_price = stock.info['regularMarketPrice']
    print(f"Using scraped values for {symbol}...")
except:
    raise ValueError("Could not fetch complete data from yfinance.")
    

#%% FCF PROJECTIONS

fcfs = []
fcf = fcf_ttm

if fcf_ttm < 0:
    sys.exit("FCF Year 0 is negative, manual assumptions required.")

for _ in range(projection_years):
    fcf *= (1 + growth_rate)
    fcfs.append(fcf)

#%% WACC

equity = market_cap
value = equity + debt

#%% Cost of equity by CAPM

cost_of_equity = risk_free + beta * (market_return - risk_free)

#%% WACC ACTUAL

wacc = (equity / value) * cost_of_equity + (debt / value) * cost_of_debt * (1 - tax_rate)

#%% TV CALCULATIONS

tv_perp = fcfs[-1] * (1 + terminal_growth) / (wacc - terminal_growth)

#%% EXIT MULTIPLE METHOD

if ebitda_ttm and ee_multiple:
    ebitda_proj = ebitda_ttm * (1 + growth_rate) ** projection_years
    tv_exit = ebitda_proj * ee_multiple
else:
   tv_exit = tv_perp

#%% TV AVERAGED OUT

tv_avg = (tv_perp + tv_exit) / 2

#%% DISCOUNTING

discounted_fcfs = [fcfs[i] / ((1 + wacc) ** (i + 1)) for i in range(projection_years)]
discounted_tv = tv_avg / ((1 + wacc) ** projection_years)

#%% VALUATIONS FINAL

enterprise_value = sum(discounted_fcfs) + discounted_tv
equity_value = enterprise_value - debt
share_price = equity_value / shares_outstanding


#%% OUTPUT RESULTS

print("--- WACC CALCULATION ---")
print(f"Cost of Equity (CAPM): {cost_of_equity * 100:.2f}%")
print(f"Cost of Debt (after-tax): {cost_of_debt * (1 - tax_rate) * 100:.2f}%")
print(f"WACC: {wacc * 100:.2f}%")

print("\n--- Technicals Check ---")
print(f"EV/EBITDA Multiple: {ee_multiple:.2f}")

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

