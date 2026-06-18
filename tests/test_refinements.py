"""Tests for the four refinements: basis consistency, SBC-assumed-0, manual
overrides, and the export writer."""

import pandas as pd
import pytest

from qqq_screen.export import Provenance, build_rows, export
from qqq_screen.overrides import (
    apply_override,
    company_from_override,
    load_overrides,
)
from qqq_screen.providers import DataUnavailable, _extract_yf
from qqq_screen.screen import Company, evaluate


# --- helpers to build Yahoo-shaped DataFrames -------------------------------
def _frame(label_to_values: dict, n: int):
    """DataFrame: rows=line items, cols=n period timestamps (newest first)."""
    cols = pd.date_range("2025-12-31", periods=n, freq="-1QE")
    return pd.DataFrame(
        {label: vals[:n] for label, vals in label_to_values.items()}, index=cols
    ).T


INFO = {"marketCap": 1_000_000, "shortName": "Test Co",
        "forwardPE": 20.0, "forwardEps": 6.0, "trailingEps": 5.0}


# --- 1. basis consistency ---------------------------------------------------
def test_ttm_basis_used_when_full_year_available():
    q_inc = _frame({
        "Total Revenue": [100] * 8,
        "Diluted Average Shares": [10] * 8,
    }, 8)
    q_cf = _frame({
        "Operating Cash Flow": [30] * 8,
        "Capital Expenditure": [-5] * 8,
        "Stock Based Compensation": [2] * 8,
    }, 8)
    c = _extract_yf("TST", q_inc=q_inc, a_inc=None, q_cf=q_cf, a_cf=None, info=INFO)
    assert c.basis == "TTM"
    assert c.revenue_ttm == 400 and c.ocf_ttm == 120  # 4 quarters summed
    assert not any("ANNUAL" in m for m in c.low_confidence)


def test_annual_basis_uses_annual_cashflow_for_consistency():
    # Only 4 income quarters -> cannot form prior TTM -> annual basis for ALL.
    q_inc = _frame({"Total Revenue": [100] * 4, "Diluted Average Shares": [10] * 4}, 4)
    a_inc = _frame({"Total Revenue": [400, 350], "Diluted Average Shares": [10, 11]}, 2)
    q_cf = _frame({"Operating Cash Flow": [30] * 4, "Capital Expenditure": [-5] * 4,
                   "Stock Based Compensation": [2] * 4}, 4)
    a_cf = _frame({"Operating Cash Flow": [118], "Capital Expenditure": [-18],
                   "Stock Based Compensation": [9]}, 1)
    c = _extract_yf("TST", q_inc=q_inc, a_inc=a_inc, q_cf=q_cf, a_cf=a_cf, info=INFO)
    assert c.basis == "annual"
    # OCF/capex/SBC come from the ANNUAL statement, not the quarter sum.
    assert c.ocf_ttm == 118 and c.capex_ttm == 18 and c.sbc_ttm == 9
    assert c.revenue_ttm == 400 and c.revenue_ttm_prior == 350
    assert any("ANNUAL" in m for m in c.low_confidence)


# --- 2. SBC assumed 0 -------------------------------------------------------
def test_missing_sbc_line_assumed_zero_not_skipped():
    q_inc = _frame({"Total Revenue": [100] * 8, "Diluted Average Shares": [10] * 8}, 8)
    q_cf = _frame({"Operating Cash Flow": [30] * 8,
                   "Capital Expenditure": [-5] * 8}, 8)  # no SBC row
    c = _extract_yf("TST", q_inc=q_inc, a_inc=None, q_cf=q_cf, a_cf=None, info=INFO)
    assert c.sbc_ttm == 0.0 and c.sbc_assumed_zero is True
    assert any("assumed 0" in m for m in c.low_confidence)


def test_cashflow_entirely_missing_is_skipped():
    q_inc = _frame({"Total Revenue": [100] * 8, "Diluted Average Shares": [10] * 8}, 8)
    with pytest.raises(DataUnavailable):
        _extract_yf("TST", q_inc=q_inc, a_inc=None, q_cf=None, a_cf=None, info=INFO)


# --- 3. overrides -----------------------------------------------------------
def _base_company():
    return Company("CEG", 5000, 4000, 1400, 200, 0.0, 800, 790, 60000, 25.0, 20.0,
                   name="Constellation", sbc_assumed_zero=True)


def test_apply_override_takes_precedence_and_flags():
    base = _base_company()
    base.low_confidence = ["SBC not reported by source - assumed 0"]
    c = apply_override(base, {"sbc_ttm": 350, "name": "Constellation Energy"})
    assert c.sbc_ttm == 350
    assert c.name == "Constellation Energy"
    assert c.manual_override and "sbc_ttm" in c.overridden_fields
    assert c.sbc_assumed_zero is False  # a supplied SBC is no longer assumed
    # stale "assumed 0" flag is dropped, and the original is not mutated
    assert not any("assumed 0" in m for m in c.low_confidence)
    assert base.low_confidence == ["SBC not reported by source - assumed 0"]


def test_company_from_override_rescues_complete_record():
    fields = {
        "name": "Foo", "revenue_ttm": 5000, "revenue_ttm_prior": 4000,
        "ocf_ttm": 1400, "capex_ttm": 200, "sbc_ttm": 100,
        "diluted_shares_now": 800, "diluted_shares_prior": 790,
        "market_cap": 60000, "forward_pe": 25, "forward_eps_growth": 20,
    }
    c = company_from_override("FOO", fields)
    assert c is not None and c.manual_override and c.basis == "override"
    assert evaluate(c).ticker == "FOO"


def test_company_from_override_returns_none_when_incomplete():
    assert company_from_override("FOO", {"sbc_ttm": 100}) is None


def test_load_overrides_missing_file(tmp_path):
    assert load_overrides(tmp_path / "nope.json") == {}


def test_load_overrides_uppercases_keys(tmp_path):
    p = tmp_path / "overrides.json"
    p.write_text('{"ceg": {"sbc_ttm": 5}}')
    assert "CEG" in load_overrides(p)


# --- 4. export writer -------------------------------------------------------
def _results():
    passing = evaluate(Company("APP", 30000, 18000, 12000, 1000, 1500, 1000, 1010,
                               90000, 25, 100, name="AppLovin", basis="TTM"))
    failing = evaluate(Company("MSFT", 245000, 212000, 118000, 28000, 10000,
                               7430, 7450, 3100000, 32, 14, name="Microsoft"))
    return [failing, passing]


class _Skip:
    def __init__(self, ticker, reason):
        self.ticker, self.reason = ticker, reason


@pytest.mark.parametrize("fmt", ["csv", "md"])
def test_export_writes_full_set_with_provenance(tmp_path, fmt):
    rows = build_rows(_results(), [_Skip("CEG", "cash-flow unavailable")],
                      {"CEG": "Constellation Energy"})
    prov = Provenance("yfinance", "qqq", "wikipedia", "2026-06-17", "2026-06-18")
    path = tmp_path / f"out.{fmt}"
    export(path, fmt, prov, rows)
    text = path.read_text()
    # provenance present, all three names present (incl. skipped)
    assert "yfinance" in text and "2026-06-17" in text
    assert "APP" in text and "MSFT" in text and "CEG" in text
    assert "Constellation Energy" in text


def test_build_rows_orders_survivors_first():
    rows = build_rows(_results(), [], {})
    assert rows[0]["passed"] is True   # survivor ranked ahead of rejected
    assert rows[0]["ticker"] == "APP"
