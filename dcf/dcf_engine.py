"""
DCF mk9: scenario-based operating-model DCF.

Run:   uv run dcf/dcf_engine.py <ticker>    (any US ticker, e.g. aapl, nvda, sndk)
Reads: dcf/companies/<ticker>.toml (all assumptions live there, no prompts).
       If the file does not exist it is built automatically from the company's history.
       Add the word rebuild to rebuild an automatic file:  uv run dcf/dcf_engine.py mu rebuild
Data:  OpenBB v5 via data_openbb.py (SEC statements, Nasdaq/Cboe price)

Flow:  settings -> data -> base year -> WACC -> bull/base/bear forecasts -> values
       -> probability-weighted target, sensitivity, reverse DCF -> printout -> optional export
History and design decisions: dcf/DECISIONS.md and `git log`.
"""

import sys
import tomllib
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from model import cost_of_capital            # noqa: E402
from scenarios import run_all, sensitivity, implied_terminal_margin  # noqa: E402
from report import print_report, maybe_export  # noqa: E402


def load_config(name):
    """Read companies/<ticker>.toml. If there is none yet, build one from the company's history."""
    path = HERE / "companies" / f"{name.lower()}.toml"
    rebuild = "rebuild" in sys.argv[2:]
    if rebuild and path.exists():
        with open(path, "rb") as f:
            if not tomllib.load(f).get("auto_generated"):
                sys.exit(f"{path.name} has been reviewed by hand (auto_generated is not true), "
                         f"so it was not rebuilt. Delete the file yourself if you really want a fresh one.")
    if rebuild and path.exists():
        ans = input(f"'rebuild' replaces {path.name} and any edits you made in it. Continue? (y/n) [n]: ")
        if ans.strip().lower() not in ("y", "yes"):
            print(f"Not rebuilt. Using your current {path.name}.")
            rebuild = False
    if rebuild or not path.exists():
        from data_openbb import get_statements, calc_beta
        from auto_settings import write_settings
        print(f"Building a settings file for {name.upper()} from SEC filings...")
        try:
            raw_beta = calc_beta(name.upper())
        except Exception:
            raw_beta = None
        write_settings(name, path, get_statements(name.upper()), raw_beta)
        print(f"Created {path}")
    print(f"Settings: {path.relative_to(ROOT)}  (last saved {datetime.fromtimestamp(path.stat().st_mtime):%Y-%m-%d %H:%M})")
    with open(path, "rb") as f:
        return tomllib.load(f)


def get_base(cfg):
    """Live data from OpenBB, with any [overrides] in the settings file taking priority."""
    from data_openbb import get_base_year, calc_beta
    base = get_base_year(cfg["ticker"])
    for k, v in cfg.get("overrides", {}).items():
        if v not in (0, "", None):
            base[k] = v
            base.setdefault("_overridden", []).append(k)
    if cfg["capital"]["beta"] == "calc":
        b = calc_beta(cfg["ticker"])
        if b is None:
            sys.exit("Could not calculate beta (too little price history). Set a number in the settings file.")
        cfg["capital"]["beta"] = b
    return base


def main():
    if len(sys.argv) < 2:
        sys.exit("Error: you must give a ticker, for example: uv run dcf/dcf_engine.py aapl\n"
                 "Add the word rebuild to rebuild an automatic settings file: uv run dcf/dcf_engine.py aapl rebuild")
    name = sys.argv[1]
    cfg = load_config(name)
    base = get_base(cfg)

    mult = cfg.get("shares", {}).get("sec_share_multiplier", 1)
    bridge = {
        "debt": base["debt"],
        "cash": base["cash"],
        "short_term_investments": base["short_term_investments"],
        "basic_shares": base["basic_shares"] * mult,
        "diluted_shares": base["diluted_shares"] * mult,
        "fx_to_price_currency": cfg.get("fx_to_price_currency", 1.0),
    }
    price = base["price"]
    market_cap = price * bridge["basic_shares"] / bridge["fx_to_price_currency"]
    ic0 = base["total_equity"] + base["debt"] - base["cash"] - base["short_term_investments"]

    coc = cost_of_capital(cfg["capital"], market_cap, bridge["debt"])
    wacc = coc["wacc"]

    results, weighted = run_all(base, cfg, wacc, bridge, ic0)
    ctx = {
        "cfg": cfg, "base": base, "bridge": bridge, "coc": coc, "price": price,
        "price_source": base.get("price_source", "override" if "price" in base.get("_overridden", []) else "live"),
        "market_cap": market_cap, "results": results, "weighted": weighted,
        "sensitivity": sensitivity(base, cfg, wacc, bridge, ic0),
        "implied_margin": implied_terminal_margin(base, cfg, wacc, bridge, ic0, price),
    }
    print_report(ctx)
    if cfg.get("auto_generated"):
        print(f"\n⚠️ {cfg['ticker']} uses AUTO-GENERATED assumptions built from its own history. "
              f"Treat the values as a first look, not a valuation. "
              f"Review dcf/companies/{cfg['ticker'].lower()}.toml.")
    maybe_export(ctx, ROOT)


if __name__ == "__main__":
    main()
