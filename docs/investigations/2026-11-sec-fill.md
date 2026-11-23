# Filling Yahoo's missing quarters from SEC filings (issue #74, 2026-11-23)

Question: when D23 finds a quarter missing in Yahoo's TTM window, can that quarter's
filed 10-Q / 10-K value replace the stale annual fallback, and what changes?

**Data.** A fresh raw Yahoo snapshot of QQQ (Nasdaq API) and SPY (Wikipedia), pulled
2026-11-23: 518 names, 517 complete (CCEP: no Yahoo fundamentals), 513 USD filers.
The 2026-11-22 snapshot behind #71 was not kept, so this is a new one.

**Where the snapshot lives.** It is stored durably at `data/snapshots/raw/2026-11-23/`
(git-ignored; never delete it). That folder holds:
- one pickle per name and `fx.pkl`;
- the trimmed SEC companyfacts used here (`sec/`) and the filing instances read for the spot-check (`sec/filings/`);
- the evidence output (`evidence.json`, `evidence.txt`, `spot.json`, `spot.txt`);
- copies of the scripts.

Every figure below replays offline from that folder, with the run date fixed at 2026-11-23.

## 1. D23 gaps today

On this snapshot, 68 names carry 250 window cells that Yahoo lacks. By date, they are:

| Date | Cells |
|---|---|
| 2026-06-30 | 196 |
| 2026-09-30 | 16 |
| 2025-12-31 | 14 |
| 2025-07-31 | 10 |
| 2026-03-31 | 6 |
| 2025-06-30 | 4 |

By line, they are:

| Line | Cells |
|---|---|
| capex | 47 |
| OCF | 40 |
| SBC | 40 |
| revenue | 37 |
| net income | 36 |
| EBIT | 31 |
| diluted shares | 19 |

The calendar 2026-06-30 hole from #71 is still there, usually as a NaN income column plus an absent cash-flow column (APH's shape).

