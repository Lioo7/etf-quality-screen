"""Foreign private issuers on Yahoo (#58, D18).

PDD, BABA, JD and BIDU expose quarterly operating cash flow but no quarterly capex
(JD: one quarter) and no quarterly SBC, while the annual statements are complete.
The whole company must then screen on the annual basis (one basis, never mixed),
with a precise note. Their statements are in CNY while Yahoo's market cap is in
USD, so market cap is normalised to the statement currency before P/S (D18). The
unlock prices come from the traded price (#62, D20), so a converted name shows them.
"""

from dataclasses import asdict, replace

import pandas as pd
import pytest

from etf_screen.providers import (
    _CACHE_SCHEMA,
    _COMPANY_FIELDS,
    PRICE_FROM_HISTORY_NOTE,
    DataUnavailable,
    _extract_yf,
)
from etf_screen.screen import (UNLOCK_IMPLIED_NOTE, UNLOCK_SUPPRESSED_NOTE, ScreenStatus,
                               evaluate)
from test_peg_basis import _FMP_EST, _Q_CF, _Q_INC, _fmp_provider, _frame
from test_screen import ACCEPTANCE

NAN = float("nan")
_INFO = {"marketCap": 2000, "shortName": "Foreign Co", "currentPrice": 480.0,
         "currency": "USD", "financialCurrency": "USD"}

# Annual frames: revenue 400 vs 360, OCF 120, capex 20, SBC 8 (distinct from quarters).
_A_INC = {"Total Revenue": [400, 360, 330], "Diluted Average Shares": [10, 10, 11],
          "Net Income": [80, 70, 60]}
_A_CF = {"Operating Cash Flow": [120, 110, 100], "Capital Expenditure": [-20, -18, -16],
         "Stock Based Compensation": [8, 7, 6]}


def _annual(rows: dict):
    n = max(len(v) for v in rows.values())
    cols = pd.date_range("2025-12-31", periods=n, freq="-1YE")
    return pd.DataFrame({k: list(v) for k, v in rows.items()}, index=cols).T


def _build(q_cf, a_cf=_A_CF, a_inc=_A_INC, info=_INFO, fx_lookup=None):
    return _extract_yf(
        "PDD", q_inc=_frame(_Q_INC, 5), a_inc=_annual(a_inc) if a_inc else None,
        q_cf=_frame(q_cf, 5) if q_cf else None,
        a_cf=_annual(a_cf) if a_cf else None, info=info, fx_lookup=fx_lookup)


def _no_capex():
    return {k: v for k, v in _Q_CF.items() if k != "Capital Expenditure"}


# --- basis selection: capex ---------------------------------------------------
def test_missing_quarterly_capex_goes_annual():
    c = _build(_no_capex())  # PDD/BABA/BIDU: OCF x5, capex x0
    assert c.basis == "annual"
    assert (c.revenue_ttm, c.revenue_ttm_prior) == (400, 360)  # annual, not quarterly
    assert (c.ocf_ttm, c.capex_ttm, c.sbc_ttm) == (120, 20, 8)
    assert c.revenue_latest_q is None and c.revenue_year_ago_q is None
    assert any("ANNUAL basis (quarterly capex not reported by source)" in n
               for n in c.low_confidence)


def test_single_quarter_of_capex_goes_annual():
    q_cf = {**_Q_CF, "Capital Expenditure": [-5, NAN, NAN, NAN, NAN]}  # JD shape
    c = _build(q_cf)
    assert c.basis == "annual" and c.capex_ttm == 20


def test_full_quarterly_capex_stays_ttm():
    c = _build(_Q_CF)
    assert c.basis == "TTM" and c.capex_ttm == 20 and c.revenue_ttm == 475
    assert not any("ANNUAL" in n for n in c.low_confidence)


def test_annual_capex_also_missing_names_the_gap():
    a_cf = {k: v for k, v in _A_CF.items() if k != "Capital Expenditure"}
    with pytest.raises(DataUnavailable, match="annual capex"):
        _build(_no_capex(), a_cf=a_cf)


def test_annual_income_too_short_is_a_precise_skip():
    with pytest.raises(DataUnavailable, match="annual revenue"):
        _build(_no_capex(), a_inc={"Total Revenue": [400], "Diluted Average Shares": [10],
                                   "Net Income": [80]})


# --- basis selection: SBC -------------------------------------------------------
def _no_sbc():
    return {k: v for k, v in _Q_CF.items() if k != "Stock Based Compensation"}


def test_missing_quarterly_sbc_with_annual_sbc_goes_annual():
    c = _build(_no_sbc())
    assert c.basis == "annual" and c.sbc_ttm == 8 and not c.sbc_assumed_zero
    assert (c.revenue_ttm, c.ocf_ttm) == (400, 120)  # every line annual, none quarterly
    assert c.revenue_latest_q is None and c.revenue_year_ago_q is None
    assert any("ANNUAL basis (quarterly SBC not reported by source)" in n
               for n in c.low_confidence)


