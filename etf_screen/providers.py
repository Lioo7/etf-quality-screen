"""Data sourcing layer — the only place that touches the network.

A :class:`DataProvider` turns a ticker into a :class:`~etf_screen.screen.Company`
of raw fundamentals. Providers **raise** :class:`DataUnavailable` when a required
field cannot be sourced; they never fabricate or silently default a value.

Three implementations:

* :class:`YFinanceProvider` — the free, key-less default.
* :class:`FMPProvider` — optional upgrade for those with a paid FMP REST tier.
* :class:`MockProvider` — canned data for offline runs and tests.

A note on units: ratios in the screen (growth %, margins, P/S, dilution) are
scale-invariant, so a provider may return raw dollars or millions as long as it
is internally consistent for a given company. ``MockProvider`` uses $M to match
the acceptance fixtures; ``YFinanceProvider``/``FMPProvider`` return raw values.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import asdict

from .cache import DiskCache
from .screen import AnnualPeriod, Company

# Fields persisted to / restored from the disk cache.
_COMPANY_FIELDS = (
    "ticker", "revenue_ttm", "revenue_ttm_prior", "ocf_ttm", "capex_ttm",
    "sbc_ttm", "diluted_shares_now", "diluted_shares_prior", "market_cap",
    "forward_pe", "forward_eps_growth", "net_income_ttm",
    "total_debt", "cash_and_equivalents", "goodwill", "total_assets",
    "operating_income", "total_equity", "goodwill_assumed_zero", "debt_assumed_zero",
    "low_confidence", "name", "basis",
    "sbc_assumed_zero", "overridden_fields", "sector", "industry", "history",
)


class DataUnavailable(Exception):
    """Raised when a required fundamental cannot be sourced for a ticker."""


class DataProvider(ABC):
    """Abstract base for all data sources."""

    name: str = "abstract"

    def __init__(self, cache: DiskCache | None = None):
        self._cache = cache

    @abstractmethod
    def _fetch_uncached(self, ticker: str) -> Company:
        """Fetch raw fundamentals for ``ticker`` or raise :class:`DataUnavailable`."""

    def fetch(self, ticker: str) -> Company:
        """Return a Company for ``ticker``, using the disk cache when available."""
        if self._cache is not None:
            cached = self._cache.get(f"{self.name}_{ticker}")
            if cached is not None:
                # Only pass keys present in the entry so older caches fall back
                # to the dataclass defaults for newly added fields.
                return Company(**{k: cached[k] for k in _COMPANY_FIELDS if k in cached})
        company = self._fetch_uncached(ticker)
        if self._cache is not None:
            self._cache.set(f"{self.name}_{ticker}", asdict(company))
        return company


# ---------------------------------------------------------------------------
# Mock
# ---------------------------------------------------------------------------
class MockProvider(DataProvider):
    """Serves canned :class:`Company` objects; raises for unknown tickers."""

    name = "mock"

    #: A tiny, hand-built universe mirroring the acceptance-test archetypes.
    #: DURABLE/DECEL/NEWLY/BURN exercise the consistency gate; ROLLUP/LEVERED
    #: exercise the balance-sheet gates (goodwill / leverage). Every entry carries
    #: current-snapshot balance-sheet lines so a ``--provider mock`` run drives the
    #: full Task-3 gate set.
    DATA: dict[str, Company] = {
        "MSFT": Company("MSFT", 245000, 212000, 118000, 28000, 10000,
                        7430, 7450, 3100000, 32.0, 14.0, 88000,
                        total_debt=97000, cash_and_equivalents=75000, goodwill=67000,
                        total_assets=512000, operating_income=109000, total_equity=238000,
                        name="Microsoft Corporation", basis="TTM",
                        sector="Technology", industry="Software—Infrastructure",
                        history=[
                            AnnualPeriod("2021", 168000, 77000, 21000, 6000),
                            AnnualPeriod("2022", 198000, 89000, 24000, 7500),
                            AnnualPeriod("2023", 212000, 102000, 26000, 9000),
                            AnnualPeriod("2024", 245000, 118000, 28000, 10000),
                        ]),
        "GOOGL": Company("GOOGL", 328000, 283000, 110000, 32000, 22000,
                         12200, 12500, 2100000, 21.0, 16.0, 74000,
                         total_debt=13000, cash_and_equivalents=110000, goodwill=30000,
                         total_assets=450000, operating_income=110000, total_equity=310000,
                         name="Alphabet Inc.", basis="TTM",
                         sector="Communication Services",
                         industry="Internet Content & Information",
                         history=[
                             AnnualPeriod("2021", 257000, 91000, 24000, 16000),
                             AnnualPeriod("2022", 283000, 99000, 31000, 19000),
                             AnnualPeriod("2023", 307000, 102000, 32000, 20000),
                             AnnualPeriod("2024", 328000, 110000, 32000, 22000),
                         ]),
        # Passes snapshot Track B; clears the gate (adj FCF > 0 every year).
        # GAAP-unprofitable (negative EBIT) -> ROIC gate bypassed.
        "CRWD": Company("CRWD", 3500, 2400, 1100, 120, 500, 245, 235,
                        80000, None, 30.0, -150,
                        total_debt=700, cash_and_equivalents=3500, goodwill=150,
                        total_assets=6500, operating_income=-50, total_equity=3000,
                        name="CrowdStrike Holdings, Inc.", basis="TTM",
                        sector="Technology", industry="Software—Infrastructure",
                        history=[
                            AnnualPeriod("2022", 1600, 500, 60, 250),
                            AnnualPeriod("2023", 2400, 800, 90, 380),
                            AnnualPeriod("2024", 3500, 1100, 120, 500),
                        ]),
        # Passes snapshot Track A and every balance-sheet gate incl. ROIC>=10%.
        "DURABLE": Company("DURABLE", 1000, 700, 400, 50, 50, 1000, 1000,
                           5000, 30.0, 20.0, 200,
                           total_debt=0, cash_and_equivalents=300, goodwill=0,
                           total_assets=1500, operating_income=200, total_equity=1000,
                           name="Durable Compounder Inc.", basis="TTM",
                           sector="Technology", industry="Software—Application",
                           history=[
                               AnnualPeriod("2021", 500, 200, 25, 25),
                               AnnualPeriod("2022", 650, 260, 30, 30),
                               AnnualPeriod("2023", 845, 340, 40, 40),
                               AnnualPeriod("2024", 1100, 440, 50, 50),
                           ]),
        # Passes snapshot Track A but a windowed year decelerates < 40 (strict FAIL).
        "DECEL": Company("DECEL", 1000, 700, 400, 50, 50, 1000, 1000,
                         5000, 30.0, 20.0, 200,
                         total_debt=0, cash_and_equivalents=300, goodwill=0,
                         total_assets=1500, operating_income=200, total_equity=1000,
                         name="Decelerator Corp.", basis="TTM",
                         sector="Technology", industry="Software—Application",
                         history=[
                             AnnualPeriod("2021", 600, 240, 30, 30),
                             AnnualPeriod("2022", 630, 240, 30, 30),
                             AnnualPeriod("2023", 800, 320, 40, 40),
                             AnnualPeriod("2024", 1000, 400, 50, 50),
                         ]),
        # Passes snapshot Track A but only 2 annual years -> INSUFFICIENT_HISTORY.
        "NEWLY": Company("NEWLY", 1000, 700, 400, 50, 50, 1000, 1000,
                         5000, 30.0, 20.0, 200,
                         total_debt=0, cash_and_equivalents=300, goodwill=0,
                         total_assets=1500, operating_income=200, total_equity=1000,
                         name="Newly Public Inc.", basis="TTM",
                         sector="Technology", industry="Software—Application",
                         history=[
                             AnnualPeriod("2023", 770, 310, 40, 40),
                             AnnualPeriod("2024", 1000, 400, 50, 50),
                         ]),
        # Passes snapshot Track B but an early year had adj FCF <= 0 -> FAIL.
        "BURN": Company("BURN", 3500, 2400, 1100, 120, 500, 245, 235,
                        70000, None, 30.0, -150,
                        total_debt=700, cash_and_equivalents=2000, goodwill=100,
                        total_assets=5000, operating_income=-50, total_equity=2500,
                        name="Cash Burner Ltd.", basis="TTM",
                        sector="Technology", industry="Software—Infrastructure",
                        history=[
                            AnnualPeriod("2022", 1600, 200, 50, 250),
                            AnnualPeriod("2023", 2400, 600, 80, 300),
                            AnnualPeriod("2024", 3500, 1100, 120, 500),
                        ]),
        # Clears snapshot Track A + the consistency gate, but goodwill is 60% of
        # assets (> 40%) -> REJECTED on the goodwill gate (serial-acquirer profile).
        "ROLLUP": Company("ROLLUP", 1000, 700, 400, 50, 50, 1000, 1000,
                          5000, 30.0, 20.0, 200,
                          total_debt=0, cash_and_equivalents=300, goodwill=1200,
                          total_assets=2000, operating_income=200, total_equity=1000,
                          name="Rollup Acquirer Inc.", basis="TTM",
                          sector="Technology", industry="Software—Application",
                          history=[
                              AnnualPeriod("2021", 500, 200, 25, 25),
                              AnnualPeriod("2022", 650, 260, 30, 30),
                              AnnualPeriod("2023", 845, 340, 40, 40),
                              AnnualPeriod("2024", 1100, 440, 50, 50),
                          ]),
        # Clears snapshot Track A + the consistency gate, but net debt is 3.7x FCF
        # (> 3.0) -> REJECTED on the leverage gate.
        "LEVERED": Company("LEVERED", 1000, 700, 400, 50, 50, 1000, 1000,
                           5000, 30.0, 20.0, 200,
                           total_debt=1500, cash_and_equivalents=200, goodwill=0,
                           total_assets=2000, operating_income=300, total_equity=1000,
                           name="Levered Compounder Inc.", basis="TTM",
                           sector="Technology", industry="Software—Application",
                           history=[
                               AnnualPeriod("2021", 500, 200, 25, 25),
                               AnnualPeriod("2022", 650, 260, 30, 30),
                               AnnualPeriod("2023", 845, 340, 40, 40),
                               AnnualPeriod("2024", 1100, 440, 50, 50),
                           ]),
    }

    def _fetch_uncached(self, ticker: str) -> Company:
        try:
            return self.DATA[ticker.upper()]
        except KeyError:
            raise DataUnavailable(f"{ticker}: no mock data") from None


# ---------------------------------------------------------------------------
# yfinance (default)
# ---------------------------------------------------------------------------
class YFinanceProvider(DataProvider):
    """Free, key-less provider backed by Yahoo Finance via the ``yfinance`` lib.

    Known limitations (handled honestly, never papered over):

    * **One accounting basis per company.** When Yahoo exposes a full trailing
      twelve months of *both* income and cash-flow quarters, every metric is
      computed on a true TTM basis. Otherwise the whole company falls back to an
      **annual** basis — revenue *and* OCF/capex/SBC all from the latest fiscal
      year — so growth, FCF margin, SBC%, and Rule of 40 share one base. The
      basis used is recorded on the Company and flagged when it is annual.
    * **Stock-based compensation may be absent.** If the cash-flow statement is
      retrieved but has no SBC line, SBC is treated as 0 and the name is flagged
      ``sbc_assumed_zero`` (utilities/staples genuinely report ~0). Only when the
      cash-flow statement cannot be retrieved at all is the name skipped.
    * Forward EPS growth derived from Yahoo's ``forwardEps``/``trailingEps`` is a
      rough proxy and is always flagged low-confidence.
    """

    name = "yfinance"

    def _fetch_uncached(self, ticker: str) -> Company:
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover - env guard
            raise DataUnavailable(
                "yfinance is not installed; run `pip install -r requirements.txt`"
            ) from exc

        tk = yf.Ticker(ticker)
        q_bal = _df(tk, "quarterly_balance_sheet", "quarterly_balancesheet")
        a_bal = _df(tk, "balance_sheet", "balancesheet")
        # Never-fabricate guard: the balance-sheet gates need a real snapshot.
        if q_bal is None and a_bal is None:
            raise DataUnavailable(f"{ticker}: balance sheet unavailable from yfinance")
        return _extract_yf(
            ticker,
            q_inc=_df(tk, "quarterly_income_stmt", "quarterly_financials"),
            a_inc=_df(tk, "income_stmt", "financials"),
            q_cf=_df(tk, "quarterly_cashflow", "quarterly_cash_flow"),
            a_cf=_df(tk, "cashflow", "cash_flow"),
            info=_info(tk),
            q_bal=q_bal, a_bal=a_bal,
        )


# Statement line labels (Yahoo varies these across versions/tickers).
_REV = ["Total Revenue", "Operating Revenue"]
_SHARES = ["Diluted Average Shares", "Diluted Shares", "Basic Average Shares"]
_OCF = ["Operating Cash Flow", "Total Cash From Operating Activities"]
_CAPEX = ["Capital Expenditure", "Capital Expenditures"]
_SBC = ["Stock Based Compensation"]
_NET_INCOME = [
    "Net Income",
    "Net Income Common Stockholders",
    "Net Income From Continuing Operation Net Minority Interest",
]
# Operating income (EBIT) from the income statement; balance-sheet snapshot lines.
_EBIT = ["Operating Income", "EBIT", "Total Operating Income As Reported"]
_TOTAL_DEBT = ["Total Debt"]
_CASH = ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"]
_GOODWILL = ["Goodwill"]
_TOTAL_ASSETS = ["Total Assets"]
_TOTAL_EQUITY = [
    "Stockholders Equity",
    "Total Equity Gross Minority Interest",
    "Common Stock Equity",
]


def _extract_yf(ticker, q_inc, a_inc, q_cf, a_cf, info, q_bal=None, a_bal=None) -> Company:
    """Build a Company from Yahoo statement frames, on a single consistent basis.

    Factored out of the provider (no network here) so the basis-selection and
    SBC-assumed-0 logic is unit-testable with hand-built DataFrames.

    Balance-sheet frames are optional here: when both are absent (the synthetic
    unit-test path) the balance-sheet fields fall back to gate-passing defaults.
    The never-fabricate guard for real runs lives in ``_fetch_uncached``, which
    raises before calling this when Yahoo exposes no balance sheet at all.
    """
    # Cash flow entirely absent -> genuinely no data -> skip the name.
    if q_cf is None and a_cf is None:
        raise DataUnavailable(f"{ticker}: cash-flow statement unavailable from yfinance")

    low_conf: list[str] = []
    rev_q = _row(q_inc, _REV)
    shares_q = _row(q_inc, _SHARES)
    ocf_q = _row(q_cf, _OCF)

    # TTM requires a full trailing year of BOTH income and cash flow.
    ttm_ok = len(rev_q) >= 8 and len(shares_q) >= 5 and len(ocf_q) >= 4

    if ttm_ok:
        basis = "TTM"
        revenue_ttm, revenue_prior = sum(rev_q[:4]), sum(rev_q[4:8])
        shares_now, shares_prior = shares_q[0], shares_q[4]
        cf = q_cf

        def amount(labels):
            vals = _row(cf, labels)
            return (sum(vals[:4]), True) if len(vals) >= 4 else (None, False)
    else:
        basis = "annual"
        rev_a = _row(a_inc, _REV)
        shares_a = _row(a_inc, _SHARES)
        if len(rev_a) < 2:
            raise DataUnavailable(f"{ticker}: insufficient revenue history from yfinance")
        if len(shares_a) < 2:
            raise DataUnavailable(f"{ticker}: no diluted share history from yfinance")
        if a_cf is None:
            raise DataUnavailable(f"{ticker}: insufficient cash-flow history from yfinance")
        revenue_ttm, revenue_prior = rev_a[0], rev_a[1]
        shares_now, shares_prior = shares_a[0], shares_a[1]
        cf = a_cf
        low_conf.append("metrics on ANNUAL basis (Yahoo exposed <8 quarters)")

        def amount(labels):
            vals = _row(cf, labels)
            return (vals[0], True) if vals else (None, False)

    ocf, ocf_found = amount(_OCF)
    capex_raw, capex_found = amount(_CAPEX)
    if not ocf_found or not capex_found:
        raise DataUnavailable(
            f"{ticker}: operating cash flow / capex unavailable from yfinance"
        )
    capex = abs(capex_raw)

    sbc_raw, sbc_found = amount(_SBC)
    sbc_assumed_zero = not sbc_found
    sbc = sbc_raw if sbc_found else 0.0
    if sbc_assumed_zero:
        low_conf.append("SBC not reported by source - assumed 0")

    # Net income gates routing -> source it on the SAME basis as revenue, and
    # raise rather than default if Yahoo doesn't expose the line.
    ni_vals = _row(q_inc if basis == "TTM" else a_inc, _NET_INCOME)
    if basis == "TTM":
        if len(ni_vals) < 4:
            raise DataUnavailable(f"{ticker}: net income unavailable from yfinance")
        net_income_ttm = sum(ni_vals[:4])
    else:
        if not ni_vals:
            raise DataUnavailable(f"{ticker}: net income unavailable from yfinance")
        net_income_ttm = ni_vals[0]

    market_cap = info.get("marketCap")
    if not market_cap:
        raise DataUnavailable(f"{ticker}: no market cap from yfinance")

    forward_pe = info.get("forwardPE")
    fwd_eps, ttm_eps = info.get("forwardEps"), info.get("trailingEps")
    if fwd_eps and ttm_eps and ttm_eps > 0:
        forward_eps_growth = (fwd_eps / ttm_eps - 1) * 100
    else:
        forward_eps_growth = 0.0
    low_conf.append("forward EPS growth is a rough Yahoo proxy")

    # Operating income (EBIT) for ROIC, on the SAME basis as revenue. Absent ->
    # NaN so ROIC is treated as not-computable (advisory), never a false reject.
    ebit_vals = _row(q_inc if basis == "TTM" else a_inc, _EBIT)
    ebit_found = len(ebit_vals) >= 4 if basis == "TTM" else bool(ebit_vals)
    if ebit_found:
        operating_income = sum(ebit_vals[:4]) if basis == "TTM" else ebit_vals[0]
    else:
        operating_income = float("nan")
        low_conf.append("operating income (EBIT) not reported - ROIC not computable")

    # Balance-sheet snapshot (most-recent column). Cash / total assets / total
    # equity are REQUIRED; goodwill and total debt get the assumed-zero treatment.
    bal = q_bal if q_bal is not None else a_bal
    if bal is None:
        total_debt = cash = goodwill = 0.0
        total_assets = total_equity = 1000.0
        goodwill_assumed_zero = debt_assumed_zero = False
    else:
        cash = _bal_amount(bal, _CASH)
        total_assets = _bal_amount(bal, _TOTAL_ASSETS)
        total_equity = _bal_amount(bal, _TOTAL_EQUITY)
        if cash is None or total_assets is None or total_equity is None:
            raise DataUnavailable(
                f"{ticker}: balance sheet missing cash / total assets / total equity"
            )
        gw = _bal_amount(bal, _GOODWILL)
        goodwill_assumed_zero = gw is None
        goodwill = gw if gw is not None else 0.0
        if goodwill_assumed_zero:
            low_conf.append("goodwill not reported by source - assumed 0")
        td = _bal_amount(bal, _TOTAL_DEBT)
        debt_assumed_zero = td is None
        total_debt = td if td is not None else 0.0
        if debt_assumed_zero:
            low_conf.append("total debt not reported by source - assumed 0")

    name = info.get("shortName") or info.get("longName") or ticker

    # Sector is informational context only; never guess it. Absent -> "Unknown".
    sector = info.get("sector") or "Unknown"
    industry = info.get("industry") or ""
    if sector == "Unknown":
        low_conf.append("sector not reported by source")

    return Company(
        ticker=ticker, revenue_ttm=revenue_ttm, revenue_ttm_prior=revenue_prior,
        ocf_ttm=ocf, capex_ttm=capex, sbc_ttm=sbc,
        diluted_shares_now=shares_now, diluted_shares_prior=shares_prior,
        market_cap=float(market_cap),
        forward_pe=float(forward_pe) if forward_pe else None,
        forward_eps_growth=forward_eps_growth, net_income_ttm=net_income_ttm,
        total_debt=float(total_debt), cash_and_equivalents=float(cash),
        goodwill=float(goodwill), total_assets=float(total_assets),
        operating_income=float(operating_income), total_equity=float(total_equity),
        goodwill_assumed_zero=goodwill_assumed_zero, debt_assumed_zero=debt_assumed_zero,
        low_confidence=low_conf,
        name=name, basis=basis, sbc_assumed_zero=sbc_assumed_zero,
        sector=sector, industry=industry,
        history=_annual_history(a_inc, a_cf),
    )


def _bal_amount(bal, labels):
    """Most-recent balance-sheet value for the first matching label, or None."""
    vals = _row(bal, labels)
    return vals[0] if vals else None


def _df(tk, *attrs):
    """Return the first non-empty DataFrame among ``attrs`` (or None)."""
    for attr in attrs:
        try:
            df = getattr(tk, attr)
        except Exception:  # pragma: no cover - yfinance network/parse errors
            continue
        if df is not None and getattr(df, "empty", True) is False:
            return df
    return None


def _row(df, labels):
    """Return a DataFrame row's values (most-recent first) for the first matching label."""
    if df is None:
        return []
    cols = sorted(df.columns, reverse=True)  # newest period first
    for label in labels:
        if label in df.index:
            series = df.loc[label, cols]
            return [float(v) for v in series if v is not None and not _isnan(v)]
    return []


