"""SEC 10-Q / 10-K facts fill a quarter D23 finds missing in Yahoo's window (#74, D25).

The fill runs before ``_extract_yf``: it writes filed SEC values into the missing cells
of the TTM window (and the year-ago revenue / share-count column), so basis selection
and growth are unchanged. Only filed values: a direct 3-month fact, or the difference
of two filed YTD facts (FY - 9M for a fiscal Q4). Diluted shares are never derived.
A line fills only when SEC agrees with Yahoo within 1% on the quarters Yahoo reports.
Anything missing or failing keeps D23's annual fallback; nothing ever raises.
"""

import json
from datetime import date, timedelta

import pandas as pd
import pytest
import requests

from etf_screen import sec
from etf_screen.cache import DiskCache
from etf_screen.providers import _extract_yf, _sec_fill, _sec_notes_kept

NAN = float("nan")
TODAY = date(2026, 11, 23)
USD = {"marketCap": 1_000_000, "shortName": "Test Co", "financialCurrency": "USD"}
QUARTERS = ["2026-09-30", "2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"]
REVENUE = [130, 120, 115, 110, 100]


# --- Yahoo frames (same shape as tests/test_ttm_gaps.py) ------------------------
def _frame(rows: dict, dates: list[str]):
    return pd.DataFrame(rows, index=pd.to_datetime(dates)).T


def _q(cf_dates=QUARTERS, **overrides):
    inc = {"Total Revenue": list(REVENUE), "Diluted Average Shares": [10] * 5,
           "Net Income": [20] * 5, "Operating Income": [25] * 5}
    n = len(cf_dates)
    cf = {"Operating Cash Flow": [30] * n, "Capital Expenditure": [-5] * n,
          "Stock Based Compensation": [2] * n}
    for key, vals in overrides.items():
        (inc if key in inc else cf)[key] = vals
    return _frame(inc, QUARTERS), _frame(cf, cf_dates)


def _a():
    a_inc = _frame({"Total Revenue": [400, 350], "Diluted Average Shares": [10, 11],
                    "Net Income": [70, 55], "Operating Income": [90, 80]},
                   ["2025-12-31", "2024-12-31"])
    a_cf = _frame({"Operating Cash Flow": [118, 100], "Capital Expenditure": [-18, -15],
                   "Stock Based Compensation": [9, 8]}, ["2025-12-31", "2024-12-31"])
    return a_inc, a_cf


# --- SEC companyfacts fixtures ----------------------------------------------------
def _fact(start, end, val, form="10-Q", filed="2026-11-01"):
    return {"start": str(start), "end": str(end), "val": val, "form": form, "filed": filed}


def _quarterly(vals_by_end: dict, **kw):
    """Direct 3-month facts, one per quarter end."""
    out = []
    for end, val in vals_by_end.items():
        e = date.fromisoformat(end)
        out.append(_fact(e - timedelta(days=90), e, val, **kw))
    return out


def _ytd(per_quarter: float, years=(2025, 2026), through=(3, 6, 9, 12)):
    """Cumulative YTD facts for December fiscal years: 3M/6M/9M on 10-Q, 12M on 10-K."""
    month_end = {3: "03-31", 6: "06-30", 9: "09-30", 12: "12-31"}
    out = []
    for y in years:
        for m in through:
            if date.fromisoformat(f"{y}-{month_end[m]}") > TODAY:
                continue
            out.append(_fact(f"{y}-01-01", f"{y}-{month_end[m]}", per_quarter * m / 3,
                             form="10-K" if m == 12 else "10-Q"))
    return out


def _facts(**concepts):
    """``concept=[facts]`` (USD) or ``concept=("shares", [facts])``."""
    gaap = {}
    for concept, spec in concepts.items():
        unit, rows = spec if isinstance(spec, tuple) else ("USD", spec)
        gaap[concept] = {"units": {unit: rows}}
    return {"facts": {"us-gaap": gaap}}


REV_FACTS = _quarterly(dict(zip(QUARTERS, REVENUE)))
CF_FACTS = {"NetCashProvidedByUsedInOperatingActivities": _ytd(30),
            "PaymentsToAcquirePropertyPlantAndEquipment": _ytd(5),
            "ShareBasedCompensation": _ytd(2)}


class FakeSec:
    """Stands in for sec.SecClient; ``facts`` may be an exception to raise."""

    def __init__(self, facts=None):
        self.facts, self.calls = facts, []

    def companyfacts(self, ticker):
        self.calls.append(ticker)
        if isinstance(self.facts, Exception):
            raise self.facts
        return self.facts


