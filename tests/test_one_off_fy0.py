"""#61: one-off items inside consensus FY0 EPS make PEG untestable, not failed.

Street actual EPS minus normalized EPS, summed over the FY0 quarters already
reported, is the one-off amount embedded in consensus FY0. At >= 10% of FY0 the
PEG test is unadministrable whatever the growth sign: the name takes the existing
NEEDS_DATA path only where PEG was the sole blocker. Gates are unchanged.
"""
import json
from dataclasses import asdict, replace
from datetime import date

import pandas as pd
import pytest

from etf_screen.export import COLUMNS, build_rows
from etf_screen.overrides import apply_override
from etf_screen.providers import (_COMPANY_FIELDS, _CACHE_SCHEMA, _extract_yf,
                                  _yf_fy0_quarter_eps)
from etf_screen.screen import (EPS_DECLINE_NOTE, ONE_OFF_FY0_MAX, ScreenStatus,
                               evaluate, fy0_one_off_check, peg_untestable_reason)
from test_screen import make
from tests.test_peg_basis import _INFO, _Q_CF, _Q_INC, _a_inc, _estimates, _frame

PRICE = 300.0


def _q(*rows):
    """FY0 quarter records: (quarter_end, eps_actual, eps_normalized)."""
    return [{"quarter_end": d, "eps_actual": a, "eps_normalized": n} for d, a, n in rows]


GOOGL_LIKE_QUARTERS = _q(("2026-03-31", 5.11, 2.67), ("2026-06-30", 9.11, 2.62),
                         ("2026-09-30", 2.12, 2.74))   # embedded +8.31


def _co(fy0, fy1, quarters=None, mcap=5000, **kw):
    """Profitable, every non-PEG filter passing; PEG inputs on the D1 basis."""
    c = make("TST", 1000, 700, 400, 50, 50, 1000, 1000, mcap,
             PRICE / fy1, 100 * (fy1 / fy0 - 1), net_income=200, **kw)
    return replace(c, eps_fy0=fy0, eps_fy1=fy1, fy0_quarter_eps=quarters)


# --- gain case (GOOGL-like: FY0 inflated, consensus growth < 0) -------------
def test_gain_case_without_quarter_data_is_a_failed_peg():
    assert evaluate(_co(19.54, 14.89)).status is ScreenStatus.REJECTED


def test_gain_case_sole_blocker_is_needs_data():
    r = evaluate(_co(19.54, 14.89, GOOGL_LIKE_QUARTERS))
    assert r.status is ScreenStatus.NEEDS_DATA
    assert "one-off items of +$8.31/share (42.5% of FY0)" in r.reasons[0]
    assert r.unlock_peg_price is None and r.peg is None
    assert not r.track_b


def test_gain_case_with_ps_failure_is_rejected_with_the_one_off_reason():
    r = evaluate(_co(19.54, 14.89, GOOGL_LIKE_QUARTERS, mcap=30000))
    assert r.status is ScreenStatus.REJECTED
    assert any("P/S" in x for x in r.reasons)
    assert any("one-off items of +$8.31/share" in x for x in r.reasons)
    assert not any(x.startswith("PEG failed") for x in r.reasons)


def test_gain_case_with_a_quality_gate_failure_is_rejected():
    r = evaluate(_co(19.54, 14.89, GOOGL_LIKE_QUARTERS, goodwill=1200, total_assets=2000))
    assert r.status is ScreenStatus.REJECTED and not r.needs_data


# --- loss case (FY0 deflated: growth overstated -> false pass) --------------
def test_loss_case_pass_becomes_needs_data():
    quarters = _q(("2026-03-31", 1.0, 3.0))   # embedded -2.00 = -20% of FY0
    assert evaluate(_co(10.0, 13.0)).status is ScreenStatus.PASS
    r = evaluate(_co(10.0, 13.0, quarters))
    assert r.status is ScreenStatus.NEEDS_DATA
    assert "one-off items of -$2.00/share (-20.0% of FY0)" in r.reasons[0]


