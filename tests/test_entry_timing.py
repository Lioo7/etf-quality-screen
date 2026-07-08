"""Tests for the entry-timing annotations (Feature 4, v0.2).

% off ATH, trailing 3-month return, and the HOT flag (3-mo return > +50% AND
within 20% of ATH). STRICTLY informational: the screen answers "what is worth
owning", not "when to buy" — these fields must never touch pass/fail, tracks,
or ranking. HOT marks a patience case, not a rejection.
"""

from datetime import date

import pandas as pd
import pytest

from etf_screen.providers import _extract_price_history
from etf_screen.screen import evaluate, rank
from test_screen import make

TODAY = date(2026, 7, 8)


def _priced(current=None, ath=None, three_mo=None):
    c = make("TIM", 1000, 700, 400, 50, 50, 1000, 1000, 5000, 30, 20, net_income=200)
    c.current_price, c.ath_price, c.price_3mo_ago = current, ath, three_mo
    return c


# --- Result annotations -------------------------------------------------------
def test_off_ath_and_3mo_return_computed():
    r = evaluate(_priced(current=88, ath=110, three_mo=80))
    assert r.pct_off_ath == pytest.approx(-20.0)
    assert r.three_month_return == pytest.approx(10.0)
    assert not r.hot


def test_hot_requires_both_conditions():
    # Parabolic AND near ATH -> HOT.
    assert evaluate(_priced(current=95, ath=100, three_mo=60)).hot
    # Parabolic but 40% off ATH (recovering from a crash) -> not HOT.
    assert not evaluate(_priced(current=60, ath=100, three_mo=38)).hot
    # Near ATH but a calm 3 months -> not HOT.
    assert not evaluate(_priced(current=95, ath=100, three_mo=90)).hot


def test_missing_prices_render_none_never_fabricated():
    r = evaluate(_priced())
    assert r.pct_off_ath is None and r.three_month_return is None and not r.hot


def test_young_listing_has_no_3mo_return():
    r = evaluate(_priced(current=50, ath=55, three_mo=None))
    assert r.pct_off_ath is not None
    assert r.three_month_return is None and not r.hot


def test_timing_never_affects_verdict_or_rank():
    calm = evaluate(_priced(current=50, ath=100, three_mo=48))
    hot = evaluate(_priced(current=95, ath=100, three_mo=55))
    assert calm.passed and hot.passed and hot.hot   # same verdict either way
    assert calm.track == hot.track == "Track A"
    # Identical fundamentals -> rank order is fully determined by the stable
    # sort, not by the HOT flag.
    assert [r.hot for r in rank([hot, calm])] in ([True, False], [False, True])
    assert all(r.passed for r in rank([hot, calm]))


# --- provider extraction (pure, hand-built series) ------------------------------
def _series(start: str, values):
    idx = pd.date_range(start, periods=len(values), freq="D")
    return pd.Series(values, index=idx)


def test_extract_price_history_basic():
    # 200 daily closes ending 2026-07-08, rising 100 -> 299.
    values = list(range(100, 300))
    closes = _series("2025-12-21", values)
    current, ath, three_mo, span = _extract_price_history(closes, TODAY)
    assert current == 299.0 and ath == 299.0
    # last close on/before 2026-04-09 (90 days back)
    assert three_mo == closes[: "2026-04-09"].iloc[-1]
    assert span == pytest.approx(199 / 365.25, abs=0.01)


def test_extract_price_history_young_listing():
    closes = _series("2026-06-01", [10.0, 11.0, 12.0])   # 3 days of history
    current, ath, three_mo, span = _extract_price_history(closes, TODAY)
    assert current == 12.0 and ath == 12.0
    assert three_mo is None       # nothing on/before the 90-day cutoff
    assert span < 0.1


def test_extract_price_history_empty_or_none():
    assert _extract_price_history(None, TODAY) == (None, None, None, None)
    assert _extract_price_history(_series("2026-01-01", []), TODAY) == (
        None, None, None, None)


def test_ath_is_max_over_available_window_not_latest():
    closes = _series("2026-01-01", [50.0, 120.0, 80.0, 90.0])
    current, ath, _, _ = _extract_price_history(closes, TODAY)
    assert current == 90.0 and ath == 120.0
