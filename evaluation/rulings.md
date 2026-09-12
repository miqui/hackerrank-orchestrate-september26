# Ruling Log — Ambiguity Resolutions

Miguel's gap analysis (2026-09-12) cross-checked against dataset evidence.
Each ruling: gap → decision → dataset evidence → artifact updated.
Rulings are binding for implementation; revisited only if a public sample contradicts.

## R1. Deadline: hard constraint vs ranking criterion
**Ruling:** `desired_completion_date` is a hard filter for *plan eligibility* (a plan that
misses it may not be RECOMMENDED), while ranking rule 1 orders only the plans that
already passed the filter. `affordable_later` vs `not_affordable`: if the full amount
becomes safe within the 90-day horizon → `affordable_later`; never within the horizon →
`not_affordable`. The deadline does NOT affect status classification, only method choice.
*Status: to confirm against samples (wait rows: does any wait plan complete after deadline?).*

## R2. amount_safe_to_pay vs spending-change plans
**Ruling:** `amount_safe_to_pay` is computed with NO spending changes (baseline safety).
A `full_payment`+`stop:` plan (sample request_06) is legal and its payment may exceed
`amount_safe_to_pay`; the output contract does not forbid it. Explanation must state
the change was applied.

## R3. partial vs installments when both safe (ranking rule 3)
**Ruling:** follow the text literally — lower total payable wins, so partial usually
beats installments. Fee-inclusive `total_payable_amount` is the comparison basis.
*Status: only 1 partial sample; backtest will confirm.*

## R4. Salary projection inside 90 days
**Ruling:** project salary at its historical cadence (median gap 29d, modal 31d) from the
LAST CONFIRMED (settled or scheduled) salary row. Dataset check: 269/275 users have >1
salary rows; 42/250 requests have a `scheduled` salary within 0-20 days of request_date.
Only scheduled (47 rows) are "confirmed" — settled history sets the cadence but future
paydays beyond the next scheduled row are NOT invented. Evidence: user_10 salary spans
Jul-Dec 2024 at weekly/monthly cadence.
*Status: highest-impact ruling; validate in backtest round 1.*

## R5. Recurrence definition
**Ruling:** recurring = ≥2 settled occurrences, same category+event_type, interval
consistency ±3 days around the modal gap, amount stability ±10%. Project at modal
cadence with modal amount until day 90. Dataset fields: `flexibility` ∈ {fixed,
reducible, stoppable, reducible_or_stoppable} (2,682 reducible / 1,297 stoppable / 225
both), `minimum_allowed_amount` bounds `reduce_to`. `subscription` event_type (2,488
rows) is recurring by definition when history shows cadence.

## R6. Essential / protected / flexible mapping
**Ruling:** profile columns map directly: `expense_categories_to_protect` = essential
(never forecast lower, never proposed for changes); `willing_to_reduce` = flexible-
reducible; `willing_to_stop` = flexible-stoppable; event `flexibility` flag must ALSO
permit the action (both profile permission AND event flexibility required).

## R7. Same-day ordering + window start
**Ruling:** day 0 = `request_date`; the 90-day window is [request_date, +89]. Within a
day, apply: starting balance → pending debits → recurring debits → income → the
request payment LAST (money must exist before spending). This is the conservative
reading; ambiguity resolves to the financially safer interpretation per the conflict
chain.

## R8. Installments extending past day 90
**Ruling:** 389/469 multi-payment options extend beyond day 90 — common case, not
exotic. Safety check covers payments within the 90-day window; payments scheduled
after day 90 are checked only against the projected balance at day 90 (no income/
expense projection beyond the window; the check verifies balance-at-90 ≥ payment due +
minimum). *Status: backtest-confirm; if samples disagree, adopt
"installments must fit entirely in horizon" instead.*

## R9. Pending semantics (now data-confirmed)
**Ruling:** pending debits (63 rows: shopping/transport/healthcare) ARE reserved.
Pending credits (8 rows) are NOT counted. Pending refunds linked via `linked_event_id`
to a settled purchase (event_1785 → event_1784 pattern) are still not counted —
"pending credits" includes refunds until settled.

## R10. Exchange rates
**Ruling:** use the event's `settlement_date` when present (10 rows lack it → fall back
to `event_date`); match exact date, else nearest-preceding; missing pair entirely →
quarantine + disclose. 134 rate rows cover 5 currencies.

## R11. reduce_to semantics
**Ruling:** `reduce_to` = new recurring amount from request_date through day 90;
floor = `minimum_allowed_amount` of the event (populated for reducible rows); if
unset, floor = 50% of current amount. Selection order among flexible events:
largest monthly saving first, then lowest event_id as tie-break. Apply changes only
when they flip a plan's safety or deadline feasibility — never cosmetically.

## R12. Explanation + transcript + cost basis
**Ruling:** `decision_explanation` = 1-2 sentences, cites the 2-3 dominant numeric
facts (sample style: "Stop the family streaming plan, then pay EUR 620.40 today. This
leaves at least EUR 800 available."). Rounding: amounts to 2 decimals as given (no
re-formatting of currency precision). Usage report cost basis: OpenRouter list prices
at run time, noted in the file.

## R13. Image vs event-row contradiction
**Ruling:** image (newer evidence) wins over the event row amount when the event
amount is blank; when both exist and conflict → explicit-amendment > newer-record
rule applies: the image is treated as an amendment only when the message/image chain
is explicit; otherwise settled event row wins and the discrepancy is disclosed in the
explanation. Image linked to an event that already has an amount: use image only if
the event is pending/scheduled; settled events keep their amount.

## R14. Message/event "same source"
**Ruling:** messages about an event are NOT the same source as the event row — but an
explicit amendment/cancellation message outranks per conflict rule 1 regardless of
source. Rule 2 (newer same-source) applies within ledgers only.

## R15. Investment lifecycle
**Ruling:** `investment_purchase` (settled debit) = past cash outflow, already
reflected; `investment_valuation` (unrealized, non_cash) = never cash;
`investment_sale` (settled credit, linked to purchase) = cash IN on settlement date;
a pending redemption/sale does NOT count until settled. Investment REQUESTS evaluate
affordability of the contribution like any debit; no price prediction.

## Reverse-engineering plan (Miguel's priority note — adopted)
Order of attack agreed: samples first. Backtest harness (task 5.2) runs BEFORE any
orchestration code is finalized; salary projection (R4), recurrence (R5), and
partial-vs-installment preference (R3) are the three rulings the sample run must
confirm first. Effort order: samples/R4/R3/R5 → evidence pipeline → ranking.