def _isnan(v) -> bool:
    try:
        return v != v  # NaN is the only value not equal to itself
    except Exception:  # pragma: no cover
        return False


def _labeled_row(df, labels) -> dict:
    """Map each period-end column -> value for the first matching label (finite only).

    Unlike :func:`_row`, this keeps the column labels so callers can align line
    items across the income and cash-flow frames by fiscal year.
    """
    if df is None:
        return {}
    for label in labels:
        if label in df.index:
            out = {}
            for col in df.columns:
                v = df.loc[label, col]
                if v is not None and not _isnan(v):
                    out[col] = float(v)
            return out
    return {}


def _fiscal_label(col) -> str:
    """Human-readable year label for a period-end column (a Timestamp or string)."""
    year = getattr(col, "year", None)
    return str(year) if year is not None else str(col)[:10]


def _annual_history(a_inc, a_cf) -> list:
    """Build a contiguous run of COMPLETE annual periods, oldest -> newest.

    Revenue comes from the income frame; OCF/capex/SBC from the cash-flow frame —
    aligned by fiscal-year column. Walking newest -> oldest, leading incomplete
    years (e.g. cash flow not yet filed) are skipped, then the run starts at the
    most recent complete year and TRUNCATES at the first backward gap. A skipped
    middle year would misalign growth, so we stop rather than skip. Lines are
    never fabricated or zero-filled — a missing line just ends the history.
    """
    if a_inc is None or a_cf is None:
        return []
    rev = _labeled_row(a_inc, _REV)
    ocf = _labeled_row(a_cf, _OCF)
    capex = _labeled_row(a_cf, _CAPEX)
    sbc = _labeled_row(a_cf, _SBC)
    cols = sorted(set(a_inc.columns) | set(a_cf.columns), reverse=True)
    periods: list[AnnualPeriod] = []
    started = False
    for col in cols:
        complete = col in rev and col in ocf and col in capex and col in sbc
        if complete:
            started = True
            periods.append(AnnualPeriod(
                fiscal_label=_fiscal_label(col),
                revenue=rev[col], ocf=ocf[col],
                capex=abs(capex[col]), sbc=sbc[col],
            ))
        elif started:
            break  # first backward gap after the run begins -> truncate
        # else: leading incomplete year -> skip and keep looking for the run start
    periods.reverse()  # oldest -> newest
    return periods


