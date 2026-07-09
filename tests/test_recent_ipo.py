"""Tests for the RECENT_IPO classification (Feature 6, v0.2).

A name whose first trading date falls inside the seasoning window (default 12
months) is set aside as RECENT_IPO — never in the shortlist, reported in its
own section like INSUFFICIENT_HISTORY. An unknown IPO date is flagged and the
name is screened normally: never guessed, never silently excluded.
"""

from datetime import date

from etf_screen.screen import ScreenStatus, _ipo_is_recent, _months_before, evaluate
from test_screen import make

TODAY = date(2026, 7, 8)


def _passing(ipo_date=None):
    """A clean Track-A snapshot; `ipo_date` drives the seasoning check."""
    c = make("IPO", 1000, 700, 400, 50, 50, 1000, 1000, 5000, 30, 20, net_income=200)
    c.ipo_date = ipo_date
    return c


# --- pure helpers -------------------------------------------------------------
def test_months_before_regular():
    assert _months_before(date(2026, 7, 8), 12) == date(2025, 7, 8)
    assert _months_before(date(2026, 7, 8), 6) == date(2026, 1, 8)


def test_months_before_clamps_month_end():
    # 12 months before Feb-29 of a leap year -> Feb-28.
    assert _months_before(date(2028, 2, 29), 12) == date(2027, 2, 28)
    # 3 months before May-31 -> Feb-28 (2026 not a leap year).
    assert _months_before(date(2026, 5, 31), 3) == date(2026, 2, 28)


def test_ipo_is_recent_boundaries():
    assert _ipo_is_recent("2026-03-01", 12, TODAY)          # 4 months ago
    assert not _ipo_is_recent("2020-01-15", 12, TODAY)      # seasoned
    assert not _ipo_is_recent("2025-07-08", 12, TODAY)      # exactly 12 mo -> seasoned
    assert _ipo_is_recent("2025-07-09", 12, TODAY)          # one day inside


def test_unknown_or_bad_dates_are_never_recent():
    assert not _ipo_is_recent(None, 12, TODAY)
    assert not _ipo_is_recent("not-a-date", 12, TODAY)
    assert not _ipo_is_recent("", 12, TODAY)


def test_zero_months_disables_the_check():
    assert not _ipo_is_recent("2026-07-01", 0, TODAY)


# --- integration with evaluate() ----------------------------------------------
def test_recent_ipo_overrides_a_would_be_pass():
    c = _passing(ipo_date="2026-03-01")
    assert evaluate(c, today=TODAY, ipo_seasoning_months=0).passed  # sanity
    r = evaluate(c, today=TODAY)
    assert r.status is ScreenStatus.RECENT_IPO and r.recent_ipo
    assert not r.passed and r.track == "—"
    assert any("recent IPO" in reason for reason in r.reasons)


def test_recent_ipo_overrides_a_would_be_rejection_too():
    # Even a snapshot-failing name is classified RECENT_IPO, not REJECTED —
    # judging an unseasoned listing on distorted inputs is false confidence.
    c = make("BADIPO", 20000, 16000, 7000, 1000, 1200, 1500, 1510, 200000, 60, 12,
             net_income=4000)
    c.ipo_date = "2026-05-01"
    r = evaluate(c, today=TODAY)
    assert r.status is ScreenStatus.RECENT_IPO


def test_seasoned_listing_screens_normally():
    r = evaluate(_passing(ipo_date="2019-06-01"), today=TODAY)
    assert r.passed and not r.recent_ipo and not r.ipo_date_unknown


def test_unknown_ipo_date_screens_normally_and_is_flagged():
    r = evaluate(_passing(ipo_date=None), today=TODAY)
    assert r.passed
    assert r.ipo_date_unknown


def test_recent_ipo_sheds_watchlist_unlock_prices():
    # A would-be-WATCHLIST name that is also inside the seasoning window is
    # RECENT_IPO with NO unlock prices — buy triggers are WATCHLIST-only and
    # an unseasoned listing has no actionable trigger.
    from test_watchlist import _wonderful_but_pricey
    c = _wonderful_but_pricey()
    assert evaluate(c, today=TODAY).watchlist  # sanity: watchlist without the IPO
    c.ipo_date = "2026-03-01"
    r = evaluate(c, today=TODAY)
    assert r.status is ScreenStatus.RECENT_IPO
    assert r.unlock_ps_price is None and r.unlock_peg_price is None


def test_pre_revenue_firewall_still_wins():
    # The firewall runs FIRST: a pre-revenue recent listing stays PRE_REVENUE.
    c = make("PRE", 0, 0, 0, 0, 0, 100, 100, 5000, None, 0, net_income=-50)
    c.ipo_date = "2026-05-01"
    r = evaluate(c, today=TODAY)
    assert r.status is ScreenStatus.PRE_REVENUE


def test_seasoning_window_is_configurable():
    c = _passing(ipo_date="2026-01-08")  # 6 months ago
    assert evaluate(c, today=TODAY, ipo_seasoning_months=12).recent_ipo
    assert evaluate(c, today=TODAY, ipo_seasoning_months=6).passed


def test_mock_fresh_ticker_is_recent_ipo():
    from etf_screen.providers import MockProvider
    c = MockProvider().fetch("FRESH")
    r = evaluate(c)  # real today: mock date is dynamic, always ~4 months back
    assert r.status is ScreenStatus.RECENT_IPO
