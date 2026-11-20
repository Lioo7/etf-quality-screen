# Roadmap and design

## Purpose

Two goals. (1) A portfolio-grade engineering project: pure screening logic separated from
networked data sourcing, deterministic tests, honest data handling, and LLM features that
are evaluated rather than trusted. (2) Decision support for a long-horizon (5+ years)
satellite sleeve: a subtractive candidate generator, never a buy signal. Trades are made
manually by the user.

## Principles

1. Deterministic code decides; LLM layers explain and never decide (D10).
2. Never fabricate data; anything assumed is flagged (CLAUDE.md invariants).
3. Every LLM claim is tagged as row data, model knowledge (to verify) or a cited source,
   and is checked by an evaluator: code checks first, an LLM judge for semantics (#18, #30).
4. No macro forecasts, sentiment signals or market timing (D11, D12).
5. Methodology changes one variable at a time, through spec-change issues and
   docs/decisions.md.

## Milestones (order and commands: docs/queue.md)

- **M1 data correctness:** SBC history (#4), fetch timeout (#7), PEG basis (#5 -> #6),
  fresh universes (#8), NOT_APPLICABLE for balance-sheet financials (#9).
- **M2 harness and CI:** harness (#10), CI (#11), README (#12), data-health summary (#13),
  /review-run (#14), property tests (#24), external review packet (#33).
- **M3 decision support:** CYCLICAL flag (#15), valuation context (#29), decision rules incl.
  sell-review categories and a decision journal (#32), run snapshots and diff (#16),
  forward scorecard vs QQQ (#21), held names (#22), drawdowns and units (#17), relative
  strength (#36), gross profitability (#37), P/S guardrail research (#20), EDGAR
  cross-check research (#23).
- **M4 AI layer:** grounded memo and evaluator (#18), LLM judge with a hand-labeled set and
  the blind-vs-named experiment (#30), context check with structural exposures (#25),
  thesis-risk events from 8-Ks and headlines (#31), filing evidence pack (#35), investor
  lenses (#27), synthesis brief (#28).

## Planned LLM pipeline (M4)

Screen rows (PASS, WATCHLIST, held names) feed:

- #25 context check: business model, cycle sensitivity, structural exposures (model
  knowledge, tagged, to verify);
- #31 recent material events: 8-K filings and headlines from the last 90 days, each cited;
- #35 filing evidence: 10-K risk-factor diff, customer concentration, segments, each cited;
- #27 lenses: checklists of published principles (Buffett/Munger, Lynch, Wood, skeptic);
- deterministic context: valuation (#29), decision category (#32), momentum (#36).

All of this feeds #28, a synthesis brief that explains agreement, disagreement, conflicts
with the deterministic flags and what to verify, and never recommends. Evaluation: code
checks plus the #30 judge, calibrated on a hand-labeled set. Backends (D7): the user's own
`claude -p`, OpenRouter, or an Anthropic API key. Every call is cached by input hash; an LLM
failure yields no output and never blocks a run.

## External review

After #6, #9 and #13, the review packet (#33) goes to an experienced investor; feedback
becomes issues labelled `feedback`.

## Non-goals

Auto-trading, price targets, buy/sell calls, backtesting LLM outputs, publishing personal
positions or amounts.
