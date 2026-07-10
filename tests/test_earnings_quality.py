"""Tests for the earnings-quality gate (Feature 3, v0.2).

Cumulative 3-year adjusted-FCF / net-income >= 0.8 for names with cumulative
net income > 0 -- a rejecting gate in the balance-sheet-gate family (layers
onto a snapshot qualifier only; never rescues). Unprofitable-cumulative names
are N/A, never a fail: Track B's own quality gate already covers them.
"""

import pytest

from etf_screen.screen import AnnualPeriod, ScreenStatus, _earnings_quality, evaluate
from test_screen import make


def _period(label, rev, ocf, capex, sbc, net_income=None):
    return AnnualPeriod(label, rev, ocf, capex, sbc, net_income=net_income)


def _track_a(history):
    """A clean Track-A snapshot (matches tests/test_balance_sheet.py's fixture)."""
    c = make("EQ", 1000, 700, 400, 50, 50, 1000, 1000, 5000, 30, 20, net_income=200)
    c.history = history
    return c


# --- pure _earnings_quality() ------------------------------------------------
def test_high_quality_passes():
    history = [
        _period("2022", 650, 260, 30, 30, net_income=100),
        _period("2023", 845, 340, 40, 40, net_income=100),
        _period("2024", 1100, 440, 50, 50, net_income=100),
    ]
    applicable, ratio, years, passed, capex_pct = _earnings_quality(history)
    # cum adj FCF = 200+260+340=800, cum NI = 300 -> ratio > 0.8 (>1 even)
    assert applicable and passed
    assert years == 3
    assert ratio == pytest.approx(800 / 300)


def test_low_quality_fails():
    history = [
        _period("2022", 650, 260, 30, 30, net_income=400),
        _period("2023", 845, 340, 40, 40, net_income=400),
        _period("2024", 1100, 440, 50, 50, net_income=400),
    ]
    applicable, ratio, years, passed, capex_pct = _earnings_quality(history)
    assert applicable and not passed
    assert ratio == pytest.approx(800 / 1200)


def test_cumulative_ni_non_positive_is_na():
    history = [
        _period("2022", 650, 260, 30, 30, net_income=-50),
        _period("2023", 845, 340, 40, 40, net_income=20),
        _period("2024", 1100, 440, 50, 50, net_income=10),
    ]
    applicable, ratio, years, passed, capex_pct = _earnings_quality(history)
    assert not applicable and passed  # N/A, never a fail
    assert ratio is None


def test_no_net_income_data_is_na():
    history = [_period("2024", 1100, 440, 50, 50, net_income=None)]
    applicable, ratio, years, passed, capex_pct = _earnings_quality(history)
    assert not applicable and passed and years == 0


def test_shorter_basis_when_only_two_years_available():
    history = [
        _period("2023", 845, 340, 40, 40, net_income=100),
        _period("2024", 1100, 440, 50, 50, net_income=100),
    ]
    applicable, ratio, years, passed, capex_pct = _earnings_quality(history)
    assert applicable and years == 2
    assert ratio == pytest.approx((260 + 340) / 200)


def test_window_caps_at_three_years_even_with_more_history():
    history = [
        _period("2021", 500, 200, 25, 25, net_income=5000),   # outside window
        _period("2022", 650, 260, 30, 30, net_income=100),
        _period("2023", 845, 340, 40, 40, net_income=100),
        _period("2024", 1100, 440, 50, 50, net_income=100),
    ]
    applicable, ratio, years, passed, capex_pct = _earnings_quality(history)
    assert years == 3   # 2021 (outside the trailing 3-yr window) is ignored


def test_middle_year_missing_net_income_is_skipped_not_fabricated():
    history = [
        _period("2022", 650, 260, 30, 30, net_income=100),
        _period("2023", 845, 340, 40, 40, net_income=None),  # gap
        _period("2024", 1100, 440, 50, 50, net_income=100),
    ]
    applicable, ratio, years, passed, capex_pct = _earnings_quality(history)
    assert years == 2   # only the two years WITH a net-income figure are used
    assert ratio == pytest.approx((200 + 340) / 200)


# --- capex-intensity annotation (v0.2.1 Fix 4; informational, never a gate) ---
def test_capex_intense_eq_failure_carries_the_reinvestment_note():
    # The NVDA/TSM live-fire case: EQ fails arithmetically, but capex over the
    # same window is ~20% of revenue -> the shortfall may be reinvestment, not
    # accrual rot. The gate STILL fails; the note flags the ambiguity.
    history = [
        _period("2022", 650, 260, 130, 30, net_income=400),
        _period("2023", 845, 340, 170, 40, net_income=400),
        _period("2024", 1100, 440, 220, 50, net_income=400),
    ]
    applicable, ratio, years, passed, capex_pct = _earnings_quality(history)
    assert applicable and not passed
    assert capex_pct == pytest.approx((130 + 170 + 220) / (650 + 845 + 1100) * 100)
    r = evaluate(_track_a(history))
    assert r.status is ScreenStatus.REJECTED           # threshold unchanged
    assert r.eq_capex_intensity == pytest.approx(capex_pct)
    assert any("capex intensity" in reason for reason in r.reasons)


