# decision-engine Specification (delta)

## Purpose
Eligibility filtering, plan ranking, and output synthesis per the output contract.

## ADDED Requirements

### Requirement: Affordability classification
`affordability_status` MUST be: `affordable_now` when the full amount is safe on `request_date` and the user accepts `full_payment`; `affordable_with_plan` when completable via partial payment, installments, or permitted spending changes; `affordable_later` when safe later within the horizon; `not_affordable` otherwise.

#### Scenario: Installment-viable request
- **WHEN** full payment is unsafe today but an installment option keeps every payment safe
- **THEN** status is `affordable_with_plan` with `recommended_payment_method: installments`.

### Requirement: Eligibility filtering
An immediate method (`full_payment`/`partial_payment`/`installments`) is eligible only if it appears in `payment_methods_user_will_consider`. `partial_payment` additionally requires `allows_partial_payment`, `0 < amount_safe_to_pay < requested_amount`, second payment ≤ `desired_completion_date`, and exactly two plan payments summing to `requested_amount`. `installments` must exactly match a supplied option and respect `max_installment_months`. `wait` is eligible when full payment becomes safe later and the user accepts `full_payment`.

#### Scenario: User refuses installments
- **WHEN** `payment_methods_user_will_consider` excludes installments or `max_installment_months` is blank
- **THEN** no installment option is recommended regardless of safety.

### Requirement: Plan ranking
When multiple eligible plans are safe, rank by: 1) completes by `desired_completion_date`, 2) no spending changes, 3) minimum total payable, 4) earlier start, 5) fewer payments, 6) lowest `payment_option_id`.

#### Scenario: Cheaper plan wins
- **WHEN** two options both finish before the deadline and are safe
- **THEN** the option with lower `total_payable_amount` (incl. financing fee) is chosen.

### Requirement: Spending changes
`spending_changes_needed` MUST reference only flexible, non-protected events in categories the user permits; `stop:` and `reduce_to:` on the same event are mutually exclusive; up to three changes; `none` when unneeded. Changes unlock plans that would otherwise be unsafe or enable earlier completion.

#### Scenario: Reduce entertainment
- **WHEN** pausing a flexible entertainment expense makes an installment plan safe before the deadline
- **THEN** `reduce_to:event_X:<amount>` is emitted with that plan if ranked first.

### Requirement: Output synthesis
Every output row MUST satisfy the exact column order and formats: chronological `YYYY-MM-DD:amount|...` plan or `none`; `earliest_date_for_full_payment = request_date` when `affordable_now`; empty when full payment is never safe; `decision_explanation` grounded in the user's actual numbers.

#### Scenario: Not affordable
- **WHEN** no safe eligible payment exists within the horizon
- **THEN** `not_recommended`, plan `none`, empty earliest date, and a grounded explanation are emitted.
