# DCF Valuation Program

A Python toolkit that values a public company two ways: a scenario-based discounted cash flow
(DCF) model and trading comparables (comps). Type a ticker and the program pulls the company's
filed financial statements and live share price, builds a first set of assumptions from the
company's own history, and prints a valuation with the checks an analyst would run by hand.

Built by Peter Du. Started in 2025 as a single script and rebuilt over nine versions; every
version is kept in `dcf/archive/` and in the git history, and the reasoning behind each design
choice is logged in `dcf/DECISIONS.md`.

Copyright (c) 2026 Peter Du. All rights reserved. This code is shared for viewing only. No
license is granted to copy, modify or redistribute it.

---

## What it does

**DCF (`dcf/`)**
- Builds an operating model for each year: revenue, EBIT margin, taxes, reinvestment
  (capex, D&A, working capital) and free cash flow to the firm.
- Runs three scenarios (bear, base, bull) and weights them by probability to get one value
  per share.
- Values the terminal year two ways (Gordon growth and an exit EV/EBITDA multiple) and shows
  what each one implies about the other, so contradictory assumptions are visible.
- Ties terminal growth to reinvestment: growing at g forever requires reinvesting g / RONIC of
  profit, so growth only adds value when the return on new investment is above WACC.
- Reverse DCF: solves for the long-run EBIT margin that today's share price implies.
- Sensitivity table of value per share across WACC and terminal growth.
- Optional export of the full output to Excel, plus a football field chart (PNG) that combines the DCF ranges with any saved comps run for the same ticker.

**Comps (`comps/`)**
- Pulls P/E (trailing and forward), EV/EBITDA, EV/Sales and P/B for a target and any peers.
- Prints the peer median for each multiple and the share price the target would have at
  that median.
- Optionally prints a second median for a core peer group (for example, direct competitors
  only, with adjacent businesses shown for reference).

**Peer beta (`dcf/peer_beta.py`)**
- For companies with a short or unrepresentative trading history: measures each peer's beta
  against its home index, removes the effect of each peer's debt, takes the median, and
  relevers it for the target.

---

## Install

