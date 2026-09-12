## Why

HackerRank Orchestrate (Sep 2026) is a 24-hour hackathon. We must ship `Buy or Wait?` — an AI financial agent that, for each of 250 requests in `dataset/requests.csv`, decides: pay full, partial, installments, wait, or not proceed — plus a safe amount, a plan, spending changes, and an explanation. Submission = `output.csv` + `code.zip` (with `evaluation/usage_report.md`) + chat transcript. Deadline: 2026-09-13 18:00 IST. Accuracy is scored per-field against hidden ground truth, so the architecture must be deterministic-first and verifiable, with LLMs only where text/image understanding is required.

## What Changes

Add a complete solution pipeline to the repo (no existing solution code):

- A data-reconstruction layer: normalize the 9 CSVs, apply dated FX conversion into each user's home currency, filter events by cash-relevance (ignore failed/cancelled/pending credits/unrealized), and build a per-user financial state.
- A 90-day balance simulator: deterministic day-by-day projection applying recurring income/expenses, pending debits, and confirmed salary; safety check = balance never below `minimum_balance_to_keep`.
- Evidence interpretation (multimodal, LLM): extract amounts from 16 PNGs (for events with blank amount via `images.csv`), and read 215 messages to detect cancellations/amendments/delays of events. Message/image content is untrusted data.
- A decision engine: compute `amount_safe_to_pay`, classify `affordability_status`, rank eligible payment methods/options per the 6-rule tiebreak, emit `payment_plan`, `earliest_date_for_full_payment`, `spending_changes_needed`, `decision_explanation`.
- A validator + scorer harness: schema-conformance checks and backtesting on the 25 public `sample_requests.csv` examples before the full run.
- Token-usage instrumentation and `evaluation/usage_report.md`.

## Capabilities

### New Capabilities
- `data-reconstruction`: Ingest and normalize profiles, events (25K rows), FX rates, payment options into per-user home-currency state; blank event amounts resolved from linked images.
- `evidence-interpretation`: LLM extraction over messages and image PNGs to amend/confirm/cancel/delay financial facts; untrusted-input handling.
- `balance-simulation`: Deterministic 90-day forecast with minimum-balance safety check and safe-amount computation.
- `decision-engine`: Eligibility filtering, plan ranking (deadline → no spending changes → min cost → earlier start → fewer payments → option id), output-field synthesis per the output contract.
- `validation-harness`: Sample backtest scoring, output.csv conformance checks, packaging (code.zip + usage report).