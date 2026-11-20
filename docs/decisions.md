# Decisions

Methodology and process decisions, with dates and reasons. The screening spec changes only through an entry here: a `spec-change` issue must update the tests that pin the old behavior and add or amend an entry in this file.

## D1 — PEG growth basis (2026-11-19)
Growth uses one earnings basis and horizon on both sides of the PEG ratio (expected: analyst-consensus adjusted EPS, current fiscal year to next). `PEG_MAX` stays 2; verdict flips are accepted. Reason: the current growth mixes GAAP trailing EPS with an analyst forward estimate, which inflates growth and yields implausibly low PEGs (Nov-19 SPY median 0.52).

**Definition (#5, 2026-11-20):** `PEG = (price / EPS_FY1) / (100 × (EPS_FY1 / EPS_FY0 − 1))`, where EPS_FY0 and EPS_FY1 are the analyst consensus averages for the current and next fiscal year. Untestable (→ NEEDS_DATA path, never 0 or a guess) when either value is absent, either is ≤ 0, the estimate currency differs from the price currency (ASML, CCEP, FER), or price is absent. Growth ≤ 0 keeps PEG N/A until the open question in the investigation is decided. FMP uses the same two figures from `analyst-estimates`. On the Nov-19 data the median PEG moves 0.50 → 1.43 (QQQ) and 0.52 → 1.70 (SPY); flips: QQQ ASML (currency guard), SPY seven energy/refining names (negative FY1 consensus). Evidence: `docs/investigations/2026-11-peg-basis.md`. Implementation: #6.

## D2 — Balance-sheet financials are NOT_APPLICABLE (2026-11-19)
Banks, insurers, brokers, asset managers and consumer lenders get `NOT_APPLICABLE`. Asset-light payment networks (V, MA) stay evaluable. Reason: for a balance-sheet business, operating cash flow includes loan and deposit flows, so FCF margin and Rule of 40 are not meaningful. A separate bank methodology is not planned; revisit only if one of these names is wanted for the satellite sleeve.

## D3 — Cyclicality is an informational flag (2026-11-19)
Add an informational `CYCLICAL` flag only. Changing the trend-mode rule (e.g. median instead of mean) is deferred until the PEG fix lands and 2–3 runs show whether it is needed. Reason: a median rule would also drop young accelerating names (RDDT) that are not cyclical.

## D4 — Universes come from issuer CSVs, never a silent stale list (2026-11-19)
The issuer CSV is the primary source for QQQ and ARKK, refreshed automatically when stale and validated. A run never uses a stale list silently; `--allow-stale` is explicit. Reason: Wikipedia's Nasdaq-100 page lost its constituents table and the tool fell back to a July static list with only a stderr warning.

## D5 — Scope: lean but portfolio-grade (2026-11-19)
CI, packaging and a skimmable README now; mypy deferred; the LLM memo is its own milestone after M1. Reason: keep a solo project small while keeping it presentable and green.

## D6 — Process: backlog in GitHub Issues (2026-11-19)
The backlog lives in GitHub Issues (one goal, evidence, scope, Done-when). `spec-change` issues update the pinning tests and add an entry here. No personal portfolio details in issues (public repo).

## D7 — LLM access (2026-11-19)
The memo feature (#18) uses a swappable backend: (a) the user's own signed-in Claude Code CLI (`claude -p`), personal use only, drawing from that user's plan limits; (b) OpenRouter (OpenAI-compatible key); (c) an Anthropic API key. Every user brings their own credential; no tokens or keys in the repo; CI uses recorded responses only. Anthropic's rules for programmatic use of subscriptions changed several times in 2026, so re-check the 'Use the Claude Agent SDK with your Claude plan' help article before relying on (a).

## D8 — No persona-agent layer (2026-11-19)
ai-hedge-fund-style agents are not integrated. They output buy/sell views, need their own paid data source, re-analyze the same numbers, and can't be evaluated against ground truth. Qualitative context comes from the audited context check (#25).

## D9 — Investor lenses (refines D8, 2026-11-19)
We build our own lens layer: checklists of publicly documented principles (Buffett/Munger, Lynch, Wood, a skeptic), structured, source-tagged, no buy/sell, plus a synthesis brief audited by an evaluator (#27, #28). D8 still holds for ai-hedge-fund itself: its code is not integrated, and its prompts may inform ours with MIT attribution.

## D10 — No LLM price targets or buy/sell calls (2026-11-20)
Valuation context (#29) and decision categories (#32) are deterministic, and the rules are written by the user; LLM layers explain them and never decide.

## D11 — No macro forecasts or news-sentiment signals (2026-11-20)
Macro enters as structural exposures (#25), and current events only as cited thesis-risk flags for final candidates (#31). Neither ever changes a verdict.

## D12 — Buy and sell discipline (2026-11-20)
No market timing and no bull/bear regime calls. Momentum and relative strength (#36) are informational context for staging entries. Selling is reviewed for the documented reasons (thesis wrong, company deteriorated, clearly better opportunity) through #32's review categories, never automated.

## D13 — Forward-tracking cadence (2026-11-20)
Screens run monthly (QQQ and SPY), by hand from a terminal via scripts/monthly_run.sh. Each run's snapshot is saved and never overwritten. Performance is computed on demand from price history; there is no daily process. Cloud schedulers aren't used (Yahoo limits cloud IPs, and a silent failure is worse than a reminder). Tracking counts from the first run after M1.
