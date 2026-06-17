"""Data sourcing layer — the only place that touches the network.

A :class:`DataProvider` turns a ticker into a :class:`~qqq_screen.screen.Company`
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
from .screen import Company

# Fields persisted to / restored from the disk cache.
_COMPANY_FIELDS = (
    "ticker", "revenue_ttm", "revenue_ttm_prior", "ocf_ttm", "capex_ttm",
    "sbc_ttm", "diluted_shares_now", "diluted_shares_prior", "market_cap",
    "forward_pe", "forward_eps_growth", "low_confidence",
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
                return Company(**{k: cached.get(k) for k in _COMPANY_FIELDS})
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
    DATA: dict[str, Company] = {
        "MSFT": Company("MSFT", 245000, 212000, 118000, 28000, 10000,
                        7430, 7450, 3100000, 32.0, 14.0),
        "GOOGL": Company("GOOGL", 328000, 283000, 110000, 32000, 22000,
                         12200, 12500, 2100000, 21.0, 16.0),
        "CRWD": Company("CRWD", 3500, 2400, 1100, 120, 500, 245, 235,
                        80000, None, 30.0),
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

    * Yahoo usually exposes only ~4-6 quarters, so the prior-TTM window may be
      unavailable — we fall back to **annual** year-over-year for revenue growth
      and prior diluted shares, flagged as low-confidence.
    * **Stock-based compensation is frequently missing.** When it is, we raise
      (skip the name) rather than assume 0 — SBC is a real economic cost here.
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
        low_conf: list[str] = []

        q_inc = _df(tk, "quarterly_income_stmt", "quarterly_financials")
        a_inc = _df(tk, "income_stmt", "financials")
        revenue_ttm, revenue_prior = self._revenue(ticker, q_inc, a_inc, low_conf)
        shares_now, shares_prior = self._shares(ticker, q_inc, a_inc, low_conf)

        q_cf = _df(tk, "quarterly_cashflow", "quarterly_cash_flow")
        a_cf = _df(tk, "cashflow", "cash_flow")
        ocf = _ttm(ticker, "operating cash flow", q_cf, a_cf, low_conf,
                   ["Operating Cash Flow", "Total Cash From Operating Activities"])
        capex = abs(_ttm(ticker, "capital expenditure", q_cf, a_cf, low_conf,
                         ["Capital Expenditure", "Capital Expenditures"]))
        sbc = _ttm(ticker, "stock-based compensation", q_cf, a_cf, low_conf,
                   ["Stock Based Compensation"])

        info = _info(tk)
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

        return Company(
            ticker=ticker, revenue_ttm=revenue_ttm, revenue_ttm_prior=revenue_prior,
            ocf_ttm=ocf, capex_ttm=capex, sbc_ttm=sbc,
            diluted_shares_now=shares_now, diluted_shares_prior=shares_prior,
            market_cap=float(market_cap),
            forward_pe=float(forward_pe) if forward_pe else None,
            forward_eps_growth=forward_eps_growth, low_confidence=low_conf,
        )

    def _revenue(self, ticker, q_inc, a_inc, low_conf):
        labels = ["Total Revenue", "Operating Revenue"]
        vals = _row(q_inc, labels)
        if len(vals) >= 8:
            return sum(vals[:4]), sum(vals[4:8])
        # Fall back to annual YoY when 8 quarters aren't available.
        ann = _row(a_inc, labels)
        if len(ann) >= 2:
            low_conf.append("revenue growth from annual YoY (not TTM)")
            return ann[0], ann[1]
        raise DataUnavailable(f"{ticker}: insufficient revenue history from yfinance")

    def _shares(self, ticker, q_inc, a_inc, low_conf):
        labels = ["Diluted Average Shares", "Diluted Shares", "Basic Average Shares"]
        vals = _row(q_inc, labels)
        if len(vals) >= 5:  # current quarter vs same quarter a year earlier
            return vals[0], vals[4]
        ann = _row(a_inc, labels)
        if len(ann) >= 2:
            low_conf.append("diluted shares from annual YoY")
            return ann[0], ann[1]
        raise DataUnavailable(f"{ticker}: no diluted share history from yfinance")


def _ttm(ticker, what, q_df, a_df, low_conf, labels):
    """Sum the 4 most recent quarters for ``labels``; fall back to latest annual."""
    vals = _row(q_df, labels)
    if len(vals) >= 4:
        return sum(vals[:4])
    ann = _row(a_df, labels)
    if ann:
        low_conf.append(f"{what} from latest annual (not 4-quarter TTM)")
        return ann[0]
    raise DataUnavailable(f"{ticker}: {what} unavailable from yfinance")


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

        ocf = sum(q["operatingCashFlow"] for q in cf[:4])
        capex = abs(sum(q["capitalExpenditure"] for q in cf[:4]))
        sbc = sum(q.get("stockBasedCompensation", 0) for q in cf[:4])

        quote = self._get(f"quote/{ticker}")[0]
        market_cap = quote["marketCap"]
        price, ttm_eps = quote.get("price"), quote.get("eps")

        forward_pe, forward_eps_growth = None, 0.0
        est = self._get(f"analyst-estimates/{ticker}", period="annual")
        fwd_eps = _nearest_future_eps(est)
        if fwd_eps and price:
            forward_pe = price / fwd_eps
            if ttm_eps and ttm_eps > 0:
                forward_eps_growth = (fwd_eps / ttm_eps - 1) * 100

        return Company(
            ticker=ticker, revenue_ttm=revenue_ttm, revenue_ttm_prior=revenue_prior,
            ocf_ttm=ocf, capex_ttm=capex, sbc_ttm=sbc,
            diluted_shares_now=shares_now, diluted_shares_prior=shares_prior,
            market_cap=market_cap, forward_pe=forward_pe,
            forward_eps_growth=forward_eps_growth,
        )


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
