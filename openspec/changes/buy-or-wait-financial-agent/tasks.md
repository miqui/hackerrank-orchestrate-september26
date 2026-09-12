## 1. Scaffold & data access

- [x] 1.1 Create `code/` package layout: `main.py`, `reconstruct.py`, `evidence.py`, `simulate.py`, `decide.py`, `validate.py`, `prompts/`, `cache/`
- [x] 1.2 Loaders for all 9 CSVs with typed parsing (dates, decimals, enums) and FX conversion helper (rate match by date+pair, nearest-preceding fallback)
- [x] 1.3 `evaluation/` folder + `evaluation/usage_report.md` template; token-usage recorder wired into every LLM call; `code/models_config.json` with model names + per-token prices; usage report GENERATED from config + call log (real + cached call counts both logged)

## 2. Evidence interpretation (LLM, cached)

- [x] 2.1 Image extraction: 16 PNGs → JSON `{amount, currency, date, doc_type}`; link via `images.csv` to blank-amount events; cache to `code/cache/`
- [x] 2.2 Message classification: structured-output calls over 215 messages → `{status: confirm|cancel|amend|delay|info, effective_date, new_amount, related_event_id, confidence}`; untrusted-content system prompt; cache; escalate to mid-tier only on invalid JSON/low confidence/sanity-check failure
- [x] 2.3 Evidence merge per R17: handle all 7 linked-chain shapes (refund-netting, cancelled-dup, pending-debit-count, failed-retry, valuation-ignore, sale-cash-in), internal-transfer net-zero, salary description traps (one-off credits, Final-payroll stop signal, freelancer patterns); message parser emits fixed enum `{intent, amount, currency, date, pct}` with ~15 intents, applied deterministically

## 3. Reconstruction & simulation

- [x] 3.1 Cash-relevance filter: exclude failed/cancelled/pending credits/unrealized; reserve pending debits; home-currency normalization
- [x] 3.2 Recurrence detection per ruling R5 (≥2 settled occurrences, same category+type, interval ±3d of modal gap, amount ±10%) producing forecast schedules; event `flexibility` + profile categories + `minimum_allowed_amount` govern changes (R6/R11)
- [x] 3.3 Day-grain 90-day simulator: `simulate(state, payments) → (ok, first_breach_day, min_balance_day)`
- [x] 3.4 Safe-amount binary search + `earliest_date_for_full_payment` sweep (pre-spending-change baseline)
- [ ] 3.5 Phrasing-invariance test: sample 10 request_text variants across the 5 canonical intents (incl. `request_117` non-English) asserting identical numeric outputs for identical state
- [ ] 3.6 FX join + hardening per R16: exact-date match for all 140 foreign events, non-inversion assertion (listed pairs only), settlement-date conversion for 8 scheduled foreign income rows, zero-fallback unit test, conversion-after-extraction for event_7307

## 4. Decision engine

- [ ] 4.1 Eligibility filters (accepted methods, `allows_partial_payment`, `max_installment_months`, option preference match)
- [ ] 4.2 Candidate enumeration (full/partial/installment options/wait × with/without spending changes) + lexicographic 6-rule ranking
- [ ] 4.3 Output synthesis: all 8 fields, formats, status/method/plan/date invariants; `decision_explanation` grounded in actual numbers

## 5. Validation & iteration loop

- [x] 5.1 Conformance validator: schema, enums, bounds, chronology, arithmetic, partial/installment rules, date-status consistency; non-zero exit on violation
- [x] 5.2 Sample backtest on 25 rows → per-field accuracy report; fix divergences (iterate ≥2 rounds); FIRST confirm rulings R4 (salary projection - superseded by R17 V3 description-keyed rule), R3 (partial-vs-installment), R5 (recurrence - description-keyed per R17), R16 rounding — samples win on any conflict, update evaluation/rulings.md
- [ ] 5.3 Adversarial/edge-case test suite: build fixtures from `evaluation/golden_cases.md` (T1-T10: injected instructions, blank amounts w/o image, pending credits, missing FX, recurrence guard, phrasing invariance, ranking rules, conflict precedence) — each asserted to degrade safely, not silently; any divergence vs the 25 public samples → samples win, spec patched first
- [ ] 5.4 Full run on 250 requests; validate; commit `output.csv`

## 6. Packaging & submission

- [ ] 6.1 README: setup, run instructions, design summary, LLM usage, interpretation choices
- [ ] 6.2 Generate final `evaluation/usage_report.md` from the real run telemetry
- [ ] 6.3 Build `code.zip` (code, prompts, config, README, evaluation/, no secrets, no cache)
- [ ] 6.4 Push final branch + PR via git-workflow.sh (betterleaks scan first); hand submission link and file locations to Miguel