# Changelog

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
