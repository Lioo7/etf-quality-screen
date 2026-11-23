"""`--universe qqq,spy`: one de-duplicated run with membership tags (#47).

Each name is fetched once and carries the universes it belongs to (console
column, export field). Membership is metadata only: it never changes
``status``, ``track`` or rank. S&P 400 (``mid`` / ``sp400``) is a live Wikipedia
universe under the D26 rules. Universe resolution is stubbed: no network.
"""

import csv
from datetime import date

import pytest

from etf_screen import cli, constituents
from etf_screen.constituents import Holdings, resolve, resolve_union
from etf_screen.providers import MockProvider
from etf_screen.screen import rank

QQQ = ["MSFT", "GOOGL", "CRWD", "DURABLE", "GOOG"]
SPY = ["MSFT", "GOOGL", "MOAT", "ROLLUP", "BRK.B", "BF.B"]


def _fake_resolve(lists, stale=()):
    calls = []

    def fake(etf, **_):
        calls.append(etf.upper())
        etf = constituents._ALIASES.get(etf.upper(), etf.upper())
        if etf not in lists:
            raise ValueError(f"could not resolve {etf}")
        return Holdings(etf, list(lists[etf]), f"{etf.lower()} source", "2026-11-20",
                        etf in stale, {t: f"{t} Inc." for t in lists[etf]})
    fake.calls = calls
    return fake


@pytest.fixture
def two(monkeypatch):
    fake = _fake_resolve({"QQQ": QQQ, "SPY": SPY + ["BRK-B"]})
    monkeypatch.setattr(constituents, "resolve", fake)
    return fake


# --- union and de-dup --------------------------------------------------------

def test_union_keeps_first_seen_order_and_tags_membership(two):
    h = resolve_union(["qqq", "spy"])
    assert h.tickers == ["MSFT", "GOOGL", "CRWD", "DURABLE", "GOOG",
                         "MOAT", "ROLLUP", "BRK-B", "BF-B"]
    assert len(h.tickers) == len(set(h.tickers))
    assert h.membership["MSFT"] == ["qqq", "spy"]
    assert h.membership["GOOGL"] == ["qqq", "spy"]
    assert h.membership["CRWD"] == ["qqq"]
    assert h.membership["MOAT"] == ["spy"]
    assert h.etf == "QQQ+SPY"
    assert h.as_of == "2026-11-20" and not h.is_stale


def test_share_class_spellings_collapse_but_goog_and_googl_stay_separate(monkeypatch):
    monkeypatch.setattr(constituents, "resolve", _fake_resolve({
        "QQQ": ["BRK.B", "GOOG", "BF-B"], "SPY": ["BRK-B", "GOOGL", "BF.B"]}))
    h = resolve_union(["qqq", "spy"])
    assert h.tickers == ["BRK-B", "GOOG", "BF-B", "GOOGL"]
    assert h.membership["BRK-B"] == ["qqq", "spy"]
    assert h.membership["BF-B"] == ["qqq", "spy"]
    assert h.membership["GOOG"] == ["qqq"] and h.membership["GOOGL"] == ["spy"]


def test_single_universe_union_is_unchanged_apart_from_tags(two):
    h = resolve_union(["qqq"])
    assert h.tickers == QQQ and h.source == "qqq source" and h.etf == "QQQ"
    assert all(h.membership[t] == ["qqq"] for t in QQQ)


def test_repeated_universe_is_resolved_once(two):
    resolve_union(["qqq", "QQQ"])
    assert two.calls == ["QQQ"]


def test_one_failing_universe_fails_the_whole_union(monkeypatch):
    monkeypatch.setattr(constituents, "resolve", _fake_resolve({"QQQ": QQQ}))
    with pytest.raises(ValueError, match="could not resolve SPY"):
        resolve_union(["qqq", "spy"])


def test_a_stale_part_marks_the_union_stale_and_names_its_source(monkeypatch):
    monkeypatch.setattr(constituents, "resolve",
                        _fake_resolve({"QQQ": QQQ, "SPY": SPY}, stale={"QQQ"}))
    h = resolve_union(["qqq", "spy"], allow_stale=True)
    assert h.is_stale
    assert "QQQ: qqq source [STALE]" in h.source and "SPY: spy source" in h.source


