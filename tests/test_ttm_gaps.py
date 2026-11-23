"""TTM sums need a dated, gap-free window of quarters (#71, D23).

Yahoo's quarterly frames sometimes carry a NaN cell in the middle of their ~5 columns.
Summing "the newest 4 finite cells" then skips that quarter and pulls in the year-ago
one. Every TTM line must instead read the same 4 quarter-end dates: adjacent ends
77-119 days apart (12- and 16-week quarters are valid), newest minus oldest 250-300
days (8-quarter window: 600-660). Any gap moves the whole company to the annual basis
with the dated reason; nothing is summed across a gap and nothing is filled.
"""

from math import isnan

import pandas as pd
import pytest

from etf_screen.providers import DataUnavailable, _extract_yf, _quarter_window

NAN = float("nan")
INFO = {"marketCap": 1_000_000, "shortName": "Test Co"}
QUARTERS = ["2026-09-30", "2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"]
FY = ["2025-12-31", "2024-12-31"]  # stale FY: the D22 blend path when TTM is usable


def _frame(rows: dict, dates: list[str]):
    return pd.DataFrame(rows, index=pd.to_datetime(dates)).T


def _q(dates=QUARTERS, **overrides):
    n = len(dates)
    inc = {"Total Revenue": [130, 120, 115, 110, 100][:n] + [90] * max(0, n - 5),
           "Diluted Average Shares": [10] * n, "Net Income": [20] * n,
           "Operating Income": [25] * n}
    cf = {"Operating Cash Flow": [30] * n, "Capital Expenditure": [-5] * n,
          "Stock Based Compensation": [2] * n}
    for key, vals in overrides.items():
        (inc if key in inc else cf)[key] = vals
    return _frame(inc, dates), _frame(cf, dates)


def _a(dates=FY, sbc=True):
    a_inc = _frame({"Total Revenue": [400, 350], "Diluted Average Shares": [10, 11],
                    "Net Income": [70, 55], "Operating Income": [90, 80]}, dates)
    cf = {"Operating Cash Flow": [118, 100], "Capital Expenditure": [-18, -15]}
    if sbc:
        cf["Stock Based Compensation"] = [9, 8]
    return a_inc, _frame(cf, dates)


def _yf(q_dates=QUARTERS, a_dates=FY, a_sbc=True, **overrides):
    q_inc, q_cf = _q(q_dates, **overrides)
    a_inc, a_cf = _a(a_dates, a_sbc)
    return _extract_yf("TST", q_inc=q_inc, a_inc=a_inc, q_cf=q_cf, a_cf=a_cf, info=INFO)


def _annual_reason(c):
    return next(m for m in c.low_confidence if m.startswith("metrics on ANNUAL basis"))


# --- consecutive quarters: unchanged ------------------------------------------
def test_consecutive_quarters_keep_the_ttm_sums():
    c = _yf()
    assert c.basis == "TTM"
    assert (c.revenue_ttm, c.ocf_ttm, c.capex_ttm, c.sbc_ttm) == (475, 120, 20, 8)
    assert (c.net_income_ttm, c.operating_income) == (80, 100)
    assert (c.diluted_shares_now, c.diluted_shares_prior) == (10, 10)


# --- a gap in revenue only ----------------------------------------------------
def test_revenue_gap_moves_the_company_to_the_annual_basis():
    # Old behavior: 130 + 115 + 110 + 100 = 455, the year-ago quarter in place of Q2.
    c = _yf(**{"Total Revenue": [130, NAN, 115, 110, 100]})
    assert c.basis == "annual"
    assert c.revenue_ttm == 400 and c.revenue_ttm_prior == 350
    assert c.ocf_ttm == 118 and c.net_income_ttm == 70  # every line on one basis
    assert "quarterly revenue missing for 2026-06-30" in _annual_reason(c)


def test_revenue_gap_without_annual_statements_is_a_precise_skip():
    q_inc, q_cf = _q(**{"Total Revenue": [130, NAN, 115, 110, 100]})
    with pytest.raises(DataUnavailable, match="quarterly revenue missing for 2026-06-30"):
        _extract_yf("TST", q_inc=q_inc, a_inc=None, q_cf=q_cf, a_cf=None, info=INFO)


def test_a_missing_quarter_column_is_a_gap():
    # The column itself is absent: adjacent ends 182 days apart.
    dates = ["2026-09-30", "2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30"]
    c = _yf(q_dates=dates)
    assert c.basis == "annual"
    assert "2026-03-31 and 2026-09-30 are 183 days apart" in _annual_reason(c)


