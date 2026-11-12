# DCF engine: design decisions and learning points

Newest first. Each entry: what changed, why, and what it teaches.

---

## 2026-11-11: comps and peer beta take any tickers; reviewed SNDK file restored

**What changed:**
- `comps/comps.py` and `dcf/peer_beta.py` now take tickers on the command line (target first,
  then peers) instead of a hard-wired SNDK peer list. Running either without tickers prints an
  error and the usage line. `--core` in comps adds a second median for a core peer group.
- `peer_beta.py` picks each peer's home index from its exchange suffix (.KS -> KOSPI, .T -> Nikkei,
  .TO -> TSX, and so on; no suffix -> SPY).
- `dcf_engine.py` no longer falls back to SNDK when no ticker is given.
- `companies/sndk.toml`: an automatic rebuild had replaced the reviewed file. The reviewed version
  was restored from git history and tagged line by line, with `auto_generated = false` so
  `rebuild` cannot overwrite it again. Offline check reproduces the reviewed result
  (probability-weighted about $974, WACC 13.8%).

**Data source rule:** one source per calculation. The DCF uses OpenBB (SEC filings, Nasdaq/Cboe
price). Comps and peer beta use yfinance for every company in the table, because OpenBB's free
sources have no forward estimates and no non-US listings. Sources are never mixed inside one
calculation, so errors are consistent across the companies being compared.

**Learning point:** a settings file that has been reviewed must be marked as reviewed, or an
automated step can silently replace the work. Git history is what made the recovery possible.

---

## 2026-11-11: type any ticker again (auto-built settings file)

**Problem:** mk9 moved every assumption into `companies/<ticker>.toml`, so a new ticker needed a
hand-made file first. mk8 could run on a ticker alone.

**Decision:** keep the settings file, but build it automatically. `uv run dcf/dcf_engine.py mu`
now creates `companies/mu.toml` from the company's SEC history if it does not exist, then runs.
Code: `auto_settings.py`.

**How the automatic numbers are built:** 3-year revenue growth (limited to -5% to 25%) fading to
terminal growth by year 6; EBIT margin starts at the last 12 months and fades to the average
annual margin on file by year 5; capex and D&A at their last-12-months share of revenue; bear and
bull are the base case with growth 3 points lower or higher and terminal margin 25% lower or
higher; beta calculated from 2 years of weekly returns.

**Learning point:** the automatic file extends the past. It is a first look, the same thing mk8's
Auto mode gave. It cannot know about new supply, contracts or a cycle turning, which is why the
SNDK file is hand-reviewed. The printout carries a warning until `auto_generated` is set to false.

---

## 2026-11-11: SNDK scenarios reviewed (bear, base, bull)

**What changed:** all three scenarios in `companies/sndk.toml` were rebuilt from sourced anchors.
The earlier numbers were placeholders.

**Anchors (sources: Sandisk Q4 FY26 and Q1 FY27 results releases and slides, Western Digital
FY23 results, TrendForce Jul 30 2026, GuruFocus):**
- FY25 revenue $7.36bn, FY26 $20.25bn, Q1 FY27 $11.03bn, Q2 FY27 guide $12.2-12.6bn.
- Q1 FY27 GAAP operating margin 79.6%. Cost base (cost of sales + operating expenses) is about
  $2.25bn a quarter, so about $9bn a year (arithmetic: 20.4% x $11.03bn x 4).
- Last downturn: Western Digital flash revenue fell from $9.75bn (FY22) to $6.06bn (FY23), -38%.
  Flash gross margin went from 35.9% to -11.9% in four quarters. Industry NAND revenue -37% in 2023.
- TrendForce: NAND supply and demand balance in 2H 2027 as new capacity comes online.
- Micron EV/EBITDA, 10 years: minimum 1.7x, median 7.0x.

| | Bear (25%) | Base (50%) | Bull (25%) |
|---|---|---|---|
| Story | Prices return to about pre-boom levels by year 3 | Prices fall about half by year 3, then settle above pre-boom | AI storage demand absorbs new supply |
| Revenue $bn, years 1-3 | 42, 22, 13 | 50, 42, 32 | 54, 60, 62 |
| EBIT margin, years 1-3 | 77%, 52%, 13% | 79%, 74%, 62% | 80%, 79%, 75% |
| Terminal EBIT margin | 20% | 35% | 50% |
| Terminal g / RONIC | 2.5% / 14% | 3.0% / 18% | 3.5% / 25% |
| Exit EV/EBITDA | 6.0x | 7.0x | 9.0x |

