# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

A virtualenv lives at `.venv` (git-ignored). Use it directly:

```bash
.venv/bin/pip install -r requirements.txt   # full deps (yfinance pulls pandas; lxml added explicitly)
.venv/bin/python -m pytest -q                # run all tests
.venv/bin/python -m pytest tests/test_screen.py::test_pricey_but_profitable_is_rejected  # single test
.venv/bin/python -m flake8 etf_screen tests  # lint (config in setup.cfg: max-line-length=100)
```

Run the screener:

```bash
.venv/bin/python -m etf_screen.cli --provider mock --tickers MSFT,GOOGL,CRWD   # offline, deterministic
.venv/bin/python -m etf_screen.cli --universe qqq --throttle 0.5               # full live run (~100 yfinance calls)
.venv/bin/python -m etf_screen.cli --tickers MSFT,GOOGL,CRWD --export md       # with file export
.venv/bin/python -m etf_screen.cli --holdings path/to/ARKK.csv --sector-context  # any ETF via issuer CSV
.venv/bin/python -m etf_screen.cli --provider mock --consistency-years 3 --consistency-mode trend  # full gate set
```

## Architecture

A CLI stock screener whose central design rule is **strict separation of screening logic from data sourcing**. The validated screening methodology is a *fixed spec* — do not redesign thresholds or filter/track routing; the acceptance tests enforce exact behavior.

**Data flow:** `cli.run()` → `constituents.resolve(etf)` (or `--tickers`) → for each ticker `provider.fetch()` → optional `overrides.apply_override()` → `screen.evaluate()` → `screen.rank()` → console report + optional `export`.