def test_net_income_gap_moves_to_annual():
    c = _yf(**{"Net Income": [20, 20, NAN, 20, 20]})
    assert c.basis == "annual" and c.net_income_ttm == 70
    assert "quarterly net income missing for 2026-03-31" in _annual_reason(c)


def test_year_ago_shares_gap_moves_to_annual():
    # shares_q[4] would have read past the year-ago quarter; date-matched it is a gap.
    c = _yf(**{"Diluted Average Shares": [10, 10, 10, 10, NAN]})
    assert c.basis == "annual"
    assert (c.diluted_shares_now, c.diluted_shares_prior) == (10, 11)
    assert "quarterly diluted shares missing for 2025-09-30" in _annual_reason(c)


def test_gap_outside_the_window_and_year_ago_column_keeps_ttm():
    dates = QUARTERS + ["2025-06-30"]
    c = _yf(q_dates=dates, **{"Total Revenue": [130, 120, 115, 110, 100, NAN]})
    assert c.basis == "TTM" and c.revenue_ttm == 475


# --- gaps in cash-flow rows ---------------------------------------------------
def test_ocf_gap_moves_to_annual():
    c = _yf(**{"Operating Cash Flow": [30, NAN, 30, 30, 30]})
    assert c.basis == "annual" and c.ocf_ttm == 118
    assert "quarterly operating cash flow missing for 2026-06-30" in _annual_reason(c)


def test_capex_gap_moves_to_annual():
    c = _yf(**{"Capital Expenditure": [-5, -5, -5, NAN, -5]})
    assert c.basis == "annual" and c.capex_ttm == 18
    assert "quarterly capex missing for 2025-12-31" in _annual_reason(c)


def test_sbc_gap_with_annual_sbc_moves_to_annual():
    c = _yf(**{"Stock Based Compensation": [2, NAN, 2, 2, 2]})
    assert c.basis == "annual" and c.sbc_ttm == 9 and not c.sbc_assumed_zero
    assert "quarterly SBC missing for 2026-06-30" in _annual_reason(c)


def test_sbc_gap_without_annual_sbc_keeps_ttm_with_sbc_assumed_zero():
    # Never a 3-quarter sum: the whole TTM SBC is assumed 0, flagged (#58 rule).
    c = _yf(a_sbc=False, **{"Stock Based Compensation": [2, NAN, 2, 2, 2]})
    assert c.basis == "TTM" and c.sbc_ttm == 0.0 and c.sbc_assumed_zero
    assert "quarterly SBC missing for 2026-06-30 - assumed 0" in c.low_confidence


def test_year_ago_revenue_gap_gets_a_dated_reason():
    c = _yf(**{"Total Revenue": [130, 120, 115, 110, NAN]})
    assert c.basis == "annual"
    assert "quarterly revenue missing for 2025-09-30" in _annual_reason(c)


def test_cash_flow_column_shifted_more_than_ten_days_is_a_gap():
    q_inc, _ = _q()
    shifted = ["2026-09-30", "2026-06-30", "2026-03-15", "2025-12-31", "2025-09-30"]
    _, q_cf = _q(shifted)
    a_inc, a_cf = _a()
    c = _extract_yf("TST", q_inc=q_inc, a_inc=a_inc, q_cf=q_cf, a_cf=a_cf, info=INFO)
    assert c.basis == "annual"
    assert "quarterly operating cash flow missing for 2026-03-31" in _annual_reason(c)


# --- EBIT stays advisory -------------------------------------------------------
def test_ebit_gap_leaves_basis_alone_and_roic_not_computable():
    c = _yf(**{"Operating Income": [25, 25, NAN, 25, 25]})
    assert c.basis == "TTM" and c.revenue_ttm == 475
    assert isnan(c.operating_income)
    assert ("operating income (EBIT) missing for 2026-03-31 - ROIC not computable"
            in c.low_confidence)


# --- unequal fiscal quarters stay on TTM --------------------------------------
# COST: 12/12/12/16-week quarters (Q4 = 112 days). KR: a 16-week Q1.
COST_Q4 = ["2026-08-30", "2026-05-10", "2026-02-15", "2025-11-23", "2025-08-31"]
COST_Q3 = ["2026-05-10", "2026-02-15", "2025-11-23", "2025-08-31", "2025-05-11"]
KR_Q1 = ["2026-05-23", "2026-01-31", "2025-11-08", "2025-08-16", "2025-05-24"]
KR_Q4 = ["2026-01-31", "2025-11-08", "2025-08-16", "2025-05-24", "2025-02-01"]


