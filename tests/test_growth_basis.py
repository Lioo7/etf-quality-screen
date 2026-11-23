"""Growth definition for the Rule of 40 and the P/S guardrail (#67, D22).

One growth number feeds both tests. Precedence:
1. a true TTM pair (8 quarters, FMP, or a fiscal year ending at the newest quarter);
2. else the estimated TTM growth: the mean of latest-FY growth and the latest-quarter YoY;
3. else the annual basis (never the quarter YoY alone).
"""

from math import isnan

import pandas as pd
import pytest

from etf_screen.cache import DiskCache
from etf_screen.export import COLUMNS, build_rows
from etf_screen.overrides import apply_override
from etf_screen.providers import DataProvider, DataUnavailable, _extract_yf
from etf_screen.screen import (GROWTH_BLEND_NOTE, GROWTH_DECEL_NOTE_PTS, Company,
                               ScreenStatus, evaluate, rank)

INFO = {"marketCap": 1_000_000, "shortName": "Test Co"}

QUARTERS = ["2026-09-30", "2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"]
Q_REV = [130, 120, 115, 110, 100]  # newest quarter +30% YoY; TTM 475


def _frame(rows: dict, dates: list[str]):
    cols = pd.to_datetime(dates)
    return pd.DataFrame({k: v[:len(dates)] for k, v in rows.items()}, index=cols).T


def _q_frames(dates=QUARTERS, revenue=Q_REV):
    n = len(dates)
    q_inc = _frame({"Total Revenue": revenue, "Diluted Average Shares": [10] * n,
                    "Net Income": [20] * n}, dates)
    q_cf = _frame({"Operating Cash Flow": [30] * n, "Capital Expenditure": [-5] * n,
                   "Stock Based Compensation": [2] * n}, dates)
    return q_inc, q_cf


def _a_frames(dates, revenue):
    n = len(dates)
    a_inc = _frame({"Total Revenue": revenue, "Diluted Average Shares": [10] * n,
                    "Net Income": [70] * n}, dates)
    a_cf = _frame({"Operating Cash Flow": [118] * n, "Capital Expenditure": [-18] * n,
                   "Stock Based Compensation": [9] * n}, dates)
    return a_inc, a_cf


def _yf(a_dates, a_rev, q_dates=QUARTERS, q_rev=Q_REV):
    q_inc, q_cf = _q_frames(q_dates, q_rev)
    a_inc, a_cf = _a_frames(a_dates, a_rev) if a_dates else (None, None)
    return _extract_yf("TST", q_inc=q_inc, a_inc=a_inc, q_cf=q_cf, a_cf=a_cf, info=INFO)


# --- provider: one fixture per data shape -------------------------------------
def test_five_quarters_and_stale_fy_blend_fy_and_quarter_growth():
    # FY ended 9 months before the newest quarter: the TTM pair can't be summed,
    # so growth is the mean of FY growth (400/350) and the quarter YoY (130/100).
    c = _yf(["2025-12-31", "2024-12-31"], [400, 350])
    assert c.basis == "TTM" and c.revenue_ttm == 475
    assert isnan(c.revenue_ttm_prior)
    assert (c.revenue_fy, c.revenue_fy_prior) == (400.0, 350.0)
    assert (c.revenue_latest_q, c.revenue_year_ago_q) == (130.0, 100.0)
    blend = [m for m in c.low_confidence if m.startswith(GROWTH_BLEND_NOTE)]
    assert blend and "2025-12-31" in blend[0]
    r = evaluate(c)
    assert r.growth == pytest.approx((100 * (400 / 350 - 1) + 30.0) / 2)
    assert r.growth_basis == "FY+quarter blend"
    assert r.growth_quarter_yoy == pytest.approx(30.0)


