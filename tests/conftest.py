"""Shared test setup."""

import os

import pytest


@pytest.fixture(autouse=True)
def _no_sec_requests(monkeypatch):
    """Offline tests never reach SEC, even when the shell exported SEC_USER_AGENT (#74)."""
    if not os.environ.get("RUN_LIVE_TESTS"):
        monkeypatch.delenv("SEC_USER_AGENT", raising=False)


@pytest.fixture(autouse=True)
def _no_universe_downloads(monkeypatch):
    """Offline tests never download a universe through the qqq default (#8)."""
    if os.environ.get("RUN_LIVE_TESTS"):
        return
    from etf_screen import constituents

    def _blocked(url, *_):
        raise AssertionError(f"offline test tried to download {url}")

    monkeypatch.setattr(constituents, "_http_get", _blocked, raising=False)
