# Changelog

## v0.2 (in progress)

- **ROIC history (advisory).** Snapshot ROIC (unchanged as a gate) is now
  joined by a multi-year median over up to 10 annual periods, plus the year
  count used. A `roic_durable` annotation is added when the median is ≥ 15%
  over ≥ 5 usable years — evidence of a moat wide enough to survive one bad
  year. Purely informational; never affects `passed`/`track`/ranking.
