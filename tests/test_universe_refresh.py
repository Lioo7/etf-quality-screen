"""`--universe` never silently uses a stale constituent list (#8, D26).

QQQ and ARKK read ``holdings/<etf>-holdings.csv``; a missing or stale file is
refreshed from its source (mocked here, never the network), and any refresh
failure is a loud error with manual steps. SPY stays on a live Wikipedia fetch.
The bundled static list only appears with ``allow_stale=True``.
"""

import json
import os
import time
from datetime import date, timedelta

import pytest

from etf_screen import cli, constituents, export
from etf_screen.constituents import holdings_from_csv, resolve

TODAY = date(2026, 11, 23)


def _us(d: date) -> str:
    return d.strftime("%m/%d/%Y")


def _ark_csv(as_of: date, n: int = 30) -> str:
    rows = ["date,fund,company,ticker,cusip,shares,market value ($),weight (%)"]
    for i in range(n):
        rows.append(f"{_us(as_of)},ARKK,CO {i},T{chr(65 + i // 26)}{chr(65 + i % 26)},X,1,$1,1%")
    return "\n".join(rows) + "\n"


def _nasdaq_json(as_of: date, n: int = 100) -> str:
    syms = [f"Q{chr(65 + i // 26)}{chr(65 + i % 26)}" for i in range(n)]
    return json.dumps({"data": {
        "date": as_of.strftime("%b %d, %Y"),
        "data": {"rows": [{"symbol": s, "companyName": f"{s} Inc."} for s in syms]},
    }})


class FakeFetch:
    def __init__(self, payload=None, exc=None):
        self.payload, self.exc, self.calls = payload, exc, []

    def __call__(self, url):
        self.calls.append(url)
        if self.exc:
            raise self.exc
        return self.payload


def _resolve(etf, tmp_path, fetch, **kw):
    return resolve(etf, holdings_dir=tmp_path, fetch=fetch, today=TODAY, **kw)


# --- fresh -------------------------------------------------------------------

def test_fresh_file_is_used_without_any_download(tmp_path):
    (tmp_path / "arkk-holdings.csv").write_text(_ark_csv(TODAY - timedelta(days=3)))
    fetch = FakeFetch(exc=AssertionError("must not download"))
    h = _resolve("ARKK", tmp_path, fetch)
    assert fetch.calls == []
    assert len(h.tickers) == 30 and h.as_of == "2026-11-20" and not h.is_stale
    assert "arkk-holdings.csv" in h.source


# --- stale -> refresh ok -----------------------------------------------------

def test_stale_file_is_refreshed_atomically_with_backup(tmp_path, capsys):
    path = tmp_path / "arkk-holdings.csv"
    old = _ark_csv(TODAY - timedelta(days=40))
    path.write_text(old)
    fetch = FakeFetch(_ark_csv(TODAY, n=35))
    h = _resolve("ARKK", tmp_path, fetch)
    assert fetch.calls == [constituents.ARKK_URL]
    assert len(h.tickers) == 35 and h.as_of == TODAY.isoformat() and not h.is_stale
    assert (tmp_path / "arkk-holdings.csv.bak").read_text() == old
    assert not list(tmp_path.glob("*.tmp"))
    assert f"refreshed from {constituents.ARKK_URL}, as-of {TODAY.isoformat()}" \
        in capsys.readouterr().err
    # The refreshed file is a plain holdings CSV the generic loader still reads.
    assert holdings_from_csv(path).tickers == h.tickers


def test_missing_file_is_downloaded(tmp_path):
    h = _resolve("ARKK", tmp_path, FakeFetch(_ark_csv(TODAY)))
    assert (tmp_path / "arkk-holdings.csv").exists()
    assert not (tmp_path / "arkk-holdings.csv.bak").exists()
    assert len(h.tickers) == 30


def test_qqq_refreshes_from_nasdaq_index_api_with_honest_label(tmp_path):
    fetch = FakeFetch(_nasdaq_json(TODAY - timedelta(days=3)))
    h = _resolve("QQQ", tmp_path, fetch)
    assert fetch.calls == [constituents.NASDAQ100_URL]
    assert len(h.tickers) == 100 and h.as_of == "2026-11-20"
    assert h.source == "Nasdaq-100 index constituents (Nasdaq API, as-of 2026-11-20)"
    assert "Invesco" not in h.source
    # A later fresh read of the written file keeps the same label.
    again = _resolve("QQQ", tmp_path, FakeFetch(exc=AssertionError("no download")))
    assert again.source == h.source and again.tickers == h.tickers


# --- stale -> refresh fails --------------------------------------------------

@pytest.mark.parametrize("fetch", [
    FakeFetch(exc=OSError("HTTP 503")),                      # network / HTTP error
    FakeFetch(_ark_csv(TODAY, n=5)),                         # count below range
    FakeFetch(_ark_csv(TODAY - timedelta(days=30))),         # download itself stale
    FakeFetch("<html>maintenance</html>"),                   # not a holdings file
    FakeFetch("ticker,company\n" + "\n".join(f"T{chr(65 + i)},C" for i in range(25))),
    FakeFetch(_ark_csv(TODAY + timedelta(days=30))),         # future-dated
])
def test_failed_refresh_is_loud_and_leaves_the_old_file(tmp_path, fetch):
    path = tmp_path / "arkk-holdings.csv"
    old = _ark_csv(TODAY - timedelta(days=40))
    path.write_text(old)
    with pytest.raises(ValueError) as exc:
        _resolve("ARKK", tmp_path, fetch)
    msg = str(exc.value)
    assert "could not refresh ARKK" in msg and "Download" in msg and str(path) in msg
    assert path.read_text() == old
    assert not list(tmp_path.glob("*.tmp")) and not list(tmp_path.glob("*.bak"))


