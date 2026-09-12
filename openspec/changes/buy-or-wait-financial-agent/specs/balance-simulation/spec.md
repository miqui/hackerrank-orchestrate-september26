# balance-simulation Specification (delta)

## Purpose
Deterministic 90-day balance forecasting with the minimum-balance safety invariant.

## ADDED Requirements

### Requirement: 90-day forecast
For each request, the system MUST simulate the user's balance day-by-day for 90 days from `request_date`, applying: starting `current_available_balance`, recurring income/expenses on their schedule, scheduled/pending items on their settlement dates, and confirmed salary on settlement date.

#### Scenario: Salary lands on day 30
- **WHEN** the next confirmed salary settles 30 days after request date
- **THEN** the balance increases on that day and safety checks before that day fail if balance dips below minimum.

### Requirement: Safety invariant
A plan is safe only if the projected balance never falls below `minimum_balance_to_keep` at any day in the 90-day horizon, and the plan completes the full request by `desired_completion_date`.

#### Scenario: Dip below minimum
- **WHEN** paying today would breach the minimum balance before the next salary
- **THEN** `amount_safe_to_pay` is reduced to the largest amount that never breaches it.

### Requirement: Safe amount computation
`amount_safe_to_pay` = the largest amount payable on `request_date` (before optional spending changes) such that the 90-day check passes, capped at `requested_amount`, in `[0, requested_amount]`.

#### Scenario: Fully affordable
- **WHEN** paying the full amount keeps the balance above minimum all 90 days
- **THEN** `amount_safe_to_pay` equals `requested_amount`.

### Requirement: Earliest safe full-payment date
`earliest_date_for_full_payment` = the first day in the forecast horizon where a single full payment passes the safety check; empty when no such day exists. It is computed independently of payment-method preferences.

#### Scenario: Safe after payday
- **WHEN** the balance can sustain a full payment starting the day after salary settlement
- **THEN** that date is returned even if the user's chosen plan is installments.