def test_partial_quarterly_sbc_with_annual_sbc_goes_annual():
    q_cf = {**_Q_CF, "Stock Based Compensation": [2, 2, NAN, NAN, NAN]}
    assert _build(q_cf).basis == "annual"


def test_no_sbc_anywhere_stays_ttm_with_assumed_zero():
    a_cf = {k: v for k, v in _A_CF.items() if k != "Stock Based Compensation"}
    c = _build(_no_sbc(), a_cf=a_cf)
    assert c.basis == "TTM" and c.sbc_assumed_zero and c.sbc_ttm == 0.0


def test_unusable_annual_statements_do_not_pull_a_ttm_name_off_ttm():
    # Annual SBC exists but the annual income frame is unusable: keep TTM (assumed-0
    # SBC, flagged) rather than turn a screenable name into a skip.
    c = _build(_no_sbc(), a_inc={"Total Revenue": [400], "Diluted Average Shares": [10],
                                 "Net Income": [80]})
    assert c.basis == "TTM" and c.sbc_assumed_zero


# --- statement-currency normalisation (D18) -------------------------------------
_CNY = {**_INFO, "financialCurrency": "CNY"}


def test_cny_statements_convert_the_usd_market_cap():
    calls = []

    def lookup(base, quote):
        calls.append((base, quote))
        return 7.0, "2026-11-22"

    c = _build(_Q_CF, info=_CNY, fx_lookup=lookup)
    assert calls == [("USD", "CNY")]
    assert c.market_cap == pytest.approx(14000.0) and c.market_cap_converted
    assert evaluate(c).p_s == pytest.approx(14000.0 / c.revenue_ttm)
    assert any("market cap converted USD->CNY at 7.0000 (2026-11-22)" in n
               for n in c.low_confidence)
    assert not any("unlock price not shown" in n for n in c.low_confidence)
    assert c.current_price == 480.0  # the quote the metrics used (D1)


def test_missing_fx_rate_skips_rather_than_guesses():
    with pytest.raises(DataUnavailable, match="CNY.*USD.*FX rate"):
        _build(_Q_CF, info=_CNY, fx_lookup=lambda b, q: None)


def test_no_fx_lookup_available_also_skips():
    with pytest.raises(DataUnavailable, match="FX rate"):
        _build(_Q_CF, info=_CNY, fx_lookup=None)


@pytest.mark.parametrize("info", [
    _INFO,                                                   # same currency
    {k: v for k, v in _INFO.items() if k != "financialCurrency"},  # unknown: unchanged
])
def test_same_or_unknown_statement_currency_does_not_look_up_a_rate(info):
    def lookup(base, quote):  # pragma: no cover - must not be called
        raise AssertionError("no FX lookup expected")

    c = _build(_Q_CF, info=info, fx_lookup=lookup)
    assert c.market_cap == 2000 and not c.market_cap_converted


def test_the_cny_name_end_to_end_gets_a_verdict_on_one_basis():
    c = _build(_no_capex(), info=_CNY, fx_lookup=lambda b, q: (7.0, "2026-11-22"))
    r = evaluate(c)
    # Annual revenue 400 / 360 and a converted cap of 14000 -> P/S 35 on ONE basis.
    assert c.basis == "annual" and r.p_s == pytest.approx(35.0)
    assert r.status is ScreenStatus.REJECTED and any("P/S" in x for x in r.reasons)


# --- unlock prices from the traded price (#62, D20) -------------------------------
def test_converted_market_cap_shows_the_unlock_price():
    # Cap in CNY, ADS trading in USD: price x (ceiling / PEG) needs neither FX nor
    # the ADS ratio, so the #58 suppression is lifted.
    base = replace(ACCEPTANCE["PriceyButProfitable"], market_cap_converted=True,
                   current_price=90.0)
    r = evaluate(base)
    assert r.status is ScreenStatus.WATCHLIST and r.unlock_basis == "price"
    assert r.unlock_peg_price == pytest.approx(90.0 * 2.0 / r.peg)
    assert not any("unlock" in n for n in r.low_confidence_notes)


def test_converted_market_cap_without_a_traded_price_shows_no_unlock():
    # The implied price (converted cap / ordinary shares) is in the statement
    # currency, so it is never used: no unlock, with a note; the verdict never moves.
    r = evaluate(replace(ACCEPTANCE["PriceyButProfitable"], market_cap_converted=True))
    assert r.status is ScreenStatus.WATCHLIST and r.unlock_basis is None
    assert r.unlock_peg_price is None and r.unlock_ps_price is None
    assert UNLOCK_SUPPRESSED_NOTE in r.low_confidence_notes
    assert UNLOCK_IMPLIED_NOTE not in r.low_confidence_notes