def _run(client, q_inc, q_cf, info=USD):
    """Fill, then extract exactly as YFinanceProvider does."""
    q_inc, q_cf, notes = _sec_fill("TST", q_inc, q_cf, info, client, TODAY)
    a_inc, a_cf = _a()
    c = _extract_yf("TST", q_inc=q_inc, a_inc=a_inc, q_cf=q_cf, a_cf=a_cf, info=info)
    notes = _sec_notes_kept(c, notes)
    c.low_confidence.extend(notes)
    return c, notes


def _annual_reason(c):
    return next(m for m in c.low_confidence if m.startswith("metrics on ANNUAL basis"))


# --- Done when: a filled cell ------------------------------------------------------
def test_missing_quarter_is_filled_from_the_10q_and_the_name_stays_on_ttm():
    q_inc, q_cf = _q(**{"Total Revenue": [130, NAN, 115, 110, 100]})
    c, notes = _run(FakeSec(_facts(Revenues=REV_FACTS)), q_inc, q_cf)
    assert c.basis == "TTM"
    assert c.revenue_ttm == 475  # the exact trailing year, nothing estimated
    assert notes == ["Q 2026-06-30 revenue from SEC 10-Q"]
    assert "Q 2026-06-30 revenue from SEC 10-Q" in c.low_confidence


def test_gap_free_window_never_calls_sec():
    client = FakeSec(_facts(Revenues=REV_FACTS))
    q_inc, q_cf = _q()
    c, notes = _run(client, q_inc, q_cf)
    assert client.calls == [] and notes == [] and c.basis == "TTM"


# --- Done when: the Q4 derivation (FY minus 9-month YTD) -------------------------
def test_fiscal_q4_is_the_10k_year_minus_the_9_month_ytd():
    q_inc, q_cf = _q(**{"Total Revenue": [130, 120, 115, NAN, 100]})
    rows = _quarterly({"2026-09-30": 130, "2026-06-30": 120, "2026-03-31": 115,
                       "2025-09-30": 100})
    rows += [_fact("2025-01-01", "2025-12-31", 410, form="10-K", filed="2026-02-10"),
             _fact("2025-01-01", "2025-09-30", 300, filed="2025-11-01")]
    c, notes = _run(FakeSec(_facts(Revenues=rows)), q_inc, q_cf)
    assert c.basis == "TTM" and c.revenue_ttm == 475  # 410 - 300 = 110
    assert notes == ["Q 2025-12-31 revenue from SEC 10-K FY - 9M YTD"]


def test_cash_flow_quarter_is_a_ytd_difference_when_yahoo_has_no_column():
    # APH's shape (#71): the income column exists with NaN revenue, and the whole
    # 2026-06-30 cash-flow column is absent. 10-Q cash flow is year-to-date only.
    cf_dates = [d for d in QUARTERS if d != "2026-06-30"]
    q_inc, q_cf = _q(cf_dates=cf_dates, **{"Total Revenue": [130, NAN, 115, 110, 100]})
    client = FakeSec(_facts(Revenues=REV_FACTS, **CF_FACTS))
    c, notes = _run(client, q_inc, q_cf)
    assert c.basis == "TTM"
    assert (c.revenue_ttm, c.ocf_ttm, c.capex_ttm, c.sbc_ttm) == (475, 120, 20, 8)
    assert "Q 2026-06-30 operating cash flow from SEC 10-Q YTD difference (6M - 3M)" in notes
    assert "Q 2026-06-30 capex from SEC 10-Q YTD difference (6M - 3M)" in notes
    assert "Q 2026-06-30 SBC from SEC 10-Q YTD difference (6M - 3M)" in notes


# --- Done when: SEC fact missing -> annual fallback -------------------------------
def test_missing_sec_fact_keeps_the_d23_annual_fallback():
    q_inc, q_cf = _q(**{"Total Revenue": [130, NAN, 115, 110, 100]})
    rows = [f for f in REV_FACTS if f["end"] != "2026-06-30"]
    c, notes = _run(FakeSec(_facts(Revenues=rows)), q_inc, q_cf)
    assert c.basis == "annual" and c.revenue_ttm == 400
    assert "quarterly revenue missing for 2026-06-30" in _annual_reason(c)
    assert notes == ["SEC has no usable fact for Q 2026-06-30 revenue - not filled "
                     "(Revenues has no filed value for the quarter)"]


