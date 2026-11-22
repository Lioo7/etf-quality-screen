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
import threading
from abc import ABC, abstractmethod
from dataclasses import asdict
from datetime import date, timedelta

from .cache import DiskCache
from .screen import EPS_DECLINE_NOTE, AnnualPeriod, Company

# Fields persisted to / restored from the disk cache.
_COMPANY_FIELDS = (
    "ticker", "revenue_ttm", "revenue_ttm_prior",
    "revenue_latest_q", "revenue_year_ago_q", "ocf_ttm", "capex_ttm",
    "sbc_ttm", "diluted_shares_now", "diluted_shares_prior", "market_cap",
    "forward_pe", "forward_eps_growth", "net_income_ttm",
    "total_debt", "cash_and_equivalents", "goodwill", "total_assets",
    "operating_income", "total_equity", "goodwill_assumed_zero", "debt_assumed_zero",
    "low_confidence", "name", "basis",
    "sbc_assumed_zero", "overridden_fields", "sector", "industry", "ipo_date",
    "current_price", "ath_price", "price_3mo_ago", "price_history_years",
    "eps_fy0", "eps_fy1", "estimate_currency", "fy1_analysts",
    "market_cap_converted", "fy0_quarter_eps", "history",
)

# Bumped when a cached field changes meaning, so a same-day entry written by older
# code is refetched instead of misread. v2 (#6): forward_eps_growth None = missing
# (the old 0.0 sentinel would now read as negative consensus growth -> REJECTED).
# v3 (#46): pre-#46 entries hold an unconverted (untestable) forward_pe for
# estimates in another currency than the price.
# v4 (#53, #58): v4 was written today by two unmerged branches (the EPS-decline note
# and statement-currency market cap / annual basis), so same-day v4 entries may come
# from either code. v5 (#53 + #58): entries carry the note, the converted market cap
# and the annual-basis fallback. v6 (#61): entries carry fy0_quarter_eps; a v5 entry
# would read as "check unavailable" and miss a one-off-distorted FY0.
_CACHE_SCHEMA = "v6"

#: Added to ``low_confidence`` when a market cap was currency-converted (#58): the
#: unlock prices use market cap / shares and are not shown until #62 reworks them.
UNLOCK_CONVERTED_NOTE = "unlock price not shown: currency-converted market cap (#62)"


class DataUnavailable(Exception):
    """Raised when a required fundamental cannot be sourced for a ticker."""


DEFAULT_FETCH_TIMEOUT = 60.0  # seconds per uncached ticker fetch (--fetch-timeout)