# --- unchanged cases -------------------------------------------------------
def test_adjusted_eps_company_street_equals_normalized_is_unchanged():
    quarters = _q(("2026-03-31", 5.0, 5.0), ("2026-06-30", 5.2, 5.2))
    for fy0, fy1, status in ((10.0, 13.0, ScreenStatus.PASS),
                             (19.54, 14.89, ScreenStatus.REJECTED)):
        assert evaluate(_co(fy0, fy1, quarters)).status is status


def test_below_threshold_is_unchanged_and_threshold_is_inclusive():
    assert ONE_OFF_FY0_MAX == pytest.approx(0.10)
    below = _q(("2026-03-31", 3.0, 3.99))    # -0.99 = -9.9%
    at = _q(("2026-03-31", 3.0, 4.0))        # -1.00 = -10%
    assert evaluate(_co(10.0, 13.0, below)).status is ScreenStatus.PASS
    assert evaluate(_co(10.0, 13.0, at)).status is ScreenStatus.NEEDS_DATA


def test_no_reported_quarter_yet_is_unchanged():
    assert evaluate(_co(10.0, 13.0, [])).status is ScreenStatus.PASS
    assert fy0_one_off_check(_co(10.0, 13.0, [])).fires is False


def test_already_untestable_peg_keeps_its_own_reason():
    c = replace(_co(19.54, 14.89, GOOGL_LIKE_QUARTERS), forward_pe=None,
                forward_eps_growth=None, low_confidence=["PEG untestable: no price"])
    assert fy0_one_off_check(c) is None
    assert "one-off" not in peg_untestable_reason(c)


def test_unprofitable_name_routing_is_unchanged():
    base = make("BRN", 3500, 2400, 1100, 120, 500, 245, 235, 30000, None, None,
                net_income=-50)
    c = replace(base, forward_pe=PRICE / 14.89, forward_eps_growth=-23.8,
                eps_fy0=19.54, eps_fy1=14.89)
    a, b = evaluate(c), evaluate(replace(c, fy0_quarter_eps=GOOGL_LIKE_QUARTERS))
    assert (a.status, a.track, a.track_b) == (b.status, b.track, b.track_b)


def test_recent_ipo_still_wins():
    r = evaluate(replace(_co(19.54, 14.89, GOOGL_LIKE_QUARTERS), ipo_date="2026-10-01"),
                 today=date(2026, 11, 22))
    assert r.status is ScreenStatus.RECENT_IPO


# --- overrides -------------------------------------------------------------
def test_suggested_overrides_complete_the_screen():
    c = _co(19.54, 14.89, GOOGL_LIKE_QUARTERS)
    task = evaluate(c).one_off
    lines = json.loads(task.override_json)["TST"]
    assert lines["forward_pe"] == pytest.approx(PRICE / 14.89, rel=1e-3)
    assert lines["eps_fy0"] == pytest.approx(11.23, abs=0.01)
    r = evaluate(apply_override(c, lines))
    assert r.status is ScreenStatus.PASS     # the check stands down for verified values
    assert r.peg == pytest.approx(task.peg_if_confirmed, rel=1e-3)


def test_overridden_growth_skips_the_check():
    c = replace(_co(10.0, 13.0, _q(("2026-03-31", 1.0, 3.0))),
                overridden_fields=["forward_eps_growth"])
    assert fy0_one_off_check(c) is None
    assert evaluate(c).status is ScreenStatus.PASS


