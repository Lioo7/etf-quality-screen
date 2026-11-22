# Changelog

## Unreleased

- **PEG on one basis (#6, D1).** Growth no longer divides an analyst forward
  EPS by GAAP trailing EPS. Both providers now use the consensus EPS for the
  current and next fiscal year:
  `PEG = (price / EPS_FY1) / (100 × (EPS_FY1 / EPS_FY0 − 1))`. Missing,
  non-positive or other-currency estimates make PEG untestable (`NEEDS_DATA`
  path); consensus growth ≤ 0 is now a failed PEG (`REJECTED`, never
  `NEEDS_DATA` or `WATCHLIST`). `PEG_MAX` stays 2.
- New informational fields `eps_fy0`, `eps_fy1`, `estimate_currency`,
  `fy1_analysts` (cached, overridable, exported). The `NEEDS_DATA` hint names
  the cause and the basis for override values.
- `forward_eps_growth` is `None` when unknown (was a `0.0` sentinel); the cache
  key is versioned so same-day entries from older code are refetched.

- **Foreign filers screen on an honest basis (#58, D18).** PDD, BABA, JD, BIDU
  (and ABNB, APP) were skipped as "operating cash flow / capex missing": Yahoo has
  no quarterly capex for them, but complete annual statements. A quarterly capex
  gap, or a quarterly SBC gap with SBC on the annual statement, now moves the whole
  company to the annual basis. Market cap is converted to the statement currency
  before P/S (CNY, EUR reporters); no FX rate means a skip. Converted names show no
  unlock price until #62. Cache schema is `v5`.

- **One-off items in consensus FY0 (#61, D19).** For companies whose street EPS
  is GAAP EPS (checked per quarter against the statement), street actual minus
  normalized EPS over the reported FY0 quarters is the one-off amount inside
  consensus FY0. At 10% of FY0 or more (either sign) PEG is untestable instead of
  failed or passed. Adjusted-EPS reporters are left alone (their consensus already
  excludes one-offs). New informational `street_basis` field; `NEEDS_DATA` entries
  carry a verification task (console and export columns `one_off_*`). US issuers
  with one currency only. A data problem is noted per name ("one-off check
  unavailable"); structural causes (mixed basis, non-US, FMP) get one console line
  and the `one_off_unavailable_cause` export column.
  Cache schema is `v7`.

## v0.2.1 — 2026-07-10

Live-fire fixes from the first real v0.2 runs (QQQ / Israel-tech /
Japan-automation, 2026-07-09).

- **TTM basis triggers again.** Yahoo now publishes only ~5 quarterly statement
  columns, so the v0.2 TTM condition (8 revenue quarters for a summable
  prior-year TTM) never held and every live name fell to ANNUAL basis. Level
  metrics still sum the newest 4 quarters (true TTM); growth now derives from
  the newest quarter vs its date-aligned year-ago counterpart — both raw
  sourced lines, flagged, never estimated — with the 8-quarter true-prior-TTM
  path kept for when it exists and the honest annual fallback unchanged.
- **IPO dates source again.** yfinance renamed Yahoo's first-trade field to
  `firstTradeDateMilliseconds` (epoch ms); the extractor now honors both names,
  each per its documented unit. RECENT_IPO works in live runs (SPCX verified);
  the never-guess rule and `ipo_date_unknown` flag are unchanged.
- **`NEEDS_DATA` classification.** "PEG untestable" is no longer conflated with
  "PEG failed": a profitable name whose only blocker is an uncomputable PEG
  (no forward estimates — unsponsored ADRs like Keyence) while every testable
  gate passes gets its own section/export status with an overrides.json hint.
  A computable failed PEG keeps its v0.2 fate; Track B stays barred.
- **Capex-intensity annotation on earnings-quality failures.** The EQ ratio
  can't tell accrual rot from reinvestment (NVDA 0.76, TSM 0.58); when capex
  exceeds 15% of revenue over the same window, the rejection carries "EQ
  shortfall may reflect capex intensity, not accruals" and the figure is
  exported. The threshold itself is unchanged.
- **One `_run_quality_gates` source.** The duplicated qualifier gate block
  (real track vs WATCHLIST/NEEDS_DATA candidacy) is now a single closure, so a
  future gate can't be added to one path and forgotten in the other.
- **Universe hygiene.** CYBR removed (acquired by Palo Alto Networks,
  delisted); DAIUF→DFKCY (prefer the sponsored ADR); dead HRLBF→HSYDF; TSEM
  kept (its 07-09 failure was a transient Yahoo miss). README notes that ROIC
  on negative-equity names (BKNG) renders N/A/unstable by construction.
- **Exports default to `results/`.** Runs now write to the git-ignored
  `results/` directory instead of the repo root (`--out` still overrides);
  committed curated snapshots live only in `examples/`.
- **QQQ static fallback → 2026-07-09.** Bundled Nasdaq-100 list refreshed from
  the live Wikipedia fetch that day (103 tickers, including SPCX added
  2026-07-07). Preferred refresh path: drop the official Invesco QQQ holdings
  CSV at `holdings/qqq-holdings.csv` and regenerate via `--holdings`.
- **`NEEDS_DATA` hint mentions `market_cap`.** Completing PEG against a fictional
  Yahoo OTC/ADR cap (Keyence/KYCCF P/S 0.1) would finish the screen against a
  fictional valuation — overrides may need a verified `market_cap`, not just
  forward estimates.
- **`otc_adr_data_suspect` flag (informational).** 5-letter OTC ADR convention
  (ends in Y or F); Yahoo caps/statements for these lines are routinely broken.
  Never a gate.
- **Financial Services asterisk (informational).** Results in sector
  `Financial Services` carry *"balance-sheet business — leverage/FCF/GM metrics
  unreliable; analyze via sector-appropriate measures"* in console + export.
  Never changes a verdict.
- Live-network acceptance tests under `RUN_LIVE_TESTS=1`.

## v0.2 — 2026-07-08

- **README "Reading a result" walkthrough.** One full mock run exercising
  every terminal state (PASS with all annotations, WATCHLIST with unlock
  prices, RECENT_IPO, every gate rejection, INSUFFICIENT_HISTORY), walked
  through gate by gate.

- **ROIC history (advisory).** Snapshot ROIC (unchanged as a gate) is now
  joined by a multi-year median over up to 10 annual periods, plus the year
  count used. A `roic_durable` annotation is added when the median is ≥ 15%
  over ≥ 5 usable years — evidence of a moat wide enough to survive one bad
  year. Purely informational; never affects `passed`/`track`/ranking.
- **Earnings-quality gate.** New rejecting gate in the balance-sheet-gate
  family: cumulative 3-year adjusted FCF ÷ cumulative 3-year net income must be
  ≥ 0.8 for names with cumulative net income > 0. Catches profits that never
  become cash (aggressive revenue recognition, ballooning receivables). N/A,
  never a fail, when cumulative net income isn't positive.
- **Gross-margin level & stability (advisory).** Latest gross margin + stddev
  over up to 10 annual years; GM ≥ 40% with stddev < 5pp over ≥ 5 years earns a
  `pricing_power` tag. Names without a cost-of-revenue line (banks, insurers)
  are `gm_not_applicable`. Informational columns only — never a gate.
- **Curated ticker-list files (`--tickers-file`).** Plain-text thesis-driven
  universes (one ticker per line, `#` comments); two starter lists committed
  under `universes/` (US-listed Israeli tech; Japanese automation ADRs, with
  the OTC data-quality caveat documented in-file). Duplicates dropped with a
  note; empty files and junk lines are loud errors.
- **Watchlist mode.** Names passing every quality gate but failing only the
  valuation filters are classified `WATCHLIST` (own section + export rows)
  with pre-computed **unlock prices** — the price at which P/S = 0.5 × growth%
  and/or PEG = 2.0. Quality-and-valuation failers stay plain rejected;
  profitable PEG-N/A names stay barred; zero/negative growth → unlock N/A.
- **Entry-timing annotations (informational).** `ATH%` (distance from the
  all-time high) and `3M%` (trailing 3-month return) from split/dividend-
  adjusted closes; `HOT` tag when the 3-month return exceeds +50% within 20%
  of the ATH — a patience case for the anti-FOMO entry rule, never a
  rejection. Never affects pass/fail, tracks, or ranking.
- **`RECENT_IPO` classification.** Names listed within the last 12 months
  (`--ipo-seasoning-months`, `0` disables) are set aside in their own section,
  never in the shortlist — unseasoned listings break the history-based inputs
  and carry hostile base rates. Unknown first-trading date → screened normally
  and flagged `ipo_date_unknown`, never guessed.