@pytest.mark.parametrize("dates,fy", [
    (COST_Q4, ["2026-08-30", "2025-08-31"]),   # FY at the newest quarter: exact pair
    (COST_Q3, ["2025-08-31", "2024-09-01"]),   # span 252 days: three 12-week quarters
    (KR_Q1, ["2026-01-31", "2025-02-01"]),
    (KR_Q4, ["2026-01-31", "2025-02-01"]),     # span 252 days
], ids=["cost-q4", "cost-q3", "kr-q1", "kr-q4"])
def test_twelve_and_sixteen_week_quarters_stay_on_ttm(dates, fy):
    c = _yf(q_dates=dates, a_dates=fy)
    assert c.basis == "TTM" and c.revenue_ttm == 475


def test_quarter_window_bounds():
    def win(dates, n=4):
        df = _frame({"Total Revenue": [1.0] * len(dates)}, dates)
        return _quarter_window(df, n)

    assert win(COST_Q4)[0] is not None
    # 120 days between adjacent ends is past a 16-week quarter.
    dates, why = win(["2026-09-30", "2026-06-01", "2026-03-03", "2025-12-03"])
    assert dates is None and "121 days apart" in why
    # Four 77-day steps fit each step but span 231 days, short of a year.
    dates, why = win(["2026-09-30", "2026-07-15", "2026-04-29", "2026-02-11"])
    assert dates is None and "span 231 days" in why
    assert win(["2026-09-30"])[1] == "fewer than 4 quarter columns"


# --- the 8-quarter path --------------------------------------------------------
def test_eight_quarters_with_a_gap_in_the_prior_year_do_not_sum_it():
    # Old behavior: 8 finite cells, so the "prior TTM" summed 100 + 90 + 85 + 80.
    dates = pd.date_range("2026-09-30", periods=9, freq="-1QE").strftime("%Y-%m-%d")
    rev = [130, 120, 115, 110, 100, NAN, 90, 85, 80]
    c = _yf(q_dates=list(dates), a_dates=["2026-09-30", "2025-09-30"],
            **{"Total Revenue": rev})
    # No 8-quarter pair; the FY ending at the newest quarter is the exact prior TTM.
    assert c.basis == "TTM" and c.revenue_ttm == 475 and c.revenue_ttm_prior == 350


def test_eight_consecutive_quarters_still_sum_the_prior_ttm():
    dates = pd.date_range("2026-09-30", periods=8, freq="-1QE").strftime("%Y-%m-%d")
    c = _yf(q_dates=list(dates), **{"Total Revenue": [130, 120, 115, 110, 100, 95, 90, 85]})
    assert c.revenue_ttm == 475 and c.revenue_ttm_prior == 370


# --- FMP -------------------------------------------------------------------------
def test_fmp_quarter_gap_raises_with_the_date(monkeypatch):
    from test_peg_basis import _FMP_EST, _fmp_provider

    dates = ["2026-09-30", "2026-06-30", "2025-12-31", "2025-09-30",
             "2025-06-30", "2025-03-31", "2024-12-31", "2024-09-30"]  # 2026-03-31 absent
    p = _fmp_provider(monkeypatch, _FMP_EST, q_dates=dates)
    with pytest.raises(DataUnavailable, match="2025-12-31 and 2026-06-30 are 181 days"):
        p.fetch("FMP")


def test_fmp_partial_sbc_is_never_summed(monkeypatch):
    from test_peg_basis import _FMP_EST, _fmp_provider

    p = _fmp_provider(monkeypatch, _FMP_EST, cf_sbc=[2, None, 2, 2])
    c = p.fetch("FMP")
    assert c.sbc_ttm == 0.0 and c.sbc_assumed_zero
    assert "quarterly SBC missing for 2026-06-30 - assumed 0" in c.low_confidence


def test_fmp_cash_flow_dates_must_match_income_dates(monkeypatch):
    from test_peg_basis import _FMP_EST, _fmp_provider

    p = _fmp_provider(monkeypatch, _FMP_EST,
                      cf_dates=["2026-09-30", "2026-06-30", "2026-03-31", "2025-09-30"])
    with pytest.raises(DataUnavailable, match="cash flow missing for 2025-12-31"):
        p.fetch("FMP")
