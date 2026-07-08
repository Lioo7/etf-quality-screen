"""Tests for the ROIC-history annotation (Feature 1, v0.2).

A multi-year MEDIAN return on invested capital is a stronger durable-moat proxy
than the single-year snapshot ROIC gate, which stays unchanged. This annotation
is purely advisory: it never fails a name and never touches ``passed``/``track``.
"""

import math

import pytest

from etf_screen.screen import AnnualPeriod, ROIC_DURABLE_MIN, evaluate, roic_history
from test_screen import make


def _period(label, ebit=None, debt=None, cash=None, equity=None):
    """A minimal AnnualPeriod for ROIC-history fixtures (revenue/ocf/etc. unused here)."""
    return AnnualPeriod(label, 1000, 400, 50, 50,
                        operating_income=ebit, total_debt=debt,
                        cash_and_equivalents=cash, total_equity=equity)


# --- pure roic_history() ----------------------------------------------------
def test_median_over_usable_years():
    history = [
        _period("2020", ebit=150, debt=0, cash=100, equity=1000),   # IC=900, 16.7%
        _period("2021", ebit=180, debt=0, cash=120, equity=1100),   # IC=980, 18.4%
        _period("2022", ebit=200, debt=0, cash=150, equity=1200),   # IC=1050, 19.0%
    ]
    rh = roic_history(history)
    assert rh.years_used == 3
    assert rh.median == pytest.approx(0.184, abs=0.01)
    assert rh.note == ""  # 3 years clears the thin-evidence floor


def test_durable_requires_median_and_five_years():
    # 5 years, all comfortably >= 15% -> durable.
    history = [_period(str(2020 + i), ebit=200 + i * 10, debt=0,
                       cash=50, equity=1000) for i in range(5)]
    rh = roic_history(history)
    assert rh.years_used == 5
    assert rh.median >= ROIC_DURABLE_MIN
    assert rh.durable


def test_high_median_but_too_few_years_is_not_durable():
    history = [
        _period("2023", ebit=300, debt=0, cash=100, equity=1000),   # IC=900, 33%
        _period("2024", ebit=320, debt=0, cash=120, equity=1050),   # IC=930, 34%
    ]
    rh = roic_history(history)
    assert rh.median >= ROIC_DURABLE_MIN
    assert rh.years_used == 2
    assert not rh.durable
    assert rh.note == "insufficient ROIC history"


def test_negative_invested_capital_year_is_skipped():
    history = [
        _period("2022", ebit=100, debt=0, cash=500, equity=100),    # IC=-400 -> skip
        _period("2023", ebit=150, debt=0, cash=100, equity=1000),   # IC=900, 16.7%
        _period("2024", ebit=160, debt=0, cash=120, equity=1050),   # IC=930, 17.2%
    ]
    rh = roic_history(history)
    assert rh.years_used == 2   # the negative-IC year was excluded, not fabricated
    assert rh.median == pytest.approx((150 / 900 + 160 / 930) / 2, abs=1e-6)


def test_missing_operating_income_year_is_skipped():
    history = [
        _period("2022", ebit=None, debt=0, cash=100, equity=1000),  # no EBIT -> skip
        _period("2023", ebit=150, debt=0, cash=100, equity=1000),
        _period("2024", ebit=160, debt=0, cash=120, equity=1050),
    ]
    rh = roic_history(history)
    assert rh.years_used == 2


def test_no_usable_years_returns_none_median():
    history = [_period("2024", ebit=None, debt=None, cash=None, equity=None)]
    rh = roic_history(history)
    assert rh.median is None and rh.years_used == 0 and not rh.durable


def test_empty_history_returns_none_median():
    rh = roic_history([])
    assert rh.median is None and rh.years_used == 0


# --- integration with evaluate() --------------------------------------------
def test_snapshot_qualifier_gets_roic_history_attached():
    history = [_period(str(2020 + i), ebit=200 + i * 10, debt=0,
                       cash=50, equity=1000) for i in range(5)]
    c = make("MOAT", 1000, 700, 400, 50, 50, 1000, 1000, 5000, 30, 20, net_income=200)
    c.history = history
    r = evaluate(c)
    assert r.passed
    assert r.roic_history is not None
    assert r.roic_history.durable


def test_non_qualifier_has_no_roic_history():
    # PriceyButProfitable-style rejection: never reaches a snapshot qualifier.
    c = make("BAD", 20000, 16000, 7000, 1000, 1200, 1500, 1510, 200000, 60, 12,
             net_income=4000)
    r = evaluate(c)
    assert not r.passed
    assert r.roic_history is None


def test_pre_revenue_has_no_roic_history():
    c = make("PRE", 0, 0, 0, 0, 0, 100, 100, 5000, None, 0, net_income=-50)
    r = evaluate(c)
    assert math.isnan(r.rule40)
    assert r.roic_history is None


# --- the single-year ROIC gate is unchanged ---------------------------------
def test_single_year_roic_gate_still_gates_independently_of_history():
    # Snapshot ROIC 8% (< 10%) still rejects under strict mode, regardless of a
    # strong multi-year history -- the two are independent computations.
    history = [_period(str(2020 + i), ebit=200 + i * 10, debt=0,
                       cash=50, equity=1000) for i in range(5)]
    c = make("BS", 1000, 700, 400, 50, 50, 1000, 1000, 5000, 30, 20, net_income=200,
             total_debt=0, total_equity=1000, cash_and_equivalents=0, operating_income=80)
    c.history = history
    r = evaluate(c, consistency_mode="strict")
    assert not r.passed and r.roic_applied and not r.pass_roic
    assert r.roic == pytest.approx(0.08)
    # The multi-year annotation is still computed and attached, independently.
    assert r.roic_history is not None and r.roic_history.durable