## R16. Exchange-rate mechanics (Miguel FX deep-dive, independently verified)
**Verified on shipped data:** 140 foreign-currency events, ALL match a rate row on their exact event/settlement date - zero fallback hits. The 5 table pairs are exactly the 5 pairs the data needs. Rates are CONSTANT per pair (dated structure = join test, not market simulation). Coverage to 2026-11-15 (USD-INR) / 2026-01-15 (EUR-ZAR).
- R10 superseded: exact match always succeeds on shipped data; nearest-prior fallback = defensive code only; unit test asserts ZERO fallback hits on shipped dataset (in evaluation/).
- No live-rate fallback ever: ground truth from the fixed table; live rates break accuracy, determinism, reproducibility, auditability. Missing rate -> error loudly (quarantine + disclose).
- Never invert or chain rates: USD-EUR=0.92 vs EUR-USD=1.09 (1/0.92=1.087) - deliberate non-reciprocal trap. Listed pairs only, as listed.
- Scheduled foreign income (8 rows: event_2288, 3611, 3833, 5911...) converts at the settlement-date rate row.
- Blank-amount foreign event (event_7307, USD): extraction returns currency + amount; convert after extraction; never assume image is home currency.
- Forward projection of foreign recurring income past last rate date: reuse latest pair rate + flag in explanation.
- Rounding (2dp vs 0dp for IDR): decided from sample outputs in backtest round 1 (feeds R12).

## R17. Event-level rulings (Miguel's financial_events deep-dive, independently verified 2026-09-12)
**V1 - minimum_allowed_amount:** fully populated on every reducible/reducible_or_stoppable row (2,907 total: 2,682 reducible + 225 both), blank on all fixed/stoppable. Floor ratio varies 0.36-0.69 (Miguel said 0.39-0.50; verified wider) - column must be read per-row, never replaced by a constant. R11's 50% fallback never triggers on shipped data. Flexibility is constant within every recurring chain: read once per chain.
**V2 - Category stability:** zero user+description chains change category (verified on 8,002 chains; Miguel reported 7,760 - same conclusion). Description is a stable key. Category-keyed recurrence is safe; description-keyed is stricter.
**V3 - Salary traps (the big one):** description variants inside category salary include one-off credits (Promotion arrears, Quarterly performance bonus), 'Final employer payroll' rows (7 users) = STOP signal for projecting that income, and freelancer multi-description income (user_09 pattern, ~7/20th dates, varying amounts) = NOT confirmed income under the rules. 'Next confirmed salary' = 47 rows, all scheduled, all populated - the ONLY confirmed future income marker. Ruling: recurrence for income requires description-stable chains AND (for projection) scheduled/confirmed status; description-keyed matching; one-off salary-category credits (bonus/arrears) never project; Final-payroll row stops all future salary projection for that user.
**V4 - Missing settlement_date (10 rows):** all 10 are unrealized investment_valuation rows (ignored anyway). Fall back to event_date is moot; keep the fallback defensively.
**V5 - Duplicates:** zero exact duplicate rows. The spec's 'duplicate records' = (a) internal-transfer messages ('between your two accounts', 5 msgs) = net zero, not income nor spend; (b) linked pairs where cancelled row duplicates a settled one (count once, V6 shape 3). NOT naive row dedup.
**V6 - Linked chains: exactly 7 shapes (counts verified, match Miguel exactly):**
  14x expense settled -> refund settled: net refund back in
   8x expense settled -> refund pending: ignore pending credit, expense stands
   8x expense cancelled -> expense settled: count settled once, cancelled row is duplicate representation
   6x expense settled -> expense pending: COUNT the pending debit (repeat charge under investigation - financially safer reading, messages confirm)
   7x debt_payment failed -> debt_payment scheduled: failed is void; scheduled retry is confirmed future debit
  10x investment_purchase -> investment_valuation unrealized: ignore valuation
   5x investment_purchase -> investment_sale settled: proceeds are real cash in
**V7 - Blank amounts (16):** ALL image-backed, one image each. 14 INR, 1 IDR, 1 USD (USD one needs no conversion). Includes scheduled rent balance and pending telecom bill - extracted amounts feed the forecast as FUTURE DEBITS, not just history. event_253 salary image (IDR) is the only income one.
**V8 - Messages (215):** ~60 non-ascii (Indonesian templates for IDR users) - regex-only parsing would break; small-model enum classification required (validates two-tier layout). 5 internal-transfer messages = net-zero duplicate case. ~25 template clusters; parser MUST emit fixed enum schema {intent: ~15 values, amount, currency, date, pct} and forecast code applies each intent deterministically. No free-form forecast reasoning by the model.
**V9 - No prompt-injection text found in messages.** The untrusted-data clause is likely tested via assert-income messages (bonus under review, gig payout pending) that must be ignored, not via injection strings. T2 (injection invariance) stays as defensive coverage.
**Scenario-space closure:** the dataset is a finite templated scenario set. Message intents map to forecast operations: first-salary@X@D, salary-reduced-to-X, temp-pay-then-resume, salary-date-move, salary-increase, contract-ended (stop income), household-income-ended, bonus-ignore, invoice-approved (confirmed income), payout-pending (ignore), rent+12%, refund-not-received (ignore), prize-received vs processing, foreign bill, two card minimums. Enum + deterministic applier = near-perfect, cheap.

## R18. Sample-confirmed conventions (from evaluation/backtest.py against dataset/sample_requests.csv, 2026-09-12)

**R18a — decision_explanation prose formatting (CONFIRMED):** money in prose uses
comma thousands separators (`IDR 5,491,000`, not `5491000`); dates in prose use
`D Month YYYY` (`15 November 2019`, not ISO `2019-11-15`). Machine-readable CSV
fields (`amount_safe_to_pay`, `payment_plan`, `earliest_date_for_full_payment`)
remain ISO dates / plain decimal, unaffected. Implemented via
`format_money_display` / `format_date_display` in `code/decide.py`, applied only
inside `_build_explanation`. Raised `decision_explanation` exact-match from 0/25
to 10/25 on the golden sample.

**R18b — earliest_date_for_full_payment is capped at desired_completion_date
(CONFIRMED, supersedes an unstated prior assumption that it searched the full
90-day safety horizon):** the field only reports a day found by searching
`[request_date, desired_completion_date]`, not the full 90-day window used for
the underlying safety *simulation*. A day that only becomes safe after the
request's own deadline is not surfaced as "earliest full payment" even if the
90-day safety check would technically accept it. Evidence: request_14
(deadline 2025-10-04) and request_24 (deadline 2026-02-08) are gold
`not_affordable` with a *blank* `earliest_date_for_full_payment`, even though an
unrestricted 90-day scan finds a later-but-still-within-horizon safe day
(2025-10-18 and 2026-03-18 respectively) — those days are outside each
request's own deadline, so they don't count. Implementation:
`simulate.earliest_date_for_full_payment` gained a `search_days` parameter that
narrows the *candidate scan range* only, while the safety check itself keeps
simulating the full `horizon_days` (90-day) window — conflating the two by
literally shrinking `horizon_days` breaks the safety check and was tried/reverted
during this session. `decide.py` now passes
`search_days = (desired_completion_date - request_date).days + 1`. Raised
`earliest_date_for_full_payment` exact-match from 9/25 to 11/25 and fixed
5 conformance violations flagged by `code/validate.py`
(`earliest_date.required_when_affordable`) that were introduced by an earlier,
incorrect attempt at this same fix (gating `earliest_full` on `full_payment`
being in the user's accepted payment methods, which the samples contradict:
request_02/12/17/19/22 are `affordable_with_plan` via installments/partial
methods only, yet still have a populated `earliest_date_for_full_payment`).

