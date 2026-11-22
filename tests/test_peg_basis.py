"""PEG on one basis (#6, D1).

``PEG = (price / EPS_FY1) / (100 x (EPS_FY1 / EPS_FY0 - 1))`` with EPS_FY0 / EPS_FY1
the analyst-consensus averages for the current and next fiscal year. Inputs that
are absent, non-positive or in another currency than the price make PEG
UNTESTABLE (NEEDS_DATA path, never 0 or a guess). Consensus growth <= 0 on two
positive EPS values is a FAILED PEG: a profitable name is REJECTED, never
NEEDS_DATA and never WATCHLIST.
"""

from dataclasses import asdict

import pandas as pd
import pytest

from etf_screen.export import COLUMNS, build_rows
from etf_screen.overrides import _INFORMATIONAL, _REQUIRED, apply_override
from etf_screen.providers import (
    _COMPANY_FIELDS,
    YFinanceProvider,
    _consensus_peg_inputs,
    _extract_yf,
    _fmp_fy_pair,
)
from etf_screen.screen import ScreenStatus, evaluate
from test_screen import ACCEPTANCE, make

_NEW_FIELDS = ("eps_fy0", "eps_fy1", "estimate_currency", "fy1_analysts")


def _kyc(fwd_pe=None, fwd_growth=None):
    """Profitable, every testable filter passing; PEG decided by the two inputs."""
    return make("KYC", 1000, 700, 400, 50, 50, 1000, 1000, 5000, fwd_pe, fwd_growth,
                net_income=200)


# --- the formula (pure helper shared by both providers) ---------------------
def test_formula_normal_case():
    fpe, growth, notes = _consensus_peg_inputs(480.0, "USD", 20.0, 24.0, "USD", 30)
    assert fpe == pytest.approx(20.0)        # 480 / 24
    assert growth == pytest.approx(20.0)     # 100 x (24 / 20 - 1)
    assert fpe / growth == pytest.approx(1.0)
    assert notes == []


@pytest.mark.parametrize("fy0,fy1", [(None, 24.0), (20.0, None), (None, None)])
def test_missing_eps_is_untestable(fy0, fy1):
    fpe, growth, notes = _consensus_peg_inputs(480.0, "USD", fy0, fy1, "USD", 30)
    assert fpe is None and growth is None
    assert any("unavailable" in n for n in notes)


@pytest.mark.parametrize("fy0,fy1", [(0.0, 24.0), (-2.0, 24.0), (20.0, 0.0), (20.0, -1.0)])
def test_non_positive_eps_is_untestable(fy0, fy1):
    fpe, growth, notes = _consensus_peg_inputs(480.0, "USD", fy0, fy1, "USD", 30)
    assert fpe is None and growth is None
    assert any("non-positive" in n for n in notes)


def test_currency_mismatch_is_untestable():
    fpe, growth, notes = _consensus_peg_inputs(1000.0, "USD", 41.4, 56.4, "EUR", 27)
    assert fpe is None and growth is None
    assert any("EUR" in n and "USD" in n for n in notes)


@pytest.mark.parametrize("price", [None, 0.0])
def test_missing_price_is_untestable(price):
    fpe, growth, notes = _consensus_peg_inputs(price, "USD", 20.0, 24.0, "USD", 30)
    assert fpe is None and growth is None
    assert any("price" in n for n in notes)


def test_negative_growth_is_a_number_not_missing():
    fpe, growth, _ = _consensus_peg_inputs(480.0, "USD", 25.0, 24.0, "USD", 30)
    assert fpe == pytest.approx(20.0)
    assert growth == pytest.approx(-4.0)


def test_few_analysts_is_a_note_only():
    fpe, growth, notes = _consensus_peg_inputs(480.0, "USD", 20.0, 24.0, "USD", 2)
    assert (fpe, growth) == (pytest.approx(20.0), pytest.approx(20.0))
    assert any("FY1 analysts" in n for n in notes)


def test_unknown_currency_computes_with_note():
    fpe, growth, notes = _consensus_peg_inputs(480.0, "USD", 20.0, 24.0, None, 30)
    assert (fpe, growth) == (pytest.approx(20.0), pytest.approx(20.0))
    assert "estimate currency unverified" in notes


# --- yfinance extraction ----------------------------------------------------
def _frame(label_to_values: dict, n: int):
    cols = pd.date_range("2025-12-31", periods=n, freq="-1QE")
    return pd.DataFrame(
        {label: vals[:n] for label, vals in label_to_values.items()}, index=cols
    ).T


# Profitable, Rule of 40 = 49, P/S 4.2 vs guardrail 15, no history (INSUFFICIENT).
_Q_INC = {"Total Revenue": [130, 120, 115, 110, 100],
          "Diluted Average Shares": [10, 10, 10, 10, 11], "Net Income": [20] * 5}