def test_oldest_part_dates_the_union(monkeypatch):
    def fake(etf, **_):
        as_of = {"QQQ": "2026-11-20", "SPY": "2026-11-23"}[etf.upper()]
        return Holdings(etf.upper(), ["MSFT"], "s", as_of, False)
    monkeypatch.setattr(constituents, "resolve", fake)
    assert resolve_union(["spy", "qqq"]).as_of == "2026-11-20"


# --- CLI ---------------------------------------------------------------------

def _args(*extra):
    return cli.build_parser().parse_args(["--provider", "mock", "--no-cache",
                                          "--consistency-years", "0", *extra])


@pytest.fixture
def fetches(monkeypatch):
    seen = []
    orig = MockProvider.fetch

    def counting(self, ticker):
        seen.append(ticker)
        return orig(self, ticker)
    monkeypatch.setattr(MockProvider, "fetch", counting)
    return seen


def _univ_header(out):
    return any("Rule40 Hist" in ln and ln.rstrip().endswith("Univ")
               for ln in out.splitlines())


def test_cli_union_fetches_each_name_once_and_shows_membership(two, fetches, capsys):
    assert cli.run(_args("--universe", "qqq,spy")) == 0
    assert sorted(fetches) == sorted(set(fetches))
    assert set(fetches) == set(resolve_union(["qqq", "spy"]).tickers)
    out = capsys.readouterr().out
    assert "Universe : qqq+spy (9 of 9 unique tickers; 11 listed, 2 in more than one)" in out
    assert "  QQQ: qqq source" in out and "  SPY: spy source" in out
    assert _univ_header(out) and "qqq;spy" in out
    assert "BRK-B (BRK.B Inc.) [spy]: " in out  # could-not-source lines are tagged
    assert "CRWD (" in out and " [qqq]" in out


def test_cli_single_universe_has_no_univ_column(two, capsys):
    assert cli.run(_args("--universe", "qqq")) == 0
    out = capsys.readouterr().out
    assert not _univ_header(out) and "qqq;spy" not in out
    assert "Source   : qqq source" in out


def test_cli_limit_counts_unique_names(two, fetches, capsys):
    assert cli.run(_args("--universe", "qqq,spy", "--limit", "6")) == 0
    assert fetches == ["MSFT", "GOOGL", "CRWD", "DURABLE", "GOOG", "MOAT"]
    assert "(6 of 9 unique tickers; 11 listed" in capsys.readouterr().out


@pytest.mark.parametrize("spelling", ["sp400", "MID", "mid,MID,"])
def test_cli_label_is_the_canonical_universe_name(monkeypatch, spelling, capsys):
    monkeypatch.setattr(constituents, "resolve", _fake_resolve({"MID": ["MSFT"]}))
    assert cli.run(_args("--universe", spelling)) == 0
    assert "Universe : mid (1 tickers)" in capsys.readouterr().out


