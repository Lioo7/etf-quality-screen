# PEG growth basis (issue #5, 2026-11-20)

Question: which growth definition puts the PEG numerator (forward P/E) and denominator
(growth) on one earnings basis and horizon? Data: Yahoo probe of the 513 names in the
Nov-19 cache (taken Nov 20) plus the cached Nov-19 `Company` objects re-run through
`evaluate()`. The cached baseline reproduces every Nov-19 QQQ/SPY verdict (0 mismatches),
so a verdict change below is caused by the PEG definition alone.

## 1. Current PEG vs same-basis candidates (20 scoped names)

| Ticker | FY elapsed | Current | FMP-style | **C1** | C2 | C3 | FY1/FY0 growth |
|---|---|---|---|---|---|---|---|
| INTU | 31% | 0.22 | 0.36 | **0.86** | N/A | 2.41 | 13% |
| NVDA | 82% | 0.11 | 4.06 | **0.12** | 0.14 | 0.17 | 72% |
| SNDK | 38% | 0.02 | 0.04 | **0.09** | 0.01 | 0.03 | 27% |
| STX | 38% | 0.03 | 0.11 | **0.12** | 0.08 | 0.12 | 63% |
| LRCX | 40% | 0.19 | 0.45 | **0.57** | 0.30 | 0.44 | 27% |
| ANET | 89% | 0.47 | 2.31 | **0.76** | 0.85 | 0.94 | 38% |
| MSFT | 39% | 0.78 | 5.60 | **0.99** | 1.58 | 1.36 | 21% |
| META | 89% | 0.75 | N/A | **1.03** | 0.80 | 0.94 | 17% |
| AAPL | 15% | 1.86 | 7.42 | **2.66** | 7.42 | 4.30 | 12% |
| V | 14% | 0.61 | 1.23 | **1.70** | 2.04 | 1.99 | 14% |
| MA | 89% | 1.12 | 4.09 | **1.77** | 1.77 | 1.90 | 15% |
| COST | 22% | 2.15 | 4.88 | **4.40** | 4.88 | 4.87 | 10% |
| ADBE | 98% | 0.16 | 0.25 | **0.70** | 0.55 | 0.65 | 12% |
| ASML | 89% | 0.37 | 14.83 | **N/A (EUR)** | 0.42 | 0.55 | 36% |
| SHOP | 89% | 1.05 | 4.10 | **1.85** | 2.08 | 2.22 | 29% |
| PLTR | 89% | 0.75 | 4.46 | **1.20** | 0.70 | 1.05 | 49% |
| GILD | 89% | N/A | N/A | **N/A** | N/A | N/A | N/A |
| XOM | 89% | 0.45 | 0.28 | **N/A** | 0.12 | 0.35 | -9% |
| JPM | 89% | 1.72 | 43.54 | **1.80** | 0.63 | 0.97 | 7% |
| KO | 89% | 6.49 | N/A | **5.00** | 2.49 | 3.40 | 5% |

Definitions (g = growth in %; EPS are analyst consensus averages from `earnings_estimate`):
- **Current**: `forwardEps / trailingEps - 1` (consensus FY1 vs GAAP TTM), P/E = `forwardPE`.
- **FMP-style**: FMP's formula emulated on Yahoo data (FY0 consensus vs GAAP TTM, P/E on FY0). Formula read from code; not run live (no FMP plan).
- **C1**: price / EPS(FY1) over EPS(FY1)/EPS(FY0) - 1. One table, one basis, one horizon.
- **C2**: P/E on FY0 over FY(-1) actual -> FY0 consensus. **C3**: P/E on FY0 over the two-year CAGR FY(-1) -> FY1.

## 2. Distribution and verdicts (Nov-19 cache, estimates of Nov 20)

| Universe | Median PEG: current -> C1 | PEG < 0.5: current -> C1 | Verdicts flipped by C1 |
|---|---|---|---|
| QQQ (97 of 100 cached) | 0.50 -> 1.43 | 40 -> 16 | 1: ASML PASS -> NEEDS_DATA |
| SPY (473 of 502 cached) | 0.52 -> 1.70 | 198 -> 49 | 7: six INSUFFICIENT_HISTORY -> NEEDS_DATA (MPC, VLO, CVX, OXY, COP, XOM), PSX REJECTED -> NEEDS_DATA |

