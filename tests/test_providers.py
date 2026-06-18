"""Offline tests for the data layer (no network): MockProvider, cache, and
ticker normalization."""

import pytest

from etf_screen.cache import DiskCache
from etf_screen.constituents import _looks_like_ticker, _normalize
from etf_screen.providers import DataUnavailable, MockProvider


def test_mock_provider_returns_known_ticker():
    c = MockProvider().fetch("MSFT")
    assert c.ticker == "MSFT" and c.revenue_ttm > 0


def test_mock_provider_raises_on_unknown():
    with pytest.raises(DataUnavailable):
        MockProvider().fetch("NOPE")


def test_cache_round_trip(tmp_path, monkeypatch):
    import etf_screen.cache as cache_mod

    monkeypatch.setattr(cache_mod, "CACHE_ROOT", tmp_path)
    cache = DiskCache("mock")
    provider = MockProvider(cache=cache)

    first = provider.fetch("MSFT")
    # Second fetch should hit the cache and reconstruct an equal Company.
    second = provider.fetch("MSFT")
    assert first == second
    assert (tmp_path / "mock").exists()


def test_normalize_dotted_ticker():
    assert _normalize("BRK.B") == "BRK-B"


def test_looks_like_ticker_rejects_junk():
    assert _looks_like_ticker("AAPL")
    assert not _looks_like_ticker("")             # empty
    assert not _looks_like_ticker("123")          # numeric
    assert not _looks_like_ticker("Industrials")  # too long for a ticker
