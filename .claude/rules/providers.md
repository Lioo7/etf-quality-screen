---
paths: ["etf_screen/providers.py"]
---
# Provider rules

- `DataProvider` ABC with `YFinanceProvider` (default, key-less), `FMPProvider` (optional, paid tier + `FMP_API_KEY`), `MockProvider` (tests/offline). Providers raise `DataUnavailable` rather than fabricate.
- `_extract_yf()` is a pure function (income / cash-flow / balance-sheet DataFrames + info dict) so basis selection and balance-sheet extraction are testable offline. It tolerates absent balance-sheet frames (test path → gate-passing defaults); the real `_fetch_uncached` raises if Yahoo exposes no balance sheet at all.
- Required balance-sheet lines (cash, total assets, total equity) are never assumed: absence raises `DataUnavailable`. Goodwill and total debt absent from a retrieved statement → 0, flagged (`goodwill_assumed_zero`, `debt_assumed_zero`); SBC likewise (`sbc_assumed_zero`).
- **One accounting basis per company.** TTM needs a full trailing year of both income and cash-flow quarters; level metrics sum the newest 4. Yahoo caps quarterly statements at ~5 columns (observed 2026-07), so the prior-year TTM is usually unpublished: growth then uses the newest quarter vs its date-aligned (350–380d) year-ago counterpart (`revenue_latest_q` / `revenue_year_ago_q`, flagged in `low_confidence`; `revenue_ttm_prior` is NaN). An 8-quarter true-prior-TTM sum wins when it exists. No aligned pair → annual fallback (revenue and OCF/capex/SBC from the latest fiscal year) so growth, FCF margin, SBC% and Rule of 40 share one base. Basis is recorded on `Company.basis`.
- Price data: `history(period="max", auto_adjust=True)` (split/dividend-adjusted; a raw-close ATH would be meaningless after a split). A failed price/IPO-date fetch flags `low_confidence` but never skips the name.
- yfinance returns raw dollars, Mock uses $M: fine, every screen metric is a scale-invariant ratio.
- Adding a `Company` field: update `_COMPANY_FIELDS` (cache round-trip) and populate it in every provider.
