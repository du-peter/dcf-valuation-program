#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Revenue & Margin Projector — Standalone script
- Revenue methods: (1) User forward estimates, (2) Segment-based, (3) Backlog-driven
- Margin methods: (A) Mean reversion, (B) Management guidance
- Outputs: Revenue, NOPAT, Capex, D&A, ΔWC, Reinvestment Rate, ROIC, FCFF
- Can be run standalone or to validate/feed inputs into DCF script
"""

import sys
import math
import numpy as np
import pandas as pd
import yfinance as yf

# -----------------------------
# HELPERS
# -----------------------------
def prompt_str_required(prompt):
    while True:
        s = input(f"{prompt}: ").strip()
        if s:
            return s
        print("⚠️  Please enter a value.")

def prompt_int(prompt, default):
    s = input(f"{prompt} [{default}]: ").strip()
    try:
        return int(s) if s else int(default)
    except Exception:
        print(f"⚠️  Invalid int; using default {default}")
        return int(default)

def prompt_float(prompt, default):
    s = input(f"{prompt} [{default}]: ").strip().lower()
    if not s:
        return float(default)
    try:
        if s.endswith("%"):
            return float(s[:-1]) / 100.0
        return float(s)
    except Exception:
        print(f"⚠️  Invalid number; using default {default}")
        return float(default)

def prompt_bool(prompt, default=True):
    d = "y" if default else "n"
    s = input(f"{prompt} (y/n) [{d}]: ").strip().upper()
    if s in ("Y", "YES"): return True
    if s in ("N", "NO"):  return False
    return default

def safe_num(x, default=0.0):
    try:
        v = float(x)
        return v if math.isfinite(v) else float(default)
    except Exception:
        return float(default)

def grab(df, keys):
    for k in keys:
        if k in df.index:
            return df.loc[k]
    raise KeyError(str(keys))

# -----------------------------
# SETUP
# -----------------------------
print("\n=== Revenue & Margin Projector ===")
symbol = prompt_str_required("Enter company ticker").upper()
projection_years = prompt_int("Projection years", 10)
tax_rate = prompt_float("Effective tax rate (decimal)", 0.21)

# Currency conversion (to USD)
currency_conversion = {
    "USD": 1, "TWD": 0.031, "CAD": 0.73, "EUR": 1.08, "GBP": 1.27, "JPY": 0.0064,
}

# -----------------------------
# DATA FETCH
# -----------------------------
print(f"\nFetching data for {symbol}...")
ticker = yf.Ticker(symbol)
info   = ticker.info

currency        = info.get("financialCurrency", "USD")
conversion_rate = currency_conversion.get(currency, 1.0)
print(f"Currency: {currency} (converted to USD @ {conversion_rate})")

acf          = ticker.cashflow
afr_annual   = ticker.financials
abs_annual   = ticker.balance_sheet

# Pull base financials
def get_latest(df, keys):
    try:
        return safe_num(grab(df, keys).dropna().iloc[0])
    except Exception:
        return 0.0

base_revenue  = get_latest(afr_annual, ["Total Revenue"])
base_ebit     = get_latest(afr_annual, ["EBIT", "Operating Income"])
base_da       = get_latest(acf,        ["Depreciation", "Depreciation And Amortization"])
base_capex    = abs(get_latest(acf,    ["Capital Expenditures", "Capital Expenditure"]))
base_nopat    = base_ebit * (1 - tax_rate)

# Working capital: current assets - current liabilities (ex-cash)
try:
    ca   = safe_num(grab(abs_annual, ["Current Assets", "Total Current Assets"]).dropna().iloc[0])
    cl   = safe_num(grab(abs_annual, ["Current Liabilities", "Total Current Liabilities"]).dropna().iloc[0])
    cash = safe_num(info.get("totalCash", 0))
    base_wc = (ca - cash) - cl
    # Prior year for delta
    ca2  = safe_num(grab(abs_annual, ["Current Assets", "Total Current Assets"]).dropna().iloc[1]) if grab(abs_annual, ["Current Assets", "Total Current Assets"]).dropna().shape[0] > 1 else ca
    cl2  = safe_num(grab(abs_annual, ["Current Liabilities", "Total Current Liabilities"]).dropna().iloc[1]) if grab(abs_annual, ["Current Liabilities", "Total Current Liabilities"]).dropna().shape[0] > 1 else cl
    prior_wc = (ca2 - cash) - cl2
    base_dwc = base_wc - prior_wc
except Exception:
    base_wc  = 0.0
    base_dwc = 0.0

# Invested capital & ROIC
try:
    total_equity = safe_num(grab(abs_annual, ["Stockholders Equity", "Total Stockholder Equity", "Total Equity Gross Minority Interest"]).dropna().iloc[0])
    total_debt   = safe_num(info.get("totalDebt", 0))
    total_cash_v = safe_num(info.get("totalCash", 0))
    invested_capital = total_equity + total_debt - total_cash_v
    base_roic = base_nopat / invested_capital if invested_capital > 0 else 0.0
except Exception:
    invested_capital = 0.0
    base_roic        = 0.0

print(f"\n--- Base Year Financials ({currency}, most recent annual) ---")
print(f"Revenue:            {base_revenue/1e6:>12,.1f}M")
print(f"EBIT:               {base_ebit/1e6:>12,.1f}M")
print(f"NOPAT:              {base_nopat/1e6:>12,.1f}M")
print(f"D&A:                {base_da/1e6:>12,.1f}M")
print(f"Capex:              {base_capex/1e6:>12,.1f}M")
print(f"ΔWorking Capital:   {base_dwc/1e6:>12,.1f}M  ← raw from financials")
print(f"Invested Capital:   {invested_capital/1e6:>12,.1f}M")
print(f"ROIC (pre-adj):     {base_roic:>12.1%}")

# -----------------------------
# ΔWC NORMALIZATION
# -----------------------------
# Flag if ΔWC looks distorted (large negative relative to revenue)
dwc_to_rev = base_dwc / base_revenue if base_revenue != 0 else 0.0
if base_dwc < 0 and abs(dwc_to_rev) > 0.10:
    print(f"\n⚠️  ΔWC is {base_dwc/1e6:,.1f}M ({dwc_to_rev:.1%} of revenue) — this looks unusually large.")
    print("    Possible causes: customer advance payments, deferred revenue, or one-time timing items.")
    print("    Options:")
    print("      (1) Keep as-is  — structural negative WC business (SaaS, subscriptions, insurance)")
    print("      (2) Normalize   — enter your own base ΔWC (one-time distortion)")
    print("      (3) Set to zero — conservative, treat as non-recurring")
    dwc_choice = input("    Select [1/2/3]: ").strip()
    if dwc_choice == "2":
        base_dwc = prompt_float(f"  Enter normalized ΔWC ({currency}M, enter as millions)", 0.0) * 1e6
        print(f"  ✅ ΔWC normalized to {base_dwc/1e6:,.1f}M")
    elif dwc_choice == "3":
        base_dwc = 0.0
        print("  ✅ ΔWC set to zero.")
    else:
        print("  ✅ Keeping ΔWC as-is.")
else:
    # Also offer override for positive or mildly negative ΔWC in case user wants to adjust
    if prompt_bool(f"  ΔWC = {base_dwc/1e6:,.1f}M. Override base ΔWC?", False):
        base_dwc = prompt_float(f"  Enter normalized ΔWC ({currency}M)", round(base_dwc/1e6, 1)) * 1e6
        print(f"  ✅ ΔWC set to {base_dwc/1e6:,.1f}M")

# Recompute reinvestment with (possibly adjusted) ΔWC
base_reinvestment      = max(0.0, base_capex - base_da + base_dwc)
base_reinvestment_rate = base_reinvestment / base_nopat if base_nopat > 0 else 0.0

print(f"\n--- Base Year (after ΔWC adjustment) ---")
print(f"ΔWorking Capital:   {base_dwc/1e6:>12,.1f}M")
print(f"Reinvestment:       {base_reinvestment/1e6:>12,.1f}M")
print(f"Reinvestment Rate:  {base_reinvestment_rate:>12.1%}")

# -----------------------------
# REVENUE PROJECTION
# -----------------------------
print("\n--- Revenue Projection ---")
print("Methods: (1) Forward estimates  (2) Segment-based  (3) Backlog-driven")
rev_method = input("Select revenue method [1/2/3]: ").strip()

projected_revenues = []

if rev_method == "1":
    print(f"\nEnter your revenue estimate for each of the {projection_years} years.")
    print(f"Base revenue: {base_revenue/1e6:,.1f}M {currency}. You can enter as millions (e.g. 1200) or growth % (e.g. 15%).")
    rev = base_revenue
    for yr in range(1, projection_years + 1):
        s = input(f"  Year {yr} revenue ({currency}M) or growth %: ").strip().lower()
        try:
            if s.endswith("%"):
                g = float(s[:-1]) / 100.0
                rev = rev * (1 + g)
            else:
                rev = float(s) * 1e6
        except Exception:
            print(f"  ⚠️  Invalid; using last revenue {rev/1e6:,.1f}M")
        projected_revenues.append(rev)

elif rev_method == "2":
    print("\nSegment-based revenue projection.")
    n_segments = prompt_int("How many business segments", 2)
    segment_revenues = []
    segment_names    = []
    for i in range(n_segments):
        name    = prompt_str_required(f"  Segment {i+1} name")
        seg_rev = prompt_float(f"  {name} current annual revenue ({currency}M)", 0) * 1e6
        segment_names.append(name)
        segment_revenues.append(seg_rev)

    # Growth rates per segment per year
    all_seg_projections = []
    for i, name in enumerate(segment_names):
        print(f"\n  Growth rates for '{name}' (enter as %, e.g. 15 for 15%):")
        seg_revs = [segment_revenues[i]]
        for yr in range(1, projection_years + 1):
            g = prompt_float(f"    Year {yr} growth %", 10) / 100.0 if "%" not in str(prompt_float) else prompt_float(f"    Year {yr} growth", 0.10)
            g = prompt_float(f"    Year {yr} growth (decimal, e.g. 0.15)", 0.10)
            seg_revs.append(seg_revs[-1] * (1 + g))
        all_seg_projections.append(seg_revs[1:])

    for yr in range(projection_years):
        total = sum(all_seg_projections[i][yr] for i in range(n_segments))
        projected_revenues.append(total)

    print(f"\n  Segment contributions in Year 1:")
    for i, name in enumerate(segment_names):
        print(f"    {name}: {all_seg_projections[i][0]/1e6:,.1f}M {currency}")

elif rev_method == "3":
    print("\nBacklog-driven revenue projection.")
    current_backlog     = prompt_float(f"Current backlog ({currency}M)", 0) * 1e6
    conversion_rate_bl  = prompt_float("Annual backlog-to-revenue conversion rate (e.g. 0.25 = 25% of backlog recognized as revenue per year)", 0.25)
    book_to_bill        = prompt_float("Expected book-to-bill ratio (new orders / revenue, e.g. 1.2)", 1.2)

    backlog = current_backlog
    rev     = base_revenue
    print(f"\n  Base revenue: {base_revenue/1e6:,.1f}M | Backlog: {current_backlog/1e6:,.1f}M")
    for yr in range(1, projection_years + 1):
        rev_from_backlog = backlog * conversion_rate_bl
        projected_revenues.append(rev_from_backlog)
        new_orders = rev_from_backlog * book_to_bill
        backlog    = backlog - rev_from_backlog + new_orders
        print(f"  Year {yr}: Revenue={rev_from_backlog/1e6:,.1f}M | New Orders={new_orders/1e6:,.1f}M | Backlog={backlog/1e6:,.1f}M")
    # Remove the inline prints from loop — they serve as progress, keep them
else:
    print("⚠️  Invalid method; defaulting to method 1 with 10% growth per year.")
    rev = base_revenue
    for yr in range(projection_years):
        rev *= 1.10
        projected_revenues.append(rev)

# -----------------------------
# MARGIN PROJECTION
# -----------------------------
print("\n--- Margin Projection ---")
print("Current EBIT margin: {:.1%}".format(base_ebit / base_revenue if base_revenue > 0 else 0))
print("Methods: (A) Mean reversion to target  (B) Management guidance per year")
margin_method = input("Select margin method [A/B]: ").strip().upper()

projected_ebit_margins = []
current_margin = base_ebit / base_revenue if base_revenue > 0 else 0.0

if margin_method == "A":
    target_margin  = prompt_float("Target EBIT margin to revert to (decimal)", round(current_margin, 3))
    reversion_years = prompt_int("Years to reach target margin", projection_years)
    for yr in range(1, projection_years + 1):
        if yr <= reversion_years:
            # Linear interpolation from current to target
            m = current_margin + (target_margin - current_margin) * (yr / reversion_years)
        else:
            m = target_margin
        projected_ebit_margins.append(m)

elif margin_method == "B":
    print(f"\nEnter guided EBIT margin for each of the {projection_years} years (decimal or %):")
    last_m = current_margin
    for yr in range(1, projection_years + 1):
        m = prompt_float(f"  Year {yr} EBIT margin", round(last_m, 3))
        projected_ebit_margins.append(m)
        last_m = m
else:
    print("⚠️  Invalid; defaulting to flat current margin.")
    projected_ebit_margins = [current_margin] * projection_years

# -----------------------------
# CAPEX & D&A PROJECTION
# -----------------------------
print("\n--- Capex & D&A Projection ---")
print(f"Current Capex: {base_capex/1e6:,.1f}M | D&A: {base_da/1e6:,.1f}M")
capex_pct_rev = base_capex / base_revenue if base_revenue > 0 else 0.05
da_pct_rev    = base_da    / base_revenue if base_revenue > 0 else 0.03

use_pct = prompt_bool("Project Capex & D&A as % of revenue (recommended)", True)

if use_pct:
    capex_pct_start  = prompt_float("Capex as % of revenue — starting year (decimal)", round(capex_pct_rev, 3))
    capex_pct_target = prompt_float("Capex as % of revenue — terminal year (decimal)", round(capex_pct_rev * 0.8, 3))
    da_pct           = prompt_float("D&A as % of revenue — flat assumption (decimal)", round(da_pct_rev, 3))
    projected_capex  = []
    projected_da     = []
    for yr in range(1, projection_years + 1):
        capex_pct = capex_pct_start + (capex_pct_target - capex_pct_start) * ((yr - 1) / max(projection_years - 1, 1))
        projected_capex.append(projected_revenues[yr - 1] * capex_pct)
        projected_da.append(projected_revenues[yr - 1] * da_pct)
else:
    projected_capex = []
    projected_da    = []
    for yr in range(1, projection_years + 1):
        projected_capex.append(prompt_float(f"  Year {yr} Capex ({currency}M)", round(base_capex / 1e6, 1)) * 1e6)
        projected_da.append(prompt_float(f"  Year {yr} D&A ({currency}M)", round(base_da / 1e6, 1)) * 1e6)

# -----------------------------
# WORKING CAPITAL PROJECTION
# -----------------------------
print("\n--- Working Capital Projection ---")
# If raw ratio was distorted (>10% of rev), default to 2% instead
wc_pct_raw     = base_dwc / base_revenue if base_revenue != 0 else 0.02
wc_pct_default = round(abs(wc_pct_raw), 3) if abs(wc_pct_raw) <= 0.10 else 0.02
print(f"  (e.g. 0.02 means for every $100M revenue increase, WC uses $2M)")
dwc_pct = prompt_float("DeltaWC as % of revenue change (decimal)", wc_pct_default)
projected_dwc = []
prev_rev = base_revenue
for yr in range(projection_years):
    delta_rev = projected_revenues[yr] - prev_rev
    projected_dwc.append(delta_rev * dwc_pct)
    prev_rev = projected_revenues[yr]

# -----------------------------
# COMPUTE DERIVED METRICS
# -----------------------------
rows = []
inv_cap = invested_capital  # carry forward and update each year

for yr in range(projection_years):
    rev    = projected_revenues[yr]
    ebit   = rev * projected_ebit_margins[yr]
    nopat  = ebit * (1 - tax_rate)
    capex  = projected_capex[yr]
    da     = projected_da[yr]
    dwc    = projected_dwc[yr]

    reinvestment      = max(0.0, capex - da + dwc)
    reinvestment_rate = reinvestment / nopat if nopat > 0 else 0.0
    fcff              = nopat - reinvestment
    roic              = nopat / inv_cap if inv_cap > 0 else 0.0
    implied_growth    = roic * reinvestment_rate

    # Update invested capital: grows by net reinvestment
    inv_cap += reinvestment

    rows.append({
        "Year":              yr + 1,
        "Revenue (M)":       round(rev / 1e6, 1),
        "EBIT Margin":       projected_ebit_margins[yr],
        "NOPAT (M)":         round(nopat / 1e6, 1),
        "Capex (M)":         round(capex / 1e6, 1),
        "D&A (M)":           round(da / 1e6, 1),
        "ΔWC (M)":           round(dwc / 1e6, 1),
        "Reinvestment (M)":  round(reinvestment / 1e6, 1),
        "Reinvest Rate":     reinvestment_rate,
        "ROIC":              roic,
        "Implied Growth":    implied_growth,
        "FCFF (M)":          round(fcff / 1e6, 1),
    })

df_out = pd.DataFrame(rows).set_index("Year")

# -----------------------------
# OUTPUT
# -----------------------------
print(f"\n=== Projection Output ({currency}, millions) ===")
print(f"{'Year':<6} {'Revenue':>10} {'EBIT Mgn':>10} {'NOPAT':>10} {'Capex':>10} {'D&A':>8} {'ΔWC':>8} {'Reinvst':>10} {'R.Rate':>8} {'ROIC':>8} {'Impl.g':>8} {'FCFF':>10}")
print("-" * 108)

for _, r in df_out.iterrows():
    print(
        f"{int(r.name):<6}"
        f"{r['Revenue (M)']:>10,.1f}"
        f"{r['EBIT Margin']:>10.1%}"
        f"{r['NOPAT (M)']:>10,.1f}"
        f"{r['Capex (M)']:>10,.1f}"
        f"{r['D&A (M)']:>8,.1f}"
        f"{r['ΔWC (M)']:>8,.1f}"
        f"{r['Reinvestment (M)']:>10,.1f}"
        f"{r['Reinvest Rate']:>8.1%}"
        f"{r['ROIC']:>8.1%}"
        f"{r['Implied Growth']:>8.1%}"
        f"{r['FCFF (M)']:>10,.1f}"
    )

print(f"\nAll figures in {currency} millions.")
print(f"FCFF = NOPAT - Reinvestment  |  Reinvestment = Capex - D&A + ΔWC")
print(f"ROIC = NOPAT / Invested Capital (updated annually)")
print(f"Implied Growth = ROIC × Reinvestment Rate")
# -----------------------------
# XLSX EXPORT
# -----------------------------
if prompt_bool("\nExport projection to xlsx (for import into DCF script)?", True):
    import os
    default_path = os.path.join(os.path.expanduser("~"), "Desktop", f"{symbol}_projection.xlsx")
    xlsx_path = input(f"  Save path [{default_path}]: ").strip()
    if not xlsx_path:
        xlsx_path = default_path
    export_rows = []
    for r in rows:
        export_rows.append({
            "Year":           r["Year"],
            "FCFF_USD_M":     round(r["FCFF (M)"] * conversion_rate, 4),
            "FCFF_local_M":   r["FCFF (M)"],
            "Revenue_M":      r["Revenue (M)"],
            "NOPAT_M":        r["NOPAT (M)"],
            "EBIT_Margin":    round(r["EBIT Margin"], 4),
            "Capex_M":        r["Capex (M)"],
            "DA_M":           r["D&A (M)"],
            "DWC_M":          r["\u0394WC (M)"],
            "Reinvest_Rate":  round(r["Reinvest Rate"], 4),
            "ROIC":           round(r["ROIC"], 4),
            "Implied_Growth": round(r["Implied Growth"], 4),
            "Currency":       currency,
            "Ticker":         symbol,
        })
    df_export = pd.DataFrame(export_rows)
    try:
        df_export.to_excel(xlsx_path, index=False, engine='openpyxl')
        print(f"  \u2705 Exported to: {xlsx_path}")
        print(f"  FCFF_USD_M column will be used by DCF import mode.")
    except Exception as e:
        print(f"  \u26a0\ufe0f  Export failed: {e}")