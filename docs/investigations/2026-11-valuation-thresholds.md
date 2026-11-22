# Valuation thresholds: PEG ceiling and P/S guardrail (issue #20, 2026-11-22)

Question: should the P/S guardrail (`P/S <= 0.5 x growth%`) use multi-year growth so a
one-year spike can't inflate it? Extended scope: the PEG ceiling against Lynch's bands,
other guardrail forms, margin blindness on Track B, and where both thresholds came from.

Data: the raw Yahoo snapshot of QQQ (Nasdaq API list, 101) and SPY (Wikipedia, 503)
taken on 2026-11-22 for #61: 518 unique names, of which 493 screen (QQQ 98, SPY 481) and
25 are skipped by the provider. Each name is replayed through `main` (ddba862) with CLI
defaults (3 years, trend, 12-month IPO window, no overrides). The baseline reproduces
all 518 verdicts of the #61 run (0 mismatches). A variant changes one threshold and
re-runs `evaluate()` in full, so routing, gates, WATCHLIST and NEEDS_DATA all apply.
The baseline has 15 PASS and 10 WATCHLIST, plus 4 RECENT_IPO and 2
INSUFFICIENT_HISTORY.

## 1. The growth input is one quarter, not one year

Yahoo caps quarterly statements at about 5 columns, so the prior-year TTM can't be
summed. Growth then falls back to the newest quarter against its year-ago quarter
(`.claude/rules/providers.md`). On this snapshot that applies to **462 of 493
names**. The issue's "378% trailing growth" for SNDK is one quarter ($11.0B vs $2.3B).
Its latest fiscal year grew 175%, and consensus FY1/FY0 revenue growth is 24%.

| | Growth used (quarter YoY) | Latest FY | 3-yr median FY | Consensus FY1/FY0 | P/S |
|---|---|---|---|---|---|
| SNDK | 378% | 175% | 10% | 24% | 3.1 |
| PLTR | 96% | 56% | 29% | 54% | 47.4 |
| ANET | 59% | 29% | 29% | 40% | 17.8 |
| LRCX | 54% | 26% | 24% | 21% | 8.8 |
| APP | 70%\* | 70% | 70% | 26% | 21.1 |
| CLX | 33% | -5% | -4% | 2% | 1.8 |

\* APP is on the annual basis (D18: quarterly capex not reported), so its growth is
its latest fiscal year.

Quarter YoY and latest-FY growth differ by a median of 5.2 points, by more than
10 points for 157 names and by more than 25 points for 63. Base effects drive much of
this: CLX's year-ago quarter was depressed, OMC and KDP made acquisitions, and refiners'
revenue follows prices. The same growth term feeds the Rule of 40 (section 5).

## 2. PEG ceiling vs Lynch's bands

Lynch (*One Up on Wall Street*): a P/E at half the growth rate (PEG 0.5) is very
positive, and a P/E at twice the growth rate (PEG 2) is very negative. Our `PEG_MAX = 2`
sits on his "very negative" line, so it rejects the clearly overpriced and is not a buy
signal.

| Ceiling | Verdict flips (all PASS -> WATCHLIST) |
|---|---|
| 2.0 (current) | — |
| 1.5 | SHOP (PEG 1.73) |
| 1.0 | SHOP, PLTR (1.15), NEM (1.11) |

Of the 418 names with a computable PEG, 125 are at or below 1.0, 190 at or below 1.5 and
257 at or below 2.0 (median 1.67). Among the 15 PASS names, 12 are already at or below
1.0. The quality gates do almost all of the rejecting: a tighter ceiling only moves
passers to WATCHLIST.

Two differences from Lynch: his growth is a multi-year (5-year) rate, while ours is
one year of consensus EPS growth (FY0 to FY1, D1), which swings more. Consensus
*long-term* growth forecasts for high-growth names are too optimistic and get revised
down (La Porta 1996; Bordalo, Gennaioli, La Porta & Shleifer 2019). Applying that to
the one-year FY1 consensus is an extrapolation, not their finding, but if it holds, the
measured PEG understates the true one for growth names. A lower ceiling for everyone
doesn't target that. The valuation-pass label (D20) only applies to WATCHLIST names,
so names that PASS on an optimistic FY1 (PLTR 1.15, SHOP 1.73) carry no caveat. That
stays open.

## 3. P/S guardrail variants