def test_no_suppression_note_when_growth_rules_out_an_unlock_anyway():
    from etf_screen.cli import _unlocks
    from test_screen import make
    # Flat revenue: P/S can never pass, PEG passes -> no unlock on any price basis.
    flat = make("FLAT", 1000, 1000, 500, 30, 30, 1000, 1000, 8000, 20, 15, net_income=300)
    r = evaluate(replace(flat, market_cap_converted=True))
    assert r.status is ScreenStatus.WATCHLIST and r.unlock_basis is None
    assert UNLOCK_SUPPRESSED_NOTE not in r.low_confidence_notes
    assert "growth <= 0" in _unlocks(r)


# --- Yahoo price source: the quote the metrics used (D1, D20) ---------------------
def _closes(last):
    return pd.Series([last - 10, last], index=pd.to_datetime(["2026-01-02", "2026-11-20"]))


def test_yahoo_current_price_is_the_quote_not_the_history_close():
    c = _extract_yf("PDD", q_inc=_frame(_Q_INC, 5), a_inc=None, q_cf=_frame(_Q_CF, 5),
                    a_cf=None, info=_INFO, closes=_closes(470.0))
    assert c.current_price == 480.0 and c.ath_price == 470.0
    assert PRICE_FROM_HISTORY_NOTE not in c.low_confidence


def test_yahoo_current_price_falls_back_to_regular_market_price():
    info = {**{k: v for k, v in _INFO.items() if k != "currentPrice"},
            "regularMarketPrice": 475.0}
    c = _extract_yf("PDD", q_inc=_frame(_Q_INC, 5), a_inc=None, q_cf=_frame(_Q_CF, 5),
                    a_cf=None, info=info, closes=_closes(470.0))
    assert c.current_price == 475.0 and PRICE_FROM_HISTORY_NOTE not in c.low_confidence


def test_yahoo_current_price_uses_the_history_close_only_without_a_quote():
    info = {k: v for k, v in _INFO.items() if k != "currentPrice"}
    c = _extract_yf("PDD", q_inc=_frame(_Q_INC, 5), a_inc=None, q_cf=_frame(_Q_CF, 5),
                    a_cf=None, info=info, closes=_closes(470.0))
    assert c.current_price == 470.0 and PRICE_FROM_HISTORY_NOTE in c.low_confidence
    none = _extract_yf("PDD", q_inc=_frame(_Q_INC, 5), a_inc=None, q_cf=_frame(_Q_CF, 5),
                       a_cf=None, info=info)
    assert none.current_price is None and PRICE_FROM_HISTORY_NOTE not in none.low_confidence


# --- cache round trip -----------------------------------------------------------
def test_converted_flag_survives_the_cache_and_the_schema_is_current(tmp_path, monkeypatch):
    import etf_screen.cache as cache_mod
    from etf_screen.cache import DiskCache
    from etf_screen.providers import MockProvider

    monkeypatch.setattr(cache_mod, "CACHE_ROOT", tmp_path)
    assert "market_cap_converted" in _COMPANY_FIELDS and _CACHE_SCHEMA == "v8"
    cache = DiskCache("mock")
    stored = asdict(replace(MockProvider().fetch("MSFT"), market_cap_converted=True))
    provider = MockProvider(cache=cache)
    cache.set(provider._cache_key("MSFT"), stored)
    assert provider.fetch("MSFT").market_cap_converted is True


# --- FMP: same mismatch, same normalisation ---------------------------------------
def test_fmp_converts_market_cap_when_reporting_currency_differs(monkeypatch):
    from etf_screen.providers import FMPProvider

    p = _fmp_provider(monkeypatch, _FMP_EST, profile_ccy="USD", reported_ccy="CNY")
    calls = []

    def fx(self, base, quote):
        calls.append((base, quote))
        return 7.0, "2026-11-22"

    monkeypatch.setattr(FMPProvider, "_fx_rate", fx)
    c = p.fetch("PDD")
    assert calls[0] == ("USD", "CNY")
    assert c.market_cap == pytest.approx(14000.0) and c.market_cap_converted
    assert c.current_price == 480.0  # the quote price: the unlock needs no FX
    assert not any("unlock price not shown" in n for n in c.low_confidence)


def test_fmp_missing_rate_skips(monkeypatch):
    from etf_screen.providers import FMPProvider

    p = _fmp_provider(monkeypatch, _FMP_EST, profile_ccy="USD", reported_ccy="CNY")
    monkeypatch.setattr(FMPProvider, "_fx_rate", lambda self, b, q: None)
    with pytest.raises(DataUnavailable, match="FX rate"):
        p.fetch("PDD")


def test_fmp_same_currency_is_untouched(monkeypatch):
    p = _fmp_provider(monkeypatch, _FMP_EST)
    c = p.fetch("FMP")
    assert c.market_cap == 2000 and not c.market_cap_converted