def test_low_capex_eq_failure_has_no_reinvestment_note():
    # Same failing ratio but capex ~4% of revenue: the shortfall reads as
    # accruals, so no softening note is attached.
    history = [
        _period("2022", 650, 260, 30, 30, net_income=400),
        _period("2023", 845, 340, 40, 40, net_income=400),
        _period("2024", 1100, 440, 50, 50, net_income=400),
    ]
    r = evaluate(_track_a(history))
    assert r.status is ScreenStatus.REJECTED
    assert not any("capex intensity" in reason for reason in r.reasons)


def test_capex_intensity_never_rescues_or_rejects():
    # A PASSING name with intense capex keeps its verdict and gets no note in
    # reasons -- the field is informational and only disambiguates a failure.
    history = [
        _period("2022", 650, 460, 130, 30, net_income=100),
        _period("2023", 845, 570, 170, 40, net_income=100),
        _period("2024", 1100, 740, 220, 50, net_income=100),
    ]
    r = evaluate(_track_a(history))
    assert r.passed and r.pass_earnings_quality
    assert r.eq_capex_intensity is not None and r.eq_capex_intensity > 15
    assert r.reasons == []


def test_capex_intensity_populated_even_when_eq_is_na():
    # Informational field: computable from revenue/capex even when the gate is
    # N/A (cumulative NI <= 0) -- the window still describes the business.
    history = [
        _period("2022", 650, 260, 130, 30, net_income=-50),
        _period("2023", 845, 340, 170, 40, net_income=-20),
        _period("2024", 1100, 440, 220, 50, net_income=-10),
    ]
    applicable, ratio, years, passed, capex_pct = _earnings_quality(history)
    assert not applicable and passed
    assert capex_pct == pytest.approx((130 + 170 + 220) / (650 + 845 + 1100) * 100)


# --- integration with evaluate() --------------------------------------------
def test_low_quality_rejects_a_clean_track_a_snapshot():
    history = [
        _period("2022", 650, 260, 30, 30, net_income=400),
        _period("2023", 845, 340, 40, 40, net_income=400),
        _period("2024", 1100, 440, 50, 50, net_income=400),
    ]
    r = evaluate(_track_a(history))
    assert r.status is ScreenStatus.REJECTED
    assert not r.pass_earnings_quality and r.earnings_quality_applicable
    assert any("earnings quality" in reason for reason in r.reasons)


def test_high_quality_leaves_a_clean_track_a_snapshot_passing():
    history = [
        _period("2022", 650, 260, 30, 30, net_income=100),
        _period("2023", 845, 340, 40, 40, net_income=100),
        _period("2024", 1100, 440, 50, 50, net_income=100),
    ]
    r = evaluate(_track_a(history))
    assert r.passed
    assert r.pass_earnings_quality and r.earnings_quality_applicable


def test_unprofitable_track_b_name_is_never_blocked_by_earnings_quality():
    # Track B name (GAAP-unprofitable) with cumulative NI <= 0 -> gate is N/A,
    # never fails, regardless of adjusted-FCF history.
    c = make("TB", 3500, 2400, 1100, 120, 500, 245, 235, 70000, None, 30.0,
             net_income=-150)
    c.history = [
        _period("2022", 1600, 500, 60, 250, net_income=-300),
        _period("2023", 2400, 800, 90, 380, net_income=-100),
        _period("2024", 3500, 1100, 120, 500, net_income=-50),
    ]
    r = evaluate(c)
    assert r.passed and r.track == "Track B"
    assert not r.earnings_quality_applicable and r.pass_earnings_quality


def test_snapshot_failer_never_reaches_earnings_quality_reasons():
    # Fails P/S outright -- earnings-quality reason must not appear (gate
    # layers onto a qualifier only, mirroring the other balance-sheet gates).
    history = [
        _period("2022", 650, 260, 30, 30, net_income=400),
        _period("2023", 845, 340, 40, 40, net_income=400),
        _period("2024", 1100, 440, 50, 50, net_income=400),
    ]
    c = make("BAD", 1000, 700, 400, 50, 50, 1000, 1000, 50000, 30, 20, net_income=200)
    c.history = history
    r = evaluate(c)
    assert not r.passed
    assert not any("earnings quality" in reason for reason in r.reasons)


def test_pre_revenue_has_moot_earnings_quality_fields():
    c = make("PRE", 0, 0, 0, 0, 0, 100, 100, 5000, None, 0, net_income=-50)
    r = evaluate(c)
    assert r.status is ScreenStatus.PRE_REVENUE
    assert not r.earnings_quality_applicable and r.earnings_quality_ratio is None