**Learning points:**
1. *Revenue and margin are not independent.* In this boom almost all the revenue growth is price,
   and costs barely moved. So margin = 1 - cost / revenue. The old bear case had revenue of $24bn
   with a 15% margin, which would need costs of $20bn against an actual cost base near $9bn.
   Each scenario now picks a revenue path first and the early margins follow from the cost base.
2. *Volume grows in a NAND downturn; price does the falling.* In 2023 Sandisk's shipments were
   flat while price and revenue fell about 38%. So the bear trough ($13bn) is still above FY25
   revenue ($7.4bn): the assumption is price back near FY25 levels on about 30% more volume
   and a richer mix (estimate).
3. *Capex was double counted.* Sandisk's "gross capex" (3.4% of revenue in Q1 FY27, guided to
   about 6% for FY27) already includes its share of JV capex, and part of that is paid through
   wafer prices, which sit in cost of sales. The cash that leaves on top of EBIT is small: PP&E
   0.5% of revenue and JV funding 1.3% in Q1 FY27. D&A is only 0.4% of revenue because the fabs
   are in the JV. The old settings (capex 5% + JV 3-4%, D&A 3-4%) are replaced.
4. *Terminal RONIC vs WACC.* Bear sets RONIC about equal to WACC (13.8%), the standard assumption
   for a business with no lasting advantage: growth then adds no value.

**Offline check (Claude, 2026-11-11, hand-entered base data: TTM revenue $29.0bn, cash $5.1bn,
debt $0.2bn, 154M diluted shares, price $988.16; WACC 13.8%):** bear about $322, base about $836,
bull about $1,904, probability-weighted about $974 per share. Reverse DCF: the price implies a
terminal EBIT margin of about 51% in the base case. Estimates only; the live run replaces them.

**Still assumptions (not sourced):** terminal margins, the probabilities, the cost growth of about
8% a year behind the early margins, JV capex percentages in years 2+, NWC at 10% of the change
in revenue.

---

## 2026-11-11: cost of capital reviewed for SNDK

| Input | Old | New | Source |
|---|---|---|---|
| Risk-free rate | 4.2% | 4.98% | US 10-year Treasury par yield, Nov 10 2026 |
| Equity risk premium | 5.0% | 4.41% | Damodaran implied ERP, Nov 1 2026 |
| Beta | 1.6 | 2.0 | `peer_beta.py`: median unlevered peer beta 2.51, relevered 2.52, Blume-adjusted 2.01 |
| Tax rate | 15% | 15% | Q1 FY27 GAAP rate 13.1% ($1,218M / $9,319M), Q2 non-GAAP guide 14.3%; rounded up (assumption) |

Cost of equity = 4.98% + 2.0 x 4.41% = 13.8% (was 12.2%).

**Learning points:**
1. *Why a peer beta.* SNDK has traded for under 2 years (91 weeks), so its own beta (2.86) rests on
   little data. Peers in the same business give more data for the same business risk.
2. *Unlever, then relever.* A company's beta mixes business risk and debt risk. Dividing by
   (1 + (1 - tax) x D/E) removes the debt part. Here every peer has almost no debt (D/E 0.01 to
   0.05), so unlevering barely changes anything.
3. *Blume adjustment.* Measured betas drift toward 1.0 over time, so the standard adjustment is
   2/3 x measured beta + 1/3 x 1.0. It matters most when the measured beta is extreme, as here.
4. *Limits.* The 2-year window (Nov 2024 to Nov 2026) is the AI memory boom, when these stocks
   moved far more than the market, so 2.5 is likely above a long-run level (inference). The Korean
   betas (1.25, 1.75) are low partly because Samsung and SK Hynix are a large share of the KOSPI,
   the index they are measured against (inference).
5. *Open:* Damodaran's page states a 5.29% Treasury rate behind his 4.41%; the 10-year was near
   4.9% at the end of October. Not reconciled. Pre-tax cost of debt (5%) not reviewed; it only
   affects the lease liabilities.

---

