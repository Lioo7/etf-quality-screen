"""Command-line entrypoint: ``python -m etf_screen.cli``.

Resolves a universe (or an explicit ticker list), fetches fundamentals through
the chosen provider (with optional manual overrides), runs the screen, and prints
a provenance header, Phase-1 elimination counts, a ranked survivor table,
per-name reasoning, an "SBC assumed 0" review section, and a "could not source"
section. Optionally exports the full result set to CSV/Markdown. Data is never
fabricated: unsourced names are reported, not guessed.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import date
from math import isnan
from pathlib import Path

from tabulate import tabulate

from . import __version__, export as export_mod
from .cache import DiskCache
from .constituents import resolve
from .overrides import apply_override, company_from_override, load_overrides
from .providers import PROVIDERS, DataProvider, DataUnavailable
from .screen import Result, evaluate, rank


@dataclass
class Skipped:
    ticker: str
    reason: str


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m etf_screen.cli",
        description="Quality-growth screener for ETF constituents.",
    )
    p.add_argument("--provider", choices=sorted(PROVIDERS), default="yfinance",
                   help="data source (default: yfinance, no API key needed)")
    p.add_argument("--universe", default="qqq",
                   help="ETF whose holdings to screen, e.g. qqq or spy (default: qqq)")
    p.add_argument("--tickers",
                   help="comma-separated tickers to screen instead of a universe")
    p.add_argument("--limit", type=int, default=None,
                   help="screen at most N tickers (useful for cheap validation)")
    p.add_argument("--refresh", action="store_true",
                   help="ignore cached data and refetch")
    p.add_argument("--no-cache", action="store_true",
                   help="disable the per-day disk cache entirely")
    p.add_argument("--throttle", type=float, default=0.0,
                   help="seconds to sleep between fetches (rate-limit courtesy)")
    p.add_argument("--overrides", default="overrides.json",
                   help="path to a git-ignored overrides.json (default: overrides.json)")
    p.add_argument("--export", choices=("csv", "md"), default=None,
                   help="also write the full result set to a file")
    p.add_argument("--out", default=None,
                   help="export path (default: etf_screen_<universe>_<date>.<ext>)")
    return p


def _resolve_universe(args) -> tuple[list[str], str, str, bool, dict[str, str]]:
    """Return (tickers, source, as_of, is_stale, names) for the universe."""
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        return tickers, "explicit --tickers", date.today().isoformat(), False, {}
    h = resolve(args.universe)
    return h.tickers, h.source, h.as_of, h.is_stale, h.names


def _make_provider(args) -> DataProvider:
    """Build the chosen provider, exiting cleanly if it can't be constructed."""
    cache = None if args.no_cache else DiskCache(
        args.provider, enabled=True, refresh=args.refresh)
    try:
        return PROVIDERS[args.provider](cache=cache)
    except DataUnavailable as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)


def _screen_one(provider, ticker, override) -> Result | Skipped:
    """Fetch + (optionally override) + evaluate one ticker, or report it skipped."""
    try:
        company = provider.fetch(ticker)
        if override:
            company = apply_override(company, override)
        return evaluate(company)
    except DataUnavailable as exc:
        if override:  # provider had nothing, but the user supplied verified data
            rescued = company_from_override(ticker, override)
            if rescued is not None:
                return evaluate(rescued)
        return Skipped(ticker, str(exc))
    except Exception as exc:  # unexpected provider/parse error -> report, skip
        return Skipped(ticker, f"unexpected error: {exc}")


def run(args) -> int:
    tickers, source, as_of, stale, names = _resolve_universe(args)
    if args.limit is not None:
        tickers = tickers[: args.limit]
    provider = _make_provider(args)
    overrides = load_overrides(args.overrides)

    results: list[Result] = []
    skipped: list[Skipped] = []
    for i, ticker in enumerate(tickers):
        outcome = _screen_one(provider, ticker, overrides.get(ticker))
        (skipped if isinstance(outcome, Skipped) else results).append(outcome)
        if args.throttle and i < len(tickers) - 1:
            time.sleep(args.throttle)

    _report(args, source, as_of, stale, tickers, results, skipped, names)

    if args.export:
        _do_export(args, source, as_of, results, skipped, names)
    return 0


def _do_export(args, source, as_of, results, skipped, names) -> None:
    universe = args.universe if not args.tickers else "tickers"
    run_date = date.today().isoformat()
    path = Path(args.out) if args.out else Path(
        export_mod.default_filename(universe, run_date, args.export))
    prov = export_mod.Provenance(args.provider, universe, source, as_of, run_date)
    rows = export_mod.build_rows(results, skipped, names)
    export_mod.export(path, args.export, prov, rows)
    print(f"\nExported {len(rows)} rows to {path}")