@pytest.mark.parametrize("payload", [
    json.dumps({"data": None}),                              # schema change
    _nasdaq_json(TODAY, n=80),                               # count out of range
    _nasdaq_json(TODAY - timedelta(days=20)),                # stale index date
    json.dumps({"data": {"date": "Nov 20, 2026", "data": {"rows": [{"symbol": None}]}}}),
])
def test_qqq_nasdaq_failures_never_fall_back_silently(tmp_path, payload):
    with pytest.raises(ValueError, match="could not refresh QQQ"):
        _resolve("QQQ", tmp_path, FakeFetch(payload))


def test_cli_exits_non_zero_when_refresh_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(constituents, "HOLDINGS_DIR", tmp_path)
    monkeypatch.setattr(constituents, "_http_get", FakeFetch(exc=OSError("down")))
    args = cli.build_parser().parse_args(["--provider", "mock", "--no-cache",
                                          "--universe", "arkk"])
    assert cli.run(args) == 2
    assert "could not refresh ARKK" in capsys.readouterr().err


def test_unreadable_local_file_is_refreshed(tmp_path):
    (tmp_path / "arkk-holdings.csv").write_text("<html>error page</html>\n")
    h = _resolve("ARKK", tmp_path, FakeFetch(_ark_csv(TODAY)))
    assert len(h.tickers) == 30


def test_future_dated_local_file_is_not_fresh(tmp_path):
    (tmp_path / "arkk-holdings.csv").write_text(_ark_csv(TODAY + timedelta(days=400)))
    fetch = FakeFetch(_ark_csv(TODAY))
    _resolve("ARKK", tmp_path, fetch)
    assert fetch.calls


# --- no date column ----------------------------------------------------------

def test_no_date_column_uses_file_mtime_never_today(tmp_path):
    path = tmp_path / "plain.csv"
    path.write_text("Ticker,Name\nAAPL,Apple\nMSFT,Microsoft\n")
    month_ago = time.time() - 30 * 86400
    os.utime(path, (month_ago, month_ago))
    h = holdings_from_csv(path)
    assert h.as_of == date.fromtimestamp(month_ago).isoformat()
    assert h.as_of != date.today().isoformat()
    assert "file modified date" in h.source


def test_undated_universe_file_older_than_max_age_is_refreshed(tmp_path):
    path = tmp_path / "arkk-holdings.csv"
    path.write_text("ticker,company\n" + "\n".join(f"T{chr(65 + i)},C" for i in range(25)))
    old = time.mktime((TODAY - timedelta(days=30)).timetuple())
    os.utime(path, (old, old))
    fetch = FakeFetch(_ark_csv(TODAY))
    h = _resolve("ARKK", tmp_path, fetch)
    assert fetch.calls and h.as_of == TODAY.isoformat()


def test_max_age_is_configurable(tmp_path):
    (tmp_path / "arkk-holdings.csv").write_text(_ark_csv(TODAY - timedelta(days=20)))
    fetch = FakeFetch(exc=AssertionError("no download"))
    h = _resolve("ARKK", tmp_path, fetch, max_age_days=30)
    assert h.as_of == (TODAY - timedelta(days=20)).isoformat() and fetch.calls == []


# --- SPY / allow-stale -------------------------------------------------------

def test_spy_wikipedia_failure_is_an_error_not_the_static_list(monkeypatch):
    def boom(_):
        raise OSError("wiki down")
    monkeypatch.setattr(constituents, "_from_wikipedia", boom)
    with pytest.raises(ValueError, match="could not resolve SPY"):
        resolve("SPY")


@pytest.mark.parametrize("n,ok", [(494, False), (495, True), (510, True), (511, False)])
def test_spy_count_check(monkeypatch, n, ok):
    syms = [f"S{chr(65 + i // 26 % 26)}{chr(65 + i % 26)}{chr(65 + i // 676)}"
            for i in range(n)]
    monkeypatch.setattr(constituents, "_from_wikipedia", lambda _: (syms, {}))
    if ok:
        h = resolve("SPY")
        assert h.source.startswith("Wikipedia (live fetch; page freshness not verified)")
    else:
        with pytest.raises(ValueError, match="495"):
            resolve("SPY")


def test_allow_stale_falls_back_to_static_and_marks_it(tmp_path):
    h = _resolve("QQQ", tmp_path, FakeFetch(exc=OSError("down")), allow_stale=True)
    assert h.is_stale and h.source == "bundled static list"


def test_allow_stale_prefers_the_existing_stale_file(tmp_path):
    (tmp_path / "arkk-holdings.csv").write_text(_ark_csv(TODAY - timedelta(days=40)))
    h = _resolve("ARKK", tmp_path, FakeFetch(exc=OSError("down")), allow_stale=True)
    assert h.is_stale and len(h.tickers) == 30


def test_export_header_says_stale(tmp_path):
    prov = export.Provenance("mock", "qqq", "bundled static list", "2026-07-09",
                             "2026-11-23", stale=True)
    for fmt in ("csv", "md"):
        out = tmp_path / f"x.{fmt}"
        export.export(out, fmt, prov, [])
        assert "STALE" in out.read_text()
    fresh = export.Provenance("mock", "qqq", "src", "2026-11-23", "2026-11-23")
    export.export(tmp_path / "y.csv", "csv", fresh, [])
    assert "STALE" not in (tmp_path / "y.csv").read_text()
