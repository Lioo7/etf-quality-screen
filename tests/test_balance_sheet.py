"""Tests for the three current-snapshot balance-sheet gates (Task 3).

Leverage (net debt / FCF) and goodwill / assets apply to every snapshot
qualifier; ROIC is gated only for a Track-A name under ``strict`` mode and is
advisory (never a failure) otherwise or when it cannot be computed. These
fixtures isolate each gate so a single rejection reason is asserted.
"""

import math

import pytest

from etf_screen.screen import ScreenStatus, evaluate
from test_screen import make


# A clean Track-A snapshot (growth 42.9%, Rule40 72.9, P/S 5, PEG 1.5, profitable).
def _track_a(**bs):
    return make("BS", 1000, 700, 400, 50, 50, 1000, 1000, 5000, 30, 20,
                net_income=200, **bs)


# --- leverage ---------------------------------------------------------------
def test_leverage_4x_is_rejected():
    # net debt 1400 / FCF 350 = 4.0x (> 3.0). ROIC kept healthy to isolate leverage.
    c = _track_a(total_debt=1400, cash_and_equivalents=0, operating_income=300)
    r = evaluate(c)
    assert r.status is ScreenStatus.REJECTED and not r.pass_leverage
    assert r.net_debt_to_fcf == pytest.approx(4.0)
    assert any("Net Debt/FCF" in reason for reason in r.reasons)


def test_net_cash_passes_leverage():
    c = _track_a(total_debt=0, cash_and_equivalents=500)
    r = evaluate(c)
    assert r.passed and r.pass_leverage
    assert r.net_debt < 0 and r.net_debt_to_fcf == 0.0


def test_net_debt_with_nonpositive_fcf_fails():
    # ocf == capex -> FCF 0 (adj margin -2% keeps Rule40 at 40.9, clearing the
    # snapshot); net debt 500 -> no cash to service it -> leverage FAIL.
    c = make("BS", 1000, 700, 100, 100, 20, 1000, 1000, 5000, 30, 20,
             net_income=200, total_debt=500, cash_and_equivalents=0,
             operating_income=300)
    r = evaluate(c)
    assert r.status is ScreenStatus.REJECTED and not r.pass_leverage
    assert math.isnan(r.net_debt_to_fcf)
    assert any("non-positive FCF" in reason for reason in r.reasons)


# --- goodwill ---------------------------------------------------------------
def test_goodwill_60pct_is_rejected():
    c = _track_a(goodwill=1200, total_assets=2000)   # 0.60 > 0.40
    r = evaluate(c)
    assert r.status is ScreenStatus.REJECTED and not r.pass_goodwill
    assert r.goodwill_to_assets == pytest.approx(0.60)
    assert any("Goodwill/Assets" in reason for reason in r.reasons)


def test_goodwill_10pct_passes():
    c = _track_a(goodwill=100, total_assets=1000)    # 0.10 <= 0.40
    r = evaluate(c)
    assert r.passed and r.pass_goodwill
    assert r.goodwill_to_assets == pytest.approx(0.10)


# --- ROIC (Track A + strict only) -------------------------------------------
def test_roic_8pct_track_a_strict_is_rejected():
    # invested capital 1000, EBIT 80 -> ROIC 8% (< 10%).
    c = _track_a(total_debt=0, total_equity=1000, cash_and_equivalents=0,
                 operating_income=80)
    r = evaluate(c, consistency_mode="strict")
    assert r.status is ScreenStatus.REJECTED
    assert r.roic_applied and not r.pass_roic
    assert r.roic == pytest.approx(0.08)
    assert any("ROIC" in reason for reason in r.reasons)


def test_same_roic_8pct_track_a_trend_is_bypassed():
    c = _track_a(total_debt=0, total_equity=1000, cash_and_equivalents=0,
                 operating_income=80)
    r = evaluate(c, consistency_mode="trend")
    assert r.passed and not r.roic_applied
    assert r.roic == pytest.approx(0.08)   # still computed for export


def test_track_b_negative_ebit_bypasses_roic():
    # GAAP-unprofitable Track-B name with negative EBIT -> ROIC never gates it.
    c = make("TB", 3000, 2000, 600, 150, 400, 500, 500, 54000, None, 40,
             net_income=-200, operating_income=-50, total_equity=1000)
    r = evaluate(c, consistency_mode="strict")
    assert r.passed and r.track == "Track B"
    assert not r.roic_applied
    assert r.roic is not None and r.roic < 0


def test_noncomputable_roic_is_advisory_not_failure():
    # invested capital <= 0 -> ROIC None; applied (Track A strict) but never fails.
    c = _track_a(total_debt=0, total_equity=100, cash_and_equivalents=200)
    r = evaluate(c, consistency_mode="strict")
    assert r.passed
    assert r.invested_capital <= 0 and r.roic is None
    assert r.roic_applied and r.pass_roic