def _report(args, source, as_of, stale, tickers, results, skipped, names) -> None:
    survivors = rank(results)
    rejected = [r for r in results if not r.passed]
    sbc_assumed = [r for r in results if r.sbc_assumed_zero]

    # --- provenance header ---
    print("=" * 78)
    print(f"etf-quality-screen v{__version__}  |  run {date.today().isoformat()}")
    print(f"Provider : {args.provider}")
    print(f"Universe : {args.universe if not args.tickers else 'explicit'} "
          f"({len(tickers)} tickers)")
    print(f"Source   : {source}{'  [STALE]' if stale else ''}")
    print(f"As-of    : {as_of}")
    print("=" * 78)

    # --- Phase-1 elimination counts ---
    print("\nPhase-1 elimination counts")
    print(f"  screened        : {len(tickers)}")
    print(f"  evaluated       : {len(results)}")
    print(f"  could not source: {len(skipped)}")
    print(f"  passed          : {len(survivors)}")
    print(f"  rejected        : {len(rejected)}")
    print(f"  SBC assumed 0   : {len(sbc_assumed)} (review below)")
    print("  rejected by filter (names may fail more than one):")
    print(f"    Rule of 40    : {sum(1 for r in rejected if not r.pass_rule40)}")
    print(f"    P/S guardrail : {sum(1 for r in rejected if not r.pass_ps)}")
    print(f"    SBC > 15%     : {sum(1 for r in rejected if not r.pass_sbc)}")
    print(f"    PEG (Track A) : {sum(1 for r in rejected if r.peg is not None and not r.pass_peg)}")

    # --- ranked survivor table ---
    print(f"\nShortlist ({len(survivors)} names) — ranked by Rule of 40\n")
    if survivors:
        rows = [[
            i + 1, r.ticker, _short(r.name), r.track, _f(r.growth), _f(r.adj_margin),
            _f(r.rule40), _f(r.p_s, 1), _f(r.dilution), _f(r.sbc_pct), _peg(r.peg),
        ] for i, r in enumerate(survivors)]
        headers = ["#", "Ticker", "Company", "Track", "Growth%", "AdjFCF%",
                   "Rule40", "P/S", "Dil%", "SBC%", "PEG"]
        print(tabulate(rows, headers=headers, floatfmt=".1f"))
    else:
        print("  (empty — a strict screen producing no survivors is a valid result)")

    # --- per-name reasoning ---
    if survivors:
        print("\nWhy survivors passed:")
        for r in survivors:
            print(f"  {r.ticker} ({_short(r.name)}): {r.track} "
                  f"(Rule40 {_f(r.rule40)}, P/S {_f(r.p_s, 1)} vs guardrail "
                  f"{_f(r.ps_guardrail, 1)}, SBC {_f(r.sbc_pct)}%"
                  f"{', PEG ' + _peg(r.peg) if r.peg else ''}){_flags(r)}")

    if rejected:
        print("\nWhy names were rejected:")
        for r in rejected:
            print(f"  {r.ticker} ({_short(r.name)}): {'; '.join(r.reasons)}{_flags(r)}")

    # --- SBC assumed 0 review section ---
    if sbc_assumed:
        print(f"\nSBC assumed 0 ({len(sbc_assumed)}) — source reported no SBC line; "
              f"VERIFY these before trusting their verdict:")
        for r in sbc_assumed:
            verdict = r.track if r.passed else "rejected"
            print(f"  {r.ticker} ({_short(r.name)}): {verdict} "
                  f"(Rule40 {_f(r.rule40)}, SBC% shown as {_f(r.sbc_pct)})")

    # --- could not source ---
    if skipped:
        print(f"\nCould not source ({len(skipped)}) — skipped, never fabricated:")
        for s in skipped:
            print(f"  {s.ticker} ({_short(names.get(s.ticker, s.ticker))}): {s.reason}")


# --- small formatting helpers ---
def _f(x: float, nd: int = 1) -> str:
    return "N/A" if x is None or isnan(x) else f"{x:.{nd}f}"


def _peg(peg) -> str:
    return "N/A" if peg is None else f"{peg:.2f}"


def _short(s: str, n: int = 24) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _flags(r: Result) -> str:
    """Render basis / override / low-confidence annotations for a result line."""
    tags = []
    if r.manual_override:
        tags.append("manual override: " + ", ".join(r.company.overridden_fields))
    if r.basis and r.basis != "TTM":
        tags.append(f"basis: {r.basis}")
    if r.company.low_confidence:
        tags.append("low-confidence: " + ", ".join(r.company.low_confidence))
    return f"  [{' | '.join(tags)}]" if tags else ""


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
