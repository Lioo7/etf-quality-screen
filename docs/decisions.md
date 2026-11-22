# Decisions

Methodology and process decisions, with dates and reasons. The screening spec changes only through an entry here: a `spec-change` issue must update the tests that pin the old behavior and add or amend an entry in this file.

## D1 — PEG growth basis (2026-11-19)
Growth uses one earnings basis and horizon on both sides of the PEG ratio (expected: analyst-consensus adjusted EPS, current fiscal year to next). `PEG_MAX` stays 2; verdict flips are accepted. Reason: the current growth mixes GAAP trailing EPS with an analyst forward estimate, which inflates growth and yields implausibly low PEGs (Nov-19 SPY median 0.52).

**Definition (#5, 2026-11-20):** `PEG = (price / EPS_FY1) / (100 × (EPS_FY1 / EPS_FY0 − 1))`, where EPS_FY0 and EPS_FY1 are the analyst consensus averages for the current and next fiscal year. Untestable (→ NEEDS_DATA path, never 0 or a guess) when either value is absent, either is ≤ 0, the estimate currency differs from the price currency (ASML, CCEP, FER), or price is absent. FMP uses the same two figures from `analyst-estimates`. On the Nov-19 data the median PEG moves 0.50 → 1.43 (QQQ) and 0.52 → 1.70 (SPY); flips: QQQ ASML (currency guard), SPY seven energy/refining names (negative FY1 consensus). Evidence: `docs/investigations/2026-11-peg-basis.md`. Implementation: #6.

**Implementation decisions (#6, 2026-11-21):** (1) Consensus growth ≤ 0 (EPS_FY1 ≤ EPS_FY0, both positive) is a *failed* PEG: a profitable name is REJECTED ("consensus EPS growth ≤ 0"), never NEEDS_DATA and never WATCHLIST, since no price or extra data fixes shrinking earnings. NEEDS_DATA stays for absent, non-positive or currency-mismatched inputs. Reason: a failed test rejects; an unadministrable one asks for data. (2) Fewer than 3 FY1 analysts adds a low-confidence note, never a gate. (3) Currency mismatch stays untestable; conversion is #46. FMP estimate rows carry no currency, so FMP compares the profile's trading currency with the statements' `reportedCurrency` (missing → compute, flagged "estimate currency unverified"); an FMP fiscal year that has ended but is not yet reported shifts FY0/FY1 a year ahead, flagged. (4) The raw inputs `eps_fy0`, `eps_fy1`, `estimate_currency`, `fy1_analysts` are informational `Company` fields (#46 and the memo need them); `forward_pe` and `forward_eps_growth` stay the gate inputs, and the NEEDS_DATA hint names the cause and the basis for override values. (5) Cyclicals at an earnings peak still get low PEGs on this basis (SNDK, STX); that is for #15 and #20, not PEG tuning. Same-day live verdict diffs: see the #6 PR.

**Currency conversion (#46, 2026-11-22):** Supersedes the currency clause above for yfinance. When the estimate currency differs from the price currency, EPS_FY1 is converted at the newest daily close of the Yahoo FX pair (`EURUSD=X`), fetched once per pair per run; the rate and its timestamp go in `low_confidence`. Growth is a ratio and is not converted. The converted forward P/E must lie within 30% of Yahoo's own `forwardPE`, else PEG stays untestable ("currency/ADR basis mismatch", e.g. one ADR = 5 ordinary shares); no `forwardPE` to compare, or no FX rate, also stays untestable, never a guessed rate. `forwardPE` is only this cross-check, never a PEG input. The informational `eps_fy0` / `eps_fy1` stay in `estimate_currency`. FMP is unchanged (mismatch stays untestable): whether FMP's `analyst-estimates` share the problem is unverified, since the endpoint needs a higher FMP plan. Live 2026-11-22 on ASML, CCEP, FER and `universes/japan-automation-adr.txt`: all 4 mismatched names (ASML, CCEP, FER in EUR; OMRNY in JPY) became testable, none hit the sanity check; ASML moves NEEDS_DATA → PASS. The cache schema moves to v3 so pre-#46 entries (unconverted, untestable forward P/E) are refetched. ADR audit (2026-11-22, 16 names incl. 4:1–10:1 ADRs; table in PR #57): for the 9 converted names (PDD, BABA, JD, BIDU, NTES, ASML, CCEP, FER, OMRNY), an earnings-scale ratio independent of Yahoo's forward P/E (consensus FY0 EPS × FX × marketCap / price vs TTM net income) lay within 0.89–1.74 wherever computable, so no shared ADR-ratio error was found and no scale gate was added. Caveat: Yahoo's `forwardPE` matched the converted P/E to within 0.02% on all five CNY names, so it is derived from the same estimate table and catches FX errors, not a shared ADR-ratio error.

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

## D14 — Universe scope (2026-11-20)
Monthly runs cover the de-duplicated union of QQQ and SPY; S&P 400 mid caps run quarterly, after earnings seasons. Small caps and non-US direct listings stay out of the routine (data quality); they remain possible ad hoc via --holdings. Curated ADR lists run ad hoc.

## D15 — License (2026-11-21)
No license (all rights reserved) for now, to keep commercial options open. Opening later (MIT, AGPL, PolyForm Noncommercial) stays possible; closing after an open release is not. Copyright covers the code, not the documented method. External contributions would need a contributor agreement first. Dependencies are permissively licensed and impose no obligation to open the code.

## D16 — Sentiment, moving averages and cost basis are context, not signals (refines D11 and D12, 2026-11-21)
VIX, breadth and index drawdown are recorded in snapshots (#16), never used as signals (fear/greed indices show weak, unstable predictive power). Moving-average distance is an informational entry-timing column only (#36). The user's cost basis is never an input to screening or decision rules (disposition effect, Odean 1998). Valuation against a company's own history belongs to #29.

## D17 — Date format (2026-11-22)
ISO 8601 (YYYY-MM-DD) everywhere in code, data, caches, exports, file names and docs: unambiguous across locales, sorts chronologically, and parses natively. Human-facing report views (HTML, #49) display '10 Jul 2026'. Never use numeric DD/MM or MM/DD.

## D18 — Market cap in the statement currency; annual basis on missing quarterly capex or SBC (2026-11-22)
P/S divides market cap by revenue, so both must share a currency. Yahoo and FMP quote market cap in the trading currency, but foreign filers report in their own (PDD, BABA, JD, BIDU in CNY; ASML, CCEP, FER in EUR). When the two currencies are known and differ, market cap is converted into the statement currency at a same-run FX rate (`market_cap_converted`, noted in `low_confidence`); no rate means a skip, never a guess. An unknown statement currency changes nothing. A converted cap hides the unlock prices until #62 derives them from the traded price (ADR ratios make market cap / shares wrong). Separately, a quarterly capex gap, or a quarterly SBC gap while the annual statement reports SBC, moves the whole company to the annual basis (one basis, never mixed, `low_confidence` names the reason); SBC is never assumed 0 on TTM when the annual statement shows it. Thresholds and routing are unchanged. Run diff (QQQ, same raw Yahoo frames, 2026-11-22): no verdict changed among names that screened before; ABNB, APP and PDD went from skipped to screened.