def test_fy_ending_at_newest_quarter_is_a_true_ttm_pair():
    # The latest FY ends on the newest quarter, so it IS the trailing year and the
    # prior FY is the prior TTM: an exact pair, no blend, no quarter noise.
    c = _yf(["2026-09-30", "2025-09-30"], [475, 380])
    assert c.revenue_ttm_prior == 380.0
    assert c.revenue_fy is None and c.revenue_fy_prior is None
    assert not any(m.startswith(GROWTH_BLEND_NOTE) for m in c.low_confidence)
    r = evaluate(c)
    assert r.growth == pytest.approx(100 * (475 / 380 - 1))
    assert r.growth_basis == "TTM"


def test_eight_quarters_true_ttm_wins_and_quarter_yoy_stays_informational():
    dates = [str(d.date()) for d in pd.date_range("2026-09-30", periods=8, freq="-1QE")]
    q_rev = [130, 120, 115, 110, 100, 95, 90, 85]
    c = _yf(["2025-12-31", "2024-12-31"], [400, 350], q_dates=dates, q_rev=q_rev)
    assert c.revenue_ttm == 475 and c.revenue_ttm_prior == 370
    assert c.revenue_fy is None
    assert (c.revenue_latest_q, c.revenue_year_ago_q) == (130.0, 100.0)
    r = evaluate(c)
    assert r.growth == pytest.approx(100 * (475 / 370 - 1))
    assert r.growth_basis == "TTM" and r.growth_quarter_yoy == pytest.approx(30.0)


def test_fiscal_year_change_is_a_precise_skip_not_stub_period_growth():
    # Two annual columns 7 months apart (a fiscal-year change): no aligned FY pair,
    # so no blend; the quarter YoY alone never drives growth, and the annual basis
    # must not compare a stub period with a full year either.
    with pytest.raises(DataUnavailable, match="fiscal-year change"):
        _yf(["2025-12-31", "2025-05-31"], [230, 350])


def test_gappy_quarters_never_take_the_fy_is_ttm_path():
    # The 2025-12-31 quarter is missing, so the 5th column is 15 months back: no
    # aligned quarter pair, and diluted_shares_prior can't be trusted as year-ago.
    # Even with the FY ending at the newest quarter, the name goes annual.
    dates = ["2026-09-30", "2026-06-30", "2026-03-31", "2025-09-30", "2025-06-30"]
    c = _yf(["2026-09-30", "2025-09-30"], [475, 380], q_dates=dates)
    assert c.basis == "annual"
    assert (c.revenue_ttm, c.revenue_ttm_prior) == (475, 380)


def test_fy_older_than_three_quarters_falls_back_to_annual():
    # The annual frame lags (newest FY ended > 300 days before the newest quarter):
    # it no longer overlaps the trailing year, so it can't anchor the estimate.
    c = _yf(["2024-12-31", "2023-12-31"], [400, 350])
    assert c.basis == "annual"


def test_single_fiscal_year_is_a_precise_skip():
    with pytest.raises(DataUnavailable, match="annual revenue history too short"):
        _yf(["2025-12-31"], [400])


def test_no_annual_frame_is_a_precise_skip():
    with pytest.raises(DataUnavailable, match="annual revenue history too short"):
        _yf(None, None)


# --- screen: precedence and divide-by-zero honesty ------------------------------
def _company(**kw):
    base = dict(ticker="TST", revenue_ttm=1000, revenue_ttm_prior=700, ocf_ttm=400,
                capex_ttm=50, sbc_ttm=50, diluted_shares_now=1000,
                diluted_shares_prior=1000, market_cap=5000, forward_pe=30.0,
                forward_eps_growth=20.0, net_income_ttm=200)
    base.update(kw)
    return Company(**base)


def test_ttm_pair_beats_blend_fields():
    r = evaluate(_company(revenue_fy=400.0, revenue_fy_prior=200.0,
                          revenue_latest_q=130.0, revenue_year_ago_q=100.0))
    assert r.growth == pytest.approx(100 * (1000 / 700 - 1))
    assert r.growth_basis == "TTM"


