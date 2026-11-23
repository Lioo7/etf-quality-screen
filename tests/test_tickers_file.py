"""Tests for the curated ticker-list loader (Feature 7, v0.2).

`--tickers-file` reads one ticker per line with '#' comments — the plain-text
universe format for thesis-driven lists (see universes/). Empty files error
loudly, duplicates are dropped with a note, junk lines raise.
"""

import pytest

from etf_screen.constituents import tickers_from_file


def _write(tmp_path, text):
    p = tmp_path / "list.txt"
    p.write_text(text)
    return p


def test_basic_list_with_comments_and_blanks(tmp_path):
    p = _write(tmp_path, """
# Header comment explaining the thesis.

CHKP    # trailing comment
cybr
NICE
""")
    assert tickers_from_file(p) == ["CHKP", "CYBR", "NICE"]  # uppercased, in order


def test_duplicates_dropped_with_note(tmp_path, capsys):
    p = _write(tmp_path, "WIX\nMNDY\nwix\n")
    assert tickers_from_file(p) == ["WIX", "MNDY"]
    assert "duplicate" in capsys.readouterr().err


def test_empty_file_is_a_loud_error(tmp_path):
    p = _write(tmp_path, "# only comments\n\n")
    with pytest.raises(ValueError, match="no tickers"):
        tickers_from_file(p)


def test_missing_file_is_a_loud_error(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        tickers_from_file(tmp_path / "nope.txt")


def test_junk_line_raises_with_location(tmp_path):
    p = _write(tmp_path, "CHKP\nnot a ticker line\n")
    with pytest.raises(ValueError, match="list.txt:2"):
        tickers_from_file(p)


def test_dotted_class_shares_normalized(tmp_path):
    p = _write(tmp_path, "BRK.B\n")
    assert tickers_from_file(p) == ["BRK-B"]  # Yahoo/FMP symbol form


def test_bundled_universe_files_load():
    # The two committed starter lists must always parse.
    for name in ("israel-tech-adr.txt", "japan-automation-adr.txt"):
        tickers = tickers_from_file(f"universes/{name}")
        assert len(tickers) >= 5
        assert all(t == t.upper() for t in tickers)


def test_cli_precedence_tickers_beats_tickers_file(tmp_path):
    from etf_screen.cli import _resolve_universe, build_parser
    p = _write(tmp_path, "CHKP\n")
    args = build_parser().parse_args(
        ["--tickers", "MSFT", "--tickers-file", str(p)])
    tickers, source, *_ = _resolve_universe(args)
    assert tickers == ["MSFT"] and "--tickers" in source


def test_cli_tickers_file_resolution(tmp_path):
    from etf_screen.cli import _resolve_universe, build_parser
    p = _write(tmp_path, "CHKP\nCYBR\n")
    args = build_parser().parse_args(["--tickers-file", str(p)])
    tickers, source, _, stale, _, label, _ = _resolve_universe(args)
    assert tickers == ["CHKP", "CYBR"]
    assert "list.txt" in source and not stale
    assert label == "list"  # file stem names the run/export