# --- verification task -----------------------------------------------------
def test_verification_task_fields():
    r = evaluate(_co(19.54, 14.89, GOOGL_LIKE_QUARTERS))
    t = r.one_off
    assert [q["quarter_end"] for q in t.quarters] == ["2026-03-31", "2026-06-30",
                                                      "2026-09-30"]
    assert [round(q["embedded"], 2) for q in t.quarters] == [2.44, 6.49, -0.62]
    assert t.embedded == pytest.approx(8.31) and t.pct_fy0 == pytest.approx(42.53, abs=0.01)
    assert t.eps_fy0_adj == pytest.approx(11.23)
    assert t.growth_adj == pytest.approx(100 * (14.89 / 11.23 - 1))
    assert t.would_pass and t.worth_checking
    assert t.verify.startswith("find the one-off items in that quarter's earnings release "
                               "or 10-Q (e.g. gains or losses on investments, impairments,")
    assert "after-tax amount per diluted share: " in t.verify and "+$6.49/share" in t.verify
    json.dumps(t.as_dict())                  # JSON-serializable


@pytest.mark.parametrize("fy0,fy1,embedded", [
    (10.0, 13.0, -4.0),     # FY0_adj 14 > FY1 13: adjusted growth <= 0
    (10.0, 13.0, -2.5),     # growth_adj 4.0%, PEG_adj 23.1/4.0 > 2
])
def test_not_worth_checking(fy0, fy1, embedded):
    quarters = _q(("2026-03-31", 3.0 + embedded, 3.0))
    t = evaluate(_co(fy0, fy1, quarters)).one_off
    assert t.fires and not t.would_pass and not t.worth_checking


def test_fy0_adj_non_positive_is_not_worth_checking():
    t = evaluate(_co(5.0, 6.0, _q(("2026-03-31", 9.0, 3.0)))).one_off   # FY0_adj -1
    assert t.growth_adj is None and t.peg_if_confirmed is None and not t.worth_checking


def test_console_task_lines(capsys):
    from etf_screen.cli import _print_one_off_task
    _print_one_off_task(evaluate(_co(19.54, 14.89, GOOGL_LIKE_QUARTERS)).one_off)
    out = capsys.readouterr().out
    assert "verify:" in out and '"forward_eps_growth"' in out and "PEG 0.62" in out
    _print_one_off_task(evaluate(_co(10.0, 13.0, _q(("2026-03-31", 0.5, 3.0)))).one_off)
    out = capsys.readouterr().out
    assert "verify:" in out and "-$2.50/share" in out           # breakdown still shown
    assert "not worth checking: PEG 5.77" in out
    _print_one_off_task(evaluate(_co(5.0, 6.0, _q(("2026-03-31", 9.0, 3.0)))).one_off)
    out = capsys.readouterr().out
    assert "(<= 0), so PEG stays undefined" in out and "> 2" not in out


def test_task_only_on_needs_data():
    # P/S fails too: the reason names the one-off, but no "worth checking" task, since
    # confirming the one-offs could not complete the screen.
    r = evaluate(_co(19.54, 14.89, GOOGL_LIKE_QUARTERS, mcap=30000))
    assert r.status is ScreenStatus.REJECTED and r.one_off is not None
    assert r.one_off_task is None
    row = build_rows([r], [], {})[0]
    assert row["one_off_eps"] == "8.31"
    assert row["one_off_task"] == row["one_off_worth_checking"] == ""
    assert evaluate(_co(19.54, 14.89, GOOGL_LIKE_QUARTERS)).one_off_task is not None
    ipo = evaluate(replace(_co(19.54, 14.89, GOOGL_LIKE_QUARTERS), ipo_date="2026-10-01"),
                   today=date(2026, 11, 22))
    assert ipo.one_off_task is None


# --- #53 note is replaced by the specific reason ---------------------------
def test_generic_eps_decline_note_is_replaced():
    c = replace(_co(19.54, 14.89, GOOGL_LIKE_QUARTERS), low_confidence=[EPS_DECLINE_NOTE])
    r = evaluate(c)
    assert not any(EPS_DECLINE_NOTE in x for x in r.reasons)
    assert EPS_DECLINE_NOTE not in r.low_confidence_notes
    row = build_rows([r], [], {})[0]
    assert EPS_DECLINE_NOTE not in row["low_confidence"]
    # without the check firing the note stays
    plain = evaluate(replace(c, fy0_quarter_eps=None))
    assert EPS_DECLINE_NOTE in plain.low_confidence_notes