# --- what counts as a filed value ---------------------------------------------------
def test_a_fact_filed_after_the_run_date_is_ignored():
    q_inc, q_cf = _q(**{"Total Revenue": [130, NAN, 115, 110, 100]})
    rows = [f if f["end"] != "2026-06-30" else {**f, "filed": "2026-12-01"}
            for f in REV_FACTS]
    c, _ = _run(FakeSec(_facts(Revenues=rows)), q_inc, q_cf)
    assert c.basis == "annual"


def test_latest_filing_before_the_run_date_wins_a_restatement():
    q_inc, q_cf = _q(**{"Total Revenue": [130, NAN, 115, 110, 100]})
    rows = [f for f in REV_FACTS if f["end"] != "2026-06-30"]
    rows += [_fact("2026-04-01", "2026-06-30", 118, filed="2026-08-01"),
             _fact("2026-04-01", "2026-06-30", 120, form="10-Q/A", filed="2026-11-01"),
             _fact("2026-04-01", "2026-06-30", 999, form="10-Q/A", filed="2026-12-01")]
    c, _ = _run(FakeSec(_facts(Revenues=rows)), q_inc, q_cf)
    assert c.revenue_ttm == 475  # 120: restated, filed before the run date


def test_facts_from_other_forms_are_ignored():
    q_inc, q_cf = _q(**{"Total Revenue": [130, NAN, 115, 110, 100]})
    rows = [f if f["end"] != "2026-06-30" else {**f, "form": "8-K"} for f in REV_FACTS]
    c, _ = _run(FakeSec(_facts(Revenues=rows)), q_inc, q_cf)
    assert c.basis == "annual"


# --- the 1% concept check -------------------------------------------------------------
def test_a_concept_that_disagrees_with_yahoo_is_skipped_for_the_next_one():
    q_inc, q_cf = _q(**{"Total Revenue": [130, NAN, 115, 110, 100]})
    off = [{**f, "val": f["val"] * 1.05} for f in REV_FACTS]  # 5% off: another scope
    facts = _facts(RevenueFromContractWithCustomerExcludingAssessedTax=off, Revenues=REV_FACTS)
    c, notes = _run(FakeSec(facts), q_inc, q_cf)
    assert c.revenue_ttm == 475 and notes == ["Q 2026-06-30 revenue from SEC 10-Q"]


def test_no_fill_when_every_concept_fails_the_check():
    q_inc, q_cf = _q(**{"Total Revenue": [130, NAN, 115, 110, 100]})
    off = [{**f, "val": f["val"] * 1.05} for f in REV_FACTS]
    c, notes = _run(FakeSec(_facts(Revenues=off)), q_inc, q_cf)
    assert c.basis == "annual"
    assert len(notes) == 1 and notes[0].startswith(
        "SEC has no usable fact for Q 2026-06-30 revenue - not filled (concept check:")


def test_derived_cash_flow_fills_pass_the_same_concept_check():
    cf_dates = [d for d in QUARTERS if d != "2026-06-30"]
    q_inc, q_cf = _q(cf_dates=cf_dates)
    facts = _facts(**{**CF_FACTS, "NetCashProvidedByUsedInOperatingActivities": _ytd(33)})
    c, notes = _run(FakeSec(facts), q_inc, q_cf)  # SEC OCF 10% above Yahoo's
    assert c.basis == "annual"
    assert any(n.startswith("SEC has no usable fact for Q 2026-06-30 operating cash flow")
               for n in notes)


# --- shares: a direct 3-month weighted average only ---------------------------------
def test_year_ago_share_count_fills_from_a_direct_3_month_fact():
    q_inc, q_cf = _q(**{"Diluted Average Shares": [10, 10, 10, 10, NAN]})
    shares = _quarterly(dict(zip(QUARTERS, [10] * 5)))
    c, notes = _run(FakeSec(_facts(
        WeightedAverageNumberOfDilutedSharesOutstanding=("shares", shares))), q_inc, q_cf)
    assert c.basis == "TTM" and c.diluted_shares_prior == 10
    assert notes == ["Q 2025-09-30 diluted shares from SEC 10-Q"]


def test_share_cells_extract_never_reads_are_not_targets():
    # _extract_yf reads diluted shares at the newest and the year-ago column only (HUM).
    client = FakeSec(_facts(Revenues=REV_FACTS))
    q_inc, q_cf = _q(**{"Diluted Average Shares": [10, 10, 10, NAN, 10]})
    c, notes = _run(client, q_inc, q_cf)
    assert client.calls == [] and notes == [] and c.basis == "TTM"


