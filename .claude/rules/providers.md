---
paths: ["etf_screen/providers.py", "etf_screen/sec.py"]
---
# Provider rules

- Network code lives here and in `etf_screen/sec.py` only.
- `DataProvider` ABC with `YFinanceProvider` (default, key-less), `FMPProvider` (optional, paid tier + `FMP_API_KEY`), `MockProvider` (tests/offline). Providers raise `DataUnavailable` rather than fabricate.
- `_extract_yf()` is a pure function (income / cash-flow / balance-sheet DataFrames + info dict) so basis selection and balance-sheet extraction are testable offline. It tolerates absent balance-sheet frames (test path → gate-passing defaults); the real `_fetch_uncached` raises if Yahoo exposes no balance sheet at all.
- Required balance-sheet lines (cash, total assets, total equity) are never assumed: absence raises `DataUnavailable`. Goodwill and total debt absent from a retrieved statement → 0, flagged (`goodwill_assumed_zero`, `debt_assumed_zero`); SBC likewise (`sbc_assumed_zero`).
- **One accounting basis per company.** TTM needs a full trailing year of both income and cash-flow quarters, read at one dated window (#71, D23): `_quarter_window()` takes the newest 4 quarter ends, anchored on the newest finite revenue column, adjacent ends 77–119 days apart (12/16-week quarters), span 250–300 days (8 quarters: 600–660). `_window_line()` reads every line at those dates (±10 days) and never returns a partial list. The year-ago share count comes from the 350–380-day column. A revenue / net income / OCF / capex / share-count gap moves the whole company to the annual basis (`quarterly <line> missing for <date>`). An SBC gap follows the #58 rule below, and an EBIT gap stays advisory (NaN ROIC, dated note). FMP's positional sums must form the same window, else `DataUnavailable` with the date. Yahoo caps quarterly statements at ~5 columns (observed 2026-07), so the prior-year TTM is usually unpublished. Growth (#67, D22): an 8-quarter true-prior-TTM sum wins when it exists. Next, a latest FY ending within 10 days of the newest quarter is the TTM itself: `revenue_ttm_prior` = prior FY. Otherwise, if the two newest FYs are 350–380d apart and the FY ended ≤ 300 days before the newest quarter, growth is the mean of FY growth and the date-aligned (350–380d) quarter YoY. That case sets `revenue_fy` / `revenue_fy_prior` with `revenue_ttm_prior` NaN, flagged `estimated TTM growth: …` in `low_confidence`. The quarter pair (`revenue_latest_q` / `revenue_year_ago_q`) stays attached on every TTM path, and is informational where a true pair wins. None of these → annual fallback (revenue and OCF/capex/SBC from the latest fiscal year) so growth, FCF margin, SBC% and Rule of 40 share one base. Fewer than 4 quarters of capex, or of SBC while the annual statement reports SBC (foreign filers, #58), also forces the annual basis, with the reason in `low_confidence`; if the annual statements cannot carry the name, a capex gap is a precise skip and an SBC gap keeps TTM with assumed-zero SBC. Basis is recorded on `Company.basis`.
- **SEC fill for D23 window gaps (#74, D25).** `_sec_fill()` (pure apart from the injected `sec.SecClient`) runs in `YFinanceProvider._fetch_uncached` before `_extract_yf`, inside the fetch deadline. `_sec_fill_targets()` finds the cells `_extract_yf` reads but Yahoo lacks:
  - flows (revenue, net income, EBIT, OCF, capex, SBC) at the 4 window dates;
  - revenue at the year-ago column too;
  - diluted shares at the newest and year-ago columns, only when a year-ago column exists and Yahoo's row is diluted (never into "Basic Average Shares").

  A cash-flow column absent at a window date counts as a gap in every cash-flow line. A row Yahoo never reports is not a target (#58 rules).

  `sec.fill_value()` takes filed 10-Q / 10-K (/A) facts with `filed` ≤ the run date; the latest filing per period wins.
  - A quarter is a direct 3-month fact, else YTD(n) − YTD(n−1) (FY − 9M for a fiscal Q4). The subtracted YTD is the version filed by the minuend's filing.
  - Shares are direct only.
  - A non-positive revenue, capex, SBC or share quarter is rejected.
  - Concepts are tried in order (`sec.LINES`); one fills only if it matches every Yahoo-reported quarter of that line within 1% (checked against the original frames).

  If a revenue, net income, share, OCF or capex gap stays unfilled, Yahoo's frames come back unchanged (`_SEC_BASIS_LINES`): the name is annual either way. Each fill adds `Q <date> <line> from SEC <method>` to `low_confidence`; `_sec_notes_kept()` drops those notes when the Company still ends up annual. The FY0 one-off check (D19) reads Yahoo's unfilled frame (`fy0_q_inc`).

  Other outcomes, each with a note, keep D23's annual fallback and never skip the name:
  - USD statements only (`financialCurrency`);
  - no gap → no request;
  - no `SEC_USER_AGENT` → no client;
  - a `SecError`, an `OSError` or malformed facts → `SEC fill failed: <cause> (gaps at <dates>)`.

  A Company carrying a failed or off note is not cached (`SEC_RETRY_PREFIXES`).

  `SecClient` details:
  - a 10 s whole-download timeout and ≤ 10 req/s;
  - marked down after a network failure or a failed tickers list, so no more requests that run;
  - a best-effort per-day `DiskCache("sec")` of trimmed facts, keyed with `sec.CACHE_SCHEMA`;
  - the user agent is never printed or put in an error.
- **Market cap is in the statement currency (D18).** `_statement_currency_cap()` converts it when `financialCurrency` (FMP: `reportedCurrency`) differs from the price currency, via the shared `DataProvider._fx_rate`; no rate → `DataUnavailable`. Converted caps set `market_cap_converted`; the valuation-pass price comes from the traded price, so it is still shown (D20).
- Price data: `history(period="max", auto_adjust=True)` (split/dividend-adjusted; a raw-close ATH would be meaningless after a split). `current_price` is the quote (`currentPrice` → `regularMarketPrice`), the last close only when neither exists (noted); FMP uses its quote price (D20). A failed price/IPO-date fetch flags `low_confidence` but never skips the name.
- yfinance returns raw dollars, Mock uses $M: fine, every screen metric is a scale-invariant ratio.
- Adding a `Company` field: update `_COMPANY_FIELDS` (cache round-trip) and populate it in every provider.
- **FY0 one-off inputs (#61, D19).** `_yf_fy0_quarter_eps()` (pure) returns `(quarters, street_basis, cause)` for the quarters after the newest annual column (≤ 15 months old, ±10-day matching). Each quarter's `epsActual` is compared with statement `Diluted EPS`: all GAAP → quarters with `Normalized Income` / `Diluted Average Shares`; all adjusted → no check, no note; mixed or self-contradicting statements → a cause. US issuers with statement = estimate = price currency only; never a partial sum or a derived normalized income. `screen.fy0_one_off_check()` decides.
