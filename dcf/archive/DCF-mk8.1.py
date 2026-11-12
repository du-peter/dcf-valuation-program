#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DCF mk8.1 — Interactive FCFF/WACC with manual-or-vendor beta, auto 3-stage growth, and cash-like net debt
- Prompts for: ticker, horizon, beta source/params, FCFF base mode, cost-of-capital inputs
- FCFF = (CFO - Capex) + Interest*(1 - tax)
- WACC via CAPM with computed beta (10y monthly vs ^GSPC, Blume, unlever→relever, clipped) OR vendor beta
- EV -> Equity via NET debt (cash + short-term investments)
- Auto 3-stage growth from 3y revenue CAGR (caps/fade); mid-year discounting
- Share-base auto-correct for multi-class issuers
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
    if s in ("y","yes"): return True
    if s in ("n","no"):  return False
    return default

def prompt_choice(prompt, choices, default):
    """
    choices: e.g. ["manual","vendor","auto"]
    accepts full words or short aliases: m/v/a
    case- and whitespace-insensitive
    """
    choices_lower = [c.lower() for c in choices]
    alias = {"m": "manual", "v": "vendor", "a": "auto"}
    cs = "/".join(choices_lower)
    s = input(f"{prompt} ({cs}) [{default}]: ").strip().lower()
    if not s:
        return default
    s = alias.get(s, s)  # expand aliases
    if s in choices_lower:
        return s
    print(f"⚠️  Invalid choice; using default {default}")
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

def parse_float_list(s, default_list):
    if not s: return default_list
    try:
        return [float(x.strip()) for x in s.split(",") if x.strip() != ""]
    except Exception:
        print(f"⚠️  Invalid list; using default {default_list}")
        return default_list

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

# -----------------------------
# INTERACTIVE CONFIG
# -----------------------------
print("\n=== DCF mk8.1 Interactive Setup ===")
symbol = prompt_str_required("Enter company ticker").upper()   # <- required; no example/default shown
projection_years = prompt_int("Projection years", 10)
use_midyear = prompt_bool("Use mid-year discounting", True)

# Growth config
use_auto_three_stage_growth = prompt_bool("Use auto 3-stage growth (from 3y revenue CAGR)", True)
auto_stage1_years = prompt_int("Stage-1 years (if auto ON)", 3)
auto_stage2_years = prompt_int("Stage-2 years (if auto ON)", 3)
terminal_growth = prompt_float("Terminal growth (as decimal)", 0.025)

# Manual growth fallbacks (used if auto OFF)
stage1_years = prompt_int("Stage-1 years (manual mode)", 3)
stage1_g = prompt_float("Stage-1 growth (manual mode, decimal)", 0.30)
stage2_gs = parse_float_list(
    prompt_str("Stage-2 growth list (manual mode, comma sep decimals)", "0.20,0.12,0.08"),
    [0.20, 0.12, 0.08]
)
growth_rate = prompt_float("Stage-3 growth (manual mode, decimal)", 0.06)

# Capital costs
risk_free = prompt_float("Risk-free rate (decimal)", 0.04543)
market_return = prompt_float("Expected market return (decimal)", 0.085)  # ERP ~ 3.96%
tax_rate = prompt_float("Effective tax rate (decimal)", 0.165)
cost_of_debt_pre_tax = prompt_float("Pre-tax cost of debt (decimal)", 0.05)

# Beta settings
beta_source = prompt_choice("Beta source", ["manual","vendor","auto"], "auto")
beta_market_index = prompt_str("Benchmark index for manual beta", "^GSPC")
beta_lookback = prompt_str("Manual beta lookback period (e.g., 10y, 5y)", "10y")
beta_interval = prompt_str("Manual beta sampling interval (e.g., 1mo, 1wk)", "1mo")
beta_clip_manual = parse_clip(prompt_str("Manual beta clip 'lo,hi' or 'none'", "0.85,1.25"), (0.85, 1.25))
beta_clip_vendor = parse_clip(prompt_str("Vendor beta clip 'lo,hi' or 'none'", "none"), None)
tgt_de_str = prompt_str("Target D/E override (decimal) or blank for current", "")
target_de_override = float(tgt_de_str) if tgt_de_str not in ("", None) else None

# FCFF base mode
fcff_base_mode = prompt_choice("FCFF base mode", ["ttm","maint","avg3y"], "maint")

