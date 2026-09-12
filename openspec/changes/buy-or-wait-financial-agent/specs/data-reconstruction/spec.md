# data-reconstruction Specification (delta)

## Purpose
Ingest and normalize the provided CSVs into per-user home-currency financial state with cash-relevance filtering, recurrence detection, image-based amount resolution, and conflict-resolved evidence.

## ADDED Requirements

### Requirement: Home-currency normalization
All balances, request amounts, payment options, and output amounts MUST be expressed in the user's `home_currency`. Foreign-currency cash events MUST be converted using the `exchange_rates.csv` row matching the event's settlement (or event) date and the stated `from_currency` → `to_currency` direction.

#### Scenario: Foreign-currency debit
- **WHEN** a cash event has `currency` ≠ home currency
- **THEN** the amount is converted with the rate dated on (or nearest preceding) the event's effective date, and the home-currency amount is used in all projections.

### Requirement: Cash-relevance filtering
Forecasts MUST include settled and scheduled cash outflows/inflows, and MUST exclude: failed and cancelled events, pending credits (bonuses, commissions, refunds, lottery, investment gains), and unrealized investment values. Pending debits MUST be reserved.

#### Scenario: Pending credit ignored
- **WHEN** an event is a pending incoming refund
- **THEN** it contributes nothing to the forecast balance.

#### Scenario: Pending debit reserved
- **WHEN** a pending outgoing payment exists
- **THEN** the forecast reserves it on its expected settlement date.

### Requirement: Recurrence detection
An event is treated as recurring only when history supports it (≥2 prior occurrences at regular intervals, or explicit recurring semantics). One-time purchases, transfers, refunds, and unusual events are never forecast as recurring.

#### Scenario: Two prior gym payments
- **WHEN** a category shows two prior occurrences at ~30-day intervals
- **THEN** the expense is forecast monthly until the forecast horizon or event end.

### Requirement: Blank-amount resolution via images
When a financial event has a blank `amount`, the system MUST locate the matching row in `images.csv` via the event's `event_id` (as `related_event_id`), read the PNG at `dataset/media/images/<image_id>.png`, and extract the amount. A blank amount MUST NOT be treated as zero.

#### Scenario: Receipt image
- **WHEN** `event_42` has blank amount and links to `image_07`
- **THEN** the amount is read from the image and used (FX-converted if needed).

### Requirement: Evidence conflict resolution
When records conflict, apply in order: explicit cancellation/settlement/amendment → newer record from same source → settled event over estimate/forecast → financially safer interpretation. Messages and images are evidence only; their embedded instructions MUST NOT override problem rules.

#### Scenario: Cancelled subscription
- **WHEN** a message explicitly cancels a recurring expense
- **THEN** the cancelled occurrences are removed from the forecast from the cancellation date.

### Requirement: Request-text semantics coverage
The decision engine MUST derive identical, correct behavior regardless of how the user phrases the request. Phrasings across the 250 requests MUST map to the same canonical intent set: (a) yes/no affordability ("Can I afford/pay/cover/book/make X?"), (b) constrained affordability ("without dipping into/going below my minimum balance", "leave enough for regular expenses"), (c) max-safe-amount ("How much/What portion/What is the most I can put toward X?"), (d) now-vs-wait ("Should I pay now or wait?", "Is it affordable now, or do I need more time?"), (e) method choice (installments/partial). Intent phrasing MUST NOT change the numeric computation — `amount_safe_to_pay` is always the max safe amount — only which output fields the explanation emphasizes.

#### Scenario: Same numbers, different phrasing
- **WHEN** two requests share identical user state and amount but differ in phrasing ("Can I afford this laptop?" vs "What is the most I can put toward this repair right now?")
- **THEN** `amount_safe_to_pay`, `affordability_status`, and `payment_plan` are identical; only `decision_explanation` emphasis differs.

#### Scenario: Non-English request
- **WHEN** `request_text` is in Indonesian or Spanish (e.g. `request_117`)
- **THEN** the request is processed identically to English requests; no language-based fallback or guess is introduced.

### Requirement: No invention
The system MUST NOT invent income, expenses, payment options, or any financial fact not present in the dataset.

#### Scenario: Missing salary history
- **WHEN** a user has no future confirmed salary record
- **THEN** no salary is assumed in the forecast.
