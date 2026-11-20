"""Annual history when the SBC line is absent vs present-but-NaN (issue #4)."""

import pandas as pd

from etf_screen.providers import _annual_history, _fmp_annual_history

YEARS = pd.to_datetime(["2025-12-31", "2024-12-31", "2023-12-31", "2022-12-31"])


def _df(rows: dict):
    return pd.DataFrame(rows, index=YEARS).T


A_INC = _df({"Total Revenue": [400.0, 350.0, 300.0, 250.0]})


def test_absent_sbc_row_gives_full_history_with_zero():
    a_cf = _df({"Operating Cash Flow": [80.0, 70.0, 60.0, 50.0],
                "Capital Expenditure": [-10.0, -9.0, -8.0, -7.0]})
    hist = _annual_history(A_INC, a_cf)
    assert len(hist) == 4
    assert all(p.sbc == 0.0 for p in hist)
    assert [p.fiscal_label for p in hist] == ["2022", "2023", "2024", "2025"]


def test_nan_sbc_in_middle_year_still_truncates():
    nan = float("nan")
    a_cf = _df({"Operating Cash Flow": [80.0, 70.0, 60.0, 50.0],
                "Capital Expenditure": [-10.0, -9.0, -8.0, -7.0],
                "Stock Based Compensation": [5.0, nan, 3.0, 2.0]})
    hist = _annual_history(A_INC, a_cf)
    assert [p.fiscal_label for p in hist] == ["2025"]


def test_present_sbc_row_unchanged():
    a_cf = _df({"Operating Cash Flow": [80.0, 70.0, 60.0, 50.0],
                "Capital Expenditure": [-10.0, -9.0, -8.0, -7.0],
                "Stock Based Compensation": [5.0, 4.0, 3.0, 2.0]})
    hist = _annual_history(A_INC, a_cf)
    assert [p.sbc for p in hist] == [2.0, 3.0, 4.0, 5.0]


def _fmp(years, sbc):
    inc = [{"calendarYear": y, "revenue": 100.0 + i} for i, y in enumerate(years)]
    cf = []
    for y in years:
        rec = {"calendarYear": y, "operatingCashFlow": 20.0, "capitalExpenditure": -5.0}
        if sbc is not None:
            rec["stockBasedCompensation"] = sbc[y]
        cf.append(rec)
    return inc, cf


def test_fmp_absent_sbc_gives_full_history_with_zero():
    inc, cf = _fmp(["2025", "2024", "2023"], None)
    hist = _fmp_annual_history(inc, cf)
    assert len(hist) == 3 and all(p.sbc == 0.0 for p in hist)


def test_fmp_null_sbc_in_one_year_still_truncates():
    inc, cf = _fmp(["2025", "2024", "2023"], {"2025": 1.0, "2024": None, "2023": 1.0})
    assert [p.fiscal_label for p in _fmp_annual_history(inc, cf)] == ["2025"]


def test_extract_yf_flags_annual_sbc_assumed_zero_when_quarterly_has_it():
    from etf_screen.providers import _extract_yf
    qcols = pd.date_range("2025-12-31", periods=8, freq="-1QE")
    q_inc = pd.DataFrame({"Total Revenue": [100] * 8, "Diluted Average Shares": [10] * 8,
                          "Net Income": [20] * 8}, index=qcols).T
    q_cf = pd.DataFrame({"Operating Cash Flow": [30] * 8, "Capital Expenditure": [-5] * 8,
                         "Stock Based Compensation": [2] * 8}, index=qcols).T
    a_cf = _df({"Operating Cash Flow": [80.0, 70.0, 60.0, 50.0],
                "Capital Expenditure": [-10.0, -9.0, -8.0, -7.0]})
    info = {"marketCap": 1_000_000, "shortName": "T", "forwardPE": 20.0,
            "forwardEps": 6.0, "trailingEps": 5.0}
    c = _extract_yf("TST", q_inc=q_inc, a_inc=A_INC, q_cf=q_cf, a_cf=a_cf, info=info)
    assert not c.sbc_assumed_zero
    assert any("annual SBC" in m for m in c.low_confidence)
