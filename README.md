# qqq-quality-screen

A small, well-tested CLI that screens the current Invesco QQQ (Nasdaq-100)
constituents — or any ETF/ticker list — through a four-filter, two-track
quality-growth methodology, pulling live fundamentals from a **pluggable** data
provider.

Screening logic and data sourcing are kept strictly separate:

| Module | Responsibility | Network? |
| --- | --- | --- |
| `qqq_screen/screen.py` | `Company`, `evaluate()`, ranking | No |
| `qqq_screen/providers.py` | `DataProvider` ABC + yfinance / FMP / Mock | Yes |
| `qqq_screen/constituents.py` | resolve ETF holdings → tickers | Yes |
| `qqq_screen/cache.py` | per-day disk cache | No |
| `qqq_screen/cli.py` | argparse entrypoint + reporting | — |

## Methodology

All metrics are computed from **raw statement lines** — never pre-packaged
ratios.

- **Revenue growth** = TTM revenue vs. prior TTM, %.
- **FCF** = operating cash flow − capex. **Adjusted FCF** = FCF − stock-based
  comp (SBC is treated as a real economic cost). Margins use adjusted FCF.
- **P/S** = market cap / TTM revenue. **SBC %** = SBC / revenue.
- **Net dilution** = YoY change in diluted shares, %.
- **PEG** = forward P/E ÷ forward EPS growth %. If GAAP-unprofitable (no forward
  P/E), PEG is **N/A** — never coerced to a number.

**Four filters:** ① Rule of 40 (`growth% + adj FCF margin% ≥ 40`) · ② PEG ≤ 2.0
(Track A only) · ③ P/S guardrail (`P/S ≤ 0.5 × growth%`) · ④ SBC ≤ 15% of revenue.

**Two tracks** (a name passes if it clears *either*):

- **Track A — profitable compounders:** must pass all four filters, including PEG.
- **Track B — hyper-growth, PEG-exempt:** Rule of 40 + P/S guardrail + SBC,
  **plus** a quality gate (`adjusted FCF > 0` AND `net dilution < revenue growth`).
  Reachable **only** when PEG is genuinely N/A.

> ⚠️ **Critical rule:** Track B is for names where PEG is genuinely N/A
> (GAAP-unprofitable). A *profitable* name that fails PEG is **rejected** — it must
> not slip into Track B. The `PriceyButProfitable` acceptance test enforces this.

Survivors are ranked by Rule of 40 (desc), tie-broken by PEG (asc), then P/S
headroom.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Requires Python 3.11+. The default provider is **yfinance**, which needs **no API
key**.

## Usage

```bash
# Default: screen the live Nasdaq-100 via yfinance (no key needed)
python -m qqq_screen.cli --universe qqq

# Cheap validation against an explicit ticker list
python -m qqq_screen.cli --provider yfinance --tickers MSFT,GOOGL,CRWD

# Offline / deterministic demo
python -m qqq_screen.cli --provider mock --tickers MSFT,GOOGL,CRWD

# Screen the S&P 500 instead, throttling requests
python -m qqq_screen.cli --universe spy --throttle 1 --limit 50
```

Flags: `--provider {yfinance,fmp,mock}` · `--universe <etf>` · `--tickers a,b,c`
· `--limit N` · `--refresh` (ignore cache) · `--no-cache` · `--throttle SECONDS`.

### Example output (mock)

```
==============================================================================
qqq-quality-screen v0.1.0  |  run 2026-06-17
Provider : mock
Universe : explicit (3 tickers)
Source   : explicit --tickers
As-of    : 2026-06-17
==============================================================================

Phase-1 elimination counts
  screened        : 3
  evaluated       : 3
  could not source: 0
  passed          : 1
  rejected        : 2
  ...

Shortlist (1 names) — ranked by Rule of 40

  #  Ticker    Track      Growth%    AdjFCF%    Rule40    P/S    Dil%    SBC%  PEG
---  --------  -------  ---------  ---------  --------  -----  ------  ------  -----
  1  CRWD      Track B       45.8       13.7      59.5   22.9     4.3    14.3  N/A

Why survivors passed:
  CRWD: Track B (Rule40 59.5, P/S 22.9 vs guardrail 22.9, SBC 14.3%)
```

## Data providers

### yfinance (default, free, no key)

Backed by Yahoo Finance. **Known limitations — handled honestly, never papered
over:**

- Yahoo usually exposes only ~4–6 quarters, so the prior-TTM window is often
  unavailable. The provider falls back to **annual year-over-year** for revenue
  growth and prior shares, flagged `low-confidence` in the output. (Cash-flow
  TTM is still summed from quarters when available, so margins mix annual revenue
  with TTM cash flow — close, but approximate.)
- **SBC is frequently missing.** When it is, the name is **skipped and reported**
  — never silently assumed to be 0.
- Forward EPS growth is a rough Yahoo proxy, always flagged `low-confidence`.

### FMP (optional upgrade — requires a paid tier)

`FMPProvider` implements the FMP v3 REST endpoints (quarterly statements, quote,
analyst estimates). It is **not required** for any default workflow. To use it:

```bash
export FMP_API_KEY=your_key_here     # never commit this
python -m qqq_screen.cli --provider fmp --tickers MSFT,GOOGL,CRWD
```

FMP fundamentals require a **Starter/Premium** plan. On a free key the statement
endpoints return `403`; the provider fails with a clear message naming the
endpoint and the tier needed — it does not silently fill gaps.

### Mock

Canned data for offline runs and the test suite.

## Constituents

`constituents.resolve(etf)` resolves holdings **key-lessly**, keyed by ETF ticker:

1. **Wikipedia index table** (primary) — e.g. the Nasdaq-100 "Current components"
   table for `QQQ`, the S&P 500 list for `SPY`. Records the URL as `source` and
   the fetch date as `as_of`.
2. **Bundled static list** (fallback) — a dated snapshot, emitted with a loud
   `STALE / as-of` warning. The index rebalances, so provenance is always shown.

The issuer's official holdings file (Invesco / SSGA) is the most authoritative
source, but its download endpoints are brittle and undocumented, so it is
deliberately not a hard dependency.

## Integrity

The screen **never invents numbers.** Missing or blocked data → the name is
skipped and listed under "could not source." A short (or empty) shortlist is an
acceptable, expected result for a strict screen — thresholds are never loosened
to populate the table.

## Development

```bash
pytest -q          # 17 tests: acceptance fixtures + edge cases + data layer
flake8 qqq_screen tests
```

The acceptance fixtures in `tests/test_screen.py` lock in the validated
behavior — especially the `PriceyButProfitable` rejection that keeps the PEG
filter from being toothless.