def _info(tk) -> dict:
    try:
        return tk.info or {}
    except Exception:  # pragma: no cover - yfinance can raise on .info
        return {}


# ---------------------------------------------------------------------------
# FMP (optional upgrade)
# ---------------------------------------------------------------------------
class FMPProvider(DataProvider):
    """Financial Modeling Prep REST provider — optional, requires a paid tier.

    Nothing in the default workflow needs this. It is only instantiated when the
    user explicitly selects ``--provider fmp`` and exports ``FMP_API_KEY``. On a
    ``403`` it raises a clear message naming the endpoint and the tier required,
    rather than silently filling gaps.
    """

    name = "fmp"
    BASE = "https://financialmodelingprep.com/api/v3"

    def __init__(self, api_key: str | None = None, cache: DiskCache | None = None):
        super().__init__(cache)
        self.api_key = api_key or os.environ.get("FMP_API_KEY")
        if not self.api_key:
            raise DataUnavailable(
                "FMP selected but FMP_API_KEY is not set. Export it, or use the "
                "default --provider yfinance which needs no key."
            )

    def _get(self, path: str, **params):
        import requests

        params["apikey"] = self.api_key
        resp = requests.get(f"{self.BASE}/{path}", params=params, timeout=30)
        if resp.status_code == 403:
            raise DataUnavailable(
                f"FMP 403 on '{path}': your API tier lacks access to this "
                f"endpoint. Fundamentals need a Starter/Premium plan."
            )
        resp.raise_for_status()
        return resp.json()

    def _fetch_uncached(self, ticker: str) -> Company:
        inc = self._get(f"income-statement/{ticker}", period="quarter", limit=8)
        cf = self._get(f"cash-flow-statement/{ticker}", period="quarter", limit=8)
        if len(inc) < 8 or len(cf) < 4:
            raise DataUnavailable(f"{ticker}: FMP returned too few quarters")

        revenue_ttm = sum(q["revenue"] for q in inc[:4])
        revenue_prior = sum(q["revenue"] for q in inc[4:8])
        shares_now = sum(q["weightedAverageShsOutDil"] for q in inc[:4])
        shares_prior = sum(q["weightedAverageShsOutDil"] for q in inc[4:8])
        if any(q.get("netIncome") is None for q in inc[:4]):
            raise DataUnavailable(f"{ticker}: net income unavailable from FMP")
        net_income_ttm = sum(q["netIncome"] for q in inc[:4])

        ocf = sum(q["operatingCashFlow"] for q in cf[:4])
        capex = abs(sum(q["capitalExpenditure"] for q in cf[:4]))
        sbc_found = any(q.get("stockBasedCompensation") is not None for q in cf[:4])
        sbc = sum(q.get("stockBasedCompensation") or 0 for q in cf[:4])

        low_conf: list[str] = []
        if not sbc_found:
            low_conf.append("SBC not reported by source - assumed 0")

        # Operating income (EBIT) for ROIC; absent -> NaN (ROIC not computable).
        if any(q.get("operatingIncome") is None for q in inc[:4]):
            operating_income = float("nan")
            low_conf.append("operating income (EBIT) not reported - ROIC not computable")
        else:
            operating_income = sum(q["operatingIncome"] for q in inc[:4])

        quote = self._get(f"quote/{ticker}")[0]
        market_cap = quote["marketCap"]
        price, ttm_eps = quote.get("price"), quote.get("eps")
        name = quote.get("name") or ticker

        # Sector/industry from the company profile (informational only).
        sector, industry = "Unknown", ""
        try:
            profile = self._get(f"profile/{ticker}")
            if profile:
                sector = profile[0].get("sector") or "Unknown"
                industry = profile[0].get("industry") or ""
        except DataUnavailable:
            pass  # profile is optional context; never block a name on it
        if sector == "Unknown":
            low_conf.append("sector not reported by source")

        forward_pe, forward_eps_growth = None, 0.0
        est = self._get(f"analyst-estimates/{ticker}", period="annual")
        fwd_eps = _nearest_future_eps(est)
        if fwd_eps and price:
            forward_pe = price / fwd_eps
            if ttm_eps and ttm_eps > 0:
                forward_eps_growth = (fwd_eps / ttm_eps - 1) * 100

        # Balance-sheet snapshot for the balance-sheet gates. Cash / total assets /
        # total equity are REQUIRED; goodwill and total debt are assumed-zero.
        bal = self._get(f"balance-sheet-statement/{ticker}", period="quarter", limit=1)
        if not bal:
            raise DataUnavailable(f"{ticker}: balance sheet unavailable from FMP")
        b = bal[0]
        cash = b.get("cashAndCashEquivalents")
        if cash is None:
            cash = b.get("cashAndShortTermInvestments")
        total_assets = b.get("totalAssets")
        total_equity = b.get("totalStockholdersEquity")
        if total_equity is None:
            total_equity = b.get("totalEquity")
        if cash is None or total_assets is None or total_equity is None:
            raise DataUnavailable(
                f"{ticker}: balance sheet missing cash / total assets / total equity"
            )
        gw = b.get("goodwill")
        goodwill_assumed_zero = gw is None
        goodwill = gw if gw is not None else 0.0
        if goodwill_assumed_zero:
            low_conf.append("goodwill not reported by source - assumed 0")
        td = b.get("totalDebt")
        debt_assumed_zero = td is None
        total_debt = td if td is not None else 0.0
        if debt_assumed_zero:
            low_conf.append("total debt not reported by source - assumed 0")

        # Annual history for the consistency gate (best-effort; never blocks a name).
        inc_a = self._get(f"income-statement/{ticker}", period="annual", limit=5)
        cf_a = self._get(f"cash-flow-statement/{ticker}", period="annual", limit=5)

        return Company(
            ticker=ticker, revenue_ttm=revenue_ttm, revenue_ttm_prior=revenue_prior,
            ocf_ttm=ocf, capex_ttm=capex, sbc_ttm=sbc,
            diluted_shares_now=shares_now, diluted_shares_prior=shares_prior,
            market_cap=market_cap, forward_pe=forward_pe,
            forward_eps_growth=forward_eps_growth, net_income_ttm=net_income_ttm,
            total_debt=float(total_debt), cash_and_equivalents=float(cash),
            goodwill=float(goodwill), total_assets=float(total_assets),
            operating_income=float(operating_income), total_equity=float(total_equity),
            goodwill_assumed_zero=goodwill_assumed_zero, debt_assumed_zero=debt_assumed_zero,
            low_confidence=low_conf,
            name=name, basis="TTM", sbc_assumed_zero=not sbc_found,
            sector=sector, industry=industry,
            history=_fmp_annual_history(inc_a, cf_a),
        )