# --- export ----------------------------------------------------------------
def test_export_columns():
    for col in ("one_off_eps", "one_off_pct_fy0", "one_off_peg_if_confirmed",
                "one_off_worth_checking", "one_off_task"):
        assert col in COLUMNS
    fired = build_rows([evaluate(_co(19.54, 14.89, GOOGL_LIKE_QUARTERS))], [], {})[0]
    assert fired["one_off_eps"] == "8.31" and fired["one_off_worth_checking"] is True
    assert json.loads(fired["one_off_task"])["quarters"][1]["quarter_end"] == "2026-06-30"
    quiet = build_rows([evaluate(_co(10.0, 13.0))], [], {})[0]
    assert quiet["one_off_task"] == "" and quiet["one_off_eps"] == ""


# --- provider: sourcing the per-quarter pairs ------------------------------
TODAY = date(2026, 11, 22)
US = {"country": "United States", "currency": "USD", "financialCurrency": "USD"}


def _inc(cols, normalized, shares, gaap=None, ni=None, unusual=None,
         norm_label="Normalized Income"):
    """Quarterly income frame. GAAP diluted EPS defaults to the GOOGL-like street
    actuals (a GAAP-basis reporter) and net income to EPS x shares (consistent)."""
    gaap = gaap if gaap is not None else GAAP_EPS[:len(cols)]
    ni = ni if ni is not None else [g * s for g, s in zip(gaap, shares)]
    frame = {}
    for i, c in enumerate(cols):
        col = {norm_label: normalized[i], "Diluted Average Shares": shares[i],
               "Diluted EPS": gaap[i], "Net Income Common Stockholders": ni[i]}
        if unusual is not None:
            col["Total Unusual Items"] = unusual[i]
        frame[pd.Timestamp(c)] = col
    return pd.DataFrame(frame)


def _eh(dates, actual):
    return pd.DataFrame({"epsActual": actual},
                        index=pd.Index([pd.Timestamp(d) for d in dates], name="quarter"))


def _annual(*cols):
    return pd.DataFrame({pd.Timestamp(c): {"Total Revenue": 1.0} for c in cols})


QCOLS = ["2026-09-30", "2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"]
GAAP_EPS = [2.12, 9.11, 5.11, 2.82, 2.87]          # newest first, = street (GAAP basis)
NORMALIZED = [33.85, 32.23, 32.71, 32.44, 26.46]
SHARES = [12.368, 12.309, 12.238, 12.228, 12.203]


def _source(eh=None, q_inc=None, a_inc=None, info=US, est_ccy="USD"):
    eh = _eh(["2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"],
             [2.82, 5.11, 9.11, 2.12]) if eh is None else eh
    q_inc = _inc(QCOLS, NORMALIZED, SHARES) if q_inc is None else q_inc
    a_inc = _annual("2025-12-31", "2024-12-31") if a_inc is None else a_inc
    return _yf_fy0_quarter_eps(eh, q_inc, a_inc, info, est_ccy, TODAY)


def test_december_year_takes_the_quarters_after_the_last_annual_column():
    quarters, basis, cause = _source()
    assert cause is None and basis == "gaap"
    assert [q["quarter_end"] for q in quarters] == ["2026-03-31", "2026-06-30",
                                                    "2026-09-30"]
    assert quarters[0]["eps_actual"] == 5.11
    assert quarters[0]["eps_normalized"] == pytest.approx(32.71 / 12.238)


def test_june_year_has_one_fy0_quarter():
    quarters, basis, cause = _source(a_inc=_annual("2026-06-30", "2025-06-30"))
    assert cause is None and [q["quarter_end"] for q in quarters] == ["2026-09-30"]