def test_instant_facts_without_a_start_are_ignored():
    q_inc, q_cf = _q(**{"Total Revenue": [130, NAN, 115, 110, 100]})
    rows = REV_FACTS + [{"start": None, "end": "2026-06-30", "val": 5, "form": "10-Q",
                         "filed": "2026-08-01"}]  # GS: an instant fact on a flow concept
    c, notes = _run(FakeSec(_facts(Revenues=rows)), q_inc, q_cf)
    assert c.revenue_ttm == 475 and notes == ["Q 2026-06-30 revenue from SEC 10-Q"]


def test_share_count_is_never_derived_from_ytd_averages():
    q_inc, q_cf = _q(**{"Diluted Average Shares": [10, 10, 10, 10, NAN]})
    rows = _quarterly({d: 10 for d in QUARTERS[:4]})  # direct facts, but not 2025-09-30
    rows += [_fact("2025-01-01", "2025-09-30", 10), _fact("2025-01-01", "2025-06-30", 10)]
    c, _ = _run(FakeSec(_facts(
        WeightedAverageNumberOfDilutedSharesOutstanding=("shares", rows))), q_inc, q_cf)
    assert c.basis == "annual"


# --- out of scope / off / failure: no fill, never a raise ---------------------------
def test_non_usd_filer_is_never_filled_nor_fetched():
    client = FakeSec(_facts(Revenues=REV_FACTS))
    q_inc, q_cf = _q(**{"Total Revenue": [130, NAN, 115, 110, 100]})
    c, notes = _run(client, q_inc, q_cf, info={**USD, "financialCurrency": "EUR"})
    assert client.calls == [] and notes == [] and c.basis == "annual"


def test_without_a_user_agent_nothing_is_fetched_and_the_gap_is_noted():
    q_inc, q_cf = _q(**{"Total Revenue": [130, NAN, 115, 110, 100]})
    c, notes = _run(None, q_inc, q_cf)
    assert c.basis == "annual"
    assert notes == ["SEC fill off (SEC_USER_AGENT unset) - gaps at 2026-06-30 not filled"]


@pytest.mark.parametrize("cause", ["timeout after 20 s", "HTTP 503", "unknown CIK for TST",
                                   "bad JSON"])
def test_failed_sec_fetch_keeps_the_annual_fallback_with_a_dated_note(cause):
    q_inc, q_cf = _q(**{"Total Revenue": [130, NAN, 115, 110, 100]})
    c, notes = _run(FakeSec(sec.SecError(cause)), q_inc, q_cf)
    assert c.basis == "annual"
    assert notes == [f"SEC fill failed: {cause} (gaps at 2026-06-30)"]


def test_malformed_companyfacts_never_raises_past_the_fill():
    q_inc, q_cf = _q(**{"Total Revenue": [130, NAN, 115, 110, 100]})
    facts = _facts(Revenues=[{"start": "garbage", "end": None, "val": "x"}])
    c, notes = _run(FakeSec(facts), q_inc, q_cf)
    assert c.basis == "annual"
    assert len(notes) == 1 and notes[0].startswith("SEC fill failed: malformed companyfacts")


def test_fill_never_mutates_the_callers_frames():
    q_inc, q_cf = _q(**{"Total Revenue": [130, NAN, 115, 110, 100]})
    before = q_inc.copy()
    _sec_fill("TST", q_inc, q_cf, USD, FakeSec(_facts(Revenues=REV_FACTS)), TODAY)
    pd.testing.assert_frame_equal(q_inc, before)


# --- the SEC client -----------------------------------------------------------------
UA = "Example Research research@example.com"
TICKERS = {"0": {"cik_str": 2179, "ticker": "TST", "title": "Test Co"}}


class _Resp:
    def __init__(self, status=200, body=None, bad_json=False):
        self.status_code, self._body, self._bad = status, body, bad_json

    def iter_content(self, size):
        data = b"not json" if self._bad else json.dumps(self._body).encode()
        return [data[i:i + size] for i in range(0, len(data), size)]


class _Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, headers=None, timeout=None, stream=False):
        assert stream  # the timeout bounds the whole download
        self.calls.append((url, headers, timeout))
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def _client(responses, cache=None):
    return sec.SecClient(UA, cache=cache, timeout=7.5, session=_Session(responses))


