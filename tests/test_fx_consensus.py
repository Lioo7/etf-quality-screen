"""Currency-converted consensus EPS for foreign filers (#46, D1).

When Yahoo's estimate currency differs from the price currency, EPS_FY1 is converted
with a same-run Yahoo FX rate before ``forward_pe = price / EPS_FY1``. Growth is a
ratio and stays currency-neutral. The converted forward P/E must agree with Yahoo's
own ``forwardPE`` within 30%, else the PEG stays untestable ("currency/ADR basis
mismatch", e.g. one ADR = 5 ordinary shares). A missing FX rate is untestable,
never a guessed rate.
"""

import pandas as pd
import pytest

from etf_screen.providers import YFinanceProvider, _consensus_peg_inputs, _extract_yf
from etf_screen.screen import ScreenStatus, evaluate
from test_peg_basis import _Q_CF, _Q_INC, _a_inc, _estimates, _frame

_FX = (1.0, "2026-11-21")


# --- the pure helper --------------------------------------------------------
def test_eur_at_one_to_one_is_testable():
    fpe, growth, notes = _consensus_peg_inputs(
        480.0, "USD", 20.0, 24.0, "EUR", 30, fx=_FX, source_forward_pe=20.5)
    assert fpe == pytest.approx(20.0)
    assert growth == pytest.approx(20.0)
    assert any("EUR->USD" in n and "1.0000" in n and "2026-11-21" in n for n in notes)
    assert not any(n.startswith("PEG untestable") for n in notes)


def test_rate_scales_the_forward_pe_but_not_growth():
    fpe, growth, _ = _consensus_peg_inputs(
        480.0, "USD", 20.0, 24.0, "EUR", 30, fx=(1.25, "t"), source_forward_pe=16.0)
    assert fpe == pytest.approx(16.0)      # 480 / (24 x 1.25)
    assert growth == pytest.approx(20.0)   # unchanged by the rate


def test_five_to_one_adr_is_caught_by_the_sanity_check():
    # EPS per ordinary share, price per ADR (1 ADR = 5 shares): the converted P/E is
    # 5x Yahoo's own per-ADR forwardPE.
    fpe, growth, notes = _consensus_peg_inputs(
        480.0, "USD", 4.0, 4.8, "EUR", 30, fx=_FX, source_forward_pe=20.0)
    assert fpe is None and growth is None
    assert any(n.startswith("PEG untestable: currency/ADR basis mismatch") for n in notes)


@pytest.mark.parametrize("src,ok", [(15.5, True), (15.0, False), (28.0, True),
                                    (29.0, False)])
def test_sanity_band_is_thirty_percent(src, ok):
    # converted forward P/E is 20; |20 / src - 1| > 0.30 rejects.
    fpe, _, _ = _consensus_peg_inputs(
        480.0, "USD", 20.0, 24.0, "EUR", 30, fx=_FX, source_forward_pe=src)
    assert (fpe is not None) is ok


def test_missing_fx_rate_is_untestable():
    fpe, growth, notes = _consensus_peg_inputs(
        480.0, "USD", 20.0, 24.0, "EUR", 30, fx=None, source_forward_pe=20.0)
    assert fpe is None and growth is None
    assert any(n.startswith("PEG untestable") and "FX rate" in n for n in notes)


@pytest.mark.parametrize("src", [None, 0.0, -3.0])
def test_no_yahoo_forward_pe_to_cross_check_is_untestable(src):
    fpe, growth, notes = _consensus_peg_inputs(
        480.0, "USD", 20.0, 24.0, "EUR", 30, fx=_FX, source_forward_pe=src)
    assert fpe is None and growth is None
    assert any("currency/ADR basis unverified" in n for n in notes)


def test_same_currency_ignores_fx_and_forward_pe():
    # The cross-check only applies to converted figures; D1's basis is unchanged.
    fpe, _, notes = _consensus_peg_inputs(
        480.0, "USD", 20.0, 24.0, "USD", 30, fx=None, source_forward_pe=99.0)
    assert fpe == pytest.approx(20.0) and notes == []


# --- yfinance extraction ----------------------------------------------------
_INFO = {"marketCap": 2000, "shortName": "Foreign Co", "currentPrice": 480.0,
         "currency": "USD", "forwardPE": 20.0}


def _yf(estimates, fx_lookup, info=_INFO):
    return _extract_yf("ASMX", q_inc=_frame(_Q_INC, 5), a_inc=_a_inc(),
                       q_cf=_frame(_Q_CF, 5), a_cf=None, info=info,
                       estimates=estimates, fx_lookup=fx_lookup)


def test_extract_converts_eur_estimates():
    calls = []

    def lookup(base, quote):
        calls.append((base, quote))
        return _FX

    c = _yf(_estimates(20.0, 24.0, currency="EUR"), lookup)
    assert calls == [("EUR", "USD")]
    assert c.forward_pe == pytest.approx(20.0)
    assert c.forward_eps_growth == pytest.approx(20.0)
    # The informational raw fields stay in the estimate currency.
    assert (c.eps_fy0, c.eps_fy1, c.estimate_currency) == (20.0, 24.0, "EUR")
    assert evaluate(c).peg == pytest.approx(1.0)


def test_extract_skips_the_lookup_for_same_currency():
    def lookup(base, quote):  # pragma: no cover - must not be called
        raise AssertionError("no FX lookup for USD estimates")

    c = _yf(_estimates(20.0, 24.0, currency="USD"), lookup)
    assert c.forward_pe == pytest.approx(20.0)


def test_extract_missing_rate_is_needs_data():
    c = _yf(_estimates(20.0, 24.0, currency="EUR"), lambda b, q: None)
    assert c.forward_pe is None and c.forward_eps_growth is None
    r = evaluate(c)
    assert r.status is ScreenStatus.NEEDS_DATA
    assert any("FX rate" in reason for reason in r.reasons)


def test_extract_adr_mismatch_is_needs_data():
    c = _yf(_estimates(4.0, 4.8, currency="EUR"), lambda b, q: _FX)
    r = evaluate(c)
    assert r.status is ScreenStatus.NEEDS_DATA
    assert any("currency/ADR basis mismatch" in reason for reason in r.reasons)


# --- the provider's FX fetch ------------------------------------------------
class _FxTicker:
    calls: list = []
    closes = {"EURUSD=X": [1.08, 1.09, float("nan")]}

    def __init__(self, symbol):
        self.symbol = symbol
        _FxTicker.calls.append(symbol)

    def history(self, **kw):
        vals = self.closes.get(self.symbol)
        if vals is None:
            return pd.DataFrame({"Close": []})
        idx = pd.date_range("2026-11-19", periods=len(vals), freq="D", tz="UTC")
        return pd.DataFrame({"Close": vals}, index=idx)


def test_provider_fx_rate_is_the_last_close_and_cached_per_run(monkeypatch):
    import yfinance

    _FxTicker.calls = []
    monkeypatch.setattr(yfinance, "Ticker", _FxTicker)
    p = YFinanceProvider(cache=None, fetch_timeout=0)
    rate, as_of = p._fx_rate("EUR", "USD")
    assert rate == pytest.approx(1.09) and as_of.startswith("2026-11-20")
    assert p._fx_rate("EUR", "USD") == (rate, as_of)
    assert _FxTicker.calls == ["EURUSD=X"]       # one request per pair per run


def test_provider_missing_fx_rate_is_none_not_a_guess(monkeypatch):
    import yfinance

    monkeypatch.setattr(yfinance, "Ticker", _FxTicker)
    p = YFinanceProvider(cache=None, fetch_timeout=0)
    assert p._fx_rate("JPY", "USD") is None
