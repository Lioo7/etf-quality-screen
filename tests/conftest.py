"""Shared test setup."""

import os

import pytest


@pytest.fixture(autouse=True)
def _no_sec_requests(monkeypatch):
    """Offline tests never reach SEC, even when the shell exported SEC_USER_AGENT (#74)."""
    if not os.environ.get("RUN_LIVE_TESTS"):
        monkeypatch.delenv("SEC_USER_AGENT", raising=False)