def test_52_week_year_matches_dates_within_ten_days():
    q_inc = _inc(["2026-10-26", "2026-07-27", "2026-04-26", "2026-01-25"],
                 [60.0, 53.2, 58.3, 43.0], [24.19, 24.29, 24.39, 24.43],
                 gaap=[2.68, 2.22, 1.87, 1.62])
    eh = _eh(["2026-01-31", "2026-04-30", "2026-07-31", "2026-10-31"], [1.62, 1.87, 2.22, 2.68])
    quarters, basis, cause = _source(eh=eh, q_inc=q_inc, a_inc=_annual("2026-01-25"))
    assert cause is None and len(quarters) == 3
    assert quarters[-1]["eps_actual"] == 2.68


def test_annual_just_reported_means_no_fy0_quarter():
    quarters, basis, cause = _source(a_inc=_annual("2026-09-30", "2025-09-30"))
    assert quarters == [] and cause is None and basis == "unavailable"


# --- street basis (D19) -------------------------------------------------------
def test_street_within_two_percent_or_two_cents_is_gaap():
    # 5.11 -> 5.20 (1.7%), 9.11 -> 9.11, 2.12 -> 2.14 (2 cents)
    eh = _eh(["2026-03-31", "2026-06-30", "2026-09-30"], [5.20, 9.11, 2.14])
    assert _source(eh=eh)[1] == "gaap"


def test_adjusted_reporter_never_fires_and_gets_no_note():
    # ADBE-like: street non-GAAP EPS is ~1.4 above GAAP every quarter (SBC and
    # amortization add-backs, which normalized income still carries).
    eh = _eh(["2026-03-31", "2026-06-30", "2026-09-30"], [6.06, 10.5, 3.55])
    assert _source(eh=eh) == (None, "adjusted", None)


def test_mixed_basis_is_unavailable():
    eh = _eh(["2026-03-31", "2026-06-30", "2026-09-30"], [5.11, 9.11, 3.55])
    quarters, basis, cause = _source(eh=eh)
    assert quarters is None and basis == "mixed" and "mixed" in cause


def test_share_count_unit_error_is_inconsistent():
    # AJG-like: Q3 diluted shares 1,000x too small -> normalized EPS would be ~1,600.
    shares = [0.012368] + SHARES[1:]
    q_inc = _inc(QCOLS, NORMALIZED, shares, ni=[g * s for g, s in zip(GAAP_EPS, SHARES)])
    quarters, basis, cause = _source(q_inc=q_inc)
    assert quarters is None and basis == "unavailable"
    assert cause == ("statement inconsistent: diluted EPS does not match net income / "
                     "shares, 2026-09-30")


def test_normalized_income_unexplained_by_unusual_items_is_inconsistent():
    # Net income positive, normalized income negative, no Total Unusual Items row.
    norm = [-0.6] + NORMALIZED[1:]
    quarters, basis, cause = _source(q_inc=_inc(QCOLS, norm, SHARES))
    assert quarters is None and "normalized income not explained" in cause


def test_large_unusual_items_explain_the_gap():
    # A real one-off loss: NI 26.2, normalized 60 (gap 33.8 > |NI|), unusual -40.
    norm = [60.0] + NORMALIZED[1:]
    unusual = [-40.0, 0.0, 0.0, 0.0, 0.0]
    quarters, basis, cause = _source(q_inc=_inc(QCOLS, norm, SHARES, unusual=unusual))
    assert basis == "gaap" and cause is None


