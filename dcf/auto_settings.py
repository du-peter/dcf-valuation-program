"""
auto_settings.py
Builds a first settings file for a ticker that does not have one yet, so that
    uv run dcf/dcf_engine.py MU
works with no setup. Every number is a starting point taken from the company's own
history, not a researched view. Review the file before trusting the output.

How the defaults are built:
- Growth:   3-year revenue growth from annual SEC filings (limited to between -5% and 25%),
            fading in equal steps to the terminal growth rate by year 6.
- Margin:   starts at the last-12-months EBIT margin and fades to the average of the annual
            EBIT margins on file (a rough through-cycle margin) by year 5.
- D&A:      last-12-months share of revenue.
- Capex:    starts at the last-12-months share of revenue and fades by year 5 to 1.15 x D&A.
            A company in a build-out spends far more than its depreciation; holding that
            rate forever while growth slows to 3% would make cash flow negative for no reason.
- Bear and bull: base growth minus/plus 3 points, terminal margin 25% lower/higher.
- Beta:     2 years of weekly returns vs SPY, then Blume-adjusted (2/3 x beta + 1/3).
- Terminal RONIC: cost of equity in the bear case (growth adds no value), +3 and +6 points
            in base and bull.
"""

from datetime import date

import pandas as pd

# Market-wide inputs. Update these when you update a reviewed company file.
RISK_FREE = 0.0498      # US 10-year Treasury, Nov 10 2026
ERP = 0.0441            # Damodaran implied ERP, Nov 1 2026
TAX = 0.21              # US federal statutory rate (assumption)


def _series(df, col):
    """A column as numbers, newest period first, empty values dropped."""
    if df is None or col not in df.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(df[col], errors="coerce").dropna()


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _fade(start, end, steps):
    """Equal steps from start to end, e.g. _fade(0.10, 0.03, 5)."""
    return [round(start + (end - start) * i / (steps - 1), 4) for i in range(steps)]


def build_defaults(symbol, statements):
    inc_a, inc_ttm, cf_ttm = statements["income_annual"], statements["income_ttm"], statements["cash_ttm"]

    rev_a = _series(inc_a, "total_revenue")
    ebit_a = _series(inc_a, "total_operating_income")
    rev_ttm = _series(inc_ttm, "total_revenue")
    ebit_ttm = _series(inc_ttm, "total_operating_income")
    if rev_ttm.empty or rev_ttm.iloc[0] <= 0:
        raise RuntimeError(f"No revenue found for {symbol} in SEC filings, so no settings file was built.")
    revenue = float(rev_ttm.iloc[0])

    # Revenue growth: compound annual rate across the annual periods on file (up to 3 years back)
    growth = 0.05
    if len(rev_a) >= 2:
        years = min(len(rev_a) - 1, 3)
        newest, oldest = float(rev_a.iloc[0]), float(rev_a.iloc[years])
        if newest > 0 and oldest > 0:
            growth = (newest / oldest) ** (1 / years) - 1
    growth = _clamp(growth, -0.05, 0.25)

    margin_now = float(ebit_ttm.iloc[0]) / revenue if not ebit_ttm.empty else 0.10
    margins_hist = [float(e) / float(r) for e, r in zip(ebit_a, rev_a) if r > 0]
    margin_avg = sum(margins_hist) / len(margins_hist) if margins_hist else margin_now
    margin_terminal = _clamp(margin_avg, 0.03, 0.60)

    capex = _series(cf_ttm, "purchase_of_plant_property_and_equipment")
    da = _series(cf_ttm, "depreciation_and_amortization")
    capex_pct = _clamp(abs(float(capex.iloc[0])) / revenue, 0.0, 0.40) if not capex.empty else 0.04
    da_pct = _clamp(abs(float(da.iloc[0])) / revenue, 0.0, 0.40) if not da.empty else 0.03

    return {"growth": growth, "margin_now": _clamp(margin_now, -0.50, 0.90),
            "margin_terminal": margin_terminal, "capex_pct": capex_pct, "da_pct": da_pct,
            "years_of_history": len(rev_a)}


