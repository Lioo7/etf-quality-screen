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
```

## Architecture

A CLI stock screener whose central design rule is **strict separation of screening logic from data sourcing**. The validated screening methodology is a *fixed spec* — do not redesign thresholds or filter/track routing; the acceptance tests enforce exact behavior.

**Data flow:** `cli.run()` → `constituents.resolve(etf)` (or `--tickers`) → for each ticker `provider.fetch()` → optional `overrides.apply_override()` → `screen.evaluate()` → `screen.rank()` → console report + optional `export`.

- **`screen.py`** — pure logic, **no network/IO**. `Company` (raw statement-line inputs, all metrics derived from these — never pre-packaged ratios), `evaluate() -> Result`, `rank()`. This is the authoritative implementation of the four filters (Rule of 40, PEG≤2, P/S guardrail, SBC≤15%) and two tracks.
- **`providers.py`** — the only place that touches the network. `DataProvider` ABC with `YFinanceProvider` (default, key-less), `FMPProvider` (optional, needs paid tier + `FMP_API_KEY`), `MockProvider` (tests/offline). Providers **raise `DataUnavailable`** rather than fabricate. `_extract_yf()` is factored out as a pure function (takes DataFrames + info dict) so basis-selection logic is testable without network.
- **`constituents.py`** — key-less ETF→tickers resolution. Wikipedia index table (primary, returns a ticker→name map) → bundled dated static list (fallback, prints a STALE warning). Keyed by ETF ticker (QQQ, SPY).
- **`overrides.py`** — git-ignored `overrides.json` (ticker→verified field values) that takes precedence over the provider; can patch fields or rescue a fully-specified skipped name.
- **`cache.py`** — per-day JSON disk cache under `.cache/<provider>/<date>/` making full runs resumable.
- **`export.py`** — CSV/Markdown writers for the full result set with a provenance header.

## Critical invariants

- **The Track B / PEG guard.** Track B is reachable **only when PEG is genuinely N/A** (GAAP-unprofitable, no forward P/E). A *profitable* name that fails the PEG filter must be **rejected**, never routed to Track B. The `PriceyButProfitable` acceptance test exists solely to enforce this — never weaken it.
- **Never fabricate data.** Missing/blocked values → skip the name and report it (the "could not source" section); never fill from memory or default silently. The one sanctioned exception: a missing SBC *line item* (when the cash-flow statement was retrieved) is treated as 0, flagged `sbc_assumed_zero`, and surfaced in a dedicated review section.
- **One accounting basis per company (yfinance).** All metrics use TTM only when a full trailing year of *both* income and cash-flow quarters exists; otherwise revenue *and* OCF/capex/SBC all fall back to annual, so growth/FCF-margin/SBC%/Rule40 share one base. Basis is recorded on `Company.basis`.
- A short or empty shortlist is an acceptable, expected result of a strict screen — **do not loosen thresholds to populate the table.**

## Conventions

- This is a solo side project: keep it lean, avoid over-engineering, maintain balanced README + inline docs.
- Adding a field to `Company`: also update `_COMPANY_FIELDS` in `providers.py` (cache round-trip), `export.COLUMNS` if it should be exported, and populate it in every provider.
- yfinance returns raw dollars, Mock uses $M — fine because every screen metric is a scale-invariant ratio.
