#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DCF mk8.3 — Four growth modes + Revenue/Margin Projector integration
- Patch notes:
    mk8.3: Growth method selector (1=Auto, 2=Manual, 3=ROIC, 4=Import from projector xlsx)
           Manual mode no longer prompts when not selected
           Import mode reads FCFF schedule from projector xlsx, skips base mode
    mk8.2: ROIC-driven growth mode (ROIC x reinvestment rate); terminal growth consistency check
    mk8.1: User-supplied beta; FCFF base modes (ttm/maint/avg3y); sensitivity table
- FCFF = (CFO - Capex) + Interest*(1 - tax)
- WACC via CAPM; EV -> Equity via net debt; mid-year discounting; share-base auto-correct
"""

import sys
import math
import numpy as np
import pandas as pd
import yfinance as yf

# -----------------------------
# HELPERS: inputs & parsing
# -----------------------------
def prompt_str(prompt, default=None):
    shown = f" [{default}]" if default is not None else ""
    s = input(f"{prompt}{shown}: ").strip()
    return s if s else default

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
        if s.endswith("bp"):
            return float(s[:-2]) / 10000.0
        return float(s)
    except Exception:
        print(f"⚠️  Invalid number; using default {default}")
        return float(default)

def prompt_bool(prompt, default=True):
    d = "y" if default else "n"
    s = input(f"{prompt} (y/n) [{d}]: ").strip().upper()
    if s in ("Y","YES"): return True
    if s in ("N","NO"):  return False
    return default

def parse_clip(s, default=None):
    """
    Accepts:
      - "", "none", "n", "no" -> None
      - "0.5, 2.5" -> (0.5, 2.5)
    """
    if s is None:
        return default
    s_clean = s.strip().lower()
    if s_clean in {"", "none", "n", "no"}:
        return None
    try:
        a, b = [float(x.strip()) for x in s_clean.split(",")]
        return (a, b)
    except Exception:
        print(f"⚠️  Invalid clip; using default {default}")
        return default

def parse_float_optional(prompt):
    """Return float or None if blank."""
    s = input(f"{prompt} (blank to skip): ").strip().lower()
    if s == "":
        return None
    try:
        if s.endswith("%"):
            return float(s[:-1]) / 100.0
        if s.endswith("bp"):
            return float(s[:-2]) / 10000.0
        return float(s)
    except Exception:
        print("⚠️  Invalid number; treating as blank.")
        return None

def grab(df, keys):
    """Return first matching row label."""
    for k in keys:
        if k in df.index:
            return df.loc[k]
    raise KeyError(str(keys))

def safe_num(x, default=0.0):
    try:
        v = float(x)
        if math.isfinite(v):
            return v
        return float(default)
    except Exception:
        return float(default)

def sanitize_vendor_beta(val, clip=None, default=1.10):
    b = safe_num(val, default)
    if not math.isfinite(b):
        b = default
    if clip is not None:
        lo, hi = clip
        b = float(np.clip(b, lo, hi))
    return b

# -----------------------------
# INTERACTIVE CONFIG
# -----------------------------
print("\n=== DCF mk8.1 Interactive Setup (User Beta) ===")
symbol = prompt_str_required("Enter company ticker").upper()
projection_years = prompt_int("Projection years", 10)
use_midyear = prompt_bool("Use mid-year discounting", True)

# Growth config — four modes
print('\n--- Growth Method ---')
print('  (1) Auto   — derived from 3y revenue CAGR')
print('  (2) Manual — you set each stage explicitly')
print('  (3) ROIC   — derived from ROIC x reinvestment rate')
print('  (4) Import — load FCFF schedule from projector xlsx')
growth_method = input('Select growth method [1/2/3/4]: ').strip()
if growth_method not in {'1','2','3','4'}:
    print('Invalid selection; defaulting to (1) Auto.')
    growth_method = '1'

use_auto_three_stage_growth = (growth_method == '1')
use_roic_growth             = (growth_method == '3')
use_import_growth           = (growth_method == '4')

# Import mode: get CSV path upfront
import_fcfs_usd = []
import_csv_path = None
if use_import_growth:
    import_csv_path = input('  Path to projector CSV: ').strip()

auto_stage1_years = prompt_int('Stage-1 years (if auto ON)', 3)
auto_stage2_years = prompt_int('Stage-2 years (if auto ON)', 3)
terminal_growth   = prompt_float('Terminal growth (as decimal)', 0.025)

# ROIC mode inputs (only if ROIC mode)
roic_target          = None
reinvest_rate_target = None
if use_roic_growth:
    print('\n--- ROIC-Driven Growth Inputs ---')
    print('Current ROIC and reinvestment rate will be auto-derived after data fetch.')
    print('You will be shown the derived values and can override them.')
    roic_target          = prompt_float('Target ROIC at end of horizon (decimal)', 0.12)
    reinvest_rate_target = prompt_float('Target reinvestment rate at end of horizon (decimal)', 0.30)

# Manual growth inputs (only prompted if manual mode selected)
stage1_years = 3; stage1_g = 0.30; stage2_gs = [0.20, 0.12, 0.08]; growth_rate = 0.06
if growth_method == '2':
    stage1_years = prompt_int('Stage-1 years', 3)
    stage1_g     = prompt_float('Stage-1 growth (decimal)', 0.30)
    stage2_gs    = [float(x.strip()) for x in prompt_str('Stage-2 growth list (comma sep decimals)', '0.20,0.12,0.08').split(',')]
    growth_rate  = prompt_float('Stage-3 growth (decimal)', 0.06)



# Capital costs
risk_free = prompt_float("Risk-free rate (decimal)", 0.04543)
market_return = prompt_float("Expected market return (decimal)", 0.085)  # ERP ~ 3.96%
tax_rate = prompt_float("Effective tax rate (decimal)", 0.165)
cost_of_debt_pre_tax = prompt_float("Pre-tax cost of debt (decimal)", 0.05)

# ✅ Beta: you type it (optional). If blank → fallback to vendor beta from yfinance.
beta_user = parse_float_optional("Enter beta (e.g., 1.10). Leave blank to use vendor beta")
beta_clip_vendor = parse_clip(prompt_str("Beta clip 'lo,hi' or 'none' (applies to vendor OR your input)", "none"), None)

# FCFF base mode (skipped for import mode)
if not use_import_growth:
    fcff_base_mode = prompt_str("FCFF base mode (ttm/maint/avg3y)", "maint").lower()
    assert fcff_base_mode in {"ttm","maint","avg3y"}
else:
    fcff_base_mode = "import"

# Currency conversion (to USD)
currency_conversion = {
    "USD": 1, "TWD": 0.031, "CAD": 0.73, "EUR": 1.08, "GBP": 1.27, "JPY": 0.0064,
}

print("\n--- Run Config ---")
print(f"Ticker: {symbol}")
print(f"Horizon: {projection_years}y | Mid-year: {use_midyear}")
if use_auto_three_stage_growth:
    growth_label = f'AUTO | Stage1={auto_stage1_years}y, Stage2={auto_stage2_years}y'
elif use_roic_growth:
    growth_label = f'ROIC-DRIVEN | Target ROIC={roic_target:.1%}, Target RR={reinvest_rate_target:.1%}'
elif use_import_growth:
    growth_label = f'IMPORT | CSV: {import_csv_path}'
else:
    growth_label = f'MANUAL | S1={stage1_years}@{stage1_g:.1%}, S2={",".join(f"{g:.1%}" for g in stage2_gs)}, S3={growth_rate:.1%}'
print(f"Growth: {growth_label}")
print(f"Terminal g: {terminal_growth:.2%}")
print(f"Beta (user): {beta_user if beta_user is not None else '— (will use vendor fallback)'}")
print(f"Beta clip: {beta_clip_vendor if beta_clip_vendor else 'none'}")
print(f"FCFF base mode: {fcff_base_mode}")
print(f"Risk-free: {risk_free:.2%} | Market return: {market_return:.2%} | Tax: {tax_rate:.2%} | Pre-tax debt: {cost_of_debt_pre_tax:.2%}")

# -----------------------------
# DATA FETCH
# -----------------------------
ticker = yf.Ticker(symbol)
info = ticker.info

currency = info.get("financialCurrency", "USD")
conversion_rate = currency_conversion.get(currency, 1.0)

# Shares / price / cap
yfin_shares_outstanding = info.get("sharesOutstanding")
if not yfin_shares_outstanding:
    raise ValueError("Missing sharesOutstanding from yfinance.")
market_cap_local = info.get("marketCap")
price_local = info.get("regularMarketPrice")
if not (market_cap_local and price_local):
    raise ValueError("Missing marketCap or regularMarketPrice from yfinance.")
market_cap = market_cap_local * conversion_rate
current_price = price_local

# Share-base sanity & auto-correct
implied_shares_local = market_cap_local / price_local if price_local else yfin_shares_outstanding
share_base_ratio = implied_shares_local / yfin_shares_outstanding if yfin_shares_outstanding else 1.0
shares_outstanding = yfin_shares_outstanding
if share_base_ratio < 0.9 or share_base_ratio > 1.1:
    print(f"⚠️ sharesOutstanding may exclude a class/dilution; overriding with implied shares (ratio={share_base_ratio:.2f}).")
    shares_outstanding = implied_shares_local  # use the same base that marketCap uses

# Debt / cash
debt = (info.get("totalDebt") or 0) * conversion_rate
total_cash = (info.get("totalCash") or 0) * conversion_rate

# Multiples (guarded; not used for TV here)
ev_info = info.get("enterpriseValue")
ebitda_info = info.get("ebitda")
ee_multiple = (ev_info / ebitda_info) if (ev_info and ebitda_info and ebitda_info > 0) else None

# -----------------------------
# STATEMENT-BASED FCFF (normalized)
# -----------------------------
qcf = ticker.quarterly_cashflow
acf = ticker.cashflow            # annual cash flow
afr_annual = ticker.financials   # annual income statement

# Pull TTM OCF & Capex from quarterly cash flow
ocf_ttm = capex_ttm = dep_ttm = 0.0
try:
    ocf_ttm   = safe_num(grab(qcf, ["Total Cash From Operating Activities","Operating Cash Flow"]).head(4).sum())
    capex_ttm = safe_num(grab(qcf, ["Capital Expenditures","Capital Expenditure"]).head(4).sum())  # usually negative
except Exception as e:
    print("⚠️ TTM OCF/Capex unavailable from quarterly cashflow:", e)

# D&A TTM (prefer quarterly; fallback to annual)
try:
    dep_ttm = safe_num(grab(qcf, ["Depreciation","Depreciation And Amortization"]).head(4).sum())
except Exception:
    try:
        dep_ttm = safe_num(grab(acf, ["Depreciation","Depreciation And Amortization"]).head(1).sum())
    except Exception:
        dep_ttm = 0.0

# Interest TTM from statements (fallback to .info)
interest_ttm = 0.0
try:
    qis = ticker.quarterly_financials
    interest_ttm = abs(safe_num(grab(qis, ["Interest Expense","Interest Expense Non-Operating"]).head(4).sum()))
except Exception:
    try:
        ais = ticker.financials
        interest_ttm = abs(safe_num(grab(ais, ["Interest Expense","Interest Expense Non-Operating"]).head(1).sum()))
    except Exception:
        interest_ttm = abs(safe_num(info.get("interestExpense", 0), 0))

# Equity-like TTM FCF = CFO - Capex (Capex typically negative)
fcf_equity_like_ttm = ocf_ttm + capex_ttm

def fcff_from_equity_like(x_equity_like: float) -> float:
    # FCFF = (CFO - Capex) + Interest*(1 - tax_rate)
    return x_equity_like + interest_ttm * (1 - tax_rate)

# Choose FCFF base per mode (skipped entirely for import mode)
fcff_base_local = 0.0
if use_import_growth:
    pass  # FCFF schedule loaded from CSV later
elif fcff_base_mode == "ttm":
    fcff_base_local = fcff_from_equity_like(fcf_equity_like_ttm)

elif fcff_base_mode == "maint":
    maint_capex = -min(abs(capex_ttm), abs(dep_ttm))  # keep capex sign negative
    fcff_base_local = (ocf_ttm + maint_capex) + interest_ttm * (1 - tax_rate)

elif fcff_base_mode == "avg3y":
    try:
        ocf_years   = grab(acf, ["Total Cash From Operating Activities","Operating Cash Flow"]).dropna().iloc[:3]
        capex_years = grab(acf, ["Capital Expenditures","Capital Expenditure"]).dropna().iloc[:3]
        fcf_equity_like_avg = safe_num((ocf_years + capex_years).mean())
        fcff_base_local = fcff_from_equity_like(fcf_equity_like_avg)
    except Exception as e:
        print("⚠️ Could not compute avg3y FCF from annual statements; falling back to TTM.", e)
        fcff_base_local = fcff_from_equity_like(fcf_equity_like_ttm)

# Convert to USD and guard (skip for import mode)
if not use_import_growth:
    fcff_ttm_usd = fcff_base_local * conversion_rate
    if not math.isfinite(fcff_ttm_usd) or fcff_ttm_usd <= 0:
        print("⚠️ FCFF base is non-positive. Consider switching fcff_base_mode or set a manual base.")
        sys.exit(1)
else:
    fcff_ttm_usd = 0.0  # not used in import mode

# Diagnostics
print(f"\n✅ FCFF base mode: {fcff_base_mode}")
if fcff_base_mode == "maint":
    print(f"OCF_TTM: {ocf_ttm:,.0f}  Capex_TTM: {capex_ttm:,.0f}  D&A_TTM: {dep_ttm:,.0f}  Int_TTM: {interest_ttm:,.0f} ({currency})")
    print(f"Maintenance capex used: {maint_capex:,.0f} ({currency})")
else:
    print(f"OCF_TTM: {ocf_ttm:,.0f}  Capex_TTM: {capex_ttm:,.0f}  D&A_TTM: {dep_ttm:,.0f}  Int_TTM: {interest_ttm:,.0f} ({currency})")
print(f"FCFF Base (USD): {fcff_ttm_usd:,.0f}")

# -----------------------------
# AUTO 3-STAGE GROWTH (from revenue CAGR)
# -----------------------------
def compute_rev_cagr_3y():
    try:
        rev = grab(afr_annual, ["Total Revenue"]).dropna()
        if rev.shape[0] < 4:
            raise ValueError("Not enough annual revenue points.")
        rev = rev.sort_index()  # oldest -> newest
        r_now = safe_num(rev.iloc[-1])
        r_3y  = safe_num(rev.iloc[-4])
        if r_now <= 0 or r_3y <= 0:
            raise ValueError("Non-positive revenues.")
        cagr = (r_now / r_3y) ** (1/3) - 1.0
        return float(cagr)
    except Exception:
        return None

auto_growth_plan = None
if use_auto_three_stage_growth:
    auto_cagr = compute_rev_cagr_3y()
    if auto_cagr is not None:
        s1 = float(np.clip(auto_cagr, 0.05, 0.30))
        s2_start = max(0.06, min(0.12, s1 * 0.6))
        s2_end   = max(0.05, min(0.10, s1 * 0.4))
        s2_list  = list(np.linspace(s2_start, s2_end, num=auto_stage2_years)) if auto_stage2_years > 1 else [s2_start]
        s3 = min(0.06, s2_end)
        auto_growth_plan = {"s1": s1, "s2": s2_list, "s3": s3}

if auto_growth_plan:
    print("Growth plan:",
          f"s1={auto_growth_plan['s1']:.1%}, ",
          f"s2={[f'{g:.1%}' for g in auto_growth_plan['s2']]}, ",
          f"s3={auto_growth_plan['s3']:.1%}")

# -----------------------------
# BETA (user-supplied or vendor fallback)
# -----------------------------
beta_vendor_raw = info.get("beta") or info.get("beta3Year") or info.get("beta5Year")
beta_vendor = sanitize_vendor_beta(beta_vendor_raw, clip=beta_clip_vendor, default=1.10)

if beta_user is None:
    beta_used, source_label = beta_vendor, "vendor (fallback)"
else:
    beta_used = float(beta_user)
    if beta_clip_vendor is not None:
        lo, hi = beta_clip_vendor
        beta_used = float(np.clip(beta_used, lo, hi))
    source_label = "user-supplied"

print("\n--- Beta Summary ---")
print(f"Vendor beta cand ({'no clip' if beta_clip_vendor is None else 'clip ' + str(beta_clip_vendor)}): "
      f"{safe_num(beta_vendor_raw, float('nan')):.3f} → cand {beta_vendor:.3f}")
print(f"β used ({source_label}): {beta_used:.3f}")

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

# Guardrail: keep (wacc - g) > 0 for TV
if wacc <= terminal_growth:
    new_g = max(0.0, wacc - 0.005)
    print(f"⚠️ Adjusting terminal growth from {terminal_growth:.3%} to {new_g:.3%} because WACC={wacc:.3%}.")
    terminal_growth = new_g

# -----------------------------
# ROIC DERIVATION (if ROIC mode selected)
# -----------------------------
roic_growth_rates = []   # will hold per-year implied growth rates if ROIC mode

if use_roic_growth:
    # Derive invested capital from balance sheet
    try:
        abs_annual = ticker.balance_sheet
        total_equity_r = safe_num(grab(abs_annual, ["Stockholders Equity","Total Stockholder Equity","Total Equity Gross Minority Interest"]).dropna().iloc[0])
        invested_capital_r = total_equity_r + debt - (info.get("totalCash") or 0) * conversion_rate
    except Exception:
        invested_capital_r = 0.0

    # Derive reinvestment from cash flow statement
    try:
        qbs = ticker.quarterly_balance_sheet
        ca_now  = safe_num(grab(qbs, ["Current Assets","Total Current Assets"]).dropna().iloc[0])
        ca_prev = safe_num(grab(qbs, ["Current Assets","Total Current Assets"]).dropna().iloc[1])
        cl_now  = safe_num(grab(qbs, ["Current Liabilities","Total Current Liabilities"]).dropna().iloc[0])
        cl_prev = safe_num(grab(qbs, ["Current Liabilities","Total Current Liabilities"]).dropna().iloc[1])
        cash_r  = safe_num(info.get("totalCash", 0)) * conversion_rate
        dwc_r   = (ca_now - cash_r - cl_now) - (ca_prev - cash_r - cl_prev)
    except Exception:
        dwc_r = 0.0

    nopat_r        = safe_num(grab(ticker.financials, ["EBIT","Operating Income"]).dropna().iloc[0]) * (1 - tax_rate) * conversion_rate
    reinvestment_r = max(0.0, capex_ttm + dep_ttm + dwc_r) * -1 if capex_ttm < 0 else max(0.0, abs(capex_ttm) - dep_ttm + dwc_r)
    # Simpler: use capex - DA + DWC
    reinvestment_r = max(0.0, abs(capex_ttm) - dep_ttm + dwc_r)
    roic_current   = nopat_r / invested_capital_r if invested_capital_r > 0 else 0.10
    rr_current     = reinvestment_r / nopat_r if nopat_r > 0 else 0.50

    print("\n--- ROIC-Driven Growth: Auto-Derived Inputs ---")
    print(f"Invested Capital (USD):  ${invested_capital_r:,.0f}")
    print(f"NOPAT (USD):             ${nopat_r:,.0f}")
    print(f"Reinvestment (USD):      ${reinvestment_r:,.0f}")
    print(f"Current ROIC:            {roic_current:.2%}")
    print(f"Current Reinvest Rate:   {rr_current:.2%}")

    # Flag if distorted
    if rr_current > 2.0 or rr_current < 0:
        print(f"⚠️  Reinvestment rate of {rr_current:.1%} looks distorted — consider overriding.")
    if roic_current < 0 or roic_current > 0.50:
        print(f"⚠️  ROIC of {roic_current:.1%} looks distorted — consider overriding.")

    # Offer overrides
    if prompt_bool(f"Override current ROIC ({roic_current:.2%})?", False):
        roic_current = prompt_float("  Enter current ROIC (decimal)", round(roic_current, 3))
    if prompt_bool(f"Override current reinvestment rate ({rr_current:.2%})?", False):
        rr_current = prompt_float("  Enter current reinvestment rate (decimal)", round(rr_current, 3))

    # Build year-by-year ROIC and RR fading linearly to targets
    print("\n--- Implied Growth Path (ROIC x Reinvestment Rate) ---")
    print(f"{'Year':<6} {'ROIC':>8} {'ReinvRate':>10} {'Impl.Growth':>12}")
    print("-" * 38)
    for yr in range(1, projection_years + 1):
        t = (yr - 1) / max(projection_years - 1, 1)
        roic_t = roic_current + (roic_target - roic_current) * t
        rr_t   = rr_current   + (reinvest_rate_target - rr_current) * t
        g_t    = roic_t * rr_t
        roic_growth_rates.append(g_t)
        print(f"{yr:<6} {roic_t:>8.2%} {rr_t:>10.2%} {g_t:>12.2%}")

    # Check consistency between terminal growth and year 10 implied growth
    year10_implied_g = roic_growth_rates[-1]
    if abs(year10_implied_g - terminal_growth) > 0.005:
        print(f"\n⚠️  Terminal growth ({terminal_growth:.2%}) differs from year 10 implied growth ({year10_implied_g:.2%}).")
        print(f"    For full consistency: terminal g should equal ROIC_terminal × RR_terminal = {year10_implied_g:.2%}")
        print(f"    Options:")
        print(f"      (1) Update terminal growth to {year10_implied_g:.2%} (fully consistent)")
        print(f"      (2) Keep {terminal_growth:.2%} (conservative — growth decelerates post-horizon)")
        print(f"      (3) Enter manually")
        tg_choice = input("    Select [1/2/3]: ").strip()
        if tg_choice == "1":
            terminal_growth = year10_implied_g
            print(f"  ✅ Terminal growth updated to {terminal_growth:.2%}")
        elif tg_choice == "3":
            terminal_growth = prompt_float("  Enter terminal growth (decimal)", round(year10_implied_g, 3))
            print(f"  ✅ Terminal growth set to {terminal_growth:.2%}")
        else:
            print(f"  ✅ Keeping terminal growth at {terminal_growth:.2%}")

    if prompt_bool("Proceed with this growth path?", True) is False:
        print("⚠️  Exiting — adjust target ROIC or reinvestment rate and rerun.")
        sys.exit(0)

# -----------------------------
# FCFF PROJECTIONS
# -----------------------------

# Import mode: load FCFF schedule from projector CSV
if use_import_growth:
    import_fcfs_usd = []
    try:
        ext = import_csv_path.strip().lower().split('.')[-1]
        if ext in ('xlsx', 'xls'):
            import openpyxl
            wb = openpyxl.load_workbook(import_csv_path)
            ws = wb.active
            headers = [cell.value for cell in ws[1]]
            fcff_col = headers.index('FCFF_USD_M')
            for row in ws.iter_rows(min_row=2, values_only=True):
                if row[fcff_col] is not None:
                    import_fcfs_usd.append(float(row[fcff_col]) * 1e6)
        else:
            import csv
            with open(import_csv_path, newline='') as csvfile:
                reader = csv.DictReader(csvfile)
                for row in reader:
                    import_fcfs_usd.append(float(row['FCFF_USD_M']) * 1e6)
        print(f"\n✅ Loaded {len(import_fcfs_usd)} years of FCFF from: {import_csv_path}")
        print(f"   FCFF schedule (USD M): {[round(v/1e6,1) for v in import_fcfs_usd]}")
        if len(import_fcfs_usd) != projection_years:
            print(f"⚠️  CSV has {len(import_fcfs_usd)} years but projection_years={projection_years}. Using CSV length.")
            projection_years = len(import_fcfs_usd)
    except Exception as e:
        sys.exit(f"⚠️  Failed to load projector CSV: {e}")

fcfs, fcf = [], fcff_ttm_usd

if use_import_growth and import_fcfs_usd:
    # Use projector FCFF schedule directly — no growth needed
    fcfs = import_fcfs_usd
elif use_roic_growth and roic_growth_rates:
    # ROIC-driven: grow base FCFF by implied growth each year
    for g in roic_growth_rates:
        fcf *= (1 + g); fcfs.append(fcf)
elif use_auto_three_stage_growth and auto_growth_plan:
    # Stage 1 (configurable)
    for _ in range(auto_stage1_years):
        fcf *= (1 + auto_growth_plan["s1"]); fcfs.append(fcf)
    # Stage 2 (fade via s2 list sized by auto_stage2_years)
    for g in auto_growth_plan["s2"]:
        fcf *= (1 + g); fcfs.append(fcf)
    # Stage 3 (fill to projection_years)
    while len(fcfs) < projection_years:
        fcf *= (1 + auto_growth_plan["s3"]); fcfs.append(fcf)
else:
    # Manual constants
    for _ in range(stage1_years):
        fcf *= (1 + stage1_g); fcfs.append(fcf)
    for g in stage2_gs:
        fcf *= (1 + g); fcfs.append(fcf)
    while len(fcfs) < projection_years:
        fcf *= (1 + growth_rate); fcfs.append(fcf)

if not fcfs:
    sys.exit("No projected FCFFs generated.")

# -----------------------------
# TERMINAL VALUE (Gordon)
# -----------------------------
tv_perp = fcfs[-1] * (1 + terminal_growth) / (wacc - terminal_growth)

# -----------------------------
# DISCOUNTING
# -----------------------------
if use_midyear:
    discounted_fcfs = [fcfs[i] / ((1 + wacc) ** (i + 0.5)) for i in range(len(fcfs))]
    discounted_tv = tv_perp / ((1 + wacc) ** (len(fcfs) - 0.5))
else:
    discounted_fcfs = [fcfs[i] / ((1 + wacc) ** (i + 1)) for i in range(len(fcfs))]
    discounted_tv = tv_perp / ((1 + wacc) ** len(fcfs))

enterprise_value_dcf = sum(discounted_fcfs) + discounted_tv

# -----------------------------
# EV -> Equity via NET debt (cash + short-term investments)
# -----------------------------
def find_short_term_investments():
    sti = safe_num(info.get("shortTermInvestments", 0.0)) * conversion_rate
    if sti != 0.0:
        return sti
    candidates = [
        "Short Term Investments","Short-term Investments","Short-term investments",
        "Short Term Marketable Securities","Marketable Securities","Marketable securities",
        "Short Term Investment Securities","Available For Sale Securities, Current",
        "Short Term Investments, Total",
    ]
    try:
        bs = ticker.balance_sheet
        for name in candidates:
            if name in bs.index:
                return safe_num(bs.loc[name].iloc[0]) * conversion_rate
    except Exception:
        pass
    try:
        qbs = ticker.quarterly_balance_sheet
        for name in candidates:
            if name in qbs.index:
                return safe_num(qbs.loc[name].iloc[0]) * conversion_rate
    except Exception:
        pass
    return 0.0

short_term_investments = find_short_term_investments()
if short_term_investments == 0.0:
    print("ℹ️ Short-term investments not found; treating as 0.")

cash_like = total_cash + short_term_investments
net_debt = debt - cash_like
equity_value_dcf = enterprise_value_dcf - net_debt
intrinsic_price = equity_value_dcf / shares_outstanding

print(f"Cash-like (cash + STI): ${cash_like:,.0f}  |  Net debt: ${net_debt:,.0f}")

# -----------------------------
# AUDIT & SANITY
# -----------------------------
def dcf_audit():
    print("\n=== DCF AUDIT ===")
    implied_local = market_cap_local / price_local if price_local else shares_outstanding
    ratio_used_vs_yfin = shares_outstanding / yfin_shares_outstanding if yfin_shares_outstanding else 1.0
    print(f"Implied shares (mcap/price): {implied_local:,.0f} | yfin shares: {yfin_shares_outstanding:,.0f} | used base: {shares_outstanding:,.0f} (used/yfin={ratio_used_vs_yfin:.2f})")
    implied_price = equity_value_dcf / shares_outstanding
    print(f"Per-share check: {implied_price:.2f} vs reported: {intrinsic_price:.2f}")
    tv_share = discounted_tv / enterprise_value_dcf
    print(f"Discounted TV / EV: {tv_share:.2%}")
    if tv_share > 0.85:
        print("⚠️ TV dominates >85% — extend horizon / adjust growth / mid-year / validate WACC & g.")

dcf_audit()

# -----------------------------
# IMPLIED GROWTH TO RECONCILE TO THE MARKET
# -----------------------------
def implied_constant_growth(target_price, fcff0, years, wacc, g_terminal, midyear=True, lo=-0.20, hi=0.50, tol=1e-4):
    so = shares_outstanding
    def price_for(g):
        f = fcff0
        path = []
        for _ in range(years):
            f *= (1 + g); path.append(f)
        tv = path[-1] * (1 + g_terminal) / max(1e-9, (wacc - g_terminal))
        if midyear:
            pv_f = sum(path[i] / ((1 + wacc) ** (i + 0.5)) for i in range(len(path)))
            pv_tv = tv / ((1 + wacc) ** (len(path) - 0.5))
        else:
            pv_f = sum(path[i] / ((1 + wacc) ** (i + 1)) for i in range(len(path)))
            pv_tv = tv / ((1 + wacc) ** len(path))
        ev = pv_f + pv_tv
        eq = ev - net_debt
        return eq / so
    
    p_lo, p_hi = price_for(lo), price_for(hi)
    
    if target_price < p_lo or target_price > p_hi:
        print(f"⚠️  Market price ${target_price:.2f} is outside the solvable range "
              f"[${p_lo:.2f} @ g={lo:.0%}, ${p_hi:.2f} @ g={hi:.0%}]. "
              f"Implied growth may be extrapolated — treat with caution.")
    
    a, b = lo, hi
    for _ in range(80):
        m = 0.5 * (a + b)
        p = price_for(m)
        if p < target_price: a = m
        else: b = m
        if abs(p - target_price) < tol: break
    
    if abs(m - lo) < tol or abs(m - hi) < tol:
        print(f"⚠️  Implied growth solver hit boundary ({m:.2%}) — result unreliable.")
    
    return m

if use_import_growth:
    # Implied growth solver not applicable in import mode — FCFF schedule is fixed
    # Instead show what terminal growth would be needed to match market price
    def tv_growth_for_market(target_price, fcfs_list, wacc, midyear=True, lo=-0.10, hi=wacc-0.001, tol=1e-4):
        so = shares_outstanding
        def price_for(g):
            if wacc <= g:
                return float('nan')
            tv = fcfs_list[-1] * (1 + g) / (wacc - g)
            if midyear:
                pv_f  = sum(fcfs_list[i] / ((1 + wacc) ** (i + 0.5)) for i in range(len(fcfs_list)))
                pv_tv = tv / ((1 + wacc) ** (len(fcfs_list) - 0.5))
            else:
                pv_f  = sum(fcfs_list[i] / ((1 + wacc) ** (i + 1)) for i in range(len(fcfs_list)))
                pv_tv = tv / ((1 + wacc) ** len(fcfs_list))
            return (pv_f + pv_tv - net_debt) / so
        a, b = lo, hi
        for _ in range(80):
            m = 0.5 * (a + b)
            p = price_for(m)
            if p < target_price: a = m
            else: b = m
            if abs(b - a) < tol: break
        return m
    implied_tv_g = tv_growth_for_market(current_price, fcfs, wacc, midyear=use_midyear)
    print(f"\nImplied terminal growth to match market price (given imported FCFF schedule): {implied_tv_g:.2%}")
else:
    implied_g = implied_constant_growth(current_price, fcff_ttm_usd, projection_years, wacc, terminal_growth, midyear=use_midyear)
    print(f"\nImplied constant growth over {projection_years}y to match market price: {implied_g:.2%}")

# -----------------------------
# OUTPUT
# -----------------------------
print("\n--- WACC CALCULATION ---")
print(f"Cost of Equity (CAPM): {cost_of_equity * 100:.2f}%")
print(f"Cost of Debt (after-tax): {cost_of_debt_after_tax * 100:.2f}%")
print(f"WACC: {wacc * 100:.2f}%")

print("\n--- DCF VALUATION RESULTS ---")
print(f"Ticker: {ticker.ticker}")
print(f"Company Name: {info.get('longName', 'N/A')}")
print(f"Financials reported in: {currency}  (converted to USD @ {conversion_rate})")
print(f"Projected FCFFs (USD): {[round(f/1e6, 2) for f in fcfs]}  (in millions)")
print(f"Terminal Value (USD): ${tv_perp:,.0f}")
print(f"Discounted TV (USD): ${discounted_tv:,.0f}")
print(f"Enterprise Value (USD): ${enterprise_value_dcf:,.0f}")
print(f"Equity Value (USD): ${equity_value_dcf:,.0f}")
print(f"Current Share Price (local): ${current_price:.2f}")
print(f"Intrinsic Share Price (USD): ${intrinsic_price:.2f}")

# -----------------------------
# SENSITIVITY TABLE (WACC vs Terminal Growth)
# -----------------------------
print("\n--- Sensitivity Analysis ---")
wacc_step  = prompt_float("WACC grid step size", 0.005)
wacc_steps = prompt_int("WACC steps either side of base", 2)
g_step     = prompt_float("Terminal g grid step size", 0.005)
g_steps    = prompt_int("Terminal g steps either side of base", 2)

wacc_range = [wacc + (i - wacc_steps) * wacc_step for i in range(2 * wacc_steps + 1)]
g_range    = [terminal_growth + (i - g_steps) * g_step for i in range(2 * g_steps + 1)]

def price_for_params(w, g):
    if w <= g:
        return float("nan")
    tv = fcfs[-1] * (1 + g) / (w - g)
    if use_midyear:
        pv_fcfs = sum(fcfs[i] / ((1 + w) ** (i + 0.5)) for i in range(len(fcfs)))
        pv_tv   = tv / ((1 + w) ** (len(fcfs) - 0.5))
    else:
        pv_fcfs = sum(fcfs[i] / ((1 + w) ** (i + 1)) for i in range(len(fcfs)))
        pv_tv   = tv / ((1 + w) ** len(fcfs))
    ev  = pv_fcfs + pv_tv
    eq  = ev - net_debt
    return eq / shares_outstanding

# Print table
col_w = 9
header = f"{'WACC/g':<8}" + "".join(f"{g:>{col_w}.2%}" for g in g_range)
print(header)
print("-" * len(header))

for w in wacc_range:
    row = f"{w:<8.2%}"
    for g in g_range:
        price = price_for_params(w, g)
        cell = f"${price:.2f}" if math.isfinite(price) else "  N/A  "
        # Mark base case
        is_base = abs(w - wacc) < 1e-6 and abs(g - terminal_growth) < 1e-6
        cell = f"[{cell}]" if is_base else f" {cell} "
        row += f"{cell:>{col_w}}"
    print(row)