def test_cme_like_is_caught_by_the_basis_rule_not_the_normalized_sanity():
    # CME 2026: normalized income negative while NI ~$1B, but Morningstar's Total
    # Unusual Items (~$1.4B/qtr) is big enough to "explain" it, so the sanity rule
    # passes; street EPS differs from GAAP every quarter -> adjusted, no fire.
    cols, shares = QCOLS[:3], [0.3586, 0.3613, 0.3632]
    gaap, ni = [2.89, 2.88, 3.18], [1.0369, 1.0418, 1.1543]
    q_inc = _inc(cols, [-0.048, -0.0805, 0.0929], shares, gaap=gaap, ni=ni,
                 unusual=[1.4181, 1.4297, 1.3893])
    eh = _eh(["2026-03-31", "2026-06-30", "2026-09-30"], [3.36, 2.99, 3.02])
    assert _source(eh=eh, q_inc=q_inc) == (None, "adjusted", None)


def test_missing_net_income_skips_the_consistency_checks_only():
    q_inc = _inc(QCOLS, NORMALIZED, SHARES, ni=[float("nan")] * 5)
    assert _source(q_inc=q_inc)[1] == "gaap"


@pytest.mark.parametrize("kwargs,cause", [
    ({"eh": pd.DataFrame()}, "no earnings history"),
    ({"a_inc": _annual("2025-06-30")}, "latest annual statement older than 15 months"),
    ({"a_inc": _annual("2024-12-31")}, "latest annual statement older than 15 months"),
    ({"a_inc": _annual("2025-09-30")}, "quarters not aligned to the fiscal year"),
    ({"q_inc": _inc(QCOLS, [33.85, float("nan"), 32.71, 32.44, 26.46], SHARES)},
     "normalized income not reported"),
    ({"q_inc": _inc(QCOLS, NORMALIZED, SHARES, norm_label="Net Income")},
     "normalized income not reported"),
    ({"q_inc": _inc(QCOLS, NORMALIZED, SHARES, gaap=[float("nan")] + GAAP_EPS[1:])},
     "GAAP diluted EPS not reported"),
    ({"eh": _eh(["2026-03-31", "2026-09-30"], [5.11, 2.12])},
     "quarters not aligned to the fiscal year"),
    ({"eh": _eh(["2026-03-31", "2026-06-30", "2026-09-30"], [5.11, float("nan"), 2.12])},
     "street actual EPS not reported"),
    ({"info": {**US, "financialCurrency": "EUR"}}, "currency mismatch or unverified"),
    ({"est_ccy": None}, "currency mismatch or unverified"),
    ({"info": {**US, "country": "United Kingdom"}}, "non-US issuer"),
])
def test_unavailable_inputs(kwargs, cause):
    assert _source(**kwargs) == (None, "unavailable", cause)


def test_same_currency_adr_like_case_never_fires():
    # 6 ordinary shares per ADS, everything in USD: street EPS per ADS is 6x the
    # per-ordinary normalized EPS, an ~83% false "one-off" without the issuer rule.
    q_inc = _inc(QCOLS, [6.0] * 5, [10.0] * 5, gaap=[3.6] * 5)   # 0.60 / ordinary
    eh = _eh(["2026-03-31", "2026-06-30", "2026-09-30"], [3.6] * 3)  # 3.60 / ADS
    quarters, basis, cause = _source(eh=eh, q_inc=q_inc,
                                     info={**US, "country": "United Kingdom"})
    assert quarters is None and cause == "non-US issuer"


# --- provider wiring through _extract_yf -----------------------------------
def _yf(estimates, eh=None, info=None, q_inc=None):
    info = {**_INFO, "country": "United States", "financialCurrency": "USD"} \
        if info is None else info
    q = _frame(_Q_INC, 5) if q_inc is None else q_inc
    return _extract_yf("TST", q_inc=q, a_inc=_a_inc(), q_cf=_frame(_Q_CF, 5), a_cf=None,
                       info=info, estimates=estimates, earnings_history=eh)


def test_unavailable_note_only_for_profitable_names_with_testable_peg():
    note = "one-off check unavailable (no earnings history)"
    assert note in _yf(_estimates(20.0, 24.0)).low_confidence
    assert not any("one-off" in n for n in _yf(None).low_confidence)   # PEG untestable