def _fmp_annual_history(inc_a: list[dict], cf_a: list[dict]) -> list[AnnualPeriod]:
    """Contiguous run of complete annual periods from FMP statements, oldest -> newest.

    Income and cash-flow records are aligned by ``calendarYear``; walking newest
    -> oldest, the run truncates at the first year missing any required line (never
    skipping a middle year, which would misalign growth). No line is zero-filled.
    """
    cf_by_year = {q.get("calendarYear"): q for q in cf_a}
    periods: list[AnnualPeriod] = []
    started = False
    for q in inc_a:  # FMP returns most-recent first
        year = q.get("calendarYear")
        cq = cf_by_year.get(year)
        rev = q.get("revenue")
        complete = (
            cq is not None and rev is not None
            and cq.get("operatingCashFlow") is not None
            and cq.get("capitalExpenditure") is not None
            and cq.get("stockBasedCompensation") is not None
        )
        if complete:
            started = True
            periods.append(AnnualPeriod(
                fiscal_label=str(year),
                revenue=float(rev),
                ocf=float(cq["operatingCashFlow"]),
                capex=abs(float(cq["capitalExpenditure"])),
                sbc=float(cq["stockBasedCompensation"]),
            ))
        elif started:
            break  # first backward gap -> truncate
    periods.reverse()  # oldest -> newest
    return periods


def _nearest_future_eps(estimates: list[dict]) -> float | None:
    """Pick the nearest future-dated ``estimatedEpsAvg`` from FMP estimates."""
    from datetime import date

    today = date.today().isoformat()
    future = [e for e in estimates if e.get("date", "") >= today and e.get("estimatedEpsAvg")]
    if not future:
        return None
    nearest = min(future, key=lambda e: e["date"])
    return nearest["estimatedEpsAvg"]


PROVIDERS = {
    "yfinance": YFinanceProvider,
    "fmp": FMPProvider,
    "mock": MockProvider,
}
