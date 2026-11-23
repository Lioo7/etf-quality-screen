"""The CLI's minimal ``.env`` loader (#74): set-if-absent, silent, never prints values."""

import os

from etf_screen.cli import _load_dotenv

SECRET = "Example Research research@example.com"


def test_loads_variables_from_the_file(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.delenv("ETF_SCREEN_TEST_VAR", raising=False)
    env = tmp_path / ".env"
    env.write_text(f"# comment\n\nexport SEC_USER_AGENT=\"{SECRET}\"\n"
                   "ETF_SCREEN_TEST_VAR = 'a=b'\nnot a pair\n")
    _load_dotenv(env)
    assert os.environ["SEC_USER_AGENT"] == SECRET
    assert os.environ["ETF_SCREEN_TEST_VAR"] == "a=b"
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""  # never printed


def test_never_overrides_a_variable_already_set(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "from the shell")
    env = tmp_path / ".env"
    env.write_text(f"SEC_USER_AGENT={SECRET}\n")
    _load_dotenv(env)
    assert os.environ["SEC_USER_AGENT"] == "from the shell"


def test_missing_file_is_silent(tmp_path, capsys):
    before = dict(os.environ)
    _load_dotenv(tmp_path / "absent.env")
    assert dict(os.environ) == before
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_unreadable_file_is_silent(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_bytes(b"\xff\xfe\x00bad")
    _load_dotenv(env)  # no raise
    assert capsys.readouterr().err == ""
