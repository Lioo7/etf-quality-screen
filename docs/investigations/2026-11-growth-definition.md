# Growth definition for the Rule of 40 and the P/S guardrail (issue #67, 2026-11-23)

Question: which revenue-growth figure should feed both the Rule of 40 and the P/S
guardrail (`P/S <= 0.5 x growth%`), so that one quarter's base effect can't decide a
verdict? Before this change, growth was the newest quarter against its year-ago quarter
for 462 of 493 screened names (D21): Yahoo exposes about 5 quarterly columns, so the
prior-year TTM can't be summed.

Data: the 2026-11-22 raw Yahoo snapshot behind #20 and D21 (QQQ via the Nasdaq API, SPY
via Wikipedia: 518 names, 493 screened). Reference growth comes from SEC companyfacts: the
TTM revenue ending at Yahoo's newest quarter against the TTM ending one year earlier,
built only from reported duration facts (a 12-month fact, or FY_prev + YTD − YTD a year
earlier). This is a scratch script, not product code; #54 isn't built.

## 1. BKNG is not misaligned

Yahoo pairs the right quarters (365 days apart), and its figures equal BKNG's 10-Q facts:

| Quarter ending | Yahoo | SEC 10-Q (3-month fact) |
|---|---|---|
| 2026-09-30 | $8.893B | $8.893B |
| 2025-09-30 | $9.008B | $9.008B |

The −1.3% quarter is real. BKNG's true TTM growth is +8.0%, and its latest FY (Dec 2025)
grew 13.4%. A single quarter understates it, and a 9-month-old FY overstates it.

## 2. Candidate definitions against SEC true-TTM growth

**Sample.** 9 fixed names (the five D21 flips plus CLX, SNDK, OMC, KDP) and 16 random
names (seed 67). CLX has no SEC TTM pair, which leaves 24 names. Absolute errors are in
percentage points.

"Mean of available YoY quarters" is the same as quarter YoY on every name. With ~5
quarters exposed, each name has exactly one aligned YoY pair. The 50/50 average of
latest FY and quarter YoY was not a planned candidate. It came up on this sample, so a
holdout of 25 fresh random names (seed 6767, none from the first sample) tests it.
SO, PEP and ETR have no SEC pair and were replaced.

| Definition | First 24: median / mean | Holdout 25: median / mean | All 49: median / mean / off by > 10 |
|---|---|---|---|
| Quarter YoY (old rule) | 4.0 / 12.1 | 5.3 / 9.9 | 4.7 / 11.0 / 12 |
| Mean of YoY quarters | 4.0 / 12.1 | 5.3 / 9.9 | 4.7 / 11.0 / 12 |
| Latest FY vs prior FY | 4.2 / 13.9 | 3.7 / 9.2 | 3.9 / 11.5 / 14 |
| 50/50 average of FY and quarter | 2.0 / 4.6 | 2.9 / 7.2 | 2.3 / 5.9 / 5 |
| Staleness-weighted blend | 1.7 / 6.1 | 2.2 / 7.1 | 2.1 / 6.6 / 7 |
| **Chosen: 50/50, exact pair at k = 0** | 2.0 / 4.5 | 2.4 / 7.1 | **2.3 / 5.8 / 5** |

**Staleness-weighted blend.** It was suggested in review: `((4 − k) / 4) x FY growth +
(k / 4) x quarter YoY`, where k is the number of quarters reported since the FY end. The
rule was to adopt it if it is at least as good as the 50/50 average. It isn't:

- It wins at the median (2.1 vs 2.3) and at k = 0.
- It has more names off by more than 10 points (7 vs 5).
- Its mean error is higher (6.6 vs 5.9).
- It is worse at k = 3, where 32 of the 49 names sit in November (3.1 vs 2.6).

So the 50/50 average stays.

**Exact pair at k = 0.** When the FY ends at the newest quarter, the FY *is* the
trailing year and the prior FY is the prior TTM. That pair is exact, so it is used
directly ("true TTM wins"), which removes the quarter noise there.

**Error by staleness** (all 49; median abs error; k = quarters since the FY end):

| k | n | Quarter YoY | Latest FY | 50/50 average | Weighted |
|---|---|---|---|---|---|
| 0 | 3 | 3.3 | 0.0 | 1.7 | 0.0 |
| 1 | 8 | 10.5 | 3.6 | 2.7 | 0.4 |
| 2 | 6 | 3.3 | 0.9 | 1.5 | 1.5 |
| 3 | 32 | 4.8 | 4.8 | 2.6 | 3.1 |

- **Latest FY degrades with staleness.** At k = 3 it is no better than the quarter.
- **Quarter YoY is noisy at every k.** Seasonality, acquisitions and base effects all hit
  it.
- **Why the average works.** The TTM's growth is roughly the mean of its four quarterly
  YoYs. The latest quarter is the newest of those. The FY covers the older end of the
  window, or the window itself at k = 0. So the average sits between the two.

On the 8 problem names, FY alone is much worse than the quarter (median 21.5 vs 9.0;
OMC, KDP and SNDK are acquisitions or spikes), and the average fixes both (3.2).

**Worst cases** for the chosen rule:

| Ticker | Error (pts) | Why |
|---|---|---|
| DD | 92.5 | SEC TTM +95.5% against ~2–4% on Yahoo; likely a revenue-concept change around the 2025 separation, so the reference is suspect |
| CRWV | 38.5 | Hypergrowth slowing within the window: 168% on Yahoo vs 130% TTM |
| FANG | 15.6 | Not investigated |
| PLTR | 11.0 | Not investigated |

## 3. Decision

When the source can't sum a true TTM pair, growth is the mean of latest-FY growth and the
latest-quarter YoY. Every input is a raw sourced line, and nothing is extrapolated.

**Precedence:**
1. A true TTM pair: 8 quarters, FMP, or an FY ending at the newest quarter (with
   the 5 newest quarterly columns gap-free).
2. The estimate, flagged `estimated TTM growth: blend of FY and latest quarter (FY ended
   ...)`.
3. The annual basis. The quarter YoY alone never decides growth.

The quarter YoY stays as an informational column (`growth_quarter_yoy`). When it sits
more than 15 points below the growth used, an advisory note says so; it never gates.

The thresholds (40, 0.5, 2) are unchanged.

## 4. Same-snapshot verdict diff (main vs branch)

The baseline (`main`, 6a67b8b) reproduces #20's 518 verdicts: 15 PASS, 10 WATCHLIST,
4 RECENT_IPO, 2 INSUFFICIENT_HISTORY. Growth basis on the branch: FY+quarter blend 440,
annual 33, TTM 20 (the FY ends at the newest quarter). The branch changes 3 verdicts:

| Ticker | Verdict | Growth | Rule of 40 | P/S vs ceiling | Cause |
|---|---|---|---|---|---|
| AAPL | REJECTED → WATCHLIST | 11.7 → 15.1 | 37.3 → 40.6 | 10.4 vs 5.9 → 7.5 | FY ends at the newest quarter (Sep): the exact TTM, equal to SEC's 15.1. Rule of 40 now passes; P/S and PEG 2.6 still block |
| PAYX | REJECTED → WATCHLIST | 5.9 → 11.4 | 35.1 → 40.6 | 6.1 vs 2.9 → 5.7 | Blend (SEC TTM 13.7). Rule of 40 passes; P/S and PEG 2.4 block |
| PLTR | PASS → WATCHLIST | 95.9 → 76.0 | 138.0 → 118.2 | 47.4 vs 47.9 → 38.0 | Blend (SEC TTM 87.1). The quarter spike no longer lifts the P/S ceiling above P/S |

**Checked against SEC growth.** For the 46 sampled names on the TTM basis, I re-ran
`evaluate()` with SEC true-TTM growth. The branch's verdict matches it on 46 of 46;
`main` matches on 43, missing exactly the three rows above. Under SEC growth, PLTR's
ceiling would be 43.6, still below its P/S of 47.4.

**Two predicted flips that didn't happen.** #20 expected ANET and BKNG to flip under
latest-FY growth. Under the blend, neither does, and both agree with their SEC-growth
verdict:
- ANET: growth 43.5 against a true 40.7, still PASS (P/S 17.8 against a ceiling of
  21.8).
- BKNG: growth 6.1 against a true 8.0, Rule of 40 36.8, still REJECTED.

**Deceleration note.** It fires on 13 names: ALNY, AMCR, BA, COF, ECHO, EQT, EXE, FSLR,
HOOD, IP, KEY, SW, TTWO.

## 5. Caveats

- **Small sample.** 49 names on one snapshot. The 50/50 average was found on the first
  24, and the holdout supports it but doesn't prove it.
- **Seasonal skew.** In November most names sit at k = 3. Rerun the comparison in a
  different quarter (D13 cadence).
- **SEC reference limits.** It uses us-gaap revenue concepts: 20-F filers and some
  utilities and staples (CLX, SO, PEP, ETR) have no pair. A concept change, like DD,
  can make the reference wrong.
- **Gappy quarter rows (pre-existing).** 40 snapshot names have a NaN revenue
  quarter in the middle of Yahoo's 5 columns (most often 2026-06-30: ABT, AMZN, BSX…).
  The aligned quarter pair still exists. But on `main` and on this branch alike,
  `revenue_ttm` sums the newest 4 *finite* columns, so it skips a quarter and includes
  the year-ago one. That affects P/S and margins, not the growth figure.

  The exact pair (FY ending at the newest quarter) divides that sum by the prior FY,
  so it now also requires the 5 newest columns to span exactly a year. BEN and EMR
  therefore fall back to the annual basis, and no verdict changes. Fixing the TTM
  level sum for all 40 names is out of scope here and is left for a follow-up issue.
- **Mixed periods in the Rule of 40.** The estimated growth is paired with a true TTM
  margin. The old quarter growth mixed periods in the same way.
- **Scripts.** Both are git-ignored, in `scratch/`:
  - `scratch/growth_defs.py`: the SEC comparison. Run from the repo root with
    `set -a; source .env; set +a` first, because it needs `SEC_USER_AGENT` (never
    printed).
  - `scratch/growth_diff.py`: replays the snapshot through one checkout.