def test_cli_union_with_a_stale_part_is_flagged_on_console_and_export(
        monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(constituents, "resolve",
                        _fake_resolve({"QQQ": QQQ, "SPY": SPY}, stale={"QQQ"}))
    out_csv = tmp_path / "x.csv"
    assert cli.run(_args("--universe", "qqq,spy", "--allow-stale",
                         "--export", "csv", "--out", str(out_csv))) == 0
    out = capsys.readouterr().out
    assert "Source   :  [STALE]" in out and "  QQQ: qqq source [STALE]" in out
    assert "# STALE constituents" in out_csv.read_text()


def test_unparseable_part_date_counts_as_oldest(monkeypatch):
    def fake(etf, **_):
        as_of = {"QQQ": "Nov 20 2026 EST", "SPY": "2026-11-23"}[etf.upper()]
        return Holdings(etf.upper(), ["MSFT"], "s", as_of, True)
    monkeypatch.setattr(constituents, "resolve", fake)
    assert resolve_union(["spy", "qqq"]).as_of == "Nov 20 2026 EST"


def test_cli_union_failure_exits_non_zero(monkeypatch, capsys):
    monkeypatch.setattr(constituents, "resolve", _fake_resolve({"QQQ": QQQ}))
    assert cli.run(_args("--universe", "qqq,spy")) == 2
    assert "could not resolve SPY" in capsys.readouterr().err


def test_export_carries_universes_column_and_union_filename(two, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.run(_args("--universe", "qqq,spy", "--export", "csv")) == 0
    (path,) = (tmp_path / "results").glob("*.csv")
    assert path.name == f"etf_screen_qqq+spy_{date.today().isoformat()}.csv"
    lines = path.read_text().splitlines()
    assert any(ln.startswith("# universe: qqq+spy") for ln in lines)
    rows = {r["ticker"]: r for r in csv.DictReader(ln for ln in lines
                                                   if not ln.startswith("#"))}
    assert rows["MSFT"]["universes"] == "qqq;spy"
    assert rows["CRWD"]["universes"] == "qqq"
    assert rows["BRK-B"]["universes"] == "spy"  # skipped by mock: still tagged


def test_membership_never_changes_status_track_or_default_rank(two, monkeypatch):
    """Parity under the default --rank rule40 (sector medians span the union)."""
    captured = {}
    orig = cli._report

    def spy_report(args, source, as_of, stale, tickers, results, *rest, **kw):
        captured[args.tickers or args.universe] = results
        return orig(args, source, as_of, stale, tickers, results, *rest, **kw)
    monkeypatch.setattr(cli, "_report", spy_report)
    union = resolve_union(["qqq", "spy"]).tickers
    assert cli.run(_args("--universe", "qqq,spy")) == 0
    assert cli.run(_args("--tickers", ",".join(union))) == 0
    a, b = captured["qqq,spy"], captured[",".join(union)]
    assert [(r.ticker, r.status, r.track) for r in a] == \
        [(r.ticker, r.status, r.track) for r in b]
    assert [r.ticker for r in rank(a)] == [r.ticker for r in rank(b)]
    assert rank(a)  # the parity check covers at least one survivor


# --- S&P 400 -----------------------------------------------------------------

def _wiki_html(n):
    rows = "".join(f"<tr><td>M{chr(65 + i // 26 % 26)}{chr(65 + i % 26)}"
                   f"{chr(65 + i // 676)}</td><td>Mid {i}</td></tr>" for i in range(n))
    return (f"<table><tr><th>Symbol</th><th>Security</th></tr>{rows}"
            f"<tr><td>BRK.B</td><td>Dot Class</td></tr></table>")


@pytest.mark.parametrize("name", ["mid", "sp400", "MID"])
def test_sp400_resolves_from_wikipedia_under_either_name(monkeypatch, name):
    urls = []

    def fetch(url, *_):
        urls.append(url)
        return _wiki_html(399)
    monkeypatch.setattr(constituents, "_http_get", fetch)
    h = resolve(name, today=date(2026, 11, 23))
    assert h.etf == "MID" and len(h.tickers) == 400 and not h.is_stale
    assert "BRK-B" in h.tickers and h.names["BRK-B"] == "Dot Class"
    assert urls == ["https://en.wikipedia.org/wiki/List_of_S%26P_400_companies"]
    assert h.membership["BRK-B"] == ["mid"]
    assert "2026-11-23" == h.as_of


@pytest.mark.parametrize("n", [300, 450])
def test_sp400_count_out_of_range_fails_loudly(monkeypatch, n):
    monkeypatch.setattr(constituents, "_http_get", lambda *_: _wiki_html(n))
    with pytest.raises(ValueError, match="could not resolve MID"):
        resolve("mid")


def test_sp400_has_no_silent_fallback_even_with_allow_stale(monkeypatch):
    def down(*_):
        raise OSError("down")
    monkeypatch.setattr(constituents, "_http_get", down)
    with pytest.raises(ValueError, match="No static fallback"):
        resolve("sp400", allow_stale=True)
