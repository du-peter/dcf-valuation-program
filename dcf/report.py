"""
report.py
Prints the results, then asks once whether to export them (Excel workbook and a football field
chart PNG; nothing is saved otherwise).
"""

import math
from datetime import date
from pathlib import Path

import pandas as pd


def _m(x):
    return f"{x/1e9:,.1f}" if isinstance(x, (int, float)) and math.isfinite(x) else "n/a"


def _p(x):
    return f"{x:.1%}" if isinstance(x, (int, float)) and math.isfinite(x) else "n/a"


def _d(x):
    return f"${x:,.2f}" if isinstance(x, (int, float)) and math.isfinite(x) else "n/a"


def forecast_table(fc):
    rows = {
        "Revenue ($bn)": [_m(v) for v in fc["revenue"]],
        "EBIT margin": [_p(v) for v in fc["ebit_margin"]],
        "NOPAT ($bn)": [_m(v) for v in fc["nopat"]],
        "Net reinvestment ($bn)": [_m(v) for v in fc["net_reinvestment"]],
        "FCFF ($bn)": [_m(v) for v in fc["fcff"]],
        "ROIC": [_p(v) for v in fc["roic"]],
        "Reinvestment rate": [_p(v) for v in fc["reinvestment_rate"]],
        "ROIC x RR (implied g)": [_p(v) for v in fc["implied_growth"]],
    }
    cols = [f"Y{i+1}" for i in range(len(fc["revenue"]))]
    return pd.DataFrame(rows, index=cols).T


def print_report(ctx):
    cfg, coc, bridge = ctx["cfg"], ctx["coc"], ctx["bridge"]
    price = ctx["price"]
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 30)

    print(f"\n================ {cfg['ticker']} | DCF mk9 | {date.today()} ================")
    print(f"Price: {_d(price)}  ({ctx['price_source']})")
    print(f"Basic shares: {bridge['basic_shares']/1e6:,.1f}M (market cap, WACC)  |  "
          f"Diluted shares: {bridge['diluted_shares']/1e6:,.1f}M (value per share)")
    print(f"Market cap: ${ctx['market_cap']/1e9:,.1f}bn  |  Debt incl. leases: ${bridge['debt']/1e9:,.2f}bn  |  "
          f"Cash + STI: ${(bridge['cash'] + bridge['short_term_investments'])/1e9:,.2f}bn")

    print("\n--- Cost of capital ---")
    print(f"Cost of equity {_p(coc['cost_of_equity'])} = rf {_p(cfg['capital']['risk_free'])} + "
          f"beta {cfg['capital']['beta']:.2f} x ERP {_p(cfg['capital']['equity_risk_premium'])}")
    print(f"After-tax cost of debt {_p(coc['cost_of_debt_after_tax'])}  |  weights E/D "
          f"{_p(coc['weight_equity'])}/{_p(coc['weight_debt'])}  |  WACC {_p(coc['wacc'])}")

    print("\n--- Scenarios ---")
    for r in ctx["results"]:
        v = r["value"]
        print(f"\n[{r['name'].upper()}]  probability {_p(r['settings'].get('probability', 0))}")
        print(forecast_table(r["forecast"]).to_string())
        print(f"Value/share  Gordon: {_d(v['gordon']['per_share'])}  (TV = {_p(v['gordon']['tv_share_of_ev'])} of EV)"
              f"   |   Exit multiple {r['settings']['exit_multiple']:.1f}x: {_d(v['exit']['per_share'])}")
        print(f"Cross-checks: Gordon TV implies {v['implied_exit_multiple']:.1f}x EBITDA  |  "
              f"exit multiple implies terminal g of {v['implied_g_note'] or _p(v['implied_g_from_exit'])}")
        if v["gordon"]["tv_share_of_ev"] > 0.85:
            print("⚠️ Terminal value is over 85% of EV: the answer depends mostly on the terminal assumptions.")

    print(f"\n>>> Probability-weighted value per share: {_d(ctx['weighted'])}  vs price {_d(price)}  "
          f"({(ctx['weighted']/price - 1):+.1%})")

    print("\n--- Reverse DCF (base scenario) ---")
    print(f"Terminal EBIT margin the market price implies: {_p(ctx['implied_margin'])}  "
          f"(base case assumes {_p(cfg['scenarios']['base']['ebit_margin_terminal'])})")

    waccs, gs, grid = ctx["sensitivity"]
    print("\n--- Sensitivity: value/share, base scenario (rows WACC, columns terminal g) ---")
    print(f"{'WACC/g':<9}" + "".join(f"{g:>11.2%}" for g in gs))
    for w, row in zip(waccs, grid):
        print(f"{w:<9.2%}" + "".join(f"{_d(x):>11}" for x in row))


def maybe_export(ctx, root):
    ans = input("\nExport results to Excel? (y/n) [n]: ").strip().lower()
    if ans not in ("y", "yes"):
        print("Not exported.")
        return
    out_dir = Path(root) / "outputs"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{ctx['cfg']['ticker']}_dcf_mk9.xlsx"   # no date in the name: each export replaces the last
    with pd.ExcelWriter(path) as xw:
        summary = pd.DataFrame(
            [(r["name"], r["settings"].get("probability", 0), r["value"]["gordon"]["per_share"],
              r["value"]["exit"]["per_share"]) for r in ctx["results"]],
            columns=["Scenario", "Probability", "Value/share (Gordon)", "Value/share (exit multiple)"])
        summary.loc[len(summary)] = ["Probability-weighted", 1.0, ctx["weighted"], None]
        summary.to_excel(xw, sheet_name="Summary", index=False)
        for r in ctx["results"]:
            pd.DataFrame(r["forecast"]).to_excel(xw, sheet_name=r["name"][:31], index_label="Year index")
        waccs, gs, grid = ctx["sensitivity"]
        pd.DataFrame(grid, index=[f"{w:.2%}" for w in waccs], columns=[f"{g:.2%}" for g in gs]).to_excel(
            xw, sheet_name="Sensitivity")
    print(f"Exported: {path}")

    # Football field chart (PNG). Adds comps bars if outputs/<TICKER>_comps.xlsx exists.
    from football_field import make_chart
    ticker = ctx["cfg"]["ticker"]
    chart = make_chart(ctx, out_dir / f"{ticker}_football_field.png", out_dir / f"{ticker}_comps.xlsx")
    if chart:
        print(f"Exported: {chart}")
        if not (out_dir / f"{ticker}_comps.xlsx").exists():
            print(f"Tip: run uv run comps/comps.py {ticker} <peers> y first to add comps bars to the chart.")