def test_blend_needs_both_positive_bases():
    nan = float("nan")
    fy_zero = evaluate(_company(revenue_ttm_prior=nan, revenue_fy=400.0,
                                revenue_fy_prior=0.0, revenue_latest_q=130.0,
                                revenue_year_ago_q=100.0))
    assert fy_zero.status is ScreenStatus.NOT_EVALUABLE
    quarter_only = evaluate(_company(revenue_ttm_prior=nan, revenue_latest_q=130.0,
                                     revenue_year_ago_q=100.0))
    assert quarter_only.status is ScreenStatus.NOT_EVALUABLE


# --- deceleration note: advisory only --------------------------------------------
def test_deceleration_note_fires_more_than_15_points_below_and_never_gates():
    plain = evaluate(_company())
    slow = evaluate(_company(revenue_latest_q=100.0, revenue_year_ago_q=95.0))  # +5.3%
    assert slow.growth == plain.growth  # the TTM pair decides growth
    assert plain.growth - slow.growth_quarter_yoy > GROWTH_DECEL_NOTE_PTS
    note = [n for n in slow.low_confidence_notes if n.startswith("growth decelerating")]
    assert note == [f"growth decelerating: latest quarter YoY 5.3% vs growth used "
                    f"{plain.growth:.1f}%"]
    assert not any(n.startswith("growth decelerating") for n in plain.low_confidence_notes)
    for attr in ("status", "passed", "track", "reasons"):
        assert getattr(slow, attr) == getattr(plain, attr)
    assert [x.ticker for x in rank([slow])] == [x.ticker for x in rank([plain])]


def test_no_deceleration_note_within_15_points():
    r = evaluate(_company(revenue_latest_q=140.0, revenue_year_ago_q=100.0))  # 40% vs 42.9%
    assert not any(n.startswith("growth decelerating") for n in r.low_confidence_notes)


# --- export, cache, overrides ------------------------------------------------------
def test_export_columns_carry_basis_and_quarter_yoy():
    assert {"growth_basis", "growth_quarter_yoy"} <= set(COLUMNS)
    with_q = build_rows([evaluate(_company(revenue_latest_q=130.0,
                                           revenue_year_ago_q=100.0))], [], {})[0]
    assert with_q["growth_basis"] == "TTM" and with_q["growth_quarter_yoy"] == "30.0"
    without = build_rows([evaluate(_company())], [], {})[0]
    assert without["growth_quarter_yoy"] == "N/A"


def test_fy_pair_survives_the_cache(tmp_path, monkeypatch):
    import etf_screen.cache as cache_mod

    monkeypatch.setattr(cache_mod, "CACHE_ROOT", tmp_path)
    company = _yf(["2025-12-31", "2024-12-31"], [400, 350])

    class Fixed(DataProvider):
        name = "fixed"

        def _fetch_uncached(self, ticker):
            return company

    provider = Fixed(cache=DiskCache("fixed"))
    provider.fetch("TST")
    again = provider.fetch("TST")  # reconstructed from JSON on disk
    assert (again.revenue_fy, again.revenue_fy_prior) == (400.0, 350.0)
    assert evaluate(again).growth == pytest.approx(evaluate(company).growth)


def test_override_basis_is_labelled_as_override_not_ttm():
    assert evaluate(_company(basis="override")).growth_basis == "override"


def test_overriding_the_prior_ttm_drops_the_stale_blend_note():
    c = _yf(["2025-12-31", "2024-12-31"], [400, 350])
    patched = apply_override(c, {"revenue_ttm_prior": 380.0})
    assert not any(m.startswith(GROWTH_BLEND_NOTE) for m in patched.low_confidence)
    assert evaluate(patched).growth_basis == "TTM"
    assert evaluate(patched).growth == pytest.approx(100 * (475 / 380 - 1))


def test_fy_pair_is_overridable():
    c = apply_override(_company(revenue_ttm_prior=float("nan"), revenue_latest_q=130.0,
                                revenue_year_ago_q=100.0),
                       {"revenue_fy": 440.0, "revenue_fy_prior": 400.0})
    assert evaluate(c).growth == pytest.approx((10.0 + 30.0) / 2)