_Q_CF = {"Operating Cash Flow": [30] * 5, "Capital Expenditure": [-5] * 5,
         "Stock Based Compensation": [2] * 5}
# The old proxy inputs are present on purpose: they must no longer be read.
_INFO = {"marketCap": 2000, "shortName": "Test Co", "currentPrice": 480.0,
         "currency": "USD", "forwardPE": 99.0, "forwardEps": 6.0, "trailingEps": 1.0}


def _estimates(fy0, fy1, currency="USD", n_fy1=30):
    return pd.DataFrame(
        {"avg": [fy0, fy1], "numberOfAnalysts": [28, n_fy1],
         "currency": [currency, currency]},
        index=["0y", "+1y"],
    )


def _yf(estimates, info=_INFO):
    return _extract_yf("TST", q_inc=_frame(_Q_INC, 5), a_inc=None,
                       q_cf=_frame(_Q_CF, 5), a_cf=None, info=info,
                       estimates=estimates)


def test_yfinance_uses_the_consensus_table_not_forward_pe():
    c = _yf(_estimates(20.0, 24.0))
    assert (c.eps_fy0, c.eps_fy1, c.estimate_currency, c.fy1_analysts) == (
        20.0, 24.0, "USD", 30)
    assert c.forward_pe == pytest.approx(20.0)          # 480 / 24, not forwardPE 99
    assert c.forward_eps_growth == pytest.approx(20.0)  # not 6 / 1 - 1
    assert not any("rough Yahoo proxy" in m for m in c.low_confidence)
    assert evaluate(c).peg == pytest.approx(1.0)


def test_yfinance_eur_estimates_are_needs_data():
    c = _yf(_estimates(41.4, 56.4, currency="EUR"))
    assert c.forward_pe is None and c.forward_eps_growth is None
    assert (c.eps_fy0, c.eps_fy1, c.estimate_currency) == (41.4, 56.4, "EUR")
    r = evaluate(c)
    assert r.status is ScreenStatus.NEEDS_DATA
    assert any("EUR" in reason for reason in r.reasons)


def test_yfinance_missing_table_is_needs_data():
    c = _yf(None)
    assert c.forward_pe is None and c.forward_eps_growth is None
    assert all(getattr(c, f) is None for f in _NEW_FIELDS)
    r = evaluate(c)
    assert r.status is ScreenStatus.NEEDS_DATA
    assert any("no consensus EPS estimates" in reason for reason in r.reasons)


def test_yfinance_nan_estimate_is_missing_not_zero():
    c = _yf(_estimates(20.0, float("nan")))
    assert c.eps_fy1 is None and c.forward_eps_growth is None


class _FakeTicker:
    """A yfinance.Ticker whose statements load but whose estimates request fails."""

    def __init__(self, ticker):
        self.quarterly_income_stmt = _frame(_Q_INC, 5)
        self.quarterly_cashflow = _frame(_Q_CF, 5)
        self.quarterly_balance_sheet = _frame(
            {"Cash And Cash Equivalents": [300] * 5, "Total Assets": [1500] * 5,
             "Stockholders Equity": [1000] * 5, "Total Debt": [0] * 5,
             "Goodwill": [0] * 5}, 5)
        self.income_stmt = self.cashflow = self.balance_sheet = None
        self.info = dict(_INFO)

    def history(self, **kw):
        raise RuntimeError("no prices in this test")

    @property
    def earnings_estimate(self):
        raise RuntimeError("Yahoo estimates request failed")


def test_failed_estimates_request_is_needs_data_not_could_not_source(monkeypatch):
    import yfinance

    monkeypatch.setattr(yfinance, "Ticker", _FakeTicker)
    c = YFinanceProvider(cache=None, fetch_timeout=0).fetch("TST")  # no DataUnavailable
    assert c.forward_pe is None and c.forward_eps_growth is None
    assert evaluate(c).status is ScreenStatus.NEEDS_DATA


# --- FMP fiscal-year pair ---------------------------------------------------
def _row(d, eps, n=10):
    return {"date": d, "estimatedEpsAvg": eps, "numberAnalystsEstimatedEps": n}


def test_fmp_pair_takes_current_and_next_fiscal_year():
    rows = [_row("2028-12-31", 9.0), _row("2025-12-31", 5.0),
            _row("2026-12-31", 6.0), _row("2027-12-31", 7.5, n=12)]
    fy0, fy1, n_fy1, shifted = _fmp_fy_pair(rows, "2026-11-20", "2025-12-31")
    assert (fy0, fy1, n_fy1, shifted) == (6.0, 7.5, 12, False)