**Open / NOT sample-confirmed — recorded for the next iteration:**
`amount_safe_to_pay` remains only 4/25 exact-match and `affordability_status`
17/25. Diagnosis so far (request_02, request_03 traced in detail): our
`safe_amount`/`earliest_date_for_full_payment` baseline forecast — built from
`build_line_items` (ledger rows + `_project_future_occurrences` cadence
projection per R4/R17-V3) — produces a materially higher safe-amount than gold
on requests with an upcoming salary credit inside the 90-day window (request_02:
ours 23,437,556.12 vs gold 17,229,139.2; request_03: ours safe-amount matches at
the *no-income* baseline giving 0.00, i.e. removing all salary makes the
forecast *never* safe, which is also wrong — gold's 873,000 sits strictly
between "count all projected salary" and "count no salary at all"). This means
gold is not simply "drop future salary" (R4 stays as originally ruled: project
already-scheduled/confirmed rows, no invented paydays) — the remaining gap is
likely in how *recurring debits* (rent, utilities, subscriptions in the
line-item stream) are being counted forward, or in an R2 semantics detail about
whether the safety floor is evaluated strictly before or after the same-day
salary credit lands (ordering tie-break). This was not resolved in this
session; flagging for the next iteration rather than guessing further changes
that could regress the 44 passing tests. `evaluation/backtest.py`'s
per-mismatch table (run `python3 evaluation/backtest.py`) enumerates every
remaining divergence with both values and the implicated ruling, to continue
from.

## R19. Protected-variable-category reserve theory — TESTED, NOT ADOPTED (2026-09-12)

