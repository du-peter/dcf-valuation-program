#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DCF mk5 — FCFF/WACC with robust manual beta and auto 3-stage growth
- FCFF = (CFO - Capex) + Interest*(1 - tax)
- WACC via CAPM with computed beta (10y monthly vs ^GSPC, Blume, unlever→relever, clipped)
- EV -> Equity via NET debt
- Auto 3-stage growth from 3y revenue CAGR (caps/fade); mid-year discounting; 10y horizon
"""

import sys
import math
import numpy as np
import pandas as pd
import yfinance as yf

# -----------------------------
# CONFIG
# -----------------------------
symbol = "nke"                      # Ticker
projection_years = 10                # Longer horizon
use_midyear = True                   # Mid-year convention

# Growth config
use_auto_three_stage_growth = True   # Auto stages from revenue CAGR (recommended)
# If you turn auto OFF, the constants below are used:
stage1_years, stage1_g = 3, 0.30     # yrs 1–3
stage2_gs = [0.20, 0.12, 0.08]       # yrs 4–6
growth_rate = 0.06                   # yrs 7–10
terminal_growth = 0.025              # long-run g

# Capital costs
risk_free = 0.04543                  # 10Y UST
market_return = 0.085                # Slightly calmer ERP than 9.043% -> ERP ~3.96%
tax_rate = 0.165
cost_of_debt_pre_tax = 0.05

# Manual beta (IB/PE style)
beta_market_index = "^GSPC"          # S&P 500 (broad, less jumpy)
beta_lookback = "10y"
beta_interval = "1mo"
beta_clip = (0.85, 1.25)             # Mega-cap sanity band
use_manual_beta = True
target_de_override = None            # e.g., 0.10 to force target D/E

# FCFF base selection for companies with lumpy capex (e.g., AMZN)
#   "ttm"   -> use raw TTM CFO - Capex (what you have now)
#   "maint" -> use maintenance capex ≈ min(|Capex_TTM|, |D&A_TTM|)
#   "avg3y" -> use 3-year average of (CFO - Capex) from ANNUAL statements
fcff_base_mode = "maint"   # try "maint" or "avg3y" for companies with lumpy capex

# Currency conversion (to USD)
currency_conversion = {
    "USD": 1, "TWD": 0.031, "CAD": 0.73, "EUR": 1.08, "GBP": 1.27, "JPY": 0.0064,
}

# -----------------------------
# HELPERS
# -----------------------------
def grab(df, keys):
    """Return first matching row label."""
    for k in keys:
        if k in df.index:
            return df.loc[k]
    raise KeyError(str(keys))

def safe_num(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return float(default)

# -----------------------------
# DATA FETCH
# -----------------------------
ticker = yf.Ticker(symbol)
info = ticker.info

currency = info.get("financialCurrency", "USD")
conversion_rate = currency_conversion.get(currency, 1.0)

# Shares / price / cap
shares_outstanding = info.get("sharesOutstanding")
if not shares_outstanding:
    raise ValueError("Missing sharesOutstanding from yfinance.")
market_cap_local = info.get("marketCap")
price_local = info.get("regularMarketPrice")
if not (market_cap_local and price_local):
    raise ValueError("Missing marketCap or regularMarketPrice from yfinance.")
market_cap = market_cap_local * conversion_rate
current_price = price_local

# --- Share-base sanity & auto-correct ---
implied_shares_local = market_cap_local / price_local
share_base_ratio = implied_shares_local / shares_outstanding
if share_base_ratio < 0.9 or share_base_ratio > 1.1:
    print(f"⚠️ sharesOutstanding may exclude a class/dilution; overriding with implied shares (ratio={share_base_ratio:.2f}).")
    shares_outstanding = implied_shares_local  # use the same base that marketCap uses

# Debt / cash
debt = (info.get("totalDebt") or 0) * conversion_rate
total_cash = (info.get("totalCash") or 0) * conversion_rate

# Multiples (guarded)
ev_info = info.get("enterpriseValue")
ebitda_info = info.get("ebitda")
ee_multiple = (ev_info / ebitda_info) if (ev_info and ebitda_info and ebitda_info > 0) else None

# -----------------------------
# STATEMENT-BASED FCFF (normalized)
# -----------------------------

qcf = ticker.quarterly_cashflow
afr = ticker.financials         # annual (income stmt)
acf = ticker.cashflow            # annual cash flow

# Pull TTM components
ocf_ttm = capex_ttm = dep_ttm = 0.0
try:
    ocf_ttm   = safe_num(grab(qcf, ["Total Cash From Operating Activities","Operating Cash Flow"]).head(4).sum())
    capex_ttm = safe_num(grab(qcf, ["Capital Expenditures","Capital Expenditure"]).head(4).sum())   # usually negative
except Exception as e:
    print("⚠️ TTM OCF/Capex unavailable from quarterly cashflow:", e)

# D&A TTM (use quarterly cashflow row if present, else annual)
try:
    dep_ttm = safe_num(grab(qcf, ["Depreciation","Depreciation And Amortization"]).head(4).sum())
except Exception:
    try:
        dep_ttm = safe_num(grab(acf, ["Depreciation","Depreciation And Amortization"]).head(1).sum())
    except Exception:
        dep_ttm = 0.0

# Interest TTM from statements (fallback to .info if needed)
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

# Build FCFF base per selected mode
fcf_equity_like_ttm = ocf_ttm + capex_ttm   # CFO - Capex (capex_ttm typically negative)

def fcff_from_equity_like(x_equity_like):
    # FCFF = (CFO - Capex) + Interest*(1 - tax)
    return x_equity_like + interest_ttm * (1 - tax_rate)

if fcff_base_mode == "ttm":
    fcff_base_local = fcff_from_equity_like(fcf_equity_like_ttm)

elif fcff_base_mode == "maint":
    # Maintenance capex proxy: use the smaller of |Capex_TTM| and |D&A_TTM|
    maint_capex = -min(abs(capex_ttm), abs(dep_ttm))  # keep capex sign (negative)
    fcff_base_local = (ocf_ttm + maint_capex) + interest_ttm * (1 - tax_rate)

elif fcff_base_mode == "avg3y":
    # Use ANNUAL CFO/Capex (more history than quarterly TTM). Average last 3 years.
    try:
        ocf_years   = grab(acf, ["Total Cash From Operating Activities","Operating Cash Flow"]).dropna().iloc[:3]
        capex_years = grab(acf, ["Capital Expenditures","Capital Expenditure"]).dropna().iloc[:3]
        fcf_equity_like_avg = safe_num((ocf_years + capex_years).mean())
        fcff_base_local = fcff_from_equity_like(fcf_equity_like_avg)
    except Exception as e:
        print("⚠️ Could not compute avg3y FCF from annual statements; falling back to TTM.", e)
        fcff_base_local = fcff_from_equity_like(fcf_equity_like_ttm)

else:
    print(f"⚠️ Unknown fcff_base_mode={fcff_base_mode}; using TTM.")
    fcff_base_local = fcff_from_equity_like(fcf_equity_like_ttm)

# Convert to USD
fcff_ttm_usd = fcff_base_local * conversion_rate

if not math.isfinite(fcff_ttm_usd) or fcff_ttm_usd <= 0:
    print("⚠️ FCFF base is non-positive. Consider switching fcff_base_mode or set a manual base.")
    sys.exit(1)

# Diagnostics
print(f"✅ FCFF base mode: {fcff_base_mode}")
print(f"OCF_TTM: {ocf_ttm:,.0f}  Capex_TTM: {capex_ttm:,.0f}  D&A_TTM: {dep_ttm:,.0f}  Int_TTM: {interest_ttm:,.0f} ({currency})")
if fcff_base_mode == "maint":
    print(f"Maintenance capex used: {maint_capex:,.0f} ({currency})")
print(f"FCFF Base (USD): {fcff_ttm_usd:,.0f}")

# -----------------------------
# AUTO 3-STAGE GROWTH (from revenue CAGR)
# -----------------------------
auto_stage1_years, auto_stage2_years = 3, 3
auto_stage3_years = max(0, projection_years - auto_stage1_years - auto_stage2_years)

def compute_rev_cagr_3y():
    try:
        afr = ticker.financials  # annual
        rev = grab(afr, ["Total Revenue"]).dropna()
        if rev.shape[0] < 4:
            raise ValueError("Not enough annual revenue points.")
        rev = rev.sort_index()  # oldest -> newest
        r_now = safe_num(rev.iloc[-1])
        r_3y = safe_num(rev.iloc[-4])
        if r_now <= 0 or r_3y <= 0:
            raise ValueError("Non-positive revenues.")
        cagr = (r_now / r_3y) ** (1/3) - 1.0
        return float(cagr)
    except Exception:
        return None

auto_cagr = compute_rev_cagr_3y()
if use_auto_three_stage_growth and auto_cagr is not None:
    # Cap & floor Stage 1 growth to a sensible range
    s1 = float(np.clip(auto_cagr, 0.05, 0.30))
    # Stage 2: fade linearly from s1 toward a mid single-digit target (~6–8%)
    s2_start = max(0.06, min(0.12, s1 * 0.6))
    s2_end   = max(0.05, min(0.10, s1 * 0.4))
    if auto_stage2_years > 1:
        s2_list = list(np.linspace(s2_start, s2_end, num=auto_stage2_years))
    else:
        s2_list = [s2_start]
    # Stage 3: steady growth (pick lower of 6% and s2_end)
    s3 = min(0.06, s2_end)
    # Terminal g: keep at configured terminal_growth (2.5% default)
    auto_growth_plan = {"s1": s1, "s2": s2_list, "s3": s3}
else:
    auto_growth_plan = None  # fall back to manual constants

# -----------------------------
# MANUAL BETA (IB/PE STYLE)
# -----------------------------
def compute_manual_beta(tick, benchmark, lookback="10y", interval="1mo",
                        tax=0.21, d_val=0.0, e_val=1.0, target_de=None, clip=(0.85, 1.25)):
    try:
        raw = yf.download([tick, benchmark], period=lookback, interval=interval,
                          auto_adjust=True, progress=False)
        # Close columns (auto_adjust=True → 'Close' only)
        if isinstance(raw.columns, pd.MultiIndex):
            tops = set(raw.columns.get_level_values(0))
            if "Close" in tops: prices = raw["Close"]
            elif "Adj Close" in tops: prices = raw["Adj Close"]
            else: raise ValueError("No Close/Adj Close in download.")
        else:
            if "Close" in raw.columns: prices = raw["Close"]
            elif "Adj Close" in raw.columns: prices = raw["Adj Close"]
            else: raise ValueError("No Close/Adj Close in download.")
        prices = prices[[tick, benchmark]].dropna(how="any")
        if prices.shape[0] < 60:  # <5y monthly
            raise ValueError("Not enough observations.")
        rets = prices.pct_change().dropna()
        mkt = rets[benchmark].values; stk = rets[tick].values
        if np.var(mkt) == 0: raise ValueError("Zero market variance.")
        beta_raw = np.cov(stk, mkt, ddof=0)[0, 1] / np.var(mkt, ddof=0)
        beta_blume = 0.67 * beta_raw + 0.33 * 1.0
        de_cur = (d_val / e_val) if (e_val and e_val > 0) else 0.0
        beta_unlev = beta_blume / (1 + (1 - tax) * de_cur)
        de_tgt = de_cur if target_de is None else float(target_de)
        beta_relev = beta_unlev * (1 + (1 - tax) * de_tgt)
        lo, hi = clip
        beta_final = float(np.clip(beta_relev, lo, hi))
        details = {"beta_raw": beta_raw, "beta_blume": beta_blume, "beta_unlevered": beta_unlev,
                   "de_current": de_cur, "de_target": de_tgt, "beta_final_clipped": beta_final,
                   "n_obs": int(rets.shape[0])}
        return beta_final, details
    except Exception as e:
        print(f"⚠️ Manual beta computation failed: {e}")
        return None, {"error": str(e)}

beta_manual, beta_details = compute_manual_beta(
    tick=symbol.upper(), benchmark=beta_market_index,
    lookback=beta_lookback, interval=beta_interval,
    tax=tax_rate, d_val=debt, e_val=market_cap,
    target_de=target_de_override, clip=beta_clip
)

beta_vendor = info.get("beta") or info.get("beta3Year") or info.get("beta5Year") or 1.10
if not isinstance(beta_vendor, (int, float)) or not math.isfinite(beta_vendor):
    beta_vendor = 1.10
beta_vendor = float(np.clip(beta_vendor, beta_clip[0], beta_clip[1]))

beta_used = beta_manual if (use_manual_beta and beta_manual is not None) else beta_vendor

print("\n--- Beta Summary ---")
if beta_manual is not None:
    print(f"Manual beta (final, clipped): {beta_used:.3f} | "
          f"raw={beta_details.get('beta_raw', float('nan')):.3f} "
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

# Guardrail: keep (wacc - g) > 0 for TV
if wacc <= terminal_growth:
    new_g = max(0.0, wacc - 0.005)
    print(f"⚠️ Adjusting terminal growth from {terminal_growth:.3%} to {new_g:.3%} because WACC={wacc:.3%}.")
    terminal_growth = new_g

# -----------------------------
# FCFF PROJECTIONS
# -----------------------------
fcfs, fcf = [], fcff_ttm_usd

if use_auto_three_stage_growth and auto_growth_plan:
    # Stage 1 (3y at s1)
    for _ in range(auto_stage1_years):
        fcf *= (1 + auto_growth_plan["s1"]); fcfs.append(fcf)
    # Stage 2 (3y fade via s2 list)
    for g in auto_growth_plan["s2"]:
        fcf *= (1 + g); fcfs.append(fcf)
    # Stage 3 (rest at s3 until projection_years)
    while len(fcfs) < projection_years:
        fcf *= (1 + auto_growth_plan["s3"]); fcfs.append(fcf)
else:
    # Manual constants (your previous chassis)
    for _ in range(stage1_years):
        fcf *= (1 + stage1_g); fcfs.append(fcf)
    for g in stage2_gs:
        fcf *= (1 + g); fcfs.append(fcf)
    while len(fcfs) < projection_years:
        fcf *= (1 + growth_rate); fcfs.append(fcf)

if not fcfs:
    sys.exit("No projected FCFFs generated.")

# -----------------------------
# TERMINAL VALUE (Gordon only by default)
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

# EV -> Equity via NET debt (treat short-term investments as cash-like)
short_term_investments = safe_num(info.get("shortTermInvestments", 0.0)) * conversion_rate
if short_term_investments == 0.0:
    # Fallback: pull from the balance sheet if the info field is missing/stale
    try:
        bs = ticker.balance_sheet
        sti_row = grab(bs, ["Short Term Investments",
                            "Short-term Investments",
                            "Short Term Investment Securities",
                            "Marketable Securities"])
        # yfinance returns most-recent period in the first column for these tables
        short_term_investments = safe_num(sti_row.iloc[0]) * conversion_rate
    except Exception:
        print("ℹ️ Short-term investments not found; treating as 0.")
        short_term_investments = 0.0

cash_like = total_cash + short_term_investments
net_debt = debt - cash_like

equity_value_dcf = enterprise_value_dcf - net_debt
intrinsic_price = equity_value_dcf / shares_outstanding

# (optional) quick print so you can sanity-check AMZN:
print(f"Cash-like (cash + STI): ${cash_like:,.0f}  |  Net debt: ${net_debt:,.0f}")

# -----------------------------
# AUDIT & SANITY
# -----------------------------
def dcf_audit():
    print("\n=== DCF AUDIT ===")
    implied_shares_local = market_cap_local / price_local
    ratio = implied_shares_local / shares_outstanding
    print(f"Implied shares (mcap/price): {implied_shares_local:,.0f} vs yfin: {shares_outstanding:,.0f} (ratio={ratio:.2f})")
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

def implied_constant_growth(target_price, fcff0, years, wacc, g_terminal, midyear=True, lo=0.00, hi=0.30, tol=1e-4):
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
    a, b = lo, hi
    for _ in range(80):
        m = 0.5 * (a + b)
        p = price_for(m)
        if p < target_price: a = m
        else: b = m
        if abs(p - target_price) < tol: break
    return m

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