def test_client_sends_the_user_agent_and_an_explicit_timeout():
    facts = _facts(Revenues=REV_FACTS)
    c = _client([_Resp(body=TICKERS), _Resp(body=facts)])
    assert c.companyfacts("tst")["facts"]["us-gaap"]["Revenues"]
    (u1, h1, t1), (u2, _, t2) = c._session.calls
    assert u1 == sec.TICKERS_URL and u2.endswith("/CIK0000002179.json")
    assert h1["User-Agent"] == UA and t1 == t2 == 7.5
    assert UA not in repr(c)


@pytest.mark.parametrize("responses,cause", [
    ([requests.Timeout("read timed out")], "timeout after 7.5 s"),
    ([requests.ConnectionError("refused")], "request failed (ConnectionError)"),
    ([_Resp(status=403)], "HTTP 403"),
    ([_Resp(bad_json=True)], "bad JSON"),
    ([_Resp(body=TICKERS), _Resp(status=404)], "HTTP 404"),
    ([_Resp(body={"0": {"ticker": "OTHER", "cik_str": 1}})], "unknown CIK for TST"),
])
def test_client_failures_are_sec_errors_without_the_user_agent(responses, cause):
    with pytest.raises(sec.SecError) as err:
        _client(responses).companyfacts("TST")
    assert str(err.value) == cause and UA not in str(err.value)


def test_client_caches_trimmed_facts_per_day(tmp_path):
    cache = DiskCache("sec")
    cache.dir = tmp_path
    facts = _facts(Revenues=REV_FACTS, SomeUnusedConcept=REV_FACTS)
    facts["facts"]["us-gaap"]["Revenues"]["units"]["USD"].append(
        _fact("2026-01-01", "2026-03-31", 1, form="8-K"))
    first = _client([_Resp(body=TICKERS), _Resp(body=facts)], cache).companyfacts("TST")
    again = _client([], cache).companyfacts("TST")  # no request left: served from disk
    assert first == again
    assert set(first["facts"]["us-gaap"]) == {"Revenues"}  # only the mapped concepts
    assert all(f["form"] != "8-K" for f in first["facts"]["us-gaap"]["Revenues"]["units"]["USD"])


def test_client_from_env_needs_a_user_agent(monkeypatch):
    monkeypatch.delenv(sec.USER_AGENT_ENV, raising=False)
    assert sec.client_from_env() is None
    monkeypatch.setenv(sec.USER_AGENT_ENV, UA)
    assert isinstance(sec.client_from_env(), sec.SecClient)


def test_a_network_failure_stops_further_requests_this_run():
    c = _client([_Resp(body=TICKERS), requests.Timeout("read timed out")])
    with pytest.raises(sec.SecError, match="timeout after 7.5 s"):
        c.companyfacts("TST")
    with pytest.raises(sec.SecError, match="skipped: timeout after 7.5 s earlier in this run"):
        c.companyfacts("TST")
    assert len(c._session.calls) == 2  # no third request


def test_a_failed_tickers_list_is_not_retried_per_name():
    c = _client([_Resp(status=503)])
    for _ in range(3):
        with pytest.raises(sec.SecError):
            c.companyfacts("TST")
    assert len(c._session.calls) == 1


def test_an_unwritable_cache_never_fails_the_fill(tmp_path):
    class _ReadOnly(DiskCache):
        def set(self, key, value):
            raise PermissionError("read-only")

    cache = _ReadOnly("sec")
    cache.dir = tmp_path
    facts = _client([_Resp(body=TICKERS), _Resp(body=_facts(Revenues=REV_FACTS))],
                    cache).companyfacts("TST")
    assert facts["facts"]["us-gaap"]["Revenues"]


def test_sec_cache_key_is_versioned(tmp_path):
    cache = DiskCache("sec")
    cache.dir = tmp_path
    _client([_Resp(body=TICKERS), _Resp(body=_facts(Revenues=REV_FACTS))],
            cache).companyfacts("TST")
    assert (tmp_path / f"companyfacts_{sec.CACHE_SCHEMA}_0000002179.json").exists()