def test_fmp_pair_flags_an_unreported_fiscal_year():
    # June year-end: FY ended 2026-06-30 but the last reported annual is 2025-06-30,
    # so the nearest future row is one year ahead of the consensus "current" year.
    rows = [_row("2026-06-30", 5.0), _row("2027-06-30", 6.0), _row("2028-06-30", 7.0)]
    fy0, fy1, _, shifted = _fmp_fy_pair(rows, "2026-11-20", "2025-06-30")
    assert (fy0, fy1, shifted) == (6.0, 7.0, True)


def test_fmp_pair_missing_next_year():
    fy0, fy1, n_fy1, _ = _fmp_fy_pair([_row("2026-12-31", 6.0)], "2026-11-20", None)
    assert fy0 == 6.0 and fy1 is None and n_fy1 is None


# --- screen: failed vs untestable PEG ---------------------------------------
def test_negative_consensus_growth_rejects_a_profitable_name():
    r = evaluate(_kyc(fwd_pe=20, fwd_growth=-5))
    assert r.status is ScreenStatus.REJECTED
    assert not r.needs_data and not r.watchlist and not r.track_b
    assert r.peg is None
    assert any("consensus EPS growth <= 0" in reason for reason in r.reasons)


def test_negative_growth_with_pricey_ps_is_not_watchlist():
    # Valuation-only blockers would normally make this a WATCHLIST name; no price
    # fixes negative growth, so it is plain REJECTED with no unlock price.
    c = make("NEG", 1000, 700, 400, 50, 50, 1000, 1000, 30000, 20, -5, net_income=200)
    r = evaluate(c)
    assert r.status is ScreenStatus.REJECTED
    assert r.unlock_ps_price is None and r.unlock_peg_price is None


def test_zero_growth_is_failed_not_untestable():
    r = evaluate(_kyc(fwd_pe=20, fwd_growth=0.0))
    assert r.status is ScreenStatus.REJECTED


def test_unprofitable_negative_growth_still_routes_to_track_b():
    base = ACCEPTANCE["HyperGrowthNoProfit"]
    c = make("HGNP", 3000, 2000, 600, 150, 400, 500, 520, 54000, 30, -10,
             net_income=base.net_income_ttm)
    r = evaluate(c)
    assert r.track_b and r.passed


@pytest.mark.parametrize("fields,needle", [
    ({}, "no consensus EPS estimates"),
    ({"eps_fy0": -1.0, "eps_fy1": 2.0}, "non-positive consensus EPS"),
    ({"eps_fy0": 41.4, "eps_fy1": 56.4, "estimate_currency": "EUR",
      "low_confidence": ["PEG untestable: consensus EPS in EUR, price in USD"]}, "EUR"),
    ({"eps_fy0": 20.0, "eps_fy1": 24.0, "estimate_currency": "USD",
      "low_confidence": ["PEG untestable: no price for the forward P/E"]}, "no price"),
])
def test_needs_data_hint_names_the_reason_and_the_override_basis(fields, needle):
    c = _kyc()
    for k, v in fields.items():
        setattr(c, k, v)
    r = evaluate(c)
    assert r.status is ScreenStatus.NEEDS_DATA
    (hint,) = r.reasons
    assert needle in hint
    assert "price / FY1 consensus EPS" in hint
    assert "100 x (FY1 / FY0 - 1)" in hint


# --- the four informational fields: cache, overrides, export ---------------
def test_new_fields_follow_the_adding_a_field_checklist():
    assert set(_NEW_FIELDS) <= set(_COMPANY_FIELDS)
    assert set(_NEW_FIELDS) <= _INFORMATIONAL
    assert not set(_NEW_FIELDS) & _REQUIRED
    assert set(_NEW_FIELDS) <= set(COLUMNS)


def test_new_fields_are_overridable_and_exported():
    c = apply_override(_kyc(), {"eps_fy0": 5.0, "eps_fy1": 6.0,
                                "estimate_currency": "USD", "fy1_analysts": 4})
    assert c.manual_override and c.eps_fy1 == 6.0
    (row,) = build_rows([evaluate(c)], [], {})
    assert (row["eps_fy0"], row["eps_fy1"], row["estimate_currency"],
            row["fy1_analysts"]) == ("5.0", "6.0", "USD", 4)
    (blank,) = build_rows([evaluate(_kyc())], [], {})
    assert blank["eps_fy0"] == "N/A" and blank["estimate_currency"] == "N/A"


def test_cache_round_trip_keeps_none_growth(tmp_path, monkeypatch):
    import etf_screen.cache as cache_mod
    from etf_screen.cache import DiskCache
    from etf_screen.providers import MockProvider

    monkeypatch.setattr(cache_mod, "CACHE_ROOT", tmp_path)
    provider = MockProvider(cache=DiskCache("mock"))
    first = provider.fetch("OPAQUE")
    second = provider.fetch("OPAQUE")
    assert first == second and second.forward_eps_growth is None