assert beta_source in {"manual", "vendor", "auto"}
assert projection_years >= (auto_stage1_years + auto_stage2_years) if use_auto_three_stage_growth else True

# Currency conversion (to USD)
currency_conversion = {
    "USD": 1, "TWD": 0.031, "CAD": 0.73, "EUR": 1.08, "GBP": 1.27, "JPY": 0.0064,
}

print("\n--- Run Config ---")
print(f"Ticker: {symbol}")
print(f"Horizon: {projection_years}y | Mid-year: {use_midyear}")
print(f"Growth: {'AUTO' if use_auto_three_stage_growth else 'MANUAL'}"
      + (f' | Stage1={auto_stage1_years}y, Stage2={auto_stage2_years}y' if use_auto_three_stage_growth else
         f' | S1={stage1_years}@{stage1_g:.1%}, S2={",".join(f"{g:.1%}" for g in stage2_gs)}, S3={growth_rate:.1%}'))
print(f"Terminal g: {terminal_growth:.2%}")
print(f"Beta source: {beta_source} | Bench: {beta_market_index} | Lookback: {beta_lookback} | Interval: {beta_interval}")
print(f"Manual β clip: {beta_clip_manual if beta_clip_manual else 'none'} | Vendor β clip: {beta_clip_vendor if beta_clip_vendor else 'none'}")
print(f"Target D/E override: {target_de_override if target_de_override is not None else 'current'}")
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

# Choose FCFF base per mode
if fcff_base_mode == "ttm":
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

else:
    print(f"⚠️ Unknown fcff_base_mode={fcff_base_mode}; using TTM.")
    fcff_base_local = fcff_from_equity_like(fcf_equity_like_ttm)

# Convert to USD and guard
fcff_ttm_usd = fcff_base_local * conversion_rate
if not math.isfinite(fcff_ttm_usd) or fcff_ttm_usd <= 0:
    print("⚠️ FCFF base is non-positive. Consider switching fcff_base_mode or set a manual base.")
    sys.exit(1)

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
# BETA (manual + vendor with explicit selector)
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
        if np.var(mkt) == 0:
            raise ValueError("Zero market variance.")
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

def sanitize_vendor_beta(val, clip=None, default=1.10):
    b = safe_num(val, default)
    if not math.isfinite(b):
        b = default
    if clip is not None:
        lo, hi = clip
        b = float(np.clip(b, lo, hi))
    return b

# Candidates
beta_manual, beta_details = compute_manual_beta(
    tick=symbol.upper(), benchmark=beta_market_index,
    lookback=beta_lookback, interval=beta_interval,
    tax=tax_rate, d_val=debt, e_val=market_cap,
    target_de=target_de_override, clip=beta_clip_manual or (0.0, 10.0)
)
beta_vendor_raw = info.get("beta") or info.get("beta3Year") or info.get("beta5Year")
beta_vendor = sanitize_vendor_beta(beta_vendor_raw, clip=beta_clip_vendor, default=1.10)

# Select per toggle
if beta_source.upper() == "manual":
    if beta_manual is not None:
        beta_used, source_label = beta_manual, "manual"
    else:
        beta_used, source_label = beta_vendor, "vendor (fallback: manual failed)"
elif beta_source.upper() == "vendor":
    beta_used, source_label = beta_vendor, "vendor"
else:  # "auto"
    if beta_manual is not None:
        beta_used, source_label = beta_manual, "manual (auto)"
    else:
        beta_used, source_label = beta_vendor, "vendor (auto)"

# Summary print
print("\n--- Beta Summary ---")
if beta_manual is not None:
    print(f"Manual beta cand (clip {beta_clip_manual if beta_clip_manual else 'none'}): {beta_manual:.3f} | "
          f"raw={beta_details.get('beta_raw', float('nan')):.3f} "
          f"blume={beta_details.get('beta_blume', float('nan')):.3f} "
          f"D/E cur={beta_details.get('de_current', float('nan')):.3f} "
          f"D/E tgt={beta_details.get('de_target', float('nan')):.3f} "
          f"n={beta_details.get('n_obs','N/A')}")
else:
    print("Manual beta cand: n/a")
print(f"Vendor beta cand ({'no clip' if beta_clip_vendor is None else f'clip '+str(beta_clip_vendor)}): "
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
# FCFF PROJECTIONS
# -----------------------------
fcfs, fcf = [], fcff_ttm_usd

if use_auto_three_stage_growth and auto_growth_plan:
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