With the fill off (today's `main`), **63 names** sit on the annual basis because of a dated gap (53 on 2026-11-22). By the first gap reported, they split as follows:
- 33 revenue;
- 17 year-ago diluted shares;
- 13 another line.

None of the 63 is a non-USD filer.

## 2. Measurement: how wrong is each fallback?

The test compares each fallback with SEC's true TTM on the same window dates. SEC's TTM is the four SEC quarter values, concept-checked as the fill does. The error is the median absolute value, over names where SEC's TTM is computable. FCF errors where SEC's FCF is close to 0 are excluded (BA: −11,888%).

| First gap | Names | Annual fallback, revenue | Annual fallback, FCF | Old skip-sum, revenue | Old skip-sum, FCF |
|---|---|---|---|---|---|
| revenue | 33 | 4.8% (max 30.1%, APH) | 10.0% | 2.4% (max 7.8%) | 8.7% |
| year-ago shares | 17 | 3.4% (max 28.7%, NVDA) | 16.2% | 0.0% | 0.0% |
| other line | 13 | 5.6% | 5.9% | 0.0% | 0.0% |

- **The annual fallback is the larger error.** It is a whole, exact fiscal year, but up to 9 months stale. Fast growers are understated most: APH −30%, NVDA −29%, TER −29%, KEYS −18%.
- **The old skip-sum was closer on revenue**, by accident: it swapped in the year-ago quarter. D23 still holds, because that sum mixed two years into one FCF margin.
- For the share-gap names, the skip-sum revenue was exact because their revenue window was complete. The fallback still cost them up to 29% of revenue.

## 3. Same-snapshot diff: fill off vs on

The fill runs over all 518 names, once off and once on (`_sec_fill` then `_extract_yf`, CLI defaults). It keeps 67 filled cells in 25 names.

- **25 of the 63 return to TTM.** They are 10 revenue-gap names, 14 share-gap names and IQV (a fiscal-Q4 OCF hole).
- **One verdict flip: APH, REJECTED → PASS (Track A).** On the fill, APH's revenue ($33.047B) and FCF ($5.121B) equal SEC's true TTM exactly. Net debt / FCF drops from 3.13 (FY2025 FCF) to 2.67, under the 3.0 cap; #71's SEC figure was 2.68.
- **Routing changes: none.** No name's net income changes sign, and the fill never touches the forward P/E or the PEG inputs. Track A ↔ Track B routing therefore can't move. The only track label that changes is APH's, and only because it now passes.
- No name skips, and no fill failed or was off.

| Name | First gap | Cells filled | Growth % | Net debt / FCF | Status |
|---|---|---|---|---|---|
| ABT | revenue 2026-06-30 | 6 | 5.7 → 10.1 | 3.67 → 4.01 | REJECTED |
| AMAT | shares 2025-07-31 | 1 | 4.4 → 14.6 | 0.05 → 0.05 | REJECTED |
| AMZN | revenue 2026-06-30 | 6 | 12.4 → 12.2 | 28.09 → n/a (FCF ≤ 0) | REJECTED |
| **APH** | revenue 2026-06-30 | 5 | 51.7 → 58.4 | **3.13 → 2.67** | **REJECTED → PASS** |
| AWK | revenue 2025-12-31 (Q4) | 1 | 9.7 → 6.9 | n/a | REJECTED |
| BA | revenue 2026-06-30 | 5 | 34.5 → 19.2 | n/a → 5203 | REJECTED |
| CHD | revenue 2026-06-30 | 6 | 1.6 → 1.3 | 1.61 → 1.66 | REJECTED |
| COR | shares 2025-06-30 | 1 | 9.3 → 7.2 | 2.78 → 2.19 | REJECTED |
| DVA | revenue 2026-06-30 | 5 | 6.5 → 5.5 | 9.84 → 9.97 | REJECTED |
| ETN | revenue 2026-06-30 | 4 | 10.3 → 19.2 | 5.74 → 5.37 | REJECTED |
| EW | revenue 2026-06-30 | 5 | 11.5 → 11.0 | 0 → 0 | REJECTED |
| HD | shares 2025-07-31 | 1 | 3.2 → 4.5 | 4.78 → 4.01 | REJECTED |
| IQV | OCF 2025-12-31 (Q4) | 1 | 5.9 → 6.9 | 6.86 → 6.77 | REJECTED |
| KEYS | shares 2025-07-31 | 1 | 8.0 → 22.2 | 0.11 → 0.10 | REJECTED |
| LOW | shares 2025-07-31 | 1 | 3.1 → 5.7 | 5.08 → 5.55 | REJECTED |
| MDLZ | revenue 2026-06-30 | 5 | 5.8 → 4.0 | 6.40 → 5.66 | REJECTED |
| NVDA | shares 2025-07-31 | 1 | 65.5 → 85.7 | 0.16 → 0.13 | REJECTED |
| PTC | shares 2025-06-30 | 1 | 19.2 → 6.2 | 1.47 → 1.34 | REJECTED |
| RJF | shares 2025-06-30 | 1 | 9.5 → 12.6 | 0 → 0 | REJECTED |
| ROST | shares 2025-07-31 | 1 | 7.7 → 10.5 | 0.20 → 0.16 | REJECTED |
| TER | shares 2025-06-30 | 1 | 13.1 → 58.5 | 0 → 0 | REJECTED |
| TGT | shares 2025-07-31 | 1 | −1.7 → 1.8 | 4.68 → 2.98 | REJECTED |
| TJX | shares 2025-07-31 | 1 | 7.1 → 6.3 | 1.69 → 1.41 | REJECTED |
| VRSK | shares 2026-09-30 | 1 | 6.6 → 6.0 | 3.42 → 3.70 | REJECTED |
| WMT | shares 2025-07-31 | 1 | 4.7 → 5.3 | 4.17 → 4.61 | REJECTED |

APH's five cells, all at 2026-06-30, are:
- revenue and net income, from direct 10-Q facts;
- OCF, capex and SBC, each as the 6M YTD minus the 3M Q1.

The EBIT cell failed the concept check (−6.0% at 2026-03-31) and stays a gap. EBIT is advisory, so ROIC is not computable and the basis doesn't change.

**Why 38 names stay annual.** These are the first basis-deciding gap SEC could not fill:

| Cause | Names | Who |
|---|---|---|
| Concept check (another scope than Yahoo's line) | 18 | Mostly capex at BSX, CHRW, FITB, HON, ILMN, FTV and EXR, plus utility, REIT and energy revenue |
| No filed value for the quarter | 10 | AAPL and ATO (newest share count not yet filed), utilities' net income, SBC on FANG, NEM, PPL and RSG |
| No SEC quarter to compare with | 7 | Banks and insurers' revenue and capex: BNY, GS, TFC, CFG, WRB |
| Derived quarter ≤ 0, rejected | 1 | LEN capex (9M − 6M = −$8M); HIG also has one |
| Not a cell gap | 2 | CINF and FLEX: every window cell fills, but Yahoo has no quarterly SBC row while the annual statement reports SBC (#58), so they are annual regardless and their fill notes are dropped |

## 4. Masking test: 20 gap-free names, one known cell blanked

For each name, one Yahoo cell is set to NaN and the fill runs. The names are the largest US filers with a complete window and an exact growth pair. APH, ABT, AMZN, ETN and FITB are not gap-free today: their real gaps are covered in §3. The masked cells are mixed: revenue 6, OCF 6, capex 4, net income 4. Four of them sit at a fiscal Q4.

| Name | Blanked cell | Where | Yahoo ($B) | SEC fill ($B) | Diff | Method |
|---|---|---|---|---|---|---|
| MSFT | revenue 2026-06-30 | fiscal Q4 (June FY) | 90.007 | 90.007 | 0.000% | 10-K FY − 9M YTD |
| GOOGL | OCF 2026-06-30 | Q2 | 39.069 | 39.069 | 0.000% | 10-Q YTD 6M − 3M |
| GOOG | capex 2026-03-31 | Q1 | −35.674 | −35.674 | 0.000% | 10-Q direct |
| SPCX | net income 2026-06-30 | Q2 | −0.541 | −0.541 | 0.000% | 10-Q direct |
| AVGO | revenue 2025-10-31 | fiscal Q4 | 18.015 | 18.015 | 0.000% | 10-K FY − 9M YTD |
| META | revenue 2026-06-30 | Q2 | 60.801 | 60.801 | 0.000% | 10-Q direct |
| TSLA | OCF 2026-06-30 | Q2 | 4.697 | 4.697 | 0.000% | 10-Q YTD 6M − 3M |
| LLY | capex 2026-03-31 | Q1 | −2.530 | not filled | — | capex concept outside the map |
| V | net income 2026-06-30 | Q3 (Sep FY) | 5.628 | 5.628 | 0.000% | 10-Q direct |
| AMD | OCF 2025-12-31 | fiscal Q4 | 2.600 | 2.600 | 0.000% | 10-K FY − 9M YTD |
| MU | revenue 2026-05-31 | Q3 | 41.456 | 41.456 | 0.000% | 10-Q direct |
| XOM | OCF 2026-06-30 | Q2 | 23.555 | not filled | — | no Q1-2026 OCF fact in companyfacts |
| JNJ | capex 2026-03-31 | Q1 | −1.049 | not filled | — | concept check −42.2% (another capex scope) |
| MA | net income 2026-06-30 | Q2 | 4.388 | 4.388 | 0.000% | 10-Q direct |
| ABBV | revenue 2025-12-31 | fiscal Q4 | 16.618 | 16.618 | 0.000% | 10-K direct 3-month fact |
| COST | revenue 2026-05-31 | Q3 | 70.527 | not filled | — | SEC period ends 2026-05-10, Yahoo labels 05-31 |
| KO | OCF 2026-06-30 | Q2 | 5.522 | 5.522 | 0.000% | 10-Q YTD 6M − 3M |
| CVX | capex 2026-03-31 | Q1 | −4.063 | −4.063 | 0.000% | 10-Q direct |
| PG | net income 2026-06-30 | fiscal Q4 (June FY) | 3.044 | 3.044 | 0.000% | 10-K FY − 9M YTD |
| CSCO | OCF 2026-07-31 | fiscal Q4 | 5.386 | 5.386 | 0.000% | 10-K FY − 9M YTD |

- **16 of 20 fill, every one equal to Yahoo's value** (0.000%, within $1M rounding). All three methods are covered: direct 8, YTD difference 3, FY − 9M 5.
- **Four don't fill, and each falls back to annual with its reason.**
  - Two are coverage gaps: LLY's capex tag and XOM's missing Q1 fact.
  - One is a correct concept-check rejection: JNJ's capex covers another scope.
  - One is a correct refusal: COST's 52/53-week quarters end 21 days before Yahoo's month-end labels, outside the ±10-day match.
- **APH's real 2026-06-30 hole is still in Yahoo today**, so it needed no mask. The diff in §3 shows `main` at net debt / FCF 3.13 (REJECTED) and the fill restoring 2.67 (PASS).

## 5. 10-Q spot-check: 5 names, 22 cells

Each filled cell was traced to the facts the fill used, with their accession numbers. The same concept and period were then read straight from each filing's XBRL instance in the EDGAR Archives, using entity-wide contexts only. The names are APH, ABT, AMZN, ETN and IQV.

- **22 of 22 match** three ways: the value the product wrote, the companyfacts recomputation, and the filing itself.
- The cells cover revenue, net income, EBIT, OCF, capex and SBC, plus IQV's FY − 9M OCF.
- IQV's 9M comes from the original 2025 Q3 10-Q (0001628280-25-046794), not the 2026 comparative: the "A as filed by B's filing" rule in action.

| Name | Cells | Example (filing) |
|---|---|---|
| APH | 5 | OCF Q2 2026 = 2,678.7 − 1,121.5 = $1,557.2M (10-Q 0001104659-26-089194 and -054128) |
| ABT | 6 | Revenue Q2 2026 = $12,593M (10-Q 0001628280-26-050134) |
| AMZN | 6 | Capex Q2 2026 = $54,208M, `PaymentsToAcquireProductiveAssets` (10-Q 0001018724-26-000026) |
| ETN | 4 | Capex Q2 2026 = 446 − 193 = $253M (10-Q 0001551182-26-000030 and -000013) |
| IQV | 1 | OCF Q4 2025 = 2,654 − 1,919 = $735M (10-K 0001628280-26-008322 − 10-Q 0001628280-25-046794) |

## 6. Caveats

- **One snapshot.** The 2026-06-30 hole is a vendor event and may clear on its own. The fill does nothing for a name without a gap.
- **The concept map is deliberately small.** Utilities, REITs, banks and insurers mostly fail the 1% check or have no comparable quarter, so they keep the annual fallback. That is the intended direction: no fill rather than a fill on another scope.
- **52/53-week filers whose Yahoo labels drift more than 10 days** from the true period end (COST) are never filled.
- **A missing income column** (not just a NaN cell) still fails D23's step check, and it is not filled.
- **No point-in-time claim.** The fill uses facts filed on or before the run date. Beyond the "A as filed by B" rule for derivations, a historical backtest needs #54.

## Scripts

These live in `scratch/` (git-ignored), with copies in the snapshot folder.
- `scratch/snapshot.py <out_dir>` pulls the raw snapshot, resumably.
- `scratch/sec_evidence.py <raw_dir> <out.json>` produces §1–§4. It needs `SEC_USER_AGENT` in `.env`, which is never printed.
- `scratch/sec_spot.py <raw_dir> <evidence.json> APH,ABT,AMZN,ETN,IQV` produces §5.