class DataProvider(ABC):
    """Abstract base for all data sources."""

    name: str = "abstract"

    def __init__(self, cache: DiskCache | None = None,
                 fetch_timeout: float = DEFAULT_FETCH_TIMEOUT):
        self._cache = cache
        self.fetch_timeout = fetch_timeout  # <= 0 disables the deadline
        self._fx_cache: dict[tuple[str, str], tuple[float, str] | None] = {}

    def _fx_rate(self, base: str, quote: str) -> tuple[float, str] | None:
        """``(rate, as_of)`` for 1 ``base`` in ``quote`` from Yahoo (``EURUSD=X``).

        The newest valid daily close, fetched once per pair per run (this provider
        instance). Any failure or an empty series -> None: the caller skips or marks
        the metric untestable, never a guessed rate. Shared by both live providers
        (FMP has no FX endpoint on the tiers this project targets).
        """
        key = (base, quote)
        if key not in self._fx_cache:
            self._fx_cache[key] = _yf_fx_close(f"{base}{quote}=X")
        return self._fx_cache[key]

    @abstractmethod
    def _fetch_uncached(self, ticker: str) -> Company:
        """Fetch raw fundamentals for ``ticker`` or raise :class:`DataUnavailable`."""

    def _fetch_with_deadline(self, ticker: str) -> Company:
        """Run ``_fetch_uncached`` under a wall-clock deadline.

        yfinance statement properties take no timeout, so one stuck Yahoo call
        could stall a whole run. The fetch runs on a *daemon* thread: on timeout
        it is abandoned (Python cannot kill a thread) and, being a daemon, it never
        blocks interpreter exit, unlike ``concurrent.futures`` workers, which are
        joined at exit.
        """
        if not self.fetch_timeout or self.fetch_timeout <= 0:
            return self._fetch_uncached(ticker)
        outcome: dict = {}

        def work() -> None:
            try:
                outcome["company"] = self._fetch_uncached(ticker)
            except BaseException as exc:  # re-raised on the caller's thread
                outcome["error"] = exc

        worker = threading.Thread(target=work, name=f"fetch-{ticker}", daemon=True)
        worker.start()
        worker.join(self.fetch_timeout)
        if worker.is_alive():
            raise DataUnavailable(f"timed out after {self.fetch_timeout:g} s")
        if "error" in outcome:
            raise outcome["error"]
        return outcome["company"]

    def fetch(self, ticker: str) -> Company:
        """Return a Company for ``ticker``, using the disk cache when available.

        Cache hits return immediately; only the uncached fetch is deadline-bound.
        """
        if self._cache is not None:
            cached = self._cache.get(self._cache_key(ticker))
            if cached is not None:
                # Only pass keys present in the entry so older caches fall back
                # to the dataclass defaults for newly added fields.
                return Company(**{k: cached[k] for k in _COMPANY_FIELDS if k in cached})
        company = self._fetch_with_deadline(ticker)
        if self._cache is not None:
            self._cache.set(self._cache_key(ticker), asdict(company))
        return company

    def _cache_key(self, ticker: str) -> str:
        return f"{self.name}_{_CACHE_SCHEMA}_{ticker}"


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
        # GAAP-unprofitable (negative EBIT) -> ROIC gate bypassed. Price profile
        # is parabolic: +58% in 3 months, 5% off the ATH -> the HOT timing tag.
        "CRWD": Company("CRWD", 3500, 2400, 1100, 120, 500, 245, 235,
                        80000, None, 30.0, -150,
                        total_debt=700, cash_and_equivalents=3500, goodwill=150,
                        total_assets=6500, operating_income=-50, total_equity=3000,
                        current_price=95.0, ath_price=100.0, price_3mo_ago=60.0,
                        price_history_years=5.0,
                        name="CrowdStrike Holdings, Inc.", basis="TTM",
                        sector="Technology", industry="Software—Infrastructure",
                        history=[
                            AnnualPeriod("2022", 1600, 500, 60, 250),
                            AnnualPeriod("2023", 2400, 800, 90, 380),
                            AnnualPeriod("2024", 3500, 1100, 120, 500),
                        ]),
        # Passes snapshot Track A and every balance-sheet gate incl. ROIC>=10%.
        # History also carries EBIT/balance-sheet lines so the ROIC-history
        # annotation has 4 usable years (median ~18%, but < ROIC_DURABLE_YEARS
        # so `durable` stays False -- MOAT below is the durable-moat example).
        "DURABLE": Company("DURABLE", 1000, 700, 400, 50, 50, 1000, 1000,
                           5000, 30.0, 20.0, 200,
                           total_debt=0, cash_and_equivalents=300, goodwill=0,
                           total_assets=1500, operating_income=200, total_equity=1000,
                           current_price=90.0, ath_price=100.0, price_3mo_ago=85.0,
                           price_history_years=8.0,
                           name="Durable Compounder Inc.", basis="TTM",
                           sector="Technology", industry="Software—Application",
                           history=[
                               AnnualPeriod("2021", 500, 200, 25, 25,
                                            operating_income=110, total_debt=0,
                                            cash_and_equivalents=150, total_equity=500),
                               AnnualPeriod("2022", 650, 260, 30, 30,
                                            operating_income=140, total_debt=0,
                                            cash_and_equivalents=200, total_equity=650),
                               AnnualPeriod("2023", 845, 340, 40, 40,
                                            operating_income=170, total_debt=0,
                                            cash_and_equivalents=250, total_equity=820),
                               AnnualPeriod("2024", 1100, 440, 50, 50,
                                            operating_income=200, total_debt=0,
                                            cash_and_equivalents=300, total_equity=1000),
                           ]),
        # Wide-moat example for the ROIC-history annotation: ROIC >= 15% every
        # year across 6 years -> roic_durable=True (median comfortably >= 15%).
        # Also carries stable ~75% gross margins across all 6 years -> the
        # pricing_power annotation (gm_latest >= 40%, stddev < 5pp, >= 5 years).
        "MOAT": Company("MOAT", 2000, 1500, 900, 100, 80, 1000, 1000,
                        12000, 28.0, 18.0, 400,
                        total_debt=0, cash_and_equivalents=600, goodwill=50,
                        total_assets=3000, operating_income=440, total_equity=2000,
                        name="Wide Moat Compounder Inc.", basis="TTM",
                        sector="Technology", industry="Software—Application",
                        history=[
                            AnnualPeriod("2019", 800, 320, 40, 30, cogs=200,
                                         operating_income=170, total_debt=0,
                                         cash_and_equivalents=200, total_equity=850),
                            AnnualPeriod("2020", 1000, 400, 50, 40, cogs=250,
                                         operating_income=220, total_debt=0,
                                         cash_and_equivalents=280, total_equity=1050),
                            AnnualPeriod("2021", 1250, 500, 60, 50, cogs=310,
                                         operating_income=270, total_debt=0,
                                         cash_and_equivalents=350, total_equity=1300),
                            AnnualPeriod("2022", 1500, 600, 70, 60, cogs=375,
                                         operating_income=320, total_debt=0,
                                         cash_and_equivalents=430, total_equity=1550),
                            AnnualPeriod("2023", 1750, 700, 80, 70, cogs=435,
                                         operating_income=380, total_debt=0,
                                         cash_and_equivalents=510, total_equity=1780),
                            AnnualPeriod("2024", 2000, 900, 100, 80, cogs=500,
                                         operating_income=440, total_debt=0,
                                         cash_and_equivalents=600, total_equity=2000),
                        ]),
        # Would clear snapshot Track A, but listed ~4 months ago (dynamic date so
        # the demo never ages out) -> RECENT_IPO under the default 12-mo seasoning.
        "FRESH": Company("FRESH", 1000, 700, 400, 50, 50, 1000, 1000,
                         5000, 30.0, 20.0, 200,
                         total_debt=0, cash_and_equivalents=300, goodwill=0,
                         total_assets=1500, operating_income=200, total_equity=1000,
                         name="Fresh Listing Inc.", basis="TTM",
                         sector="Technology", industry="Software—Application",
                         ipo_date=(date.today() - timedelta(days=120)).isoformat(),
                         history=[
                             AnnualPeriod("2023", 770, 310, 40, 40),
                             AnnualPeriod("2024", 1000, 400, 50, 50),
                         ]),
        # Clears snapshot Track A + every balance-sheet gate, but cumulative 3-yr
        # net income (1200) badly outruns cumulative adjusted FCF (800, ratio
        # 0.67 < 0.8) -- the classic "profits that never become cash" red flag.
        "LEAKY": Company("LEAKY", 1000, 700, 400, 50, 50, 1000, 1000,
                         5000, 30.0, 20.0, 200,
                         total_debt=0, cash_and_equivalents=300, goodwill=0,
                         total_assets=1500, operating_income=200, total_equity=1000,
                         name="Leaky Earnings Corp.", basis="TTM",
                         sector="Technology", industry="Software—Application",
                         history=[
                             AnnualPeriod("2021", 500, 200, 25, 25, net_income=400),
                             AnnualPeriod("2022", 650, 260, 30, 30, net_income=400),
                             AnnualPeriod("2023", 845, 340, 40, 40, net_income=400),
                             AnnualPeriod("2024", 1100, 440, 50, 50, net_income=400),
                         ]),
        # The Keyence archetype: profitable, every testable filter passes, but no
        # forward estimates exist (unsponsored ADR) -> PEG is UNTESTABLE, not
        # failed -> NEEDS_DATA with the overrides.json hint (v0.2.1 Fix 3).
        "OPAQUE": Company("OPAQUE", 1000, 700, 400, 50, 50, 1000, 1000,
                          5000, None, None, 200,
                          total_debt=0, cash_and_equivalents=300, goodwill=0,
                          total_assets=1500, operating_income=200, total_equity=1000,
                          name="Opaque ADR Co.", basis="TTM",
                          sector="Industrials", industry="Specialty Machinery",
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
    * PEG inputs come from the ``earnings_estimate`` consensus table (D1). A failed
      estimates request leaves PEG untestable (NEEDS_DATA path), never skips the name.
      Estimates in another currency than the price are converted at a same-run
      Yahoo FX rate (#46), cross-checked against Yahoo's ``forwardPE``.
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
        # Split/dividend-adjusted closes for the informational entry-timing
        # annotations. auto_adjust=True is deliberate: raw closes would make the
        # ATH of any splitter meaningless. A failed price fetch never skips the
        # name -- timing is informational, the fields just stay None (N/A).
        try:
            closes = tk.history(period="max", auto_adjust=True)["Close"]
        except Exception:
            closes = None
        return _extract_yf(
            ticker,
            q_inc=_df(tk, "quarterly_income_stmt", "quarterly_financials"),
            a_inc=_df(tk, "income_stmt", "financials"),
            q_cf=_df(tk, "quarterly_cashflow", "quarterly_cash_flow"),
            a_cf=_df(tk, "cashflow", "cash_flow"),
            info=_info(tk),
            q_bal=q_bal, a_bal=a_bal, closes=closes,
            estimates=_yf_estimates(tk), fx_lookup=self._fx_rate,
            revenue_estimates=_yf_revenue_estimates(tk),
            earnings_history=_yf_earnings_history(tk),
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
# Cost of revenue, for the per-year gross-margin annotation (banks/insurers
# typically don't report this line -> gross margin is N/A for them, never guessed).
_COGS = ["Cost Of Revenue", "Reconciled Cost Of Revenue"]


def _extract_yf(ticker, q_inc, a_inc, q_cf, a_cf, info,
                q_bal=None, a_bal=None, closes=None, estimates=None,
                fx_lookup=None, revenue_estimates=None, earnings_history=None) -> Company:
    """Build a Company from Yahoo statement frames, on a single consistent basis.

    Factored out of the provider (no network here) so the basis-selection and
    SBC-assumed-0 logic is unit-testable with hand-built DataFrames.

    Balance-sheet frames are optional here: when both are absent (the synthetic
    unit-test path) the balance-sheet fields fall back to gate-passing defaults.
    The never-fabricate guard for real runs lives in ``_fetch_uncached``, which
    raises before calling this when Yahoo exposes no balance sheet at all.

    ``fx_lookup(base, quote) -> (rate, as_of) | None`` is called only when the
    estimate currency differs from the price currency (#46); None -> no rate.
    """
    # Cash flow entirely absent -> genuinely no data -> skip the name.
    if q_cf is None and a_cf is None:
        raise DataUnavailable(f"{ticker}: cash-flow statement unavailable from yfinance")

    low_conf: list[str] = []
    rev_q = _row(q_inc, _REV)
    shares_q = _row(q_inc, _SHARES)
    ocf_q = _row(q_cf, _OCF)

    # TTM requires a full trailing year of BOTH income and cash flow. Yahoo has
    # capped quarterly statements at ~5 columns (observed 2026-07: MSFT/AAPL/NVDA
    # all expose exactly 5 income quarters), so the prior-year TTM (quarters
    # 5-8) is usually unpublished. Growth then comes from the newest quarter vs
    # its date-aligned year-ago counterpart — both raw sourced lines, never an
    # estimate. The 8-quarter true-prior-TTM path is kept for when it exists.
    full_prior = len(rev_q) >= 8
    rev_latest_q, rev_year_ago_q = _yoy_quarter_pair(_labeled_row(q_inc, _REV))
    yoy_growth_ok = rev_latest_q is not None and rev_year_ago_q > 0
    ttm_ok = (len(rev_q) >= 4 and len(shares_q) >= 5 and len(ocf_q) >= 4
              and (full_prior or yoy_growth_ok))

    # Foreign private issuers (20-F / 6-K) often expose quarterly OCF but no quarterly
    # capex or SBC, with complete annual statements (#58). A TTM basis would then
    # skip the name on capex, or assume SBC = 0 while the annual statement shows it.
    # Either gap moves the WHOLE company to the annual basis, never a mix.
    annual_reason = None
    if ttm_ok and len(_row(q_cf, _CAPEX)) < 4:
        annual_reason = "quarterly capex not reported by source"  # annual, or a precise skip
    elif (ttm_ok and len(_row(q_cf, _SBC)) < 4 and _labeled_row(a_cf, _SBC)
          and _annual_usable(a_inc, a_cf)):
        # Only when the annual statements can carry the name: otherwise keep TTM with
        # the flagged assumed-zero SBC rather than turn a screenable name into a skip.
        annual_reason = "quarterly SBC not reported by source"
    if annual_reason:
        ttm_ok = False

    if ttm_ok:
        basis = "TTM"
        revenue_ttm = sum(rev_q[:4])
        if full_prior:
            revenue_prior = sum(rev_q[4:8])
            rev_latest_q = rev_year_ago_q = None  # true TTM pair wins
        else:
            revenue_prior = float("nan")  # not summable -> quarterly YoY pair
            low_conf.append(
                "growth from latest-quarter YoY (source exposes <8 quarters; "
                "prior-year TTM not summable)"
            )
        shares_now, shares_prior = shares_q[0], shares_q[4]
        cf = q_cf

        def amount(labels):
            vals = _row(cf, labels)
            return (sum(vals[:4]), True) if len(vals) >= 4 else (None, False)
    else:
        rev_latest_q = rev_year_ago_q = None  # annual basis: TTM pair only
        basis = "annual"
        rev_a = _row(a_inc, _REV)
        shares_a = _row(a_inc, _SHARES)
        if len(rev_a) < 2:
            raise DataUnavailable(
                f"{ticker}: annual revenue history too short ({len(rev_a)} year(s)) "
                f"from yfinance")
        if len(shares_a) < 2:
            raise DataUnavailable(f"{ticker}: no diluted share history from yfinance")
        if a_cf is None:
            raise DataUnavailable(f"{ticker}: insufficient cash-flow history from yfinance")
        revenue_ttm, revenue_prior = rev_a[0], rev_a[1]
        shares_now, shares_prior = shares_a[0], shares_a[1]
        cf = a_cf
        low_conf.append(f"metrics on ANNUAL basis ({annual_reason or 'Yahoo exposed <8 quarters'})")

        def amount(labels):
            vals = _row(cf, labels)
            return (vals[0], True) if vals else (None, False)

    ocf, ocf_found = amount(_OCF)
    capex_raw, capex_found = amount(_CAPEX)
    if not ocf_found or not capex_found:
        missing = " and ".join(n for n, found in (("operating cash flow", ocf_found),
                                                  ("capex", capex_found)) if not found)
        raise DataUnavailable(f"{ticker}: {basis} {missing} unavailable from yfinance")
    capex = abs(capex_raw)

    sbc_raw, sbc_found = amount(_SBC)
    sbc_assumed_zero = not sbc_found
    sbc = sbc_raw if sbc_found else 0.0
    if sbc_assumed_zero:
        low_conf.append("SBC not reported by source - assumed 0")
    elif _sbc_row_absent(a_cf):
        low_conf.append("annual SBC not reported by source - assumed 0 in history")

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
    price_ccy = info.get("currency")
    market_cap, cap_converted, cap_note = _statement_currency_cap(
        ticker, float(market_cap), price_ccy, info.get("financialCurrency"), fx_lookup)
    if cap_converted:
        low_conf.extend([cap_note, UNLOCK_CONVERTED_NOTE])

    # PEG inputs on ONE basis (D1): both EPS figures from the consensus table,
    # P/E on the same FY1 figure. forwardEps / trailingEps are not read: they mix
    # GAAP trailing EPS with an estimate on another basis. forwardPE is read only as
    # the cross-check for currency-converted estimates (#46), never as an input.
    eps_fy0, eps_fy1, est_ccy, fy1_analysts = _yf_consensus(estimates)
    price = _pos_num(info.get("currentPrice")) or _pos_num(info.get("regularMarketPrice"))
    fx = None
    if fx_lookup is not None and est_ccy and price_ccy and est_ccy != price_ccy:
        fx = fx_lookup(est_ccy, price_ccy)
    forward_pe, forward_eps_growth, peg_notes = _consensus_peg_inputs(
        price, price_ccy, eps_fy0, eps_fy1, est_ccy, fy1_analysts,
        fx=fx, source_forward_pe=_pos_num(info.get("forwardPE")))
    low_conf.extend(peg_notes)
    decline = _eps_decline_note(forward_eps_growth, _yf_revenue_growth(revenue_estimates))
    if decline:
        low_conf.append(decline)
    # One-off items inside consensus FY0 (#61): raw per-quarter pairs only; screen.py
    # decides. The "unavailable" note matters only where PEG decides the verdict.
    fy0_quarter_eps, one_off_gap = _yf_fy0_quarter_eps(
        earnings_history, q_inc, a_inc, info, est_ccy, date.today())
    if one_off_gap and net_income_ttm > 0 and forward_eps_growth is not None:
        low_conf.append(f"one-off check unavailable ({one_off_gap})")

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

    # First trading date for the IPO-seasoning classification. Yahoo exposes it
    # as an epoch timestamp; absent -> None (flagged), the name screens normally.
    ipo_date = _yf_first_trade_date(info)
    if ipo_date is None:
        low_conf.append("first trading date unknown - IPO seasoning not checked")

    # Adjusted-close snapshot for the entry-timing annotations (informational).
    current_price, ath_price, price_3mo_ago, price_history_years = (
        _extract_price_history(closes, date.today()))
    if current_price is None:
        low_conf.append("price history unavailable - entry timing not computed")

    return Company(
        ticker=ticker, revenue_ttm=revenue_ttm, revenue_ttm_prior=revenue_prior,
        revenue_latest_q=rev_latest_q, revenue_year_ago_q=rev_year_ago_q,
        ocf_ttm=ocf, capex_ttm=capex, sbc_ttm=sbc,
        diluted_shares_now=shares_now, diluted_shares_prior=shares_prior,
        market_cap=market_cap, market_cap_converted=cap_converted,
        forward_pe=forward_pe,
        forward_eps_growth=forward_eps_growth, net_income_ttm=net_income_ttm,
        total_debt=float(total_debt), cash_and_equivalents=float(cash),
        goodwill=float(goodwill), total_assets=float(total_assets),
        operating_income=float(operating_income), total_equity=float(total_equity),
        goodwill_assumed_zero=goodwill_assumed_zero, debt_assumed_zero=debt_assumed_zero,
        low_confidence=low_conf,
        name=name, basis=basis, sbc_assumed_zero=sbc_assumed_zero,
        sector=sector, industry=industry, ipo_date=ipo_date,
        current_price=current_price, ath_price=ath_price,
        price_3mo_ago=price_3mo_ago, price_history_years=price_history_years,
        eps_fy0=eps_fy0, eps_fy1=eps_fy1, estimate_currency=est_ccy,
        fy1_analysts=fy1_analysts, fy0_quarter_eps=fy0_quarter_eps,
        history=_annual_history(a_inc, a_cf, a_bal),
    )


def _annual_usable(a_inc, a_cf) -> bool:
    """True when the annual statements can carry a company on their own (#58)."""
    return (len(_row(a_inc, _REV)) >= 2 and len(_row(a_inc, _SHARES)) >= 2
            and bool(_row(a_inc, _NET_INCOME)) and bool(_row(a_cf, _OCF))
            and bool(_row(a_cf, _CAPEX)))


def _statement_currency_cap(ticker, market_cap, price_ccy, stmt_ccy, fx_lookup):
    """``(market_cap, converted, note)`` with the cap in the STATEMENT currency (D18).

    P/S divides market cap (trading currency) by revenue (statement currency); for a
    CNY or EUR reporter listed in USD that mixes currencies. When both currencies are
    known and differ, the cap is converted at a same-run FX rate and flagged. A
    missing rate raises (never a guessed rate); an unknown currency changes nothing.
    """
    if not price_ccy or not stmt_ccy or price_ccy == stmt_ccy:
        return market_cap, False, None
    fx = fx_lookup(price_ccy, stmt_ccy) if fx_lookup is not None else None
    if fx is None:
        raise DataUnavailable(
            f"{ticker}: statements in {stmt_ccy}, market cap in {price_ccy}, "
            f"no {price_ccy}{stmt_ccy} FX rate")
    rate, as_of = fx
    return (market_cap * rate, True,
            f"market cap converted {price_ccy}->{stmt_ccy} at {rate:.4f} ({str(as_of)[:10]})")


def _extract_price_history(closes, today: date):
    """``(current, ath, three_months_ago, span_years)`` from an adjusted-close series.

    Pure (no network) so it is unit-testable with a hand-built pandas Series.
    ``closes`` is indexed by trading day, oldest -> newest. The 3-months-ago
    price is the last close on/before ``today - 90d`` — None when the listing is
    younger than that (a young listing genuinely has no 3-month return, so it is
    reported as N/A, not zero). ``span_years`` records how much history the ATH
    actually covers, so "ATH" over a short window is never mistaken for a
    life-of-listing high. All None when the series is missing/empty.
    """
    if closes is None or len(closes) == 0:
        return None, None, None, None
    days = [getattr(idx, "date", lambda: idx)() for idx in closes.index]
    values = [float(v) for v in closes]
    current = values[-1]
    ath = max(values)
    cutoff = today - timedelta(days=90)
    past = [v for d, v in zip(days, values) if d <= cutoff]
    price_3mo_ago = past[-1] if past else None
    span_years = (days[-1] - days[0]).days / 365.25
    return current, ath, price_3mo_ago, span_years


def _yf_first_trade_date(info: dict) -> str | None:
    """ISO first-trading date from Yahoo's epoch field, or None (never guessed).

    yfinance has shipped this under two names with two units: the older
    ``firstTradeDateEpochUtc`` (epoch seconds) and, as of ~2026,
    ``firstTradeDateMilliseconds``. Each is converted per its documented unit —
    the unit is never guessed from the value's magnitude.
    """
    try:
        ts = info.get("firstTradeDateEpochUtc")
        if not ts:
            ms = info.get("firstTradeDateMilliseconds")
            ts = float(ms) / 1000.0 if ms else None
        if not ts:
            return None
        from datetime import datetime, timezone
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).date().isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _yoy_quarter_pair(rev_by_col: dict) -> tuple[float | None, float | None]:
    """``(latest_q, year_ago_q)`` revenue when a same-fiscal-quarter pair exists.

    The year-ago counterpart must sit 350-380 days before the newest column so
    a fiscal-calendar shift or a missing middle quarter can never silently pair
    mismatched quarters (comparing different fiscal quarters would re-introduce
    seasonality into growth). ``(None, None)`` when no aligned pair exists or
    the columns are not dated.
    """
    if not rev_by_col:
        return None, None
    cols = sorted(rev_by_col, reverse=True)
    newest = cols[0]
    for col in cols[1:]:
        try:
            delta_days = (newest - col).days
        except TypeError:  # string-labeled columns -> can't verify alignment
            return None, None
        if 350 <= delta_days <= 380:
            return float(rev_by_col[newest]), float(rev_by_col[col])
    return None, None


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


def _sbc_row_absent(a_cf) -> bool:
    """True when the cash-flow frame has no finite SBC value in any year.

    A missing row and an all-NaN row both count (parity with the snapshot path,
    where ``_row`` drops NaNs). A row with some finite years is NOT absent.
    """
    return a_cf is not None and not _labeled_row(a_cf, _SBC)


def _fiscal_label(col) -> str:
    """Human-readable year label for a period-end column (a Timestamp or string)."""
    year = getattr(col, "year", None)
    return str(year) if year is not None else str(col)[:10]


def _annual_history(a_inc, a_cf, a_bal=None) -> list:
    """Build a contiguous run of COMPLETE annual periods, oldest -> newest.

    Revenue comes from the income frame; OCF/capex/SBC from the cash-flow frame —
    aligned by fiscal-year column. Walking newest -> oldest, leading incomplete
    years (e.g. cash flow not yet filed) are skipped, then the run starts at the
    most recent complete year and TRUNCATES at the first backward gap. A skipped
    middle year would misalign growth, so we stop rather than skip. Lines are
    never fabricated or zero-filled — a missing line just ends the history. The one
    sanctioned exception is SBC: a cash-flow frame with no finite SBC value in any
    year (row missing or all NaN) means the company doesn't report it (sbc=0, as in
    the snapshot path); a row with some finite years that is NaN in one year is
    still a gap.

    Operating income, net income, COGS (income frame) and total debt / cash /
    total equity (balance-sheet frame, when supplied) are looked up per year on a
    BEST-EFFORT basis for the advisory ROIC-history / gross-margin annotations and
    the earnings-quality gate — a year missing one of these simply carries `None`
    for that field rather than truncating the (revenue/OCF/capex/SBC-gated) run.
    """
    if a_inc is None or a_cf is None:
        return []
    rev = _labeled_row(a_inc, _REV)
    ocf = _labeled_row(a_cf, _OCF)
    capex = _labeled_row(a_cf, _CAPEX)
    sbc = _labeled_row(a_cf, _SBC)
    sbc_absent = _sbc_row_absent(a_cf)
    net_income = _labeled_row(a_inc, _NET_INCOME)
    operating_income = _labeled_row(a_inc, _EBIT)
    cogs = _labeled_row(a_inc, _COGS)
    total_debt = _labeled_row(a_bal, _TOTAL_DEBT) if a_bal is not None else {}
    cash = _labeled_row(a_bal, _CASH) if a_bal is not None else {}
    total_equity = _labeled_row(a_bal, _TOTAL_EQUITY) if a_bal is not None else {}
    cols = sorted(set(a_inc.columns) | set(a_cf.columns), reverse=True)
    periods: list[AnnualPeriod] = []
    started = False
    for col in cols:
        complete = col in rev and col in ocf and col in capex and (col in sbc or sbc_absent)
        if complete:
            started = True
            periods.append(AnnualPeriod(
                fiscal_label=_fiscal_label(col),
                revenue=rev[col], ocf=ocf[col],
                capex=abs(capex[col]), sbc=sbc.get(col, 0.0),
                operating_income=operating_income.get(col),
                total_debt=total_debt.get(col),
                cash_and_equivalents=cash.get(col),
                total_equity=total_equity.get(col),
                net_income=net_income.get(col),
                cogs=cogs.get(col),
            ))
        elif started:
            break  # first backward gap after the run begins -> truncate
        # else: leading incomplete year -> skip and keep looking for the run start
    periods.reverse()  # oldest -> newest
    return periods


def _yf_estimates(tk):
    """The consensus ``earnings_estimate`` table, or None when the request fails.

    PEG inputs are optional for a name: a failed request makes PEG untestable
    (NEEDS_DATA path), it never skips the name as "could not source".
    """
    try:
        df = tk.earnings_estimate
    except Exception:
        return None
    return df if df is not None and getattr(df, "empty", True) is False else None


def _yf_revenue_estimates(tk):
    """The consensus ``revenue_estimate`` table, or None on any failure (note-only input)."""
    try:
        df = tk.revenue_estimate
    except Exception:
        return None
    return df if df is not None and getattr(df, "empty", True) is False else None


def _yf_revenue_growth(table) -> float | None:
    """Consensus revenue growth % (``+1y`` avg over ``0y`` avg); None unless both > 0."""
    if table is None:
        return None
    try:
        r0, r1 = _pos_num(table.loc["0y", "avg"]), _pos_num(table.loc["+1y", "avg"])
    except (KeyError, IndexError):
        return None
    if r0 is None or r1 is None or r0 <= 0 or r1 <= 0:
        return None
    return 100 * (r1 / r0 - 1)


def _yf_earnings_history(tk):
    """Yahoo ``earnings_history`` (street actual EPS per quarter), or None on any failure."""
    try:
        df = tk.earnings_history
    except Exception:
        return None
    return df if df is not None and getattr(df, "empty", True) is False else None


# Morningstar quarterly-income labels (Yahoo's 2026 dataset). "Normalized Income" is net
# income excluding unusual items, after their tax effect. No fallback label: Net Income
# would hide exactly the one-offs this check measures. Not derived from Total Unusual
# Items when NaN: on AMZN Q2 2026 those rows are NaN too (#61 PR).
_NORMALIZED_INCOME = ["Normalized Income"]
_DILUTED_AVG_SHARES = ["Diluted Average Shares"]
#: An FY0 quarter matches a statement column within this many days (52/53-week years).
FY0_QUARTER_MATCH_DAYS = 10
#: The newest annual column anchors FY0 only when it is at most ~15 months old.
FY0_ANNUAL_MAX_AGE_DAYS = 457


def _as_date(x) -> date | None:
    try:
        return date.fromisoformat(str(x)[:10])
    except ValueError:
        return None


def _yf_fy0_quarter_eps(earnings_history, q_inc, a_inc, info, est_ccy, today: date):
    """``(quarters, None)`` or ``(None, cause)`` for the #61 one-off check.

    FY0 quarters are those ending after the newest annual statement column, not
    ``info.lastFiscalYearEnd`` (stale for NVDA in 2026-11). Each is a dict
    ``{quarter_end, eps_actual, eps_normalized}``: street actual EPS (``epsActual``)
    and normalized income / diluted average shares from the same quarter's column.
    US issuers only with statement = estimate = price currency: an ADR's street EPS
    is per ADS while statement EPS is per ordinary share (a 1:6 ADR would show an
    ~83% false one-off even in USD). Any gap returns a cause, never a partial sum.
    """
    if (earnings_history is None or getattr(earnings_history, "empty", True)
            or "epsActual" not in earnings_history.columns):
        return None, "no earnings history"
    if (info.get("country") or "") != "United States":
        return None, "non-US issuer"
    stmt_ccy, price_ccy = info.get("financialCurrency"), info.get("currency")
    if not (stmt_ccy and price_ccy and est_ccy) or len({stmt_ccy, price_ccy, est_ccy}) > 1:
        return None, "currency mismatch or unverified"
    annual = [d for d in map(_as_date, a_inc.columns if a_inc is not None else []) if d]
    if not annual:
        return None, "no annual statement to anchor the fiscal year"
    fy_end = max(annual)
    if (today - fy_end).days > FY0_ANNUAL_MAX_AGE_DAYS:
        return None, "latest annual statement older than 15 months"
    cutoff = fy_end + timedelta(days=15)
    cols = [(d, c) for c in (q_inc.columns if q_inc is not None else [])
            if (d := _as_date(c)) and d > cutoff]
    reported = sorted((d, v) for i, v in zip(earnings_history.index,
                                             earnings_history["epsActual"])
                      if (d := _as_date(i)) and d > cutoff)
    if len(cols) > 3 or len(cols) != len(reported):
        return None, "quarters not aligned to the fiscal year"
    norm = _labeled_row(q_inc, _NORMALIZED_INCOME)
    shares = _labeled_row(q_inc, _DILUTED_AVG_SHARES)
    quarters = []
    for d, actual in reported:
        match = [c for cd, c in cols if abs((cd - d).days) <= FY0_QUARTER_MATCH_DAYS]
        if len(match) != 1:
            return None, "quarters not aligned to the fiscal year"
        actual = _pos_num(actual)
        if actual is None:
            return None, "street actual EPS not reported"
        if match[0] not in norm:
            return None, "normalized income not reported"
        if not shares.get(match[0]):
            return None, "diluted shares not reported"
        quarters.append({"quarter_end": d.isoformat(), "eps_actual": actual,
                         "eps_normalized": norm[match[0]] / shares[match[0]]})
    return quarters, None


def _eps_decline_note(eps_growth, revenue_growth) -> str | None:
    """#53: the FY0 one-off note when EPS growth <= 0 while revenue growth > 0."""
    if eps_growth is None or revenue_growth is None:
        return None
    return EPS_DECLINE_NOTE if eps_growth <= 0 and revenue_growth > 0 else None


def _yf_fx_close(symbol: str) -> tuple[float, str] | None:
    """``(last valid close, its timestamp)`` of a Yahoo FX pair, or None on any failure."""
    try:
        import yfinance as yf

        closes = yf.Ticker(symbol).history(period="5d")["Close"].dropna()
        closes = closes[closes > 0]
        if closes.empty:
            return None
        return float(closes.iloc[-1]), closes.index[-1].isoformat()
    except Exception:
        return None


def _pos_num(v) -> float | None:
    """``float(v)`` when it is a finite number, else None (NaN/None/garbage -> None)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _yf_consensus(estimates):
    """``(eps_fy0, eps_fy1, currency, fy1_analysts)`` from Yahoo's estimate table.

    Rows ``0y`` / ``+1y`` are the current and next fiscal year; ``avg`` is the
    consensus EPS. Absent rows or NaN cells come back as None, never 0.
    """
    if estimates is None:
        return None, None, None, None

    def cell(row, col):
        try:
            return estimates.loc[row, col]
        except (KeyError, IndexError):
            return None

    n = _pos_num(cell("+1y", "numberOfAnalysts"))
    ccy = cell("+1y", "currency") or cell("0y", "currency")
    return (_pos_num(cell("0y", "avg")), _pos_num(cell("+1y", "avg")),
            ccy if isinstance(ccy, str) and ccy else None,
            int(n) if n is not None else None)


#: Fewer FY1 analysts than this -> a low_confidence note (never a gate).
MIN_FY1_ANALYSTS = 3
#: Converted forward P/E vs the source's own forwardPE: a relative gap above this
#: is a currency/ADR basis mismatch (e.g. one ADR = 5 ordinary shares), #46.
FX_PE_TOLERANCE = 0.30


def _consensus_peg_inputs(price, price_ccy, eps_fy0, eps_fy1, est_ccy, fy1_analysts,
                          fx=None, source_forward_pe=None):
    """``(forward_pe, forward_eps_growth, notes)`` on one basis (D1, shared by providers).

    ``forward_pe = price / EPS_FY1`` and ``growth = 100 x (EPS_FY1 / EPS_FY0 - 1)``.
    Untestable -> ``(None, None)`` with a note when either EPS is absent or <= 0,
    or price is absent. Growth <= 0 on two positive EPS values is returned as is: a
    failed PEG, not missing data. An unknown currency computes with a note; few
    analysts is a note.

    Estimate currency != price currency (#46): EPS_FY1 is converted at
    ``fx = (rate, as_of)`` (growth is currency-neutral). No rate -> untestable. The
    converted P/E must lie within ``FX_PE_TOLERANCE`` of ``source_forward_pe``;
    a larger gap, or no source P/E to compare, -> untestable.
    """
    if eps_fy0 is None or eps_fy1 is None:
        return None, None, ["PEG untestable: consensus EPS estimates unavailable"]
    if eps_fy0 <= 0 or eps_fy1 <= 0:
        return None, None, [f"PEG untestable: non-positive consensus EPS "
                            f"(FY0 {eps_fy0:.2f}, FY1 {eps_fy1:.2f})"]
    notes: list[str] = []
    converted = est_ccy is not None and price_ccy is not None and est_ccy != price_ccy
    if est_ccy is None or price_ccy is None:
        notes.append("estimate currency unverified")
    elif converted and fx is None:
        return None, None, [f"PEG untestable: consensus EPS in {est_ccy}, price in "
                            f"{price_ccy}, no {est_ccy}{price_ccy} FX rate"]
    if price is None or price <= 0:
        return None, None, ["PEG untestable: no price for the forward P/E"]
    forward_pe = price / eps_fy1
    if converted:
        rate, as_of = fx
        forward_pe = price / (eps_fy1 * rate)
        if source_forward_pe is None or source_forward_pe <= 0:
            return None, None, [f"PEG untestable: currency/ADR basis unverified "
                                f"(consensus EPS in {est_ccy}, price in {price_ccy}, "
                                f"no source forward P/E to cross-check)"]
        if abs(forward_pe / source_forward_pe - 1) > FX_PE_TOLERANCE:
            return None, None, [f"PEG untestable: currency/ADR basis mismatch "
                                f"(converted forward P/E {forward_pe:.1f} vs source "
                                f"{source_forward_pe:.1f})"]
        notes.append(f"consensus EPS converted {est_ccy}->{price_ccy} at "
                     f"{rate:.4f} ({as_of})")
    if fy1_analysts is not None and fy1_analysts < MIN_FY1_ANALYSTS:
        notes.append(f"only {fy1_analysts} FY1 analysts - PEG estimate thin")
    return forward_pe, 100 * (eps_fy1 / eps_fy0 - 1), notes


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

    def __init__(self, api_key: str | None = None, cache: DiskCache | None = None,
                 fetch_timeout: float = DEFAULT_FETCH_TIMEOUT):
        super().__init__(cache, fetch_timeout)
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
        market_cap = float(quote["marketCap"])
        price = _pos_num(quote.get("price"))
        name = quote.get("name") or ticker

        # Sector/industry/IPO date from the company profile (informational only).
        sector, industry, ipo_date, price_ccy = "Unknown", "", None, None
        try:
            profile = self._get(f"profile/{ticker}")
            if profile:
                sector = profile[0].get("sector") or "Unknown"
                industry = profile[0].get("industry") or ""
                ipo_date = profile[0].get("ipoDate") or None
                price_ccy = profile[0].get("currency") or None
        except DataUnavailable:
            pass  # profile is optional context; never block a name on it
        if sector == "Unknown":
            low_conf.append("sector not reported by source")
        if ipo_date is None:
            low_conf.append("first trading date unknown - IPO seasoning not checked")

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
        # bal_a additionally feeds the advisory ROIC-history annotation.
        inc_a = self._get(f"income-statement/{ticker}", period="annual", limit=10)
        cf_a = self._get(f"cash-flow-statement/{ticker}", period="annual", limit=10)
        if sbc_found and not any(q.get("stockBasedCompensation") is not None for q in cf_a):
            low_conf.append("annual SBC not reported by source - assumed 0 in history")
        bal_a = self._get(f"balance-sheet-statement/{ticker}", period="annual", limit=10)

        # PEG inputs on ONE basis (D1): the current- and next-FY consensus rows.
        # FMP estimate rows carry no currency; they are taken to be in the
        # statements' reportedCurrency, compared with the profile's trading currency.
        try:
            est = self._get(f"analyst-estimates/{ticker}", period="annual")
        except Exception:  # 403 on the tier, HTTP/JSON error: PEG untestable, not skipped
            est = []
        last_fy_end = inc_a[0].get("date") if inc_a else None
        eps_fy0, eps_fy1, fy1_analysts, shifted = _fmp_fy_pair(
            est, date.today().isoformat(), last_fy_end)
        if shifted:
            low_conf.append("FY0 shifted: the fiscal year just ended is not yet "
                            "reported, so FY0/FY1 are one year ahead")
        est_ccy = next((r.get("reportedCurrency") for r in inc + inc_a
                        if r.get("reportedCurrency")), None)
        # Statement-currency market cap (D18): same normalisation as the Yahoo path.
        market_cap, cap_converted, cap_note = _statement_currency_cap(
            ticker, market_cap, price_ccy, est_ccy, self._fx_rate)
        if cap_converted:
            low_conf.extend([cap_note, UNLOCK_CONVERTED_NOTE])
        forward_pe, forward_eps_growth, peg_notes = _consensus_peg_inputs(
            price, price_ccy, eps_fy0, eps_fy1, est_ccy, fy1_analysts)
        low_conf.extend(peg_notes)
        if net_income_ttm > 0 and forward_eps_growth is not None:  # #61 is Yahoo-only
            low_conf.append("one-off check unavailable (not supported by FMP)")

        return Company(
            ticker=ticker, revenue_ttm=revenue_ttm, revenue_ttm_prior=revenue_prior,
            ocf_ttm=ocf, capex_ttm=capex, sbc_ttm=sbc,
            diluted_shares_now=shares_now, diluted_shares_prior=shares_prior,
            market_cap=market_cap, market_cap_converted=cap_converted,
            forward_pe=forward_pe,
            forward_eps_growth=forward_eps_growth, net_income_ttm=net_income_ttm,
            total_debt=float(total_debt), cash_and_equivalents=float(cash),
            goodwill=float(goodwill), total_assets=float(total_assets),
            operating_income=float(operating_income), total_equity=float(total_equity),
            goodwill_assumed_zero=goodwill_assumed_zero, debt_assumed_zero=debt_assumed_zero,
            low_confidence=low_conf,
            name=name, basis="TTM", sbc_assumed_zero=not sbc_found,
            sector=sector, industry=industry, ipo_date=ipo_date,
            eps_fy0=eps_fy0, eps_fy1=eps_fy1, estimate_currency=est_ccy,
            fy1_analysts=fy1_analysts,
            history=_fmp_annual_history(inc_a, cf_a, bal_a),
        )


def _fmp_annual_history(
    inc_a: list[dict], cf_a: list[dict], bal_a: list[dict] | None = None,
) -> list[AnnualPeriod]:
    """Contiguous run of complete annual periods from FMP statements, oldest -> newest.

    Income and cash-flow records are aligned by ``calendarYear``; walking newest
    -> oldest, the run truncates at the first year missing any required line (never
    skipping a middle year, which would misalign growth). No line is zero-filled.

    Operating income, net income, COGS, and (when ``bal_a`` is supplied) total
    debt / cash / total equity are looked up per year on a BEST-EFFORT basis for
    the advisory ROIC-history / gross-margin annotations and the earnings-quality
    gate — a year missing one of these carries ``None`` for that field only,
    never truncating the (revenue/OCF/capex/SBC-gated) run.
    """
    cf_by_year = {q.get("calendarYear"): q for q in cf_a}
    bal_by_year = {b.get("calendarYear"): b for b in (bal_a or [])}
    # No year reports SBC at all -> not reported by the company: 0 (snapshot parity).
    sbc_absent = not any(q.get("stockBasedCompensation") is not None for q in cf_a)
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
            and (sbc_absent or cq.get("stockBasedCompensation") is not None)
        )
        if complete:
            started = True
            bq = bal_by_year.get(year)
            periods.append(AnnualPeriod(
                fiscal_label=str(year),
                revenue=float(rev),
                ocf=float(cq["operatingCashFlow"]),
                capex=abs(float(cq["capitalExpenditure"])),
                sbc=float(cq.get("stockBasedCompensation") or 0.0),
                operating_income=_fmp_num(q.get("operatingIncome")),
                net_income=_fmp_num(q.get("netIncome")),
                cogs=_fmp_num(q.get("costOfRevenue")),
                total_debt=_fmp_num(bq.get("totalDebt")) if bq else None,
                cash_and_equivalents=_fmp_num(bq.get("cashAndCashEquivalents")) if bq else None,
                total_equity=_fmp_num(bq.get("totalStockholdersEquity")) if bq else None,
            ))
        elif started:
            break  # first backward gap -> truncate
    periods.reverse()  # oldest -> newest
    return periods


def _fmp_num(v) -> float | None:
    """``float(v)`` or ``None`` — best-effort optional numeric field from FMP JSON."""
    return None if v is None else float(v)


def _days_between(earlier: str, later: str) -> int:
    return (date.fromisoformat(later[:10]) - date.fromisoformat(earlier[:10])).days


def _fmp_fy_pair(estimates: list[dict], today: str, last_reported_fy_end: str | None):
    """``(eps_fy0, eps_fy1, fy1_analysts, shifted)`` from FMP annual estimates.

    FY0 is the first annual row dated on/after ``today`` (the fiscal year in
    progress), FY1 the next one. ``shifted`` is True when a fiscal year has ended
    but is not yet reported (a past-dated row newer than ``last_reported_fy_end``):
    then the pair sits one year ahead of the consensus "current" year. Absent
    values are None, never 0.
    """
    rows = sorted((e for e in estimates or [] if e.get("date")), key=lambda e: e["date"])
    future = [e for e in rows if e["date"] >= today]
    past = [e for e in rows if e["date"] < today]
    # 52/53-week years put the estimate and statement dates of one fiscal year a few
    # days apart, so "newer" means more than ~60 days after the last reported year end.
    shifted = bool(past and last_reported_fy_end
                   and _days_between(last_reported_fy_end, past[-1]["date"]) > 60)
    fy0 = _pos_num(future[0].get("estimatedEpsAvg")) if future else None
    fy1_row = future[1] if len(future) > 1 else None
    fy1 = _pos_num(fy1_row.get("estimatedEpsAvg")) if fy1_row else None
    n = _pos_num(fy1_row.get("numberAnalystsEstimatedEps")) if fy1_row else None
    return fy0, fy1, int(n) if n is not None else None, shifted


PROVIDERS = {
    "yfinance": YFinanceProvider,
    "fmp": FMPProvider,
    "mock": MockProvider,
}
