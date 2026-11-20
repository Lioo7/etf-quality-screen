"""A hung data request can never stall a run (issue #7).

yfinance statement properties take no timeout, so ``DataProvider.fetch`` puts a
wall-clock deadline on each uncached fetch. A timed-out name lands in "could not
source" and the run continues; the abandoned call must not block process exit.
"""

import subprocess
import sys
import textwrap
import threading
import time
from dataclasses import asdict

import pytest

from etf_screen import cli
from etf_screen.cache import DiskCache
from etf_screen.providers import PROVIDERS, DataUnavailable, MockProvider


class HangingProvider(MockProvider):
    """Mock data, except HANG blocks forever (like a stuck Yahoo call)."""

    name = "hanging"

    def _fetch_uncached(self, ticker):
        if ticker == "HANG":
            threading.Event().wait()  # never set
        return super()._fetch_uncached(ticker)


def test_hung_fetch_raises_data_unavailable():
    provider = HangingProvider(fetch_timeout=0.2)
    start = time.monotonic()
    with pytest.raises(DataUnavailable, match=r"timed out after 0\.2 s"):
        provider.fetch("HANG")
    assert time.monotonic() - start < 5


def test_fast_fetch_and_errors_pass_through():
    provider = HangingProvider(fetch_timeout=5)
    assert provider.fetch("MSFT").ticker == "MSFT"
    with pytest.raises(DataUnavailable, match="no mock data"):
        provider.fetch("NOPE")  # the worker's own exception is re-raised


def test_cached_ticker_skips_the_deadline(tmp_path, monkeypatch):
    import etf_screen.cache as cache_mod

    monkeypatch.setattr(cache_mod, "CACHE_ROOT", tmp_path)
    cache = DiskCache("hanging")
    entry = asdict(MockProvider().fetch("MSFT"))
    entry["ticker"] = "HANG"
    provider = HangingProvider(cache=cache, fetch_timeout=0.01)
    cache.set(provider._cache_key("HANG"), entry)
    # A cache hit never reaches the (hanging) network path, even at a tiny deadline.
    assert provider.fetch("HANG").ticker == "HANG"


def test_run_reports_timeout_and_continues(monkeypatch, capsys):
    monkeypatch.setitem(PROVIDERS, "hanging", HangingProvider)
    args = cli.build_parser().parse_args(
        ["--provider", "mock", "--no-cache", "--tickers", "HANG,MSFT",
         "--fetch-timeout", "0.2"])
    args.provider = "hanging"
    assert cli.run(args) == 0
    out = capsys.readouterr().out
    assert "evaluated       : 1" in out
    assert "HANG (HANG): timed out after 0.2 s" in out


def test_process_exits_despite_abandoned_hung_call():
    # A non-daemon worker (e.g. concurrent.futures) would be joined at interpreter
    # exit and hang the process forever; the subprocess timeout catches that.
    script = textwrap.dedent("""
        import threading
        from etf_screen import cli
        from etf_screen.providers import PROVIDERS, MockProvider

        class Hanging(MockProvider):
            name = "hanging"
            def _fetch_uncached(self, ticker):
                if ticker == "HANG":
                    threading.Event().wait()
                return super()._fetch_uncached(ticker)

        PROVIDERS["mock"] = Hanging
        raise SystemExit(cli.main(["--provider", "mock", "--no-cache",
                                   "--tickers", "HANG,MSFT", "--fetch-timeout", "0.3"]))
    """)
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True,
                          text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert "HANG (HANG): timed out after 0.3 s" in proc.stdout