- **`screen.py`** — pure logic, **no network/IO**. `Company` (raw statement-line inputs, all metrics derived from these — never pre-packaged ratios), `evaluate(c, consistency_years=0, consistency_mode="strict") -> Result`, `rank()`. The verdict pipeline, in order: (0) **pre-revenue firewall** (`revenue_ttm <= 0` short-circuits to `PRE_REVENUE` before any math); (1) the four snapshot filters (Rule of 40, PEG≤2, P/S guardrail, SBC≤15%) + two-track routing — the authoritative, fixed core; (2) for a snapshot qualifier, the **current-snapshot balance-sheet gates** (leverage `net_debt/FCF≤3`, goodwill `≤40%` of assets, ROIC `≥10%`) and the **multi-year consistency gate** (`track_a_consistency` / `track_b_consistency` over `Company.history`). `ScreenStatus` (PASS / REJECTED / PRE_REVENUE / INSUFFICIENT_HISTORY / NOT_EVALUABLE) is the single verdict source — `Result.passed/evaluable/insufficient_history` are read-only **properties** derived from it. Also holds the **informational** sector-context layer (`sector_medians()`, `annotate_sector_context()`) — see invariants.
- **`providers.py`** — the only place that touches the network. `DataProvider` ABC with `YFinanceProvider` (default, key-less), `FMPProvider` (optional, needs paid tier + `FMP_API_KEY`), `MockProvider` (tests/offline). Providers **raise `DataUnavailable`** rather than fabricate. `_extract_yf()` is factored out as a pure function (takes income/cash-flow/**balance-sheet** DataFrames + info dict) so basis-selection and balance-sheet extraction are testable without network; it tolerates absent balance-sheet frames (test path → gate-passing defaults), while the real `_fetch_uncached` raises if Yahoo exposes no balance sheet at all.
- **`constituents.py`** — key-less ETF→tickers resolution. Wikipedia index table (primary, returns a ticker→name map) → bundled dated static list (fallback, prints a STALE warning). Keyed by ETF ticker (QQQ, SPY). Also `holdings_from_csv()` (the `--holdings` flag): a generic issuer-holdings-CSV loader (ARK/iShares/SSGA/…) that finds the ticker column by name, tolerates preamble/footer rows, and reuses `_looks_like_ticker` to drop junk — lets the tool screen *any* ETF.
- **`overrides.py`** — git-ignored `overrides.json` (ticker→verified field values) that takes precedence over the provider; can patch fields or rescue a fully-specified skipped name.
- **`cache.py`** — per-day JSON disk cache under `.cache/<provider>/<date>/` making full runs resumable.
- **`export.py`** — CSV/Markdown writers for the full result set with a provenance header.

## Critical invariants

- **The Track B / PEG guard.** Track B is reachable **only when PEG is genuinely N/A** (GAAP-unprofitable, no forward P/E). A *profitable* name that fails the PEG filter must be **rejected**, never routed to Track B. The `PriceyButProfitable` acceptance test exists solely to enforce this — never weaken it.
- **Never fabricate data.** Missing/blocked values → skip the name and report it (the "could not source" section); never fill from memory or default silently. The sanctioned exceptions are *line items absent from a statement that was retrieved*: SBC (`sbc_assumed_zero`), and on the balance sheet **goodwill** (`goodwill_assumed_zero`) and **total debt** (`debt_assumed_zero`) → treated as 0, flagged, and surfaced in a review section. The required balance-sheet lines (**cash, total assets, total equity**) are never assumed — their absence raises `DataUnavailable`. (`Company`'s gate-passing field defaults exist only for test ergonomics; providers always populate real values.)
- **Pre-revenue firewall runs first.** `revenue_ttm <= 0` → `PRE_REVENUE`, returned before any metric math (no divide-by-zero, no gate). A name with current revenue > 0 but a zero *prior* year stays a normal `NOT_EVALUABLE` divide-by-zero case.
- **Balance-sheet gates layer onto a snapshot qualifier only**, and **status precedence is fixed**: a balance-sheet failure is definitive (REJECTED) and wins over the historical gate; otherwise INSUFFICIENT_HISTORY, then a consistency FAIL, else PASS. A name that fails the *snapshot* is a normal rejection — its balance sheet and history are moot. Leverage + goodwill apply to every qualifier (both tracks); **ROIC gates only a Track-A name under `strict` mode** and is advisory (never fails) when not computable (`invested_capital ≤ 0` or EBIT absent → `roic is None`).
- **Strict vs trend is Track A only; Track B never changes with the mode.** `evaluate()` defaults to `strict` (preserves library/test behavior); the **CLI defaults to `trend`**. `strict` = Rule of 40 ≥ 40 every windowed year; `trend` = recency anchor (latest ≥ 40) + mean ≥ 30 + no lumpy sub-floor collapse (a launch year is forgiven). The windowing (N+1 contiguous annual periods) is mode-independent. `tests/test_consistency.py` pins `consistency_mode="strict"` on the strict-behavior cases — don't drop the pins.
- **One accounting basis per company (yfinance).** All metrics use TTM only when a full trailing year of *both* income and cash-flow quarters exists; otherwise revenue *and* OCF/capex/SBC all fall back to annual, so growth/FCF-margin/SBC%/Rule40 share one base. Basis is recorded on `Company.basis`.
- A short or empty shortlist is an acceptable, expected result of a strict screen — **do not loosen thresholds to populate the table.**
- **Sector context is informational only.** The per-sector medians and the `--sector-context` / `--rank sector-relative` views must **never** change `passed`, `track`, or which names survive — the four filters and two tracks stay the sole gates. `tests/test_sector.py` has a regression proving verdicts+rank are identical with and without the feature; never weaken it.

## Conventions

- This is a solo side project: keep it lean, avoid over-engineering, maintain balanced README + inline docs.
- Adding a field to `Company`: also update `_COMPANY_FIELDS` in `providers.py` (cache round-trip), `export.COLUMNS` if it should be exported, populate it in **every** provider, and — if it has a default — keep that default gate-passing so the positional/keyword test fixtures stay green. A new patchable scalar also goes in `overrides._OVERRIDABLE` (and stays out of `_REQUIRED` if it shouldn't block a from-scratch rescue).
- `Result`'s verdict fields (`passed`, `evaluable`, `insufficient_history`) are **properties** derived from `ScreenStatus` — set `status`, never these. Add a new outcome by extending the enum + its properties, not by adding a parallel boolean.
- yfinance returns raw dollars, Mock uses $M — fine because every screen metric is a scale-invariant ratio.