def test_extract_populates_the_fields_and_caches_them():
    assert "fy0_quarter_eps" in _COMPANY_FIELDS and _CACHE_SCHEMA == "v9"
    assert "street_basis" in _COMPANY_FIELDS
    c = _yf(_estimates(20.0, 24.0))
    assert c.fy0_quarter_eps is None and c.street_basis == "unavailable"
    stored = replace(c, fy0_quarter_eps=GOOGL_LIKE_QUARTERS)
    cached = asdict(stored)
    from etf_screen.screen import Company
    back = Company(**{k: cached[k] for k in _COMPANY_FIELDS if k in cached})
    assert back.fy0_quarter_eps == GOOGL_LIKE_QUARTERS


def test_fmp_notes_the_check_as_unavailable(monkeypatch):
    from tests.test_foreign_filer import _FMP_EST, _fmp_provider
    c = _fmp_provider(monkeypatch, _FMP_EST).fetch("FMP")
    assert c.fy0_quarter_eps is None and c.street_basis == "unavailable"
    assert c.net_income_ttm > 0 and c.forward_eps_growth is not None
    assert "one-off check unavailable (not supported by FMP)" in c.low_confidence


def test_street_basis_is_informational_and_exported():
    assert "street_basis" in COLUMNS
    base = _co(10.0, 13.0)
    rows = [evaluate(replace(base, street_basis=b)) for b in
            (None, "gaap", "adjusted", "mixed", "unavailable")]
    assert {(r.status, r.track, r.passed) for r in rows} == {(ScreenStatus.PASS, "Track A",
                                                              True)}
    assert build_rows(rows[2:3], [], {})[0]["street_basis"] == "adjusted"
    assert build_rows(rows[:1], [], {})[0]["street_basis"] == "N/A"


# --- note noise: structural causes are summarized, data problems stay per name ----
@pytest.mark.parametrize("cause,shown", [
    ("street EPS basis mixed (GAAP and adjusted quarters)", False),
    ("non-US issuer", False),
    ("not supported by FMP", False),
    ("statement inconsistent: diluted EPS does not match net income / shares, 2026-09-30",
     True),
    ("normalized income not reported", True),
    ("GAAP diluted EPS not reported", True),
    ("street actual EPS not reported", True),
    ("no earnings history", True),
    ("quarters not aligned to the fiscal year", True),
])
def test_per_name_note_only_for_data_problems(cause, shown):
    note = f"one-off check unavailable ({cause})"
    r = evaluate(replace(_co(10.0, 13.0), low_confidence=["other note", note]))
    assert r.one_off_unavailable_cause == cause
    assert (note in r.low_confidence_notes) is shown
    assert "other note" in r.low_confidence_notes
    assert note in r.company.low_confidence           # stored data unchanged
    row = build_rows([r], [], {})[0]
    assert row["one_off_unavailable_cause"] == cause
    assert (note in row["low_confidence"]) is shown


def test_console_summary_line_for_structural_causes():
    from etf_screen.cli import one_off_not_run_line

    def r(cause):
        notes = [] if cause is None else [f"one-off check unavailable ({cause})"]
        return evaluate(replace(_co(10.0, 13.0), low_confidence=notes))
    results = [r("street EPS basis mixed (GAAP and adjusted quarters)"),
               r("street EPS basis mixed (GAAP and adjusted quarters)"),
               r("non-US issuer"), r("not supported by FMP"),
               r("no earnings history"), r(None)]
    assert one_off_not_run_line(results) == (
        "one-off check not run for 4 names (mixed basis 2, non-US 1, FMP 1): see export")
    assert one_off_not_run_line(results[4:]) is None
    assert build_rows(results[5:], [], {})[0]["one_off_unavailable_cause"] == ""
