"""Tests for the pre-revenue firewall (Task 3).

``revenue_ttm <= 0`` short-circuits :func:`evaluate` to ``PRE_REVENUE`` before any
metric math runs — ahead of the divide-by-zero / balance-sheet / consistency
gates. (A name with current revenue > 0 but a zero prior year stays a normal
NOT_EVALUABLE divide-by-zero case — covered in ``test_screen``.)
"""

import math

import pytest

from etf_screen.screen import ScreenStatus, evaluate
from test_screen import make


@pytest.mark.parametrize("rev", [0, -100])
def test_pre_revenue_fires_first(rev):
    # Even with otherwise-broken inputs (zero prior, huge goodwill), the firewall
    # wins: no metric math, no balance-sheet gate, just PRE_REVENUE.
    c = make("PRE", rev, 0, 0, 0, 0, 100, 100, 5000, None, 0,
             net_income=-50, goodwill=9999, total_assets=1, total_debt=9999)
    r = evaluate(c, consistency_years=3, consistency_mode="trend")
    assert r.status is ScreenStatus.PRE_REVENUE
    assert not r.passed and not r.evaluable
    assert r.track == "—"
    assert r.consistency is None
    assert math.isnan(r.rule40)        # metric math never ran
    assert any("pre-revenue" in reason for reason in r.reasons)


def test_positive_revenue_is_not_pre_revenue():
    c = make("OK", 1000, 700, 400, 50, 50, 1000, 1000, 5000, 30, 20, net_income=200)
    r = evaluate(c)
    assert r.status is not ScreenStatus.PRE_REVENUE and r.evaluable
