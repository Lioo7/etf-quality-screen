"""Tests for the gross-margin level & stability annotation (Feature 2, v0.2).

Purely advisory: gross margin is where pricing power shows up first, but
hard-gating on it would double-punish capital-intensive sectors the screen is
already structurally biased against, so this never fails a name or changes
`passed`/`track`/ranking.
"""

import pytest

from etf_screen.screen import AnnualPeriod, evaluate, margin_profile
from test_screen import make


def _period(label, rev, cogs=None):
    return AnnualPeriod(label, rev, rev * 0.4, rev * 0.05, rev * 0.05, cogs=cogs)


# --- pure margin_profile() ---------------------------------------------------
def test_stable_high_margin_earns_pricing_power():
    history = [_period(str(2019 + i), 1000 + i * 100, cogs=250 + i * 25)
               for i in range(6)]  # GM ~= 75% every year
    mp = margin_profile(history)
    assert mp.years_used == 6
    assert mp.gm_latest == pytest.approx(75.0, abs=0.5)
    assert mp.gm_stddev < 5.0
    assert mp.pricing_power


def test_high_margin_but_unstable_is_not_pricing_power():
    history = [
        _period("2020", 1000, cogs=200),   # 80%
        _period("2021", 1000, cogs=600),   # 40%
        _period("2022", 1000, cogs=250),   # 75%
        _period("2023", 1000, cogs=550),   # 45%
        _period("2024", 1000, cogs=200),   # 80%
    ]
    mp = margin_profile(history)
    assert mp.years_used == 5
    assert mp.gm_stddev >= 5.0
    assert not mp.pricing_power


def test_stable_but_low_margin_is_not_pricing_power():
    history = [_period(str(2020 + i), 1000, cogs=700) for i in range(5)]  # 30%
    mp = margin_profile(history)
    assert mp.gm_latest == pytest.approx(30.0)
    assert mp.gm_stddev == pytest.approx(0.0)
    assert not mp.pricing_power   # below the 40% floor


def test_high_stable_but_too_few_years_is_not_pricing_power():
    history = [_period(str(2023 + i), 1000, cogs=250) for i in range(2)]  # 75%, 2y
    mp = margin_profile(history)
    assert mp.years_used == 2
    assert not mp.pricing_power


def test_no_cogs_line_is_not_applicable():
    # Banks/insurers typically don't report cost of revenue.
    history = [_period(str(2020 + i), 1000, cogs=None) for i in range(5)]
    mp = margin_profile(history)
    assert mp.not_applicable
    assert mp.gm_latest is None and mp.gm_stddev is None and mp.years_used == 0


def test_empty_history_is_not_applicable():
    mp = margin_profile([])
    assert mp.not_applicable


def test_missing_cogs_year_is_skipped_not_fabricated():
    history = [
        _period("2022", 1000, cogs=250),   # 75%
        _period("2023", 1000, cogs=None),  # gap -> skipped
        _period("2024", 1000, cogs=300),   # 70%
    ]
    mp = margin_profile(history)
    assert mp.years_used == 2
    assert mp.gm_latest == pytest.approx(70.0)


# --- integration with evaluate() --------------------------------------------
def test_margin_profile_attached_for_snapshot_qualifier():
    history = [_period(str(2019 + i), 1000 + i * 100, cogs=250 + i * 25)
               for i in range(6)]
    c = make("MOAT", 1000, 700, 400, 50, 50, 1000, 1000, 5000, 30, 20, net_income=200)
    c.history = history
    r = evaluate(c)
    assert r.passed
    assert r.margin_profile is not None and r.margin_profile.pricing_power


def test_non_qualifier_has_no_margin_profile():
    c = make("BAD", 20000, 16000, 7000, 1000, 1200, 1500, 1510, 200000, 60, 12,
             net_income=4000)
    r = evaluate(c)
    assert not r.passed
    assert r.margin_profile is None


def test_margin_profile_never_gates_the_verdict():
    # Low margin + unstable + not_applicable must never change passed/track --
    # only reachable via history, which the four-filter snapshot never reads.
    history = [
        _period("2020", 1000, cogs=None),
        _period("2021", 1000, cogs=None),
    ]
    c = make("OK", 1000, 700, 400, 50, 50, 1000, 1000, 5000, 30, 20, net_income=200)
    c.history = history
    r = evaluate(c)
    assert r.passed
    assert r.margin_profile.not_applicable