**Hypothesis tested:** `amount_safe_to_pay` divergence (4/25 exact match) is
explained by a missing reserve for protected VARIABLE-spend categories
(groceries/healthcare/etc.) that never form a single description-keyed
recurring chain (R5) because real-world spend under one category is logged
under many different descriptions ("Supermarket basket", "Weekly produce
market", ...). `reconstruct._category_fallback_chains` already existed
(aggregates leftover settled debits per (user, category) at the AVERAGE
historical amount/gap) but was **never wired into `main.build_ledgers_and_chains`**
— a genuine gap, confirmed by inspection.

**Evidence for the theory (partial):** for request_05 (user_05), gold
`amount_safe_to_pay`=737 implies a total reserve of headroom(33,375.10) −
gold = 32,638.10. Description-keyed chains alone (R5) give reserve
21,529.62 (94% short of the true reserve). Adding groceries+healthcare
prior-90d raw sums (9,365.94 + 2,076.40 = 11,442.34) gets to 32,971.96 —
94% of the way to gold's implied reserve, a striking partial fit.

**What was actually tried (all measured against the full 25-sample set,
not just request_05/10/14):**
1. Wire `_category_fallback_chains` into `build_ledgers_and_chains`,
   gated to categories in `expense_categories_to_protect`, projected
   forward at the chain's average interval like any other recurring
   chain (i.e. the existing `_project_future_occurrences` continues it
   at its average cadence for the full 90-day horizon). Result:
   **2/25** (regression from the 4/25 baseline).
2. Same wiring but reserving only ONE occurrence (`modal_amount`, not
   projected forward at all) — computed directly against `headroom` as
   `safe = min(requested, max(0, headroom - single_occurrence_sum))`,
   bypassing the day-grain simulator entirely. Result: **4/25** (net
   neutral — same requests matched as baseline, no additional gains).
3. Reserving the full prior-90-day raw category sum (not averaged/
   projected at all, i.e. treating the historical 90-day total as the
   forward-90-day reserve verbatim) computed the same way as (2).
   Result: **4/25**, but the *ratio* of gold-implied-reserve to this
   90-day-sum-based projected reserve varies from 0.36x to 3.27x across
   the 25 requests with no discernible pattern tied to currency, user,
   category count, or request size — ruling out a single scalar
   correction factor.
4. Applying every discovered spending-change candidate
   (`discover_spending_change_options`, R6/R11) as a blanket reduction
   to the baseline (rather than only the minimal set needed to flip one
   plan's safety, per R11) before computing `safe_amount`. Result:
   **4/25** (unchanged; the affected requests barely moved and are not
   the same ones failing on the reserve dimension).

**Conclusion:** the protected-variable-category reserve is a real and
partially-correct structural insight (R17-V2/R18 already independently
motivated `_category_fallback_chains`'s existence, and it visibly closes
~94% of the request_05 gap) but no discovered scalar rule (raw 90-day sum,
averaged/projected-forward sum, single-occurrence reserve, or blanket
spending-change application) reproduces gold across the sample set well
enough to beat the existing 4/25 baseline — every variant tested was
`<=` 4/25, several were worse. **Decision: R19 is NOT adopted / NOT wired
into the pipeline.** `code/main.py` was left unmodified (byte-identical
to the pre-session version; verified via `git diff` producing only the
expected "file was untracked, now tracked" diff, no logic change) to avoid
regressing the working 4/25 baseline. `_category_fallback_chains` remains
defined in `reconstruct.py`, unused, as prior art for the next iteration.

**Leads for next iteration:** the per-request reserve/proj90 ratio table
(computed but not included here for brevity — regenerate via a short
script over `dataset/sample_requests.csv` + `_category_fallback_chains`)
shows ratios clustering loosely by request SIZE (small IDR/local-currency
household requests trend >1.0x, large ones trend <1.0x), suggesting gold's
real reserve may be based on a *forward-looking simulated* variable-spend
stream (average amount at average interval, run through the full day-grain
`simulate()` alongside chains, INSTEAD of computed as a static headroom
subtraction) rather than any closed-form headroom arithmetic — this was
not tried in this session because it requires piping fallback chains
through `simulate()`'s day-grain engine per-request rather than a scalar
formula, which is a larger change than the remaining budget allowed for
safely (44 tests must stay green). The closed-form ratio table itself
(reserve_gold vs proj90-projected-reserve per request) is reproducible via
the script used in this session's diagnosis; see backtest mismatch output
for exact per-request our/gold values as of this ruling.

## R19-addendum. Day-grain simulated safe-amount w/ variable-category streams — TESTED, NOT ADOPTED (2026-09-12, follow-up session)

**Hypothesis tested:** the R19 lead's own next step — instead of a
closed-form scalar reserve subtracted from headroom, feed the protected
VARIABLE-category fallback chains (`reconstruct._category_fallback_chains`)
into the existing day-grain simulator (`code/simulate.py`) as genuine
forward payment-event streams (cadence-projected at each chain's average
historical interval/amount, per request, starting from the request date),
alongside the existing recurring-chain + confirmed-income streams already
used by `safe_amount`. Binary-search X exactly as `safe_amount` already
does; adopt only if this beats the 4/25 baseline.

**Variants tried (all measured on the full 25-sample set via a scratch
harness built on top of `decide.build_line_items` + `simulate.safe_amount`,
never wired into `code/main.py`/`code/decide.py`):**

| # | Streams added on top of the existing baseline (chains + confirmed income) | Anchor/occurrence convention | amount matches |
|---|---|---|---|
| 1 | all `expense_categories_to_protect` categories, cadence-projected | continue from last historical occurrence, fast-forward missed cycles to request_date | 2/25 |
| 2 | same as 1 | re-anchor cadence to start counting from request_date (ignore historical recency) | 3/25 |
| 3 | same as 1 | overdue occurrence lands immediately on request_date, then continues at cadence | 2/25 |
| 4 | `groceries` only | fast-forward anchor | 3/25 |
| 5 | `groceries` + `healthcare` | fast-forward anchor | 3/25 |
| 6 | `healthcare` only | fast-forward anchor | 4/25 (net neutral vs baseline — same 4 requests matched, request_01/09/12/16, none of the previously-4/25-failing requests flipped) |
| 7 | replay each historical occurrence's own calendar offset shifted forward by exactly 90 days (no averaging at all — literal pattern repeat) | any protected-category subset tried (healthcare-only best) | ≤4/25, never better |
| 8 | added cadence-projected salary/income (relaxing R4's "no invented paydays," projecting even chains without `confirmed_future_dates`) on top of variants 1–7 | — | no variant combination improved on its own non-salary result; several regressed further |

**Additional negative evidence:** traced request_10 (user_10, gold
`amount_safe_to_pay`=12700) in detail per the lead's own suggestion. The
user is a gig-worker with genuinely irregular multi-source income
(`Delivery platform payout`, `Weekly app earnings` — no stable cadence,
amounts swinging 54,774–82,410) already almost entirely composed of
variable-category-like recurring debit chains (fresh food, taxi, delivery
service, etc., mostly < 30-day cadence, already caught by R5's
description-keyed chains without needing the R19 fallback at all). Adding
the category-fallback stream on top pushed our figure to 55,713–256,747
depending on variant — further from gold (12,700), not closer — confirming
this user's gap is NOT explained by a missing variable-spend reserve; it is
some other unresolved mechanic (likely income-cadence/timing) outside this
addendum's scope.

**Conclusion:** every day-grain-simulated variant of the R19 reserve theory
tested in this follow-up session is `<= 4/25`, matching or regressing the
existing closed-form-rejected baseline; none surpasses it. The one
apparent "improvement" (healthcare-only, 4/25) is net-neutral — it matches
the exact same 4 requests the baseline (no variable streams at all)
already matches, so it adds a whole synthetic income/expense stream for
zero measured gain and is rejected as unjustified complexity per the
adopt-only-if-it-improves rule. **Decision: this R19 addendum is NOT
adopted.** `code/simulate.py` and `code/decide.py` are unmodified (still
the R18-confirmed baseline); `reconstruct._category_fallback_chains`
remains defined-but-unwired, as before. No test changes were needed since
no production code changed; the 44/44 test suite and `evaluation/backtest.py`
(4/25 amount, 17/25 status, 18/25 method, 15/25 plan, 11/25 earliest,
22/25 spending, 10/25 explanation) are unchanged from the pre-session
baseline — verified via `python3 -m unittest discover -s tests` and
`python3 evaluation/backtest.py`.

**Leads for the next iteration:** the R19 base ruling's per-request
reserve/proj90 ratio observation (small local-currency household requests
trend >1.0x, large ones trend <1.0x) still isn't explained by cadence
projection at any tested anchor convention — the gap looks less like a
missing spend *stream* and more like a different reserve *basis* entirely
(e.g. a fixed number-of-months' multiple of minimum_balance_to_keep, or a
day-count-weighted partial-month allocation of the CURRENT calendar
month's already-observed spend rather than a full cadence replay). Not
tried this session; the day-grain-simulation mechanism itself
(`code/simulate.py`) is sound and reusable for whichever reserve basis is
eventually confirmed — only the stream-composition question remains open.

## R20. Day-0 no-changes capacity, reserve-composition variants — TESTED, NOT ADOPTED (2026-09-12, follow-up session)

**Confirmed anchor semantic (from the 2026-09-12T20:30:00Z cross-sample
ratio analysis, see `log.txt`):** `amount_safe_to_pay` = day-0 no-changes
capacity — the max X payable *today* (with all recurring obligations and
protected spending reserved, no `stop`/`reduce_to` changes applied) that
keeps `balance - X - reserve >= minimum_balance_to_keep`, capped at
`requested_amount`. Evidence for the anchor itself (req_12 safe==requested
exactly; req_19 safe==plan's first partial payment; req_21/req_06/req_11
safe < the with-changes plan payment) is solid and already matches how
`code/simulate.py`'s `safe_amount` binary-search is structured. The
open question this ruling addresses is purely **reserve composition** —
what to subtract from headroom before capping at requested_amount.

**Variants tested (scratch harness built on `decide.build_line_items` +
`reconstruct._category_fallback_chains`, run against all 25 sample rows,
never wired into `code/main.py`/`code/decide.py`/`code/simulate.py`):**

| Variant | Reserve definition | amount matches |
|---|---|---|
| baseline (current production) | existing day-grain `simulate.safe_amount` binary search over the full 90-day horizon with all chains + projected occurrences | 4/25 |
| (a) next-income window | sum of scheduled debits (minus credits) from `request_date` up to the next confirmed/scheduled income date (or 90-day horizon if none) | 4/25 |
| (b) next-income capped at 90d | same as (a) but window end = `min(next_income_date, request_date+90)` | 4/25 |
| (c30/c60/c90) fixed N-day window | sum of all scheduled debits (minus credits) due within the next 30/60/90 days from `request_date` | 4/25 each |
| (a_pv) / (b_pv) | variant (a)/(b) reserve + a monthlyized protected-variable-category add-on (`_category_fallback_chains` raw-90d-total / 3, capped at the raw 90d total) added only for users whose salary chain is `stopped` | 4/25 each |

Every variant reproduces **exactly the same 4/25 baseline matches** —
none flips any of the 21 currently-mismatched requests, and none
regresses the 4 that already match. This is a stronger and more uniform
negative result than R19/R19-addendum (which saw some variants actively
regress to 2-3/25); here every day-0-anchored reserve-composition variant
is net-neutral, meaning the discrepancy is not explained by *which window*
of obligations gets reserved, nor by adding a protected-variable-category
top-up scaled to a monthly or 90-day figure.

**Diagnostic detail (full per-request comparison table, all variants vs
gold, computed but reproducible via a short harness over
`dataset/sample_requests.csv` + `decide.build_line_items` +
`reconstruct._category_fallback_chains` — not persisted as a script per
the "no scratch files left behind" convention used in prior R19 sessions):**
for every mismatching request, ALL reserve-composition variants (a, b,
c30/60/90, a_pv, b_pv) moved TOGETHER in the same direction relative to
gold — e.g. request_05 gold=737 vs baseline=12269.74; variants a/c90/b_pv
all land at 12269.74 or 15488 (never closer to 737), while c30/c60 land at
15488 (further away). request_10 gold=12700 vs baseline=256747.74 and
every variant stays at 256747.74 or worse (266700). This "moves together"
pattern across structurally different windowing choices is itself
evidence the amount_safe_to_pay gap is NOT primarily a reserve-*window*
problem (how far forward to look) or a reserve-*category* problem (which
spend categories to include) — R19/R19-addendum already ruled out
category composition; R20 now additionally rules out reserve *window*
choice (next-income anchoring, fixed 30/60/90-day windows). The remaining
lever most likely sits in how the underlying `build_line_items` (or
FX/settlement date handling, or the treatment of already-scheduled
one-off events like bonuses/pending debits inside the 90-day chain
projection) values the events that DO fall inside any of these windows —
not in the window boundary itself.

**Decision: R20 is NOT adopted.** No production file (`code/decide.py`,
`code/simulate.py`, `code/reconstruct.py`, `code/main.py`) was modified;
`git status` shows the same pre-existing untracked/modified files as
before this session, with zero diff introduced. `44/44` unittest suite,
`code/validate.py --output output.csv --requests dataset/requests.csv`
(conformant), and `evaluation/backtest.py` were all re-verified unchanged
after testing (`amount 4/25`, `status 17/25`, `method 18/25`, `plan
15/25`, `earliest 11/25`, `spending 22/25`, `explanation 10/25` — byte-
identical to the pre-R20 baseline). The scratch comparison script used to
produce the table above was deleted after use (not committed) per the
"leave the tree clean on a negative result" convention from R19.

## R21. earliest_date/payment_plan/explanation mismatches are downstream of the unsolved amount_safe_to_pay gap — TESTED, NOT ADOPTED (2026-09-12, follow-up session)

**Task:** mine gold conventions to independently improve
`earliest_date_for_full_payment` (11/25), `payment_plan` (15/25), and
`decision_explanation` (10/25), on the theory that each field's own
construction logic (simulation window, plan-shape rules, template
wording) has bugs separate from the already-diagnosed
`amount_safe_to_pay` gap (R19/R19-addendum/R20, still 4/25 and NOT
resolved by any tested reserve/window/category variant).

**Method:** built a request-by-request coincidence table (via
`evaluation/backtest.py`'s mismatch list) cross-referencing every
mismatching `request_id` per field against the set of `request_id`s that
already mismatch on `amount_safe_to_pay`.

**Finding (conclusive, all 25 samples checked):**
- `earliest_date_for_full_payment`: 14/14 mismatching requests also
  mismatch on `amount_safe_to_pay`. Zero isolated cases.
- `payment_plan`: 10/10 mismatching requests also mismatch on
  `amount_safe_to_pay`. Zero isolated cases.
- `decision_explanation`: 14/15 mismatching requests also mismatch on
  `amount_safe_to_pay` (the explanation prose embeds the wrong plan/
  amount/date because those upstream fields are wrong — e.g. request_04
  gold plan waits to `2024-06-15` while ours pays in full on
  `2024-06-04` because our `amount_safe_to_pay`/day-0 capacity baseline
  is wrong, which cascades into a wrong `earliest_date_for_full_payment`,
  wrong `payment_plan`, wrong `recommended_payment_method`, and thus a
  structurally different (not just mis-worded) explanation sentence).
  The **one** isolated case, request_09, is a genuine gold-data prose
  inconsistency, not a template bug: request_01 and request_09 are both
  `affordable_now`/`full_payment` with an otherwise-identical situation,
  yet gold phrases them differently — request_01: "This leaves at least
  ZAR 18,000 available over the next 90 days." (matches our template
  exactly) vs request_09: "This keeps the EUR 600 minimum available over
  the next 90 days." (different wording, same meaning). Our fixed
  R18a-confirmed template already reproduces the request_01 form
  correctly; there is no discoverable second-template rule that would
  make request_09 match without a special case keyed to a single
  request_id, which is overfitting per the adopt-only-if-it-generalizes
  standard used throughout this rulings log.

**Other convention checks performed (all came back non-actionable):**
- `payment_plan` shape: verified full_payment→single entry,
  partial→exactly 2 entries summing to the requested amount,
  installments→N equal entries at the payment option's own cadence,
  wait→single full entry at `earliest_date_for_full_payment`. Every
  currently-produced plan (when the underlying method/amount/date is
  right) already has the correct SHAPE; every mismatch is a
  value-only mismatch (wrong date or wrong amount), not a
  shape/structure bug. No shape-level fix is available or needed.
- request_12 "oddity" flagged in the task brief (installments chosen
  over full_payment despite full being payable today) is NOT a bug: the
  user's `payment_methods_user_will_consider` = `partial_payment|
  installments` (no `full_payment`), so `_build_full_payment_candidates`
  in `code/decide.py` correctly excludes full_payment via the
  `methods_accept` eligibility filter (checked at the top of that
  function and of `_build_wait_candidate`/`_build_installment_candidates`
  — all three gate on `methods_accept` already). Our output for
  request_12 is an EXACT MATCH on every field already; T9-style
  preference-exclusion handling is confirmed correct on this sample,
  not a gap.
- `earliest_date_for_full_payment` search-range/day convention (R18b:
  capped at `desired_completion_date`) remains correct in isolation —
  traced several mismatching requests (03/04/08/13/18/23) where gold's
  earliest date is a **payday** (15th-of-month salary credit) rather
  than request_date, confirming the existing "search for first day the
  FULL amount is safe with no changes" semantic (not "with the plan's
  changes") is the right convention; the mismatch is that OUR simulator
  finds an earlier day safe than gold does, because our day-0 capacity/
  balance model (the `amount_safe_to_pay` engine) already disagrees with
  gold before the earliest-date search even runs.

**Conclusion:** the three target fields have no independent bugs to fix
via convention-mining — their own construction logic (window, shape,
template) is already sample-confirmed correct (R18a/R18b) and each
remaining mismatch is a downstream symptom of the still-open
`amount_safe_to_pay` baseline discrepancy (day-0 capacity model, per
R19/R19-addendum/R20's own "next hypothesis: a valuation question, not
composition" lead). **Decision: R21 is a negative/diagnostic result —
NOT adopted, no code changed.** `code/decide.py`, `code/simulate.py`,
`code/reconstruct.py`, `code/main.py` are byte-identical to the
pre-session state; `44/44` unittest suite and
`evaluation/backtest.py` are unchanged (`amount 4/25`, `status 17/25`,
`method 18/25`, `plan 15/25`, `earliest 11/25`, `spending 22/25`,
`explanation 10/25`) — verified via `python3 -m unittest discover -s
tests` and `python3 evaluation/backtest.py` after this session's
diagnostic-only scratch work (no scratch files persisted).

**Leads for the next iteration:** do NOT attack earliest_date/
payment_plan/decision_explanation directly again without first re-
attacking `amount_safe_to_pay`'s day-0 capacity model per R20's own
final lead (a *valuation* question — FX/settlement convention on
individual reserved line items, partial credit for one-off salary-
category bonus/arrears rows, or a probabilistic/partial treatment of
pending debits) — fixing that one field is very likely to raise all
four remaining low-scoring fields simultaneously, since 14/14, 10/10,
and 14/15 of their respective mismatches are already proven to be
downstream of it.

**Leads for the next iteration:** since R19 (category composition) and
R20 (reserve window) are both now ruled out as the source of the 21/25
`amount_safe_to_pay` misses, the next hypothesis to test is a *valuation*
question rather than a *composition* question — e.g. whether individual
line items inside the reserved window should be valued at their
FX-converted home-currency amount using a different settlement
convention, whether one-off salary-category credits (bonus/arrears,
R17-V3) that are currently excluded from chains should partially count
toward available balance in the no-changes baseline, or whether pending
debits (R9) are being reserved at their full amount when gold expects a
partial/probabilistic treatment. A per-request "which single line item,
if removed or revalued, flips our figure to gold" search (rather than
another reserve-window/category sweep) is the recommended next probe.

## R22. Amount-irregular protected chains + protected-variable-category forward
streams (day-0 reserve, skip-past-due) — request_05 NAILED IN ISOLATION,
NET REGRESSION ACROSS 25 — TESTED, NOT ADOPTED (2026-09-12)

**Hypothesis (from the 2026-09-12T21:05:00Z orchestrator hands-on
decomposition of request_05, see `log.txt`):** `amount_safe_to_pay`'s
reserve = every recurring chain's future occurrences in [request_date+1,
request_date+90] PLUS two structures neither R5 nor R19 captured, both
restricted to `expense_categories_to_protect` categories only (per R6 —
protected essentials are reserved regardless of amount stability;
non-protected variable spend is not synthesized into a reserve, which is
why R19's blanket-category variants regressed while this one is
category-scoped):

1. **Amount-irregular protected chains:** date-regular (interval stable
   ±3d, ≥3 settled occurrences / ≥2 gaps) chains that R5's ±10%
   amount-stability gate rejects (e.g. user_05's monthly "Therapy
   appointment", 632.59–777.27, a >20% swing) are still reserved by gold,
   projected forward at cadence using the chain's LAST settled amount
   (not modal, not average).
2. **Protected-variable-category streams:** categories with no
   description-level chain at all (many different descriptions per
   category, e.g. groceries: "Supermarket basket", "Weekly produce
   market", "Fresh food shop", ...) are projected forward from the last
   occurrence at the prior-90-day MEDIAN gap, at the prior-90-day AVERAGE
   amount per occurrence.
3. **Skip past-due, day-0 anchor:** an occurrence whose projected date
   has already passed request_date is NOT counted retroactively (e.g.
   user_05's "Fuel refill" chain last occurred 2025-08-06 + 70d interval
   = 2025-10-15, which is BEFORE request_05's 2025-11-06 request_date —
   only the next FUTURE occurrence, day+48, counts). Both new structures
   and R5's existing chains follow this rule uniformly (existing chain
   projection in `decide._project_future_occurrences` already only walks
   forward from the last occurrence, so this was already implicitly true
   there; the new structures had to replicate it explicitly).

**request_05 decomposition (exact, verified against gold=737):** balance
46,475.10 − min_balance 13,100.00 − reserve 32,638.41 = 736.69 (gold:
737, a 0.31 rounding-scale delta — NOT an exact match but the closest
this ruling log has ever gotten). Reserve breakdown: Apartment rent
transfer (regular chain, 3 occ × 4,972 = 14,916.00) + Vehicle loan
payment (3 × 968 = 2,904.00) + Cloud storage plan (3 × 113.30 = 339.90)
+ Dependent care payment (3 × 840.40 = 2,521.20) + Fuel refill
(irregular chain, only 1 FUTURE occurrence counted after skipping the
past-due day-−22 one, 1 × 424.26 = 424.26) + groceries
(protected-variable stream, 13 × 720.46average ≈ 9,365.94, computed via
median-gap/average-amount over the prior 90 days) + Therapy appointment
(amount-irregular protected chain, LAST amount 722.37 × 3 occurrences =
2,167.11) = 32,638.41.

**Implementation (built as `reconstruct.protected_irregular_chains` +
`reconstruct.protected_variable_streams` + `decide._project_r22_extra_streams`,
wired into `decide.build_line_items` via an optional `protect_categories`
kwarg and `decide_request` passing `profile.expense_categories_to_protect`):**
tested end-to-end through the REAL day-grain simulator
(`simulate.safe_amount`/`simulate.simulate`), not a closed-form scalar
subtraction (a closed-form headroom-arithmetic variant was tried FIRST
and was much worse — 0-3/25 — because it double-counts income/credits and
ignores same-day netting that the simulator already handles correctly;
routing through the existing simulator was the fix that got request_05 to
within 0.31 of gold).

**Full 25-sample backtest result (with R22 wired into
`decide.decide_request`):**

| Field | Baseline (R22 NOT wired) | With R22 wired |
|---|---|---|
| amount_safe_to_pay | 4/25 | **2/25** (regression) |
| affordability_status | 17/25 | 16/25 (regression) |
| recommended_payment_method | 18/25 | 17/25 (regression) |
| payment_plan | 15/25 | 15/25 |
| earliest_date_for_full_payment | 11/25 | 11/25 |
| spending_changes_needed | 22/25 | 21/25 (regression) |
| decision_explanation | 10/25 | 9/25 (regression) |

Per-request diagnostic (comparing baseline vs R22 exact-match status
against gold, all 25 requests): request_05 improved from 12,269.74 (way
off) to 736.69 (within 0.31, still not an exact match) — a near-miss, not
a win. Two PREVIOUSLY-EXACT matches REGRESSED: request_01 (base=25256
exact vs gold 25256; R22=19172.93, broken by the new
`__var__:groceries`-style stream now firing where R5's existing chains
already covered the true cadence, over-reserving) and request_12
(base=65164 exact; R22=61459.13, same over-reservation pattern). Every
other of the 21 already-mismatching requests moved (mostly closer, a few
farther) but NONE crossed into an exact match — the 0.31-scale rounding
gap on request_05 recurs on nearly every other request too (a
systematic near-miss pattern, not noise), suggesting either (a) gold
applies a slightly different rounding/truncation convention on the final
reserve sum that this implementation does not replicate, or (b) one of
the three structures (irregular-chain last-amount, variable-stream
median/average, or the ≥3-occurrence gate) is subtly mis-specified for
a subset of users even though it is exactly right for user_05.

**Decision: R22 is NOT adopted.** Net regression on 5 of 7 backtest
fields is disqualifying per the adopt-only-if-it-improves-without-
regressing standard used throughout this log — reverted immediately
after measurement. `code/decide.py`'s `decide_request` and
`build_line_items` call site are back to the pre-session baseline (no
`protect_categories` kwarg passed); `44/44` unittest suite,
`python3 code/main.py --sample`/`--full`, and
`code/validate.py --output output.csv --requests dataset/requests.csv`
(conformant) were all re-verified clean after the revert. The new
helper functions (`reconstruct.protected_irregular_chains`,
`reconstruct.protected_variable_streams`, `reconstruct.ProtectedVariableStream`,
`decide._project_r22_extra_streams`, and `build_line_items`'s optional
`protect_categories` parameter) are left DEFINED BUT UNWIRED in
`reconstruct.py`/`decide.py` — same treatment R19 gave
`_category_fallback_chains` — as prior art for the next iteration,
since they are the closest working approximation of gold's true formula
found so far (0.31 off on the one sample checked in isolation) and
implement the (validated-in-principle) skip-past-due + day-0 + last-
amount + median-cadence conventions cleanly if a future session finds
the missing rounding/valuation detail.

**Leads for the next iteration:** (1) the 0.31-scale near-miss recurring
across nearly every request (not just request_05) points at a
consistent SMALL discrepancy in how one component is valued/rounded —
audit whether gold uses the exact prior-90-day AVERAGE for variable
streams or some other central tendency (mode of a coarser bucket,
trimmed mean excluding the single highest/lowest occurrence), and
whether the median-gap computation should round differently (floor vs
round-half-even) for even-count occurrence series. (2) request_01/
request_12 regressing shows the new structures must NOT fire when R5's
existing description-keyed chains (even amount-STABLE ones) already
cover that category's true cadence — the current implementation only
excludes CLAIMED DESCRIPTIONS, not claimed CATEGORIES, from the
variable-stream aggregation; a category-level exclusion (skip the whole
category fallback if ANY chain already exists for that category, not
just that exact description) is untested and may fix both regressions
without hurting request_05 (groceries in user_05 genuinely has zero
existing chains, so this refinement would be additive there). (3) test
category-level exclusion + trimmed-mean/rounding variants together
across all 25 before re-wiring — do not re-attempt a partial fix
without a full-25 backtest re-run given how fragile the affordability_status/
spending_changes/explanation fields are to `amount_safe_to_pay` shifts
(R21 already proved these are downstream-coupled).

**R22 addendum: category-level exclusion tested, STILL NOT ADOPTED
(2026-09-12, follow-up session).** Implemented lead (2) exactly as
specified: `reconstruct.protected_irregular_chains` now builds
`covered_categories` from ALL existing R5 chains for the user (not just
claimed descriptions) and skips any protected category already covered
by a regular chain before considering it for the amount-irregular
variant; `reconstruct.protected_variable_streams` now excludes a
category if it is covered by EITHER an existing R5 chain OR a newly
built irregular chain (`covered_categories = existing ∪ irregular`,
checked per `(user_id, category)` — previously it only checked claimed
*descriptions*, which is why request_01/request_12's groceries/etc.
categories double-reserved even though those categories had no
description-level match). Re-wired `decide.decide_request` to pass
`protect_categories=set(profile.expense_categories_to_protect)` into
`build_line_items` and re-ran the full 25-sample backtest.

**Result: still a net regression, NOT adopted.** `amount_safe_to_pay`
3/25 (down from baseline 4/25) — WORSE than the original R22 attempt's
already-regressive 2/25 mismeasurement in one sub-metric sense but still
below baseline. Root cause identified via direct diagnosis (not
speculation): request_01 (user_01) and request_05 (user_05) have
*structurally identical* groceries signatures — 13 settled occurrences
in the prior 90 days, stable 7-day cadence, ~13 different descriptions,
zero existing R5/irregular chain coverage for that category — yet gold
clearly reserves groceries forward for user_05 (R22's original
decomposition nailed request_05 to within 0.31) while gold's
`amount_safe_to_pay` for request_01 (25256, exact match at baseline) is
fully explained WITHOUT any groceries reserve at all: the baseline
(un-wired) simulator already returns exactly 25256, and the ONLY way to
reproduce that is to omit the synthetic groceries stream entirely for
user_01. Category-level exclusion is therefore not the missing
variable — the bug is not mis-attribution of chain coverage; two users
with indistinguishable groceries cadence/count data get opposite
treatment in gold. This means the real gating condition R22 needs is
something the current per-category cadence/count signature does not
capture (candidates for a future session: maybe it's gated by whether
the category is *also* in the user's `expense_categories_user_is_willing
_to_reduce`/`_to_stop` lists — i.e. gold may only synthesize a
protected-variable stream for a category when NO adjustable/stoppable
category exists to soak up the difference; or maybe it's gated by
absolute account size/headroom margin — request_01's uncapped headroom
without the stream, 29,255.65, is already far above the requested
25,256, so gold may simply not bother projecting further reserves once
the request is trivially affordable, whereas request_05's tighter margin
forces gold to account for every category; or the true rule may involve
the number of *distinct descriptions* per category, a per-user total
protected-category count, or something in `expense_categories_to_protect`
ordering/precedence not yet inspected).

**Decision: R22 remains NOT adopted after this follow-up.** Reverted the
`decide.py` wiring (`decide_request`'s `build_line_items` call no longer
passes `protect_categories`) immediately after measurement; the improved
category-level exclusion logic in `reconstruct.protected_irregular_chains`/
`reconstruct.protected_variable_streams` is LEFT IN PLACE (unwired) as an
improvement over the prior description-level exclusion, since it is
provably more correct in isolation (it no longer double-reserves a
category already covered by an existing chain) even though it does not
resolve the net-regression verdict. Verified after revert: `44/44`
unittest suite (`python3 -m unittest discover -s tests`), backtest fields
restored to the pre-session baseline exactly (`amount 4/25, status 17/25,
method 18/25, plan 15/25, earliest 11/25, spending 22/25, explanation
10/25`), `python3 code/main.py --sample`/`--full` regenerated, and
`python3 code/validate.py --output output.csv --requests
dataset/requests.csv` reports conformant.

**Leads for the next iteration:** do not re-attempt category-level
exclusion again without first resolving the request_01-vs-request_05
contradiction above — pull the FULL profile diff between user_01 and
user_05 (all list fields: protect/willing_to_stop/willing_to_reduce,
payment method preferences, priorities) and the full headroom margin at
each candidate day, not just the groceries slice, to find what
distinguishes "gold reserves this category" from "gold does not."

## R23. Horizon-to-next-income reserve model (income scheduled-only,
debit + irregular + protected-variable streams, request_14 sweep) —
TESTED, NOT ADOPTED (2026-09-12)

**Hypothesis (from the 2026-09-12T21:55:00Z log entry, "RESERVE HORIZON =
NEXT INCOME cracked"):** `amount_safe_to_pay`'s reserve horizon is not
always the full 90 days — it is `min(90, days until the next
confirmed/projected income event)`. Reserve composition inside that
horizon = R5 debit chains (cadence-projected) + income credits at their
**scheduled/confirmed rows only** (never cadence-projected, since
`Next confirmed salary`-style chains carry `modal_interval_days=0` and
inventing cadence for them was already shown to 78x-over-project) +
R22's amount-irregular protected chains (last-amount cadence) + R22's
protected-variable-category streams (median-gap/average-amount),
restricted to `expense_categories_to_protect`. Two isolated probes from
the prior session supported this: request_01 (salary day 12) hit cap
33,179.08 → safe = min(25256, 33179) = 25256 **exact**; request_05 (no
income, horizon 90) landed at 736.69 vs gold 737 (0.31 rounding delta) —
both already-known R22 near-misses, now additionally explained by a
*shorter* horizon for salaried users.

**Open item from the prior session — request_14 (income days ~13/44/75)
resolved:** our cap was 993.17 vs gold's implied reserve giving 597.74
(target reserve ≈ 1134.00, our reserve ≈ 738.57 when the horizon is cut
at day 13 inclusive of the next income date). Swept every hypothesis
listed in the task brief:

| Hypothesis | Test | Result |
|---|---|---|
| (a) horizon inclusive of income day (`horizon_days = offset+1`, so the reserve simulation runs through and including the payday) | `horizon_extra` sweep {-1, 0, +1} around the "+1 inclusive" base, both with income-chains scheduled-only and income-chains cadence-projected-for-horizon-determination-only | No effect on request_14 in isolation (993.17 unchanged across all three offsets) and net-neutral on the full 25-sample set (always 4/25) |
| (b) income chains projected at cadence for HORIZON DETERMINATION even though the reserve itself only counts scheduled rows (i.e. two different treatments of the same chain: use projected-cadence date to find "next income" but never inject a projected income row into the ledger) | Implemented `next_income_horizon()` using cadence projection (chain.modal_interval_days > 0) for user_14's `Payroll before leave`/`Payroll after returning from leave` chains (31-day cadence) | Correctly finds day 13 (2025-08-17) as next income for user_14; matches the task brief's stated day. Full-25: still 4/25 (net neutral — same requests match as scheduled-only credit horizon) |
| (c) pending-debit reservation on top of (a)/(b) | Not separately probed — user_14 has no pending debits in the ledger for this window (verified directly: no `pending` rows fall in [request_date, day 13]) | N/A, ruled out by data |
| (d) groceries treated as therapy-type irregular/variable stream despite already forming an R5 chain (`Neighbourhood grocer`, 42-day cadence, 2 occurrences) | Tried BOTH (i) leaving groceries as the existing R5 chain only (baseline treatment) and (ii) additionally synthesizing a protected-variable stream from the OTHER 13 groceries-category rows not claimed by that chain (median gap 7d, avg amount ≈107-110) | (i) alone: our reserve 738.57, headroom 993.17 (matches the already-known 993.17 baseline exactly — confirms `Neighbourhood grocer`'s existing chain is the only groceries structure firing). (ii) added ≈220-330 more reserve (1-3 weekly occurrences at ~107-110 each depending on exact horizon-end date), landing our figure at headroom ≈663-773 — CLOSER to gold's 597.74 implied reserve but still not exact, and this variant regresses other requests (adding a second groceries structure on top of an existing chain reproduces R22's original over-reservation bug for users where a chain already covers the category) |
| (e) fuel/past-due occurrences counted retroactively (shifting phase) | Verified `Fuel refill`'s chain last occurrence + interval already lands INSIDE the window (2025-08-11, day 8) for request_14 — no past-due occurrence exists to test the skip-vs-count distinction on this specific request; this hypothesis is moot for request_14 specifically | N/A for this request |

**Full 25-sample backtest (best variant — horizon = next projected income
day inclusive, credit chains scheduled-only in the reserve valuation,
debit chains + R22 irregular/variable streams unchanged):**

| Field (all measured via a scratch harness reusing `decide.build_line_items`/`reconstruct.protected_irregular_chains`/`protected_variable_streams`, never wired into `code/decide.py`/`code/main.py`) | Baseline (R23 NOT wired) | R23 best variant |
|---|---|---|
| amount_safe_to_pay | 4/25 | 4/25 (net neutral — SAME 4 requests match: request_01, request_09, request_12, request_16; zero flips either direction across every horizon-offset {-1,0,+1} × credit-treatment {scheduled-only, cadence} combination tried) |

Every combination of (horizon inclusive/exclusive of income day) ×
(income credits scheduled-only vs cadence-projected-for-horizon-only) ×
(groceries as existing-chain-only vs existing-chain-plus-synthetic-var-
stream) landed at either 4/25 (net neutral) or worse (adding the second
groceries structure). request_14 itself never reached an exact match in
any variant (closest: ≈663-773 headroom vs gold's implied 597.74,
narrowed from the original 993.17 gap but not closed) — the remaining
~65-175 unit-scale gap on this one request is the same order of
magnitude as the "0.31-scale near-miss" pattern R22 already flagged
across nearly every request, reinforcing that leg's diagnosis: this
looks like a rounding/central-tendency or an additional un-identified
small reserve component, not a structural horizon-window bug.

**Decision: R23 is NOT adopted** — it does not clear the 15/25
amount-match adoption bar (in fact it never moves off the existing 4/25
baseline at all), so per the adopt-only-if-it-improves-without-
regressing standard used throughout this log, no production code was
changed. `code/decide.py`, `code/simulate.py`, `code/reconstruct.py`,
`code/main.py` remain byte-identical to the pre-session baseline (only
the scratch harness — deleted after use, not committed, per the R19/R20
"leave the tree clean on a negative result" convention — was used for
every variant above). Re-verified after the sweep: `44/44` unittest
suite, `python3 code/main.py --sample`/`--full` regenerated,
`python3 code/validate.py --output output.csv --requests
dataset/requests.csv` reports conformant, and `evaluation/backtest.py`
is byte-identical to the pre-session baseline (`amount 4/25, status
17/25, method 18/25, plan 15/25, earliest 11/25, spending 22/25,
explanation 10/25`).

**Leads for the next iteration:** the horizon-to-next-income concept is
directionally consistent with two isolated near-exact probes
(request_01 exact, request_05 within 0.31) but adds ZERO net
measurable improvement once swept against the full 25-sample set —
every one of the 21 already-mismatching requests that was re-checked
either didn't move or moved by the same small residual amount as the
already-diagnosed R22 near-miss pattern. This suggests the true
missing piece is NOT the reserve window (R20 already ruled this out
for the flat-90-day case; R23 now additionally rules it out for the
next-income-anchored case) — it is very likely the SAME small
valuation/rounding detail R21/R22 already flagged (which central
tendency to use for variable-stream amounts; whether the median-gap
calc should floor/round differently on even-count series; whether a
still-undiscovered small reserve component such as a partial-day
proration or an additional protected-category class is missing). Do
not re-attempt a pure horizon-window variant again without first
running the R21/R22-recommended "which single line item, if removed or
revalued, flips our figure to gold" per-request probe on ALL 21
mismatching requests (not just request_01/05/14) to find that
remaining valuation detail.