Requires [uv](https://docs.astral.sh/uv/) (it installs the right Python version for you).

```
git clone https://github.com/du-peter/dcf-valuation-program.git
cd dcf-valuation-program
uv sync
```

---

## How to run it

Run every command from the project folder.

| Command | What it does |
|---|---|
| `uv run dcf/dcf_engine.py aapl` | DCF on any US ticker. The first run builds `dcf/companies/aapl.toml` from the company's history, then values it. Later runs reuse that file. |
| `uv run dcf/dcf_engine.py aapl rebuild` | Rebuilds an automatic settings file from fresh data. Asks first, and refuses to overwrite a file marked as reviewed. |
| `uv run comps/comps.py AAPL MSFT GOOGL META` | Comps: target first, then its peers. |
| `uv run comps/comps.py SNDK MU WDC STX --core MU` | Comps with a second median for the core peers. |
| `uv run comps/comps.py ... y` | Any comps run, exported to `outputs/<TICKER>_comps.xlsx`. |
| `uv run dcf/peer_beta.py SNDK MU 000660.KS 285A.T` | Peer beta: target first, then peers. |

Each DCF run pulls the latest price and filed statements. The settings file holds only the
assumptions, so you do not need to rebuild it to get current data.

---

## Settings files and line tags

All DCF assumptions live in one file per company, `dcf/companies/<ticker>.toml`. There are no
prompts, so every run can be repeated exactly. To change an assumption, edit the number, save,
and run the same command again (without `rebuild`).

Every line carries a tag that says what kind of number it is:

| Tag | Meaning |
|---|---|
| DATA | Taken from filings, company guidance or price history. Check it; rarely change it. |
| MARKET | The same for every company (risk-free rate, equity risk premium). Update a few times a year. |
| ASSUMPTION | A judgment. Review these before trusting the output. The long-run EBIT margin (`ebit_margin_terminal`) usually moves the answer most. |
| SETTING | How the model runs. Usually left as is. |

An automatically built file is marked `auto_generated = true`, and the output carries a warning
until you review it and set that line to `false`.

---

## Reading the output

| Section | What to look at |
|---|---|
| Cost of capital | Cost of equity from CAPM, after-tax cost of debt, and the WACC that discounts every scenario. |
| Scenarios | Value per share under each terminal method, the share of enterprise value that comes from the terminal value, and the cross-checks: the EV/EBITDA multiple the Gordon value implies and the growth rate the exit multiple implies. If a cross-check looks unreasonable, an assumption in that scenario is inconsistent. |
| ROIC check | Return on invested capital times the reinvestment rate, year by year, as a sanity check on implied growth. |
| Probability-weighted value | The scenarios combined, compared with the current price. |
| Reverse DCF | The long-run margin the market price implies, compared with your base case. |
| Sensitivity | Value per share as WACC and terminal growth move. |

---

## Data sources

Each calculation uses one data source throughout, so any error in a source is at least
consistent across the companies being compared.

| Tool | Source | Why |
|---|---|---|
| DCF | [OpenBB](https://openbb.co/) v5: SEC filings for statements, Nasdaq or Cboe for price | Statements straight from company filings. |
| Comps, peer beta | yfinance | Provides forward P/E and non-US listings, which OpenBB's free sources do not. |

---

## Limitations

| Case | Why |
|---|---|
| Non-US companies (DCF) | Statements come from SEC filings only. Comps and peer beta do cover non-US tickers. |
| Banks and insurers | A free cash flow DCF does not fit businesses where debt is an operating input. They need a dividend discount, excess return or P/B vs ROE approach. |
| Recent IPOs and loss-making companies | The automatic settings file has little history to work from; set assumptions by hand. |
| Automatic settings files in general | They extend the company's past (growth capped at 25%, margin fading to its historical average). For a business that has changed recently, the result will be off until the assumptions are reviewed. |
| Off-balance-sheet assets | The ROIC check can overstate returns when productive assets sit outside the balance sheet (for example, in a joint venture). |

---

## Worked examples

These settings files are examples of the method. They are not investment recommendations.

| File | Status |
|---|---|
| `dcf/companies/sndk.toml` | Sandisk. Hand-reviewed: memory-cycle scenarios, peer beta, sourced anchors. Reasoning in `dcf/DECISIONS.md`. |
| `dcf/companies/nvda.toml` | Nvidia. Hand-reviewed scenarios anchored to company guidance and consensus, sources listed in the file. |
| `dcf/companies/mu.toml` | Micron. Automatically built, not reviewed: shows what a first-run file looks like. |

![SNDK football field, Nov 11 2026](docs/images/sndk_football_field.png)

Sandisk, Nov 11, 2026 (WACC 13.8%): bear $322, base $836, bull $1,904, probability-weighted $975,
against a price of $953 at the time of the chart. On an earlier run that day at $982, the reverse
DCF showed the price implied a long-run EBIT margin of about 50%, against 35% in the base case.

---

## Folder layout

```
dcf-valuation-program/
├── dcf/
│   ├── dcf_engine.py      the program you run
│   ├── data_openbb.py     data: statements, price, beta
│   ├── auto_settings.py   builds a first settings file from a company's history
│   ├── forecast.py        operating model, year by year
│   ├── model.py           WACC, terminal values, enterprise value to value per share
│   ├── scenarios.py       bear/base/bull, weighting, sensitivity, reverse DCF
│   ├── report.py          printout and Excel export
│   ├── football_field.py  football field chart (PNG) on export
│   ├── peer_beta.py       peer beta helper
│   ├── DECISIONS.md       design decisions and the reasoning behind them
│   ├── companies/         one settings file per company
│   └── archive/           every earlier version, pp to mk8.3
├── comps/
│   └── comps.py           trading comparables
├── docs/images/          example chart shown in this README
├── outputs/               Excel exports (created when you export; not tracked)
└── pyproject.toml, uv.lock, .python-version
```