# --- review fixes: one basis per difference, impossible values, share rows ----------
def test_q4_subtracts_the_9m_as_filed_by_the_10k_not_a_later_recast():
    # The FY 10-K (filed 2026-02) includes a segment; a 2026 10-Q recasts the 9M-2025
    # comparative without it. FY - recast 9M would hold 9 months of the segment.
    q_inc, q_cf = _q(**{"Total Revenue": [130, 120, 115, NAN, 100]})
    rows = _quarterly({"2026-09-30": 130, "2026-06-30": 120, "2026-03-31": 115,
                       "2025-09-30": 100})
    rows += [_fact("2025-01-01", "2025-12-31", 410, form="10-K", filed="2026-02-10"),
             _fact("2025-01-01", "2025-09-30", 300, filed="2025-11-01"),
             _fact("2025-01-01", "2025-09-30", 240, filed="2026-11-01")]  # recast
    c, _ = _run(FakeSec(_facts(Revenues=rows)), q_inc, q_cf)
    assert c.revenue_ttm == 475  # 410 - 300, not 410 - 240


def test_a_quarter_that_is_not_positive_is_rejected():
    # Q4 = FY - 9M: a 9M above the FY (mismatched filings) gives a negative revenue Q4.
    q_inc, q_cf = _q(**{"Total Revenue": [130, 120, 115, NAN, 100]})
    rows = _quarterly({"2026-09-30": 130, "2026-06-30": 120, "2026-03-31": 115,
                       "2025-09-30": 100})
    rows += [_fact("2025-01-01", "2025-12-31", 290, form="10-K", filed="2026-02-10"),
             _fact("2025-01-01", "2025-09-30", 300, filed="2025-11-01")]
    c, notes = _run(FakeSec(_facts(Revenues=rows)), q_inc, q_cf)
    assert c.basis == "annual"
    assert len(notes) == 1 and "not positive" in notes[0]


def test_a_basic_share_row_is_never_filled_with_a_diluted_count():
    q_inc, q_cf = _q()
    q_inc = q_inc.rename(index={"Diluted Average Shares": "Basic Average Shares"})
    q_inc.loc["Basic Average Shares", pd.Timestamp("2025-09-30")] = NAN
    client = FakeSec(_facts(WeightedAverageNumberOfDilutedSharesOutstanding=(
        "shares", _quarterly(dict(zip(QUARTERS, [10] * 5))))))
    _, notes = _run(client, q_inc, q_cf)
    assert client.calls == [] and notes == []


def test_no_fill_is_kept_when_a_basis_gap_stays_open():
    # Revenue fills, OCF can't: the name is annual either way, so Yahoo's frames stand
    # and no "from SEC" note sits on an annual name.
    cf_dates = [d for d in QUARTERS if d != "2026-06-30"]
    q_inc, q_cf = _q(cf_dates=cf_dates, **{"Total Revenue": [130, NAN, 115, 110, 100]})
    facts = _facts(Revenues=REV_FACTS, **{k: v for k, v in CF_FACTS.items()
                                          if k != "NetCashProvidedByUsedInOperatingActivities"})
    q_inc2, q_cf2, notes = _sec_fill("TST", q_inc, q_cf, USD, FakeSec(facts), TODAY)
    assert q_inc2 is q_inc and q_cf2 is q_cf
    assert notes and all(n.startswith("SEC has no usable fact") for n in notes)


def test_a_failed_fill_is_not_cached_for_the_day(tmp_path):
    from etf_screen import providers as P

    class _Prov(P.YFinanceProvider):
        def _fetch_uncached(self, ticker):
            from etf_screen.screen import Company
            return Company(ticker=ticker, revenue_ttm=1, revenue_ttm_prior=1, ocf_ttm=1,
                           capex_ttm=0, sbc_ttm=0, diluted_shares_now=1,
                           diluted_shares_prior=1, market_cap=1, forward_pe=None,
                           forward_eps_growth=None, net_income_ttm=1,
                           low_confidence=["SEC fill failed: HTTP 503 (gaps at 2026-06-30)"])

    cache = DiskCache("yfinance")
    cache.dir = tmp_path
    _Prov(cache=cache, fetch_timeout=0, sec_client=FakeSec()).fetch("TST")
    assert not list(tmp_path.glob("*.json"))


def test_fill_notes_are_dropped_when_the_name_is_annual_anyway():
    # CINF / FLEX: every window gap fills, but Yahoo has no quarterly SBC row while
    # the annual statement reports SBC (#58), so the name is annual regardless.
    q_inc, q_cf = _q(**{"Total Revenue": [130, NAN, 115, 110, 100]})
    q_cf = q_cf.drop(index="Stock Based Compensation")
    c, notes = _run(FakeSec(_facts(Revenues=REV_FACTS)), q_inc, q_cf)
    assert c.basis == "annual" and "quarterly SBC not reported" in _annual_reason(c)
    assert notes == [] and not any(n.startswith("Q ") for n in c.low_confidence)
