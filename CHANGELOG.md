# Changelog

## v0.2 (in progress)

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
- **`RECENT_IPO` classification.** Names listed within the last 12 months
  (`--ipo-seasoning-months`, `0` disables) are set aside in their own section,
  never in the shortlist — unseasoned listings break the history-based inputs
  and carry hostile base rates. Unknown first-trading date → screened normally
  and flagged `ipo_date_unknown`, never guessed.
