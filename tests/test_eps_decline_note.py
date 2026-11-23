"""#53: EPS decline despite revenue growth -> informational note, verdict unchanged."""
import pandas as pd
import pytest

from etf_screen.providers import (EPS_DECLINE_NOTE, _eps_decline_note, _extract_yf,
                                  _yf_revenue_growth)
from etf_screen.screen import ScreenStatus, evaluate
from tests.test_peg_basis import _INFO, _Q_CF, _Q_INC, _a_inc, _estimates, _frame


def _rev(fy0, fy1):
    return pd.DataFrame({"avg": [fy0, fy1]}, index=["0y", "+1y"])


def _yf(eps, rev):
    return _extract_yf("TST", q_inc=_frame(_Q_INC, 5), a_inc=_a_inc(),
                       q_cf=_frame(_Q_CF, 5), a_cf=None, info=_INFO,
                       estimates=eps, revenue_estimates=rev)


def test_revenue_growth_from_the_fake_table():
    assert _yf_revenue_growth(_rev(100.0, 110.0)) == pytest.approx(10.0)


@pytest.mark.parametrize("table", [None, _rev(float("nan"), 110.0), _rev(0.0, 110.0),
                                   pd.DataFrame({"avg": [1.0]}, index=["0y"])])
def test_revenue_growth_missing_is_none(table):
    assert _yf_revenue_growth(table) is None


@pytest.mark.parametrize("eps_g,rev_g,flagged", [
    (-5.0, 8.0, True), (0.0, 8.0, True),     # decline / flat EPS, revenue up
    (-5.0, 0.0, False), (-5.0, -3.0, False),  # revenue not growing
    (5.0, 8.0, False),                       # EPS growing
    (None, 8.0, False), (-5.0, None, False),  # untestable inputs
])
def test_note_rule(eps_g, rev_g, flagged):
    assert (_eps_decline_note(eps_g, rev_g) is not None) is flagged


def test_flagged_name_is_rejected_with_the_note_in_the_reason():
    c = _yf(_estimates(20.0, 15.0), _rev(100.0, 110.0))
    assert EPS_DECLINE_NOTE in c.low_confidence
    r = evaluate(c)
    assert r.status is ScreenStatus.REJECTED
    assert any(EPS_DECLINE_NOTE in x for x in r.reasons)


def test_note_never_changes_the_verdict_or_other_fields():
    with_rev = evaluate(_yf(_estimates(20.0, 15.0), _rev(100.0, 110.0)))
    without = evaluate(_yf(_estimates(20.0, 15.0), None))
    assert (with_rev.status, with_rev.track, with_rev.passed) == (
        without.status, without.track, without.passed)
    assert not any(EPS_DECLINE_NOTE in x for x in without.reasons)


def test_revenue_decline_gets_no_note():
    c = _yf(_estimates(20.0, 15.0), _rev(100.0, 95.0))
    assert EPS_DECLINE_NOTE not in c.low_confidence


def test_growing_eps_gets_no_note():
    c = _yf(_estimates(20.0, 24.0), _rev(100.0, 110.0))
    assert EPS_DECLINE_NOTE not in c.low_confidence


def test_overridden_growth_does_not_carry_a_stale_note_into_the_reason():
    import dataclasses
    c = _yf(_estimates(20.0, 15.0), _rev(100.0, 110.0))
    c = dataclasses.replace(c, forward_eps_growth=10.0, forward_pe=60.0)  # as an override
    r = evaluate(c)  # PEG now fails on valuation (WATCHLIST), not on EPS decline
    assert not any(EPS_DECLINE_NOTE in x for x in r.reasons)
