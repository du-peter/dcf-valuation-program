"""
forecast.py
Operating model: revenue -> EBIT margin -> NOPAT -> reinvestment -> FCFF, one scenario at a time.
Replaces mk8.3's growth modes (see dcf/DECISIONS.md, 2026-11-11).

FCFF = NOPAT + D&A - capex - JV capex - change in net working capital
NOPAT = EBIT x (1 - tax). EBIT is GAAP, so stock-based compensation is already a cost here.
"""


def _expand(values, n):
    """List of n values; if the list is shorter, its last value repeats."""
    if not values:
        raise ValueError("Empty list in scenario settings.")
    return [values[min(i, len(values) - 1)] for i in range(n)]


def revenue_path(base_revenue, sc, n):
    """Explicit revenue (in $bn) for the first years, then growth rates for the rest."""
    explicit = [x * 1e9 for x in sc.get("revenue_bn", [])][:n]
    path = list(explicit)
    growth = sc.get("growth_after", [0.03])
    rev = path[-1] if path else base_revenue
    k = 0
    while len(path) < n:
        g = growth[min(k, len(growth) - 1)]
        rev = rev * (1 + g)
        path.append(rev)
        k += 1
    return path


def margin_path(sc, n):
    """EBIT margin: explicit early-year margins, then a straight-line fade to the terminal margin."""
    early = list(sc.get("ebit_margin_early", []))[:n]
    target = sc["ebit_margin_terminal"]
    reach_year = max(sc.get("terminal_margin_year", n), len(early) + 1)
    path = list(early)
    start = early[-1] if early else target
    start_year = len(early)
    for yr in range(len(early) + 1, n + 1):
        if yr >= reach_year:
            path.append(target)
        else:
            frac = (yr - start_year) / (reach_year - start_year)
            path.append(start + (target - start) * frac)
    return path


def build_forecast(base, sc, n, tax_rate, invested_capital_0):
    rev = revenue_path(base["revenue"], sc, n)
    margins = margin_path(sc, n)
    capex_pct = _expand(sc["capex_pct_revenue"] if isinstance(sc["capex_pct_revenue"], list)
                        else [sc["capex_pct_revenue"]], n)
    jv_pct = _expand([sc.get("jv_capex_pct_revenue", 0.0)], n)
    da_pct = _expand([sc["da_pct_revenue"]], n)
    nwc_pct = sc["nwc_pct_revenue"]

    out = {k: [] for k in ("revenue", "ebit_margin", "ebit", "nopat", "da", "capex", "jv_capex",
                           "delta_nwc", "net_reinvestment", "fcff", "ebitda",
                           "roic", "reinvestment_rate", "implied_growth", "invested_capital")}
    prev_rev = base["revenue"]
    ic = invested_capital_0
    for t in range(n):
        r = rev[t]
        ebit = r * margins[t]
        nopat = ebit * (1 - tax_rate)
        da = r * da_pct[t]
        capex = r * capex_pct[t]
        jv = r * jv_pct[t]
        dnwc = nwc_pct * (r - prev_rev)
        net_reinv = capex + jv - da + dnwc
        fcff = nopat - net_reinv

        roic = nopat / ic if ic > 0 else float("nan")
        rr = net_reinv / nopat if nopat > 0 else float("nan")

        for k, v in (("revenue", r), ("ebit_margin", margins[t]), ("ebit", ebit), ("nopat", nopat),
                     ("da", da), ("capex", capex), ("jv_capex", jv), ("delta_nwc", dnwc),
                     ("net_reinvestment", net_reinv), ("fcff", fcff), ("ebitda", ebit + da),
                     ("roic", roic), ("reinvestment_rate", rr), ("implied_growth", roic * rr),
                     ("invested_capital", ic)):
            out[k].append(v)
        ic = ic + net_reinv
        prev_rev = r
    return out