def _scenario(name, prob, growth, d, g_term, margin_term, roic, multiple, story):
    capex_end = max(d["da_pct"] * 1.15, 0.01)
    capex_path = _fade(d["capex_pct"], capex_end, 5) if d["capex_pct"] > capex_end else [round(d["capex_pct"], 4)]
    return f'''
[scenarios.{name}]
# {story}
probability = {prob}                                  # ASSUMPTION
revenue_bn = []                                     # SETTING: empty = start from last-12-months revenue
growth_after = {_fade(growth, g_term, 6)}   # ASSUMPTION: {growth:.1%} (from history) fading to terminal growth
ebit_margin_early = [{d["margin_now"]:.3f}]                     # DATA: last-12-months EBIT margin
ebit_margin_terminal = {margin_term:.3f}                      # ASSUMPTION: normal-year margin (most important input)
terminal_margin_year = 5                            # ASSUMPTION: year the normal margin is reached
capex_pct_revenue = {capex_path}   # DATA then ASSUMPTION: last-12-months, fading to 1.15 x D&A
jv_capex_pct_revenue = 0.0                          # SETTING: only for companies with a joint venture
da_pct_revenue = {d["da_pct"]:.3f}                          # DATA: last-12-months D&A / revenue
nwc_pct_revenue = 0.10                              # ASSUMPTION: working capital needed per $1 of new revenue
terminal_growth = {g_term}                              # ASSUMPTION: growth forever after year 10
terminal_roic = {roic:.3f}                              # ASSUMPTION: return on new investment after year 10
exit_multiple = {multiple}                                # ASSUMPTION: EV/EBITDA at year 10 (cross-check)
'''


def write_settings(symbol, path, statements, raw_beta=None):
    """raw_beta: measured beta (or None if it could not be calculated)."""
    d = build_defaults(symbol, statements)
    g, m = d["growth"], d["margin_terminal"]
    if raw_beta is not None:
        beta = round(2 / 3 * raw_beta + 1 / 3, 2)
        beta_line = (f"beta = {beta}                        # DATA: measured {raw_beta:.2f} (2y weekly vs SPY), "
                     f"Blume-adjusted to 2/3 x {raw_beta:.2f} + 1/3")
    else:
        beta = 1.2
        beta_line = "beta = 1.2                         # ASSUMPTION: beta could not be calculated"
    ke = RISK_FREE + beta * ERP      # cost of equity, used to set terminal RONIC
    text = f'''# {symbol.upper()} settings for DCF mk9
# AUTO-GENERATED on {date.today()} from the company's own history. NOT REVIEWED.
# History used: {d["years_of_history"]} annual periods. Revenue growth {g:.1%} a year,
# last-12-months EBIT margin {d["margin_now"]:.1%}, average annual EBIT margin {m:.1%}.
#
# Every line is tagged:
#   DATA        taken from the company's filings or price history. Check, rarely change.
#   MARKET      market-wide input, same for every company. Update a few times a year.
#   ASSUMPTION  a judgment. Review each one before trusting the output.
#   SETTING     how the model runs. Usually leave as is.
# Rebuild from fresh data: uv run dcf/dcf_engine.py {symbol.lower()} rebuild

ticker = "{symbol.upper()}"
auto_generated = true          # SETTING: set to false once you have reviewed the assumptions
horizon_years = 10             # SETTING: years forecast before the terminal value
midyear = true                 # SETTING: cash flows arrive mid-year on average
primary_tv = "gordon"          # SETTING: "gordon" or "exit"; the other is shown as a cross-check
fx_to_price_currency = 1.0     # SETTING: 1.0 when filings and share price are in the same currency

[capital]
risk_free = {RISK_FREE}               # MARKET: US 10-year Treasury yield
equity_risk_premium = {ERP}     # MARKET: Damodaran implied ERP
{beta_line}
pre_tax_cost_of_debt = 0.06        # ASSUMPTION: matters only for companies with real debt
tax_rate = {TAX}                    # ASSUMPTION: US federal rate; replace with the company's effective rate

[shares]
sec_share_multiplier = 1           # SETTING: set to the split ratio if a split happened after the last filing

[overrides]
price = 0                          # SETTING: 0 = live price; enter a number to test another price
'''
    text += _scenario("bear", 0.25, g - 0.03, d, 0.02, m * 0.75, ke, 6.0,
                      "Bear: growth 3 points below history, normal margin 25% below the historical average. "
                      "RONIC = cost of equity, so growth adds no value.")
    text += _scenario("base", 0.50, g, d, 0.03, m, ke + 0.03, 8.0,
                      "Base: growth at its 3-year history, margin fades to the historical average.")
    text += _scenario("bull", 0.25, g + 0.03, d, 0.035, min(m * 1.25, 0.65), ke + 0.06, 10.0,
                      "Bull: growth 3 points above history, normal margin 25% above the historical average.")
    path.write_text(text)
    return d