## 2026-11-11: implied growth from the exit multiple now explains itself

**Bug:** `_implied_g` in `model.py` printed "n/a" in the bear case with no reason.

**Cause:** it assumed terminal value always rises as g rises. That is only true when RONIC is
comfortably above WACC. Growth needs reinvestment of g / RONIC. In the bear case RONIC (12%) was
below WACC (12.2%), so terminal value peaks at about g = 7.5% and then falls. The 6.0x exit
multiple asked for more value than any growth rate could give, so there was no answer to find.

**Fix:** search from g = 0 up to the g with the highest terminal value, and print the reason
when nothing fits (instead of "n/a").

**Learning point:** growth only adds value when RONIC is above WACC. If the cross-check prints
"none", the exit multiple and the terminal RONIC in that scenario contradict each other, and one
of them has to change. That is an assumption to fix in `companies/<ticker>.toml`, not in the code.

---

## 2026-11-11: mk9 replaces the four growth modes with an operating model

**Decision:** Option 1. Auto, Manual, ROIC and Import growth modes (mk8.3) are replaced by one
operating model run as bull / base / bear scenarios. ROIC x reinvestment stays as an automatic
check printed for every forecast year.

**Why:**
- All mk8.3 modes except Import took one year of free cash flow and grew it. For a cyclical company
  (Sandisk, memory chips) the base year can be a peak or a trough, so the whole valuation inherits
  that distortion.
- Auto mode applied *revenue* growth (3-year CAGR) to *cash flow*. Cash flow and revenue do not
  grow at the same rate when margins change, which is exactly what happens over a cycle.
- Building revenue -> margin -> reinvestment -> FCFF forces each assumption to be stated
  separately, so you can see which one drives the answer.

**Learning points:**
1. *Value the normal year, not the current year.* For cyclicals, the terminal value should rest on a
   through-cycle (mid-cycle) margin. In mk9 that is `ebit_margin_terminal`.
2. *Growth is not free.* Terminal cash flow = NOPAT x (1 - g / RONIC). To grow at g forever,
   a company must reinvest g / RONIC of its profit. mk8.3 grew the last cash flow by g with no extra
   reinvestment, which overstates terminal value.
3. *Stock-based compensation:* mk8.3 started from cash flow from operations (CFO), which adds SBC
   back because it is non-cash. mk9 starts from GAAP EBIT, where SBC is already an expense, so it is
   treated as a real cost with no extra setting.
4. *Two terminal methods, each checking the other:* Gordon growth shows the EV/EBITDA multiple it
   implies; the exit multiple shows the growth rate it implies. If the implied number looks
   unreasonable, the assumption behind it is wrong.
5. *Reverse DCF on the right variable:* mk8.3 solved for one constant growth rate. For a cyclical,
   the better question is "what normal margin is the market pricing in?", so mk9 solves for the
   terminal EBIT margin.

**Other fixes in mk9:**
- Currency: value per share is converted to the trading currency before comparing with the price
  (mk8.3 compared a USD value with a local-currency price, wrong for MDA.TO).
- Shares: basic shares for market cap and WACC weights; diluted shares for value per share.
- Debt includes lease liabilities, matching yfinance's definition.
- Joint-venture capex (Sandisk's share of Flash Ventures with Kioxia) is a separate cost line.
- No input() prompts: all assumptions live in `companies/<ticker>.toml`, so every run can be repeated.
- One valuation function (`model.py`) is reused by scenarios, sensitivity and reverse DCF.
- Export to Excel only when you answer y at the end.

**Dropped from mk8.3:** Auto growth mode, FCFF base modes (ttm / maint / avg3y: the operating
model has no single base-year FCFF), Revenue & Margin Projector as a separate script (its method is
now built in; segment and backlog revenue methods to be added later).

---

## 2026-11-10: data source moved from yfinance to OpenBB v5

- OpenBB v5 removed its Yahoo Finance and FMP sources. Statements now come from SEC filings
  (`obb.sec.*`), prices from Nasdaq with Cboe as backup.
- Checked side by side for SNDK (a one-time comparison script, not included here): cash flow and balance sheet items matched
  yfinance exactly. Differences explained: diluted vs basic shares, leases counted as debt,
  operating income vs Yahoo's "EBIT" (which includes non-operating income).
