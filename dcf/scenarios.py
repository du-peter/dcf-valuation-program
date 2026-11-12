"""
scenarios.py
Runs everything that reuses model.value_from_forecast():
- bull / base / bear values and the probability-weighted target
- WACC x terminal-growth sensitivity table (on the base scenario)
- reverse DCF: the terminal EBIT margin the market price implies (base scenario)
"""

import copy

from forecast import build_forecast
from model import value_from_forecast, solve


def run_scenario(name, sc, base, cfg, wacc, bridge, ic0):
    n = cfg["horizon_years"]
    fc = build_forecast(base, sc, n, cfg["capital"]["tax_rate"], ic0)
    val = value_from_forecast(fc, wacc, sc["terminal_growth"], sc["terminal_roic"],
                              sc["exit_multiple"], bridge, cfg.get("midyear", True),
                              cfg.get("primary_tv", "gordon"))
    return {"name": name, "settings": sc, "forecast": fc, "value": val}


def run_all(base, cfg, wacc, bridge, ic0):
    results = []
    for name, sc in cfg["scenarios"].items():
        results.append(run_scenario(name, sc, base, cfg, wacc, bridge, ic0))
    probs = [r["settings"].get("probability", 0) for r in results]
    total_p = sum(probs)
    if abs(total_p - 1.0) > 1e-6:
        print(f"⚠️ Scenario probabilities add to {total_p:.0%}, not 100%. Weighting them proportionally.")
    weighted = sum(p * r["value"]["per_share"] for p, r in zip(probs, results)) / total_p if total_p else float("nan")
    return results, weighted


def sensitivity(base, cfg, wacc, bridge, ic0, scenario="base", w_step=0.005, g_step=0.005, steps=2):
    sc0 = cfg["scenarios"][scenario]
    n = cfg["horizon_years"]
    fc = build_forecast(base, sc0, n, cfg["capital"]["tax_rate"], ic0)
    waccs = [wacc + (i - steps) * w_step for i in range(2 * steps + 1)]
    gs = [sc0["terminal_growth"] + (j - steps) * g_step for j in range(2 * steps + 1)]
    grid = []
    for w in waccs:
        row = []
        for g in gs:
            v = value_from_forecast(fc, w, g, sc0["terminal_roic"], sc0["exit_multiple"], bridge,
                                    cfg.get("midyear", True), "gordon")
            row.append(v["gordon"]["per_share"])
        grid.append(row)
    return waccs, gs, grid


def implied_terminal_margin(base, cfg, wacc, bridge, ic0, price, scenario="base"):
    """Reverse DCF: which terminal EBIT margin makes the base-case value equal today's price?"""
    sc0 = cfg["scenarios"][scenario]
    n = cfg["horizon_years"]

    def price_for(margin):
        sc = copy.deepcopy(sc0)
        sc["ebit_margin_terminal"] = margin
        fc = build_forecast(base, sc, n, cfg["capital"]["tax_rate"], ic0)
        return value_from_forecast(fc, wacc, sc["terminal_growth"], sc["terminal_roic"],
                                   sc["exit_multiple"], bridge, cfg.get("midyear", True),
                                   "gordon")["gordon"]["per_share"]

    return solve(price_for, price, -0.20, 0.95)
