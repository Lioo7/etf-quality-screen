# CLAUDE.md

Guidance for Claude Code in this repository.

## Commands

A virtualenv lives at `.venv` (git-ignored). Use it directly:

```bash
.venv/bin/pip install -r requirements.txt   # full deps
.venv/bin/python -m pytest -q                # all tests
.venv/bin/python -m pytest tests/test_screen.py::test_pricey_but_profitable_is_rejected  # one test
RUN_LIVE_TESTS=1 .venv/bin/python -m pytest tests/test_live_yfinance.py -q  # live Yahoo (network)
.venv/bin/python -m flake8 etf_screen tests  # lint (setup.cfg: max-line-length=100)
```

```bash
.venv/bin/python -m etf_screen.cli --provider mock --tickers MSFT,GOOGL,CRWD   # offline
.venv/bin/python -m etf_screen.cli --universe qqq --throttle 0.5               # live (~100 calls)
.venv/bin/python -m etf_screen.cli --tickers MSFT,GOOGL,CRWD --export md       # file export
.venv/bin/python -m etf_screen.cli --holdings path/to/ARKK.csv --sector-context  # issuer CSV
.venv/bin/python -m etf_screen.cli --provider mock --consistency-years 3 --consistency-mode trend
```

## Backlog

GitHub Issues. Run `gh issue list` before new work; one issue per session. Reference issues in commits (`closes #N`, `refs #N`). Format: `.github/ISSUE_TEMPLATE/task.md`. Decisions and reasons: `docs/decisions.md`; a `spec-change` issue adds an entry there. No personal portfolio details in issues (public repo). Order and command per issue: docs/queue.md; every new issue gets a row there in the same change. `/next` shows what to run. Design and milestones: docs/roadmap.md. Evidence behind methodology: docs/research-basis.md. A decision that changes either updates it in the same change.

## Architecture

A CLI stock screener that strictly separates screening logic from data sourcing. The methodology is a *fixed spec*: do not redesign thresholds or track routing; acceptance tests enforce exact behavior.

Flow: `cli.run()` → `constituents.resolve()` / `--tickers` → `provider.fetch()` → `overrides.apply_override()` → `screen.evaluate()` → `screen.rank()` → report + optional `export`.

- `screen.py`: pure logic, no IO. The verdict pipeline and status semantics are documented in its module docstring. `ScreenStatus` is the single verdict source; `Result.passed` etc. are derived properties, so set `status`, never those.
- `providers.py`: the only network code; providers raise `DataUnavailable` rather than fabricate. Provider rules: `.claude/rules/providers.md`.
- `constituents.py`: ETF → tickers (Wikipedia, bundled static fallback, `--holdings` CSV, `--tickers-file`). Precedence: `--tickers` > `--tickers-file` > `--holdings` > `--universe`.
- `overrides.py` (git-ignored `overrides.json` beats the provider), `cache.py` (per-day JSON cache), `export.py` (CSV/Markdown).

## Critical invariants

- **Track B / PEG guard.** Track B only when PEG is genuinely N/A (GAAP-unprofitable, no forward P/E). A profitable name failing PEG is rejected, never Track B. The `PriceyButProfitable` test enforces this; never weaken it.
- **Never fabricate data.** Missing values → skip and report ("could not source"). Sanctioned exceptions are line items absent from a retrieved statement: SBC, goodwill, total debt → 0, flagged. Cash, total assets, total equity are never assumed.
- **Pre-revenue firewall runs first** (`revenue_ttm <= 0` → `PRE_REVENUE`, before any math).
- **Status precedence is fixed.** Balance-sheet failure (REJECTED) beats the history gate; then INSUFFICIENT_HISTORY, consistency FAIL, else PASS. A snapshot failer's gates are moot.
- **WATCHLIST and NEEDS_DATA are not loopholes.** WATCHLIST only when every quality gate passes and valuation is the sole blocker; NEEDS_DATA only when PEG is untestable (consensus EPS missing, ≤ 0 or in another currency, or consensus FY0 carries one-off items ≥ 10% of FY0, D19), P/S passes and no gate fails (no unlock price). Consensus EPS growth ≤ 0 is a failed PEG: REJECTED, never NEEDS_DATA or WATCHLIST, unless the D19 one-off check makes PEG untestable. Neither enters `rank()`.
- **RECENT_IPO is the unconditional final override** (the firewall still wins). Unknown `ipo_date` is never guessed.
- **Strict vs trend is Track A only**; Track B never changes with the mode. `evaluate()` defaults to `strict`, the CLI to `trend`. `tests/test_consistency.py` pins `strict` on strict cases; keep the pins.
- **Advisory layers never gate** (sector context, ROIC history, margin profile, entry timing/HOT, EQ capex note, OTC flag, financial note, street basis): they must never change `passed`, `track` or rank. `tests/test_sector.py` proves it; never weaken it.
- A short or empty shortlist is expected: **do not loosen thresholds to populate the table.**

## Conventions

- Solo side project: lean but portfolio-grade. CI must stay green; avoid over-engineering; keep README and inline docs balanced.
- Adding a `Company` field: update `_COMPANY_FIELDS` in `providers.py` (cache round-trip), `export.COLUMNS` if exported, populate it in every provider, keep any default gate-passing, and add patchable scalars to `overrides._OVERRIDABLE` (informational ones to `_INFORMATIONAL`).
- Informational fields (`ipo_date`, price snapshot) default to `None` = unknown, rendered N/A, never a fabricated value. `current_price` is the quote the metrics used (Yahoo `currentPrice` → `regularMarketPrice` → last close, noted; FMP quote), so the WATCHLIST valuation-pass price is `current_price x ceiling / metric` (D20).
