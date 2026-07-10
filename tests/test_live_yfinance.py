"""Live-network acceptance tests (v0.2.1) — skipped unless RUN_LIVE_TESTS=1.

These hit Yahoo Finance for real and pin the two live-fire regressions from the
2026-07-09 QQQ run: the TTM basis must actually trigger for a mega-cap with
quarterly data, and the IPO date must actually be sourced. They are deliberately
excluded from CI (network, rate limits, data drift) — run locally with:

    RUN_LIVE_TESTS=1 .venv/bin/python -m pytest tests/test_live_yfinance.py -q
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_LIVE_TESTS"),
    reason="live yfinance test; set RUN_LIVE_TESTS=1 to run",
)


@pytest.fixture(scope="module")
def msft():
    from etf_screen.providers import YFinanceProvider
    return YFinanceProvider().fetch("MSFT")


def test_msft_resolves_to_ttm_basis(msft):
    # The v0.2 condition required 8 income quarters; Yahoo publishes ~5, so
    # every name fell back to ANNUAL. MSFT must now resolve to TTM.
    assert msft.basis == "TTM"


def test_msft_growth_is_evaluable(msft):
    from etf_screen.screen import evaluate
    r = evaluate(msft)
    assert r.evaluable
    assert -50 < r.growth < 100  # sane YoY revenue growth, not a NaN artifact


def test_msft_ipo_date_is_sourced(msft):
    # MSFT listed 1986-03-13; Yahoo's first-trade-date field must surface it
    # (firstTradeDateMilliseconds as of yfinance ~2026).
    assert msft.ipo_date is not None
    assert msft.ipo_date.startswith("1986")


def test_keyence_classifies_needs_data():
    # The motivating Fix-3 case: KYCCF (unsponsored ADR) passes every testable
    # gate but has no forward estimates -> NEEDS_DATA, not a PEG rejection.
    from etf_screen.providers import DataUnavailable, YFinanceProvider
    from etf_screen.screen import ScreenStatus, evaluate
    try:
        c = YFinanceProvider().fetch("KYCCF")
    except DataUnavailable as exc:
        pytest.skip(f"KYCCF fundamentals not sourceable live: {exc}")
    r = evaluate(c, consistency_years=3, consistency_mode="trend")
    assert r.status is ScreenStatus.NEEDS_DATA


def test_recent_listing_classifies_recent_ipo():
    # SPCX listed 2026-06-12 -- inside the default 12-month seasoning window,
    # so a live run must classify it RECENT_IPO, not screen it as a normal name.
    from etf_screen.providers import DataUnavailable, YFinanceProvider
    from etf_screen.screen import ScreenStatus, evaluate
    try:
        c = YFinanceProvider().fetch("SPCX")
    except DataUnavailable as exc:
        pytest.skip(f"SPCX fundamentals not sourceable live: {exc}")
    assert c.ipo_date is not None and c.ipo_date.startswith("2026")
    r = evaluate(c)
    assert r.status in (ScreenStatus.RECENT_IPO, ScreenStatus.PRE_REVENUE)
    if r.status is ScreenStatus.RECENT_IPO:
        assert r.recent_ipo
