"""
model.py
The single valuation function. Everything else (scenarios, sensitivity, reverse DCF,
Monte Carlo later) calls value_from_forecast(), so a fix here fixes every output.

Steps:  forecast FCFF -> discount at WACC -> terminal value (two methods)
        -> enterprise value -> equity value -> value per share
"""

import math


def cost_of_capital(cap, market_cap, debt):
    """CAPM cost of equity and WACC.
    Weights use MARKET values: equity = price x BASIC shares, debt = book debt incl. leases."""
    ke = cap["risk_free"] + cap["beta"] * cap["equity_risk_premium"]
    kd_after_tax = cap["pre_tax_cost_of_debt"] * (1 - cap["tax_rate"])
    total = market_cap + debt
    w_e = market_cap / total if total > 0 else 1.0
    w_d = 1 - w_e
    wacc = w_e * ke + w_d * kd_after_tax
    return {"cost_of_equity": ke, "cost_of_debt_after_tax": kd_after_tax,
            "weight_equity": w_e, "weight_debt": w_d, "wacc": wacc}


def discount_factor(t, wacc, midyear):
    """Year t cash flow. Mid-year convention assumes cash arrives evenly through the year."""
    return 1 / (1 + wacc) ** (t - 0.5 if midyear else t)


def value_from_forecast(fc, wacc, terminal_growth, terminal_roic, exit_multiple,
                        bridge, midyear=True, primary_tv="gordon"):
    """
    fc: forecast dict from forecast.build_forecast() (lists by year: fcff, nopat, ebitda)
    bridge: net-debt items and share counts for EV -> equity -> per share
    Returns a dict with EV, equity, value per share under both terminal methods.
    """
    n = len(fc["fcff"])
    pv_fcff = sum(fc["fcff"][i] * discount_factor(i + 1, wacc, midyear) for i in range(n))

    # --- Terminal value 1: Gordon growth, with reinvestment consistent with g ---
    # FCFF(N+1) = NOPAT(N+1) x (1 - g / RONIC). Growing forever needs reinvestment;
    # this keeps terminal cash flow consistent with the growth rate assumed.
    if wacc <= terminal_growth:
        tv_gordon = float("nan")
    else:
        nopat_next = fc["nopat"][-1] * (1 + terminal_growth)
        reinvest_share = terminal_growth / terminal_roic if terminal_roic > 0 else 0.0
        fcff_next = nopat_next * (1 - reinvest_share)
        tv_gordon = fcff_next / (wacc - terminal_growth)
    # Mid-year convention: Gordon TV discounted at N - 0.5 (same timing as the year-N cash flow)
    pv_tv_gordon = tv_gordon * discount_factor(n, wacc, midyear)

    # --- Terminal value 2: exit multiple (EV/EBITDA), discounted at end of year N ---
    tv_exit = fc["ebitda"][-1] * exit_multiple
    pv_tv_exit = tv_exit * discount_factor(n, wacc, False)

    def to_equity(ev):
        equity = ev - bridge["debt"] + bridge["cash"] + bridge["short_term_investments"]
        per_share = equity / bridge["diluted_shares"] * bridge["fx_to_price_currency"]
        return equity, per_share

    ev_g = pv_fcff + pv_tv_gordon
    ev_x = pv_fcff + pv_tv_exit
    eq_g, ps_g = to_equity(ev_g)
    eq_x, ps_x = to_equity(ev_x)

    # Cross-checks: what each terminal method implies about the other
    ebitda_n = fc["ebitda"][-1]
    implied_exit_multiple = tv_gordon / ebitda_n if ebitda_n > 0 else float("nan")
    implied_g_from_exit, implied_g_note = _implied_g(tv_exit, fc["nopat"][-1], wacc, terminal_roic)

    primary = "gordon" if primary_tv == "gordon" else "exit"
    return {
        "pv_fcff": pv_fcff,
        "gordon": {"tv": tv_gordon, "pv_tv": pv_tv_gordon, "ev": ev_g, "equity": eq_g, "per_share": ps_g,
                   "tv_share_of_ev": pv_tv_gordon / ev_g if ev_g else float("nan")},
        "exit": {"tv": tv_exit, "pv_tv": pv_tv_exit, "ev": ev_x, "equity": eq_x, "per_share": ps_x,
                 "tv_share_of_ev": pv_tv_exit / ev_x if ev_x else float("nan")},
        "implied_exit_multiple": implied_exit_multiple,
        "implied_g_from_exit": implied_g_from_exit,
        "implied_g_note": implied_g_note,
        "per_share": ps_g if primary == "gordon" else ps_x,
        "primary_tv": primary,
    }


def _implied_g(tv, nopat_n, wacc, roic):
    """Find the growth rate g where the Gordon formula gives the same terminal value as the
    exit multiple:  TV = NOPAT x (1+g) x (1 - g/RONIC) / (WACC - g).
    Returns (g, note). g is nan when no growth rate fits; note then says why.

    TV does not always rise with g. Growth needs reinvestment of g/RONIC, so when RONIC is
    low compared with WACC, more growth adds little value or destroys it. The search
    therefore runs from g = 0 up to the g that gives the highest TV, not all the way to WACC."""
    nan = float("nan")
    if tv <= 0 or nopat_n <= 0 or roic <= 0:
        return nan, "not meaningful (terminal value, NOPAT or RONIC is zero or negative)"

    def tv_for(g):
        return nopat_n * (1 + g) * (1 - g / roic) / (wacc - g)

    # Step through g = 0.00%, 0.01%, ... just below WACC and keep the g with the highest TV
    steps = int((wacc - 1e-4) / 1e-4)
    g_peak = max((i * 1e-4 for i in range(steps + 1)), key=tv_for)

    if tv < tv_for(0.0):
        return nan, "below 0%: the exit multiple values the business at less than zero growth would"
    if tv > tv_for(g_peak):
        return nan, (f"none: at RONIC {roic:.1%} vs WACC {wacc:.1%} no growth rate gives a terminal "
                     f"value this high, so the exit multiple and RONIC contradict each other")

    a, b = 0.0, g_peak          # TV rises with g on this stretch, so bisection works
    for _ in range(100):
        m = (a + b) / 2
        if tv_for(m) < tv:
            a = m
        else:
            b = m
    return (a + b) / 2, ""


def solve(fn, target, lo, hi, iters=100):
    """Generic bisection: find x in [lo, hi] where fn(x) = target (fn increasing in x)."""
    f_lo, f_hi = fn(lo), fn(hi)
    if not (min(f_lo, f_hi) <= target <= max(f_lo, f_hi)) or any(map(math.isnan, (f_lo, f_hi))):
        return float("nan")
    increasing = f_hi > f_lo
    a, b = lo, hi
    for _ in range(iters):
        m = (a + b) / 2
        if (fn(m) < target) == increasing:
            a = m
        else:
            b = m
    return (a + b) / 2