SPY has 166 names with C1 PEG > 2. INTU, SHOP, LRCX, ANET, NVDA and the other large PEG
moves keep their verdict: they stay under 2 on C1 (SHOP 1.05 -> 1.85 is closest). C2 and C3
flip more (INTU to NEEDS_DATA/WATCHLIST, SHOP to WATCHLIST), because FY(-1) actuals for a
name whose FY0 estimate dips give N/A or inflated growth.

## 3. Missing-field rates (513 names probed)

| Field | Missing / unusable |
|---|---|
| `earnings_estimate` table, FY0 and FY1 avg | 2 (0.4%) |
| FY(-1) `yearAgoEps` | 9 (1.8%) |
| `forwardPE`, `info` fetch error | 0 |
| Trailing EPS <= 0 (breaks current formula) | 40 (7.8%) |
| FY0 consensus EPS <= 0 / FY1 <= 0 | 22 (4.3%) / 15 (2.9%) |
| FY1 analysts < 3 | 12 (2.3%) |
| `growth_estimates` LTG stock value | 513 (100%) |
| Table currency not USD | ASML, CCEP, FER |

LTG is empty for every name (only the index trend is returned): not usable.
Fiscal-year timing: 423 of 512 names have FY0 more than 80% elapsed (December year-ends,
Nov 20), 31 less than 25% (e.g. INTU 31%, AAPL 15%, V 14%). A FY that just started is
mostly forecast, but both C1 inputs are then forecast too, so the basis stays consistent.

## 4. Findings

1. **Why the current PEG is too low.** It divides a forward-year adjusted EPS by GAAP trailing EPS, which includes one-off charges and SBC and is often depressed. SNDK 131%, STX 243%, INTU 52% "growth" against 13-27% FY1/FY0 consensus.
2. **`forwardPE` is not always on the table's basis.** `forwardEps` differs from the `+1y` table average by more than 2% for 43 of 510 names (TPL, PANW, DASH, TTWO, RKLB...). Taking P/E from the same table fixes this. Using `forwardPE` instead flips the same SPY names and no QQQ name (the ASML flip comes from the currency guard in finding 3), so this is a correctness choice, not a verdict one.
3. **Currency.** ASML, CCEP and FER tables are in EUR while the price is USD. Ratios inside one table are currency-neutral, but price / EPS is not. Convert nothing; mark the PEG untestable.
4. **FMP's formula is a third, different basis.** `_nearest_future_eps` returns the nearest dated annual estimate (usually FY0), then growth is vs GAAP `quote.eps`. With FY0 ~89% elapsed, growth is near zero and PEG explodes (MSFT 5.6, JPM 43.5, ASML 14.8), unlike Yahoo's too-low PEG. The same-basis formula must be implemented in both providers.
5. **Negative growth is not missing data.** All seven SPY flips to NEEDS_DATA come from negative FY1 consensus (-4% to -28%, refiners/energy), not missing fields. Today's code returns PEG N/A for growth <= 0 and the NEEDS_DATA hint asks for data that cannot help. See open question.

## 5. Recommendation

**PEG = (price / EPS_FY1) / (100 x (EPS_FY1 / EPS_FY0 - 1))**, EPS_FY0 and EPS_FY1 the analyst
consensus averages for the current and next fiscal year, price from `info`.
- Untestable (existing NEEDS_DATA path, never 0, never a guess) when: either table value is absent, EPS_FY0 <= 0 or EPS_FY1 <= 0, the table currency is not the price currency, or price is absent.
- Growth <= 0 keeps today's behavior (PEG N/A) until the open question below is decided.
- `PEG_MAX` stays 2; routing and the PriceyButProfitable guard are untouched.
- FMP: use the same two figures from `analyst-estimates` (current-FY and next-FY rows), not GAAP TTM EPS.

Open question for #6 (spec decision, not made here): a profitable name whose FY1 EPS is
below FY0 is routed to NEEDS_DATA (PSX: previously REJECTED at PEG 2.64). Options: keep, or
treat negative consensus growth as a failed PEG (REJECTED) while keeping NEEDS_DATA only for
missing data. The second matches "a failed test rejects, an unadministrable one asks for data"
but touches routing, which #6 lists as out of scope.

## 6. Caveats

- Estimates were pulled Nov 20, one day after the cached Nov-19 financials; revisions in that day are not separated.
- 29 SPY and 3 QQQ names are not in the cache and are excluded from the counts.
- FMP numbers are emulated from Yahoo consensus; its live data was not available.
- Scratch scripts (`scratch/`, git-ignored): `peg_probe.py`, `candidates.py`, `flips.py`, `stats.py`.