Each variant replaces the ceiling G in `P/S <= G`; g is the current growth input.

| Variant | Ceiling | P/S test pass | Not computable | Verdict flips |
|---|---|---|---|---|
| Current | 0.5 g | 312 / 493 | 0 | — |
| One third | g / 3 | 246 / 493 | 0 | PLTR, SHOP: PASS -> WATCHLIST |
| Capped | 0.5 min(g, 40) (P/S <= 20) | 307 / 493 | 0 | APP, PLTR: PASS -> WATCHLIST |
| Geometric | 10 x ((1 + g) / 1.2)^5 (= current at g = 20%) | 409 / 493 | 0 | none |
| Median FY growth | 0.5 x median of 3 annual growths | 198 / 477 | 16 | ANET, PLTR: PASS -> WATCHLIST |
| Min(current, median) | 0.5 x min(g, median) | 158 / 477 | 16 | ANET, PLTR: PASS -> WATCHLIST |
| Latest FY growth | 0.5 x latest annual growth | 254 / 488 | 5 | ANET, PLTR: PASS -> WATCHLIST |
| Consensus revenue growth | 0.5 x FY1/FY0 consensus | 237 / 491 | 2 | APP, NEM, PLTR: PASS -> WATCHLIST; BX: WATCHLIST -> PASS |

Notes:
- **Spikes inflate the ceiling; one verdict turns on a spike.** 83 names have growth
  above twice their 3-year median (and above 20%). Six of them PASS: SNDK, LRCX, APH, VRT,
  ANET and PLTR. The first four pass the P/S test under every growth definition, because
  their P/S (3 to 9) sits far below any ceiling. For profitable names the binding
  valuation test is usually PEG, which is on the consensus basis since #6. The SNDK
  problem is a low P/E at peak earnings (PEG 0.08), which is #15's (CYCLICAL), not a P/S
  calibration. ANET is the spike case: its latest FY and 3-year median both grew 29%, and
  only the quarter (59%) lifts its ceiling above its P/S of 17.8. Under any multi-year
  form it moves PASS -> WATCHLIST.
- **Multi-year growth also catches acceleration.** PLTR, the other flip, accelerated
  (annual growth 17% → 29% → 56%), and its quarter (96%) adds a spike on top. The median
  holds an accelerating name to stale growth. Latest-FY growth doesn't, but it still
  flips PLTR, because P/S 47.4 exceeds 0.5 x 56%. The median also leaves 16 names without
  a ceiling (fewer than 4 annual periods), and that would need a new untestable path.
- **One third and the cap are recalibrations, not spike fixes.** Neither has a source;
  the one-third rule is a SaaS rule of thumb for growth-adjusted EV / revenue. The cap is
  the only form that limits an extreme multiple (PLTR: P/S 47.4 against a ceiling of 47.9
  passes today), and it also catches APP, whose 70% growth is steady.
- **The geometric form loosens.** Multiples should rise faster than linearly with
  *durable* growth, but applied to a trailing quarter it rewards spikes most: no name
  tightens and 97 more pass the P/S test.
- **Consensus growth** puts P/S on PEG's forward basis, but for high-growth names it
  is plausibly optimistic and slow to revise. That is extrapolated from findings on
  long-term growth and EPS forecasts (section 2; Chan, Jegadeesh & Lakonishok 1996),
  not measured here for revenue. It also mixes a forward valuation with the trailing
  quality gates.

## 4. Margin blindness (Track B)

P/S = P/E x net margin, so the same P/S means very different things at a 15% and an
80% gross margin. Two margin-aware variants, each equal to today's rule at a 70% gross
margin (the EV variant also needs zero net debt; GM from the latest annual COGS):
- P/S scaled: `P/S x (0.70 / GM) <= 0.5 g`
- EV / gross profit: `EV / GP <= 0.5 g / 0.70`

Track B is almost empty in QQQ + SPY. Only 24 screened names are GAAP-unprofitable, and
4 of them have Rule of 40 >= 40. Only GILD reaches the valuation test (WATCHLIST; P/S
6.7 against 3.4), and it stays WATCHLIST under both variants. Neither variant flips any
QQQ + SPY verdict except SHOP (Track A, GM 48%: PASS -> WATCHLIST). The cached ARKK names
(Nov-22) show the blind spot without a verdict at stake, since both are rejected on Rule
of 40:

| | GM | P/S | Ceiling: current / GM-scaled / EV-GP |
|---|---|---|---|
| RKLB | 34% | 34.7 | 36.4 / 17.9 / 20.8 |
| CRWV | 15% | 2.2 | 84.1 / 17.5 / 4.6 |

On Track A the opposite effect shows. P/S is the only blocker for five profitable
WATCHLIST names whose PEG passes: V (P/S 16.3, PEG 1.67), MA (15.0, 1.74), VRSN (15.9,
1.95), IBKR (10.5, 0.95) and BX (9.9, 0.62). These are high-margin franchises. Where PEG
is computable, the P/S guardrail acts as a second, margin-blind valuation test. The
GM-scaled variant would free none of them: their P/S is high relative to growth at any
margin.

## 5. Side finding: the Rule of 40 uses the same one-quarter growth

Using the latest fiscal year's growth in both the Rule of 40 and P/S (one definition,
same base) changes 5 verdicts:

| Ticker | Quarter YoY / latest FY growth | Rule of 40: now -> FY | Verdict |
|---|---|---|---|
| ANET | 58.5% / 28.6% | 96.3 -> 66.5 | PASS -> WATCHLIST (P/S) |
| PLTR | 95.9% / 56.2% | 138.0 -> 98.3 | PASS -> WATCHLIST (P/S) |
| BKNG | -1.3% / 13.4% | 29.5 -> 44.1 | REJECTED -> PASS |
| AAPL | 11.7% / 15.1% | 37.3 -> 40.6 | REJECTED -> WATCHLIST (PEG 2.61) |
| PAYX | 5.9% / 16.9% | 35.1 -> 46.1 | REJECTED -> WATCHLIST (PEG 2.44) |

The quality gates depend on the growth input more than the P/S guardrail does. BKNG's
negative quarter YoY needs a look at the quarter alignment. This is outside #20's scope.

## 6. Provenance

`PEG_MAX = 2.0` and `PS_GUARDRAIL_FACTOR = 0.5` arrived in the first screening commit
(c9343c7, 2026-06-17) as "the validated spec", with no source in code, README or docs.
PEG 2 matches Lynch's "very negative" boundary, a fit found after the fact rather than
a recorded reason. No published source was found for 0.5 x growth.

## 7. Recommendation

- **PEG: keep 2.** It sits on Lynch's upper band, so it rejects the clearly overpriced,
  and the quality gates do the selecting. Tightening to 1.5 or 1.0 moves 1 or 3 passers
  to WATCHLIST and doesn't target the suspected weakness: possibly optimistic FY1
  consensus for high-growth names, which goes unflagged on PASS names (open).
- **P/S guardrail: keep the 0.5 x growth formula; no separate multi-year growth for
  P/S.** Only one verdict on this snapshot turns on a spike (ANET, PASS -> WATCHLIST),
  and no rejection does. The median form also holds accelerating names to stale growth
  and leaves 16 names without a ceiling. Giving P/S its own growth definition would
  put the P/S test and the Rule of 40 on different bases. One third and the cap are
  unsourced recalibrations, and the geometric form loosens.
- **The weakness is the growth input** (single-quarter YoY for 94% of names), and it
  affects the Rule of 40 more than P/S. A follow-up issue should decide the growth
  definition for the Rule of 40 and P/S together; it is not filed with this change. A
  changed definition fixes the guardrail's spike exposure (ANET) as a side effect.
- **Margin-aware P/S: not now.** Track B has one name at the valuation test in QQQ +
  SPY. Revisit with a run where Track B has names (ARKK, S&P 400 mid caps #47). Whether
  the P/S guardrail should apply to Track A names with a computable PEG is an open
  spec question (V, MA, VRSN, IBKR, BX); it isn't decided here.

## 8. Caveats

- One snapshot (2026-11-22). The flips are a handful of names, so treat them as
  illustrations, not rates. Revisit after three monthly runs (D13).
- ARKK names were checked only where already cached. The Nov-19 ARKK export predates
  #6 and #58.
- Gross margin comes from the latest annual statement (no TTM COGS on `Company`); 40
  names (mostly financials) have none.
- Script: `scratch/valuation_variants.py` (git-ignored), run as
  `.venv/bin/python -I scratch/valuation_variants.py <repo> <raw_dir> <out.json>`.
