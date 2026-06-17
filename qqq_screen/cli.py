"""Command-line entrypoint: ``python -m qqq_screen.cli``.

Resolves a universe (or an explicit ticker list), fetches fundamentals through
the chosen provider, runs the screen, and prints a provenance header, Phase-1
elimination counts, a ranked survivor table, per-name reasoning, and a
"could not source" section. Data is never fabricated: unsourced names are
reported, not guessed.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import date
from math import isnan

from tabulate import tabulate

from . import __version__
from .cache import DiskCache
from .constituents import resolve
from .providers import PROVIDERS, DataProvider, DataUnavailable
from .screen import Result, evaluate, rank


@dataclass
class Skipped:
    ticker: str
    reason: str


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m qqq_screen.cli",
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
    return p


def _resolve_universe(args) -> tuple[list[str], str, str, bool]:
    """Return (tickers, source, as_of, is_stale) for the requested universe."""
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        return tickers, "explicit --tickers", date.today().isoformat(), False
    h = resolve(args.universe)
    return h.tickers, h.source, h.as_of, h.is_stale


def _make_provider(args) -> DataProvider:
    """Build the chosen provider, exiting cleanly if it can't be constructed."""
    cache = None if args.no_cache else DiskCache(
        args.provider, enabled=True, refresh=args.refresh)
    try:
        return PROVIDERS[args.provider](cache=cache)
    except DataUnavailable as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)


def run(args) -> int:
    tickers, source, as_of, stale = _resolve_universe(args)
    if args.limit is not None:
        tickers = tickers[: args.limit]
    provider = _make_provider(args)

    results: list[Result] = []
    skipped: list[Skipped] = []
    for i, ticker in enumerate(tickers):
        try:
            company = provider.fetch(ticker)
            results.append(evaluate(company))
        except DataUnavailable as exc:
            skipped.append(Skipped(ticker, str(exc)))
        except Exception as exc:  # unexpected provider/parse error -> report, skip
            skipped.append(Skipped(ticker, f"unexpected error: {exc}"))
        if args.throttle and i < len(tickers) - 1:
            time.sleep(args.throttle)

    _report(args, source, as_of, stale, tickers, results, skipped)
    return 0


def _report(args, source, as_of, stale, tickers, results, skipped) -> None:
    survivors = rank(results)
    rejected = [r for r in results if not r.passed]

    # --- provenance header ---
    print("=" * 78)
    print(f"qqq-quality-screen v{__version__}  |  run {date.today().isoformat()}")
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
    print("  rejected by filter (names may fail more than one):")
    print(f"    Rule of 40    : {sum(1 for r in rejected if not r.pass_rule40)}")
    print(f"    P/S guardrail : {sum(1 for r in rejected if not r.pass_ps)}")
    print(f"    SBC > 15%     : {sum(1 for r in rejected if not r.pass_sbc)}")
    print(f"    PEG (Track A) : {sum(1 for r in rejected if r.peg is not None and not r.pass_peg)}")

    # --- ranked survivor table ---
    print(f"\nShortlist ({len(survivors)} names) — ranked by Rule of 40\n")
    if survivors:
        rows = [[
            i + 1, r.ticker, r.track, _f(r.growth), _f(r.adj_margin),
            _f(r.rule40), _f(r.p_s, 1), _f(r.dilution), _f(r.sbc_pct),
            _peg(r.peg),
        ] for i, r in enumerate(survivors)]
        headers = ["#", "Ticker", "Track", "Growth%", "AdjFCF%", "Rule40",
                   "P/S", "Dil%", "SBC%", "PEG"]
        print(tabulate(rows, headers=headers, floatfmt=".1f"))
    else:
        print("  (empty — a strict screen producing no survivors is a valid result)")

    # --- per-name reasoning ---
    if survivors:
        print("\nWhy survivors passed:")
        for r in survivors:
            conf = _conf(r)
            print(f"  {r.ticker}: {r.track} (Rule40 {_f(r.rule40)}, "
                  f"P/S {_f(r.p_s, 1)} vs guardrail {_f(0.5 * r.growth, 1)}, "
                  f"SBC {_f(r.sbc_pct)}%{', PEG ' + _peg(r.peg) if r.peg else ''}){conf}")

    if rejected:
        print("\nWhy names were rejected:")
        for r in rejected:
            print(f"  {r.ticker}: {'; '.join(r.reasons)}")

    # --- could not source ---
    if skipped:
        print(f"\nCould not source ({len(skipped)}) — skipped, never fabricated:")
        for s in skipped:
            print(f"  {s.ticker}: {s.reason}")


# --- small formatting helpers ---
def _f(x: float, nd: int = 1) -> str:
    return "N/A" if x is None or isnan(x) else f"{x:.{nd}f}"


def _peg(peg) -> str:
    return "N/A" if peg is None else f"{peg:.2f}"


def _conf(r: Result) -> str:
    lc = r.company.low_confidence
    return f"  [low-confidence: {', '.join(lc)}]" if lc else ""


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