def test_old_cache_entries_are_not_read(tmp_path, monkeypatch):
    # A pre-#6 entry stored "missing growth" as 0.0, which now means a failed PEG.
    import etf_screen.cache as cache_mod
    from etf_screen.cache import DiskCache
    from etf_screen.providers import MockProvider

    monkeypatch.setattr(cache_mod, "CACHE_ROOT", tmp_path)
    cache = DiskCache("mock")
    stale = asdict(MockProvider().fetch("OPAQUE"))
    stale["forward_pe"], stale["forward_eps_growth"] = 25.0, 0.0
    cache.set("mock_OPAQUE", stale)
    assert MockProvider(cache=cache).fetch("OPAQUE").forward_pe is None


# --- FMP end to end (fake HTTP) ---------------------------------------------
def _fmp_provider(monkeypatch, estimates, profile_ccy="USD", reported_ccy="USD",
                  estimates_error=None):
    from etf_screen.providers import DataUnavailable, FMPProvider

    q = [{"revenue": 100 + i, "weightedAverageShsOutDil": 10, "netIncome": 20,
          "operatingIncome": 25, "reportedCurrency": reported_ccy,
          "date": "2026-09-30"} for i in range(8)]
    cf = [{"operatingCashFlow": 30, "capitalExpenditure": -5,
           "stockBasedCompensation": 2} for _ in range(4)]
    bal = [{"cashAndCashEquivalents": 300, "totalAssets": 1500,
            "totalStockholdersEquity": 1000, "goodwill": 0, "totalDebt": 0}]
    annual = [{"date": "2025-12-31", "reportedCurrency": reported_ccy,
               "revenue": 400, "netIncome": 80}]

    def fake_get(self, path, **params):
        if path.startswith("analyst-estimates"):
            if estimates_error:
                raise DataUnavailable(estimates_error)
            return estimates
        if path.startswith("income-statement"):
            return annual if params.get("period") == "annual" else q
        if path.startswith("cash-flow-statement"):
            return cf
        if path.startswith("balance-sheet-statement"):
            return bal
        if path.startswith("quote"):
            return [{"marketCap": 2000, "price": 480.0, "name": "Fmp Co"}]
        if path.startswith("profile"):
            return [{"sector": "Technology", "industry": "Software",
                     "currency": profile_ccy}]
        raise AssertionError(path)

    monkeypatch.setattr(FMPProvider, "_get", fake_get)
    return FMPProvider(api_key="x", cache=None, fetch_timeout=0)


_FMP_EST = [_row("2026-12-31", 20.0, 30), _row("2027-12-31", 24.0, 30)]


def test_fmp_computes_the_consensus_peg(monkeypatch):
    c = _fmp_provider(monkeypatch, _FMP_EST).fetch("FMP")
    assert c.forward_pe == pytest.approx(20.0)
    assert c.forward_eps_growth == pytest.approx(20.0)
    assert (c.eps_fy0, c.eps_fy1, c.estimate_currency, c.fy1_analysts) == (
        20.0, 24.0, "USD", 30)


def test_fmp_currency_mismatch_is_untestable(monkeypatch):
    from etf_screen.providers import FMPProvider

    # Deterministic FX for the market-cap conversion (D18); never a live Yahoo call.
    monkeypatch.setattr(FMPProvider, "_fx_rate", lambda self, b, q: (1.08, "2026-11-22"))
    c = _fmp_provider(monkeypatch, _FMP_EST, profile_ccy="USD",
                      reported_ccy="EUR").fetch("FMP")
    assert c.forward_pe is None and c.forward_eps_growth is None
    assert any(m.startswith("PEG untestable") and "EUR" in m and "USD" in m
               for m in c.low_confidence)


def test_fmp_unknown_reported_currency_computes_with_note(monkeypatch):
    c = _fmp_provider(monkeypatch, _FMP_EST, reported_ccy=None).fetch("FMP")
    assert c.forward_pe == pytest.approx(20.0)
    assert "estimate currency unverified" in c.low_confidence


def test_fmp_failed_estimates_request_is_needs_data_not_could_not_source(monkeypatch):
    c = _fmp_provider(monkeypatch, [], estimates_error="FMP 403").fetch("FMP")
    assert c.forward_pe is None and c.forward_eps_growth is None
    assert c.eps_fy0 is None and c.eps_fy1 is None  # fetch did not raise DataUnavailable


def test_fmp_pair_tolerates_a_few_days_of_date_skew():
    rows = [_row("2026-01-03", 5.0), _row("2027-01-02", 6.0), _row("2028-01-01", 7.0)]
    *_, shifted = _fmp_fy_pair(rows, "2026-11-20", "2025-12-31")
    assert shifted is False
