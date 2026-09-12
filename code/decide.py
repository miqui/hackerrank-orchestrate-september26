"""Decision engine (tasks 4.1-4.3): eligibility filtering, plan ranking, and
output-row synthesis per evaluation/rulings.md and the decision-engine /
balance-simulation specs.

Pure code — NO LLM calls happen anywhere in this module (D1: intelligence
lives in forecast+ranking, not in a model). Stdlib only, Decimal money,
deterministic (no randomness, no wall-clock reads).

Pipeline (per request):
  1. Build a flat list of dated cash-movement "line items" (event_id, date,
     amount, direction, kind, description) from the evidence-applied ledger
     plus projected future recurring occurrences (R4/R5).
  2. Compute amount_safe_to_pay / earliest_date_for_full_payment on the
     BASELINE (no spending changes) per R2.
  3. Enumerate eligible plan candidates (full/partial/installments/wait,
     each with and without spending-change variants) per the eligibility
     filters (R-eligibility) and rank them per R3 / the 6-rule lexicographic
     order.
  4. Synthesize the output row (amounts, plan string, explanation).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal, ROUND_DOWN
from typing import Optional

from loaders import FinancialProfile, PaymentOption, Request
from reconstruct import CashEvent, RecurrenceChain
from simulate import (
    HORIZON_DAYS,
    PaymentEvent,
    SimulationResult,
    earliest_date_for_full_payment,
    safe_amount,
    simulate,
)

# Money-formatting config (R12/R16): 2dp fixed, trailing ".00" dropped when
# the value is a whole number (matches the majority of sample outputs,
# e.g. "122500" vs "620.40"). Exposed as a constant so a backtest harness
# can flip to always-2dp or IDR-specific 0dp without editing logic below.
ROUNDING_ALWAYS_TWO_DP = False
CENT = Decimal("0.01")


# ---------------------------------------------------------------------------
# Flat line-item model (bridges reconstruct.CashEvent + recurrence
# projection + spending changes into simulate.PaymentEvent lists).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LineItem:
    event_id: str
    event_date: date
    amount: Decimal
    direction: str  # 'debit' | 'credit'
    kind: str
    description: str
    projected: bool = False  # True for cadence-projected future occurrences


@dataclass(frozen=True)
class SpendingChange:
    kind: str  # 'stop' | 'reduce_to'
    event_id: str
    description: str
    amount: Optional[Decimal] = None  # target amount for reduce_to
    monthly_saving: Decimal = Decimal("0")


@dataclass
class PlanCandidate:
    method: str  # full_payment | partial_payment | installments | wait
    payments: list  # list[(date, Decimal)], chronological
    completes_by_deadline: bool
    spending_changes: list  # list[SpendingChange]
    total_payable: Decimal
    start_date: date
    num_payments: int
    payment_option_id: Optional[str]


@dataclass
class DecisionResult:
    request_id: str
    amount_safe_to_pay: Decimal
    affordability_status: str
    recommended_payment_method: str
    payment_plan: str
    earliest_date_for_full_payment: str
    spending_changes_needed: str
    decision_explanation: str


# ---------------------------------------------------------------------------
# Formatting helpers (R12/R16)
# ---------------------------------------------------------------------------

def format_money(value: Decimal) -> str:
    q = value.quantize(CENT)
    s = format(q, "f")
    if ROUNDING_ALWAYS_TWO_DP:
        return s
    # R18 (sample-confirmed): trailing zeros are dropped only when the
    # ENTIRE fractional part is zero (620.40 stays "620.40", not "620.4";
    # a whole value like 25256.00 becomes "25256"). Previously this
    # stripped one trailing zero unconditionally which corrupted any
    # amount ending in a single zero cent digit.
    if "." in s:
        whole, frac = s.split(".", 1)
        if frac == "00":
            s = whole
    return s if s not in ("", "-") else "0"


def format_money_display(value: Decimal) -> str:
    """Like format_money but with thousands separators, for prose in
    decision_explanation (R12/R18-sample-confirmed: sample explanations use
    comma-grouped amounts, e.g. 'IDR 15,952,906.67', while payment_plan and
    other machine-read fields stay unformatted)."""
    plain = format_money(value)  # e.g. "25256" or "620.4" or "15952906.67"
    sign = "-" if plain.startswith("-") else ""
    plain = plain.lstrip("-")
    whole, _, frac = plain.partition(".")
    grouped = f"{int(whole):,}"
    if frac:
        if len(frac) == 1:
            frac = frac + "0"
        return f"{sign}{grouped}.{frac}"
    return f"{sign}{grouped}"


def format_date(d: date) -> str:
    return d.isoformat()


def format_date_display(d: date) -> str:
    """Day-Month(name)-Year prose format for decision_explanation
    (R12/R18-sample-confirmed: '15 April 2025', not '2025-04-15')."""
    return f"{d.day} {d.strftime('%B')} {d.year}"


def format_plan(payments: list) -> str:
    if not payments:
        return "none"
    parts = [f"{format_date(d)}:{format_money(a)}" for d, a in sorted(payments, key=lambda p: p[0])]
    return "|".join(parts)


def format_changes(changes: list) -> str:
    if not changes:
        return "none"
    parts = []
    for c in changes:
        if c.kind == "stop":
            parts.append(f"stop:{c.event_id}")
        else:
            parts.append(f"reduce_to:{c.event_id}:{format_money(c.amount)}")
    return "|".join(parts)


# ---------------------------------------------------------------------------
# Line-item construction: ledger rows + cadence-projected future recurrence
# ---------------------------------------------------------------------------

def _ledger_line_items(ledger: list) -> list:
    items = []
    for ce in ledger:
        if ce.home_amount == 0:
            continue
        kind = "pending_debit" if (ce.status == "pending" and ce.direction == "debit") else (
            "income" if ce.direction == "credit" else "recurring_debit"
        )
        items.append(LineItem(
            event_id=ce.event_id,
            event_date=ce.effective_date,
            amount=ce.home_amount,
            direction=ce.direction,
            kind=kind,
            description=ce.description,
        ))
    return items


def _project_future_occurrences(chains: list, horizon_end: date) -> list:
    """Continue recurring chains beyond their last known ledger occurrence
    at modal cadence/modal amount up to `horizon_end` (R4/R5).

    Salary chains project NO further than their already-scheduled
    confirmed_future_dates (already present in the ledger) — no invented
    future paydays (R4/R17-V3). Stopped chains project nothing.
    """
    items = []
    for chain in chains:
        if chain.stopped or not chain.occurrences:
            continue
        if chain.modal_interval_days <= 0:
            # 'Next confirmed salary'-style chains have no settled cadence
            # to project from; their scheduled rows are already ledger line
            # items (confirmed_future_dates already fed reconstruct's
            # ledger), so there is nothing further to invent here (R17-V3).
            continue
        last_date = max(oc.effective_date for oc in chain.occurrences)
        anchor = chain.occurrences[-1]
        cur = last_date + timedelta(days=chain.modal_interval_days)
        while cur <= horizon_end:
            items.append(LineItem(
                event_id=anchor.event_id,
                event_date=cur,
                amount=chain.modal_amount,
                direction=chain.direction,
                kind="income" if chain.direction == "credit" else "recurring_debit",
                description=chain.description,
                projected=True,
            ))
            cur += timedelta(days=chain.modal_interval_days)
    return items


def _project_r22_extra_streams(
    irregular_chains: list,
    variable_streams: list,
    request_date: date,
    horizon_end: date,
) -> list:
    """R22: project amount-irregular protected chains (at their LAST
    occurrence amount) and protected-variable-category synthetic streams
    forward, SKIPPING any past-due occurrence (an occurrence whose next
    projected date already falls before request_date is not counted
    retroactively — only future occurrences in [request_date, horizon_end]
    contribute)."""
    items = []
    for chain in irregular_chains:
        if not chain.occurrences or chain.modal_interval_days <= 0:
            continue
        last_date = max(oc.effective_date for oc in chain.occurrences)
        cur = last_date + timedelta(days=chain.modal_interval_days)
        while cur < request_date:
            cur += timedelta(days=chain.modal_interval_days)
        while cur <= horizon_end:
            items.append(LineItem(
                event_id=chain.occurrences[-1].event_id,
                event_date=cur,
                amount=chain.modal_amount,
                direction=chain.direction,
                kind="income" if chain.direction == "credit" else "recurring_debit",
                description=chain.description,
                projected=True,
            ))
            cur += timedelta(days=chain.modal_interval_days)
    for s in variable_streams:
        cur = s.last_date + timedelta(days=s.interval_days)
        while cur < request_date:
            cur += timedelta(days=s.interval_days)
        while cur <= horizon_end:
            items.append(LineItem(
                event_id=f"__var__:{s.category}",
                event_date=cur,
                amount=s.amount,
                direction="debit",
                kind="recurring_debit",
                description=f"__var__:{s.category}",
                projected=True,
            ))
            cur += timedelta(days=s.interval_days)
    return items


def build_line_items(
    ledger: list, chains: list, request_date: date, horizon_days: int = HORIZON_DAYS,
    protect_categories: Optional[set] = None,
) -> list:
    horizon_end = request_date + timedelta(days=horizon_days - 1)
    items = _ledger_line_items(ledger) + _project_future_occurrences(chains, horizon_end)
    if protect_categories:
        from reconstruct import protected_irregular_chains, protected_variable_streams
        irr_chains = protected_irregular_chains(ledger, chains, protect_categories)
        var_streams = protected_variable_streams(ledger, chains, irr_chains, protect_categories, request_date)
        items += _project_r22_extra_streams(irr_chains, var_streams, request_date, horizon_end)
    return items


def apply_spending_changes(items: list, changes: list, request_date: date) -> list:
    """Zero out (stop) or floor (reduce_to) matching event_id line items
    dated on/after request_date. Past/settled occurrences are untouched —
    changes only affect forward-looking amounts."""
    if not changes:
        return items
    by_id = {c.event_id: c for c in changes}
    out = []
    for it in items:
        c = by_id.get(it.event_id)
        if c is not None and it.event_date >= request_date:
            if c.kind == "stop":
                continue  # drop entirely
            new_amt = c.amount if c.amount is not None else it.amount
            out.append(replace(it, amount=new_amt))
        else:
            out.append(it)
    return out


def to_payment_events(items: list) -> list:
    return [PaymentEvent(it.event_date, it.amount, it.direction, kind=it.kind, label=it.description) for it in items]


# ---------------------------------------------------------------------------
# Spending-change candidate discovery (R6/R11)
# ---------------------------------------------------------------------------

def _numeric_suffix(event_id: str) -> tuple:
    digits = "".join(ch for ch in event_id if ch.isdigit())
    return (int(digits) if digits else 0, event_id)


def discover_spending_change_options(chains: list, profile: FinancialProfile) -> list:
    """One candidate change per eligible recurring chain: prefer 'stop' when
    the category is in expense_categories_user_is_willing_to_stop AND the
    chain flexibility permits stopping; else 'reduce_to' floor when the
    category is in willing_to_reduce AND flexibility permits reducing.
    Protected categories are never touched (R6). Sorted by largest monthly
    saving first, then lowest event_id (R11)."""
    protect = set(profile.expense_categories_to_protect)
    willing_stop = set(profile.expense_categories_user_is_willing_to_stop)
    willing_reduce = set(profile.expense_categories_user_is_willing_to_reduce)

    options = []
    for chain in chains:
        if chain.direction != "debit" or not chain.occurrences:
            continue
        if chain.category in protect:
            continue
        anchor = chain.occurrences[-1]
        can_stop = chain.flexibility in ("stoppable", "reducible_or_stoppable") and chain.category in willing_stop
        can_reduce = chain.flexibility in ("reducible", "reducible_or_stoppable") and chain.category in willing_reduce

        if can_stop:
            saving = chain.modal_amount
            options.append(SpendingChange(
                kind="stop", event_id=anchor.event_id, description=chain.description,
                amount=None, monthly_saving=saving,
            ))
        elif can_reduce:
            floor = chain.minimum_allowed_amount if chain.minimum_allowed_amount is not None else chain.modal_amount * Decimal("0.5")
            if floor >= chain.modal_amount:
                continue
            saving = chain.modal_amount - floor
            options.append(SpendingChange(
                kind="reduce_to", event_id=anchor.event_id, description=chain.description,
                amount=floor, monthly_saving=saving,
            ))

    options.sort(key=lambda c: (-c.monthly_saving, _numeric_suffix(c.event_id)))
    return options


def _passes_full_payment(profile: FinancialProfile, request_date: date, items: list, amount: Decimal) -> bool:
    req_ev = PaymentEvent(request_date, amount, "debit", kind="request_payment")
    result = simulate(profile.current_available_balance, profile.minimum_balance_to_keep, request_date,
                       to_payment_events(items), req_ev)
    return result.ok


def find_minimal_spending_changes_for_full_payment(
    baseline_items: list, changes_options: list, profile: FinancialProfile,
    request_date: date, requested_amount: Decimal, max_changes: int = 3,
) -> Optional[list]:
    """Greedily add changes (largest saving first) until a full payment on
    request_date becomes safe, capped at max_changes (R11: 'apply only
    when they flip a plan's safety'). Returns None if even all allowed
    changes are insufficient."""
    if _passes_full_payment(profile, request_date, baseline_items, requested_amount):
        return []  # already safe; no changes needed

    applied: list = []
    for opt in changes_options:
        if len(applied) >= max_changes:
            break
        applied.append(opt)
        trial_items = apply_spending_changes(baseline_items, applied, request_date)
        if _passes_full_payment(profile, request_date, trial_items, requested_amount):
            return applied
    return None


def _plan_safe(profile: FinancialProfile, request_date: date, items: list, payments: list) -> bool:
    """Safety check for a multi-payment plan (partial/installments): each
    payment applies on its own date, all within the same 90-day window
    starting at request_date."""
    events = to_payment_events(items)
    balance = profile.current_available_balance
    min_bal = profile.minimum_balance_to_keep
    # Feed each payment as its own request_payment-kind event on its date;
    # simulate() only accepts one request_payment, so fold multiple
    # payments into the payment_events list directly with that kind.
    combined = list(events)
    for d, amt in payments:
        combined.append(PaymentEvent(d, amt, "debit", kind="request_payment", label="plan_payment"))
    result = simulate(balance, min_bal, request_date, combined)
    return result.ok


# ---------------------------------------------------------------------------
# Candidate construction
# ---------------------------------------------------------------------------

def _build_full_payment_candidates(
    request: Request, profile: FinancialProfile, baseline_items: list,
    change_options: list, methods_accept: set,
) -> list:
    candidates = []
    if "full_payment" not in methods_accept:
        return candidates

    if _passes_full_payment(profile, request.request_date, baseline_items, request.requested_amount):
        candidates.append(PlanCandidate(
            method="full_payment",
            payments=[(request.request_date, request.requested_amount)],
            completes_by_deadline=request.request_date <= request.desired_completion_date,
            spending_changes=[],
            total_payable=request.requested_amount,
            start_date=request.request_date,
            num_payments=1,
            payment_option_id=None,
        ))
        return candidates  # baseline already safe; spending-change variant adds nothing

    changes = find_minimal_spending_changes_for_full_payment(
        baseline_items, change_options, profile, request.request_date, request.requested_amount,
    )
    if changes:
        candidates.append(PlanCandidate(
            method="full_payment",
            payments=[(request.request_date, request.requested_amount)],
            completes_by_deadline=request.request_date <= request.desired_completion_date,
            spending_changes=changes,
            total_payable=request.requested_amount,
            start_date=request.request_date,
            num_payments=1,
            payment_option_id=None,
        ))
    return candidates


def _find_partial_second_date(profile, request, baseline_items, first_amount, remainder):
    d = request.request_date
    end = request.desired_completion_date
    while d <= end:
        if _plan_safe(profile, request.request_date, baseline_items,
                      [(request.request_date, first_amount), (d, remainder)]):
            return d
        d += timedelta(days=1)
    return None


def _build_partial_candidate(request: Request, profile: FinancialProfile, baseline_items: list,
                              amount_safe: Decimal, methods_accept: set) -> Optional[PlanCandidate]:
    if "partial_payment" not in methods_accept or not request.allows_partial_payment:
        return None
    if not (Decimal("0") < amount_safe < request.requested_amount):
        return None
    remainder = request.requested_amount - amount_safe
    second_date = _find_partial_second_date(profile, request, baseline_items, amount_safe, remainder)
    if second_date is None or second_date > request.desired_completion_date:
        return None
    return PlanCandidate(
        method="partial_payment",
        payments=[(request.request_date, amount_safe), (second_date, remainder)],
        completes_by_deadline=second_date <= request.desired_completion_date,
        spending_changes=[],
        total_payable=request.requested_amount,
        start_date=request.request_date,
        num_payments=2,
        payment_option_id=None,
    )


def _installment_schedule(option: PaymentOption) -> list:
    payments = []
    d = option.first_payment_date
    freq = option.payment_frequency_days or 0
    for i in range(option.number_of_payments):
        payments.append((d, option.payment_amount))
        d = d + timedelta(days=freq) if freq else d
    return payments


def _option_span_months(option: PaymentOption) -> int:
    if option.number_of_payments <= 1 or not option.payment_frequency_days:
        return 0
    total_days = option.payment_frequency_days * (option.number_of_payments - 1)
    # Round up to whole months (30-day months) so a plan exactly matching
    # the user's max is not incorrectly rejected.
    return -(-total_days // 30)


def _build_installment_candidates(
    request: Request, profile: FinancialProfile, baseline_items: list,
    options: list, change_options: list, methods_accept: set,
) -> list:
    candidates = []
    if "installments" not in methods_accept or not profile.max_installment_months:
        return candidates

    for opt in options:
        if opt.payment_method != "installments":
            continue
        if _option_span_months(opt) > profile.max_installment_months:
            continue
        payments = _installment_schedule(opt)
        last_date = payments[-1][0]
        completes = last_date <= request.desired_completion_date

        if _plan_safe(profile, request.request_date, baseline_items, payments):
            candidates.append(PlanCandidate(
                method="installments", payments=payments, completes_by_deadline=completes,
                spending_changes=[], total_payable=opt.total_payable_amount,
                start_date=payments[0][0], num_payments=opt.number_of_payments,
                payment_option_id=opt.payment_option_id,
            ))
            continue

        # Try spending changes to flip safety (T8).
        applied: list = []
        for change in change_options:
            if len(applied) >= 3:
                break
            applied.append(change)
            trial_items = apply_spending_changes(baseline_items, applied, request.request_date)
            if _plan_safe(profile, request.request_date, trial_items, payments):
                candidates.append(PlanCandidate(
                    method="installments", payments=payments, completes_by_deadline=completes,
                    spending_changes=list(applied), total_payable=opt.total_payable_amount,
                    start_date=payments[0][0], num_payments=opt.number_of_payments,
                    payment_option_id=opt.payment_option_id,
                ))
                break
    return candidates


def _build_wait_candidate(request: Request, profile: FinancialProfile, earliest_full: Optional[date],
                           methods_accept: set) -> Optional[PlanCandidate]:
    if earliest_full is None or "full_payment" not in methods_accept:
        return None
    return PlanCandidate(
        method="wait",
        payments=[(earliest_full, request.requested_amount)],
        completes_by_deadline=earliest_full <= request.desired_completion_date,
        spending_changes=[],
        total_payable=request.requested_amount,
        start_date=earliest_full,
        num_payments=1,
        payment_option_id=None,
    )


# ---------------------------------------------------------------------------
# Ranking (R3 / decision-engine spec "Plan ranking")
# ---------------------------------------------------------------------------

def _ranking_key(c: PlanCandidate) -> tuple:
    return (
        0 if c.completes_by_deadline else 1,
        0 if not c.spending_changes else 1,
        c.total_payable,
        c.start_date,
        c.num_payments,
        c.payment_option_id or "",
    )


# ---------------------------------------------------------------------------
# Top-level per-request decision
# ---------------------------------------------------------------------------

def decide_request(
    request: Request,
    profile: FinancialProfile,
    ledger: list,
    chains: list,
    payment_options: list,
) -> DecisionResult:
    methods_accept = set(profile.payment_methods_user_will_consider)
    baseline_items = build_line_items(ledger, chains, request.request_date)

    amount_safe = safe_amount(
        profile.current_available_balance, profile.minimum_balance_to_keep,
        request.request_date, to_payment_events(baseline_items), request.requested_amount,
    )
    # R18 (sample-confirmed): earliest_date_for_full_payment is scanned
    # only within [request_date, desired_completion_date], NOT the full
    # 90-day safety-check horizon. A day that is technically safe but
    # falls after the request's own deadline is not a usable "earliest
    # full payment" recommendation — the sample rows leave the field
    # blank (and classify not_affordable) whenever the first safe day
    # would land after the deadline, even though a later-still-safe day
    # exists inside the 90-day window (request_14: earliest full-payment
    # day found by the unrestricted scan was 2025-10-18, but the deadline
    # is 2025-10-04 and gold's earliest_date_for_full_payment is blank;
    # request_24 is the same pattern with 2026-03-18 vs deadline
    # 2026-02-08). The 90-day simulation window used INSIDE each day's
    # safety check is unchanged — only the set of candidate days searched
    # is capped at the deadline.
    deadline_horizon_days = (request.desired_completion_date - request.request_date).days + 1
    earliest_full = earliest_date_for_full_payment(
        profile.current_available_balance, profile.minimum_balance_to_keep,
        request.request_date, to_payment_events(baseline_items), request.requested_amount,
        search_days=max(deadline_horizon_days, 1),
    )

    change_options = discover_spending_change_options(chains, profile)

    candidates: list = []
    candidates += _build_full_payment_candidates(request, profile, baseline_items, change_options, methods_accept)
    partial = _build_partial_candidate(request, profile, baseline_items, amount_safe, methods_accept)
    if partial:
        candidates.append(partial)
    candidates += _build_installment_candidates(
        request, profile, baseline_items, [o for o in payment_options if o.request_id == request.request_id],
        change_options, methods_accept,
    )
    wait = _build_wait_candidate(request, profile, earliest_full, methods_accept)
    if wait:
        candidates.append(wait)

    full_safe_today = _passes_full_payment(profile, request.request_date, baseline_items, request.requested_amount)
    deadline_candidates = [c for c in candidates if c.completes_by_deadline and c.method != "wait"]

    if full_safe_today and "full_payment" in methods_accept:
        status = "affordable_now"
    elif deadline_candidates:
        status = "affordable_with_plan"
    elif earliest_full is not None:
        status = "affordable_later"
    else:
        status = "not_affordable"

    chosen: Optional[PlanCandidate] = None
    if status == "affordable_now":
        pool = [c for c in candidates if c.completes_by_deadline]
        if pool:
            chosen = min(pool, key=_ranking_key)
    elif status == "affordable_with_plan":
        pool = [c for c in candidates if c.completes_by_deadline]
        if pool:
            chosen = min(pool, key=_ranking_key)
    elif status == "affordable_later":
        wait_pool = [c for c in candidates if c.method == "wait"]
        if wait_pool:
            chosen = min(wait_pool, key=_ranking_key)
        else:
            status = "not_affordable"  # user won't accept full_payment; nothing to recommend

    if chosen is None:
        recommended_method = "not_recommended"
        plan_str = "none"
        changes_str = "none"
    else:
        recommended_method = chosen.method
        plan_str = format_plan(chosen.payments)
        changes_str = format_changes(chosen.spending_changes)

    # Contract: not_affordable rows must have blank earliest_date_for_full_payment
    if status == "not_affordable":
        earliest_full = None

    earliest_field = format_date(earliest_full) if earliest_full is not None else ""

    explanation = _build_explanation(request, profile, status, chosen, amount_safe, earliest_full)

    return DecisionResult(
        request_id=request.request_id,
        amount_safe_to_pay=amount_safe,
        affordability_status=status,
        recommended_payment_method=recommended_method,
        payment_plan=plan_str,
        earliest_date_for_full_payment=earliest_field,
        spending_changes_needed=changes_str,
        decision_explanation=explanation,
    )


# ---------------------------------------------------------------------------
# Explanation synthesis (R12) — 1-2 sentences, 2-3 dominant numeric facts.
# ---------------------------------------------------------------------------

def _headroom_for_plan(profile: FinancialProfile, request: Request, items: list, chosen: PlanCandidate) -> Decimal:
    applied_items = apply_spending_changes(items, chosen.spending_changes, request.request_date) if chosen.spending_changes else items
    events = to_payment_events(applied_items)
    for d, amt in chosen.payments:
        events.append(PaymentEvent(d, amt, "debit", kind="request_payment", label="plan_payment"))
    result = simulate(profile.current_available_balance, profile.minimum_balance_to_keep, request.request_date, events)
    return result.min_balance_day_value


def _change_desc(changes: list) -> str:
    parts = []
    for c in changes:
        if c.kind == "stop":
            parts.append(f"Stop the {c.description.lower()}")
        else:
            parts.append(f"Reduce the {c.description.lower()} to {format_money(c.amount)}")
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return " and ".join([", ".join(parts[:-1]), parts[-1]])


def _build_explanation(request: Request, profile: FinancialProfile, status: str,
                        chosen: Optional[PlanCandidate], amount_safe: Decimal,
                        earliest_full: Optional[date]) -> str:
    cur = profile.home_currency
    amt = format_money_display(request.requested_amount)
    min_bal = format_money_display(profile.minimum_balance_to_keep)

    if status == "not_affordable" or chosen is None:
        return (
            f"Do not make this payment by {format_date_display(request.desired_completion_date)}. "
            f"None of the available options keeps the {cur} {min_bal} minimum protected."
        )

    if chosen.method == "full_payment":
        if chosen.spending_changes:
            desc = _change_desc(chosen.spending_changes)
            return (
                f"{desc}, then pay {cur} {amt} today. "
                f"This leaves at least {cur} {format_money_display(amount_safe)} available."
            )
        return (
            f"Pay {cur} {amt} today. "
            f"This leaves at least {cur} {min_bal} available over the next 90 days."
        )

    if chosen.method == "partial_payment":
        d1, p1 = chosen.payments[0]
        d2, p2 = chosen.payments[1]
        return (
            f"Pay {cur} {format_money_display(p1)} today and the remaining {cur} {format_money_display(p2)} "
            f"on {format_date_display(d2)}. This completes the full request and keeps the {cur} {min_bal} minimum protected."
        )

    if chosen.method == "installments":
        d1, p1 = chosen.payments[0]
        return (
            f"Use {chosen.num_payments} installments of {cur} {format_money_display(p1)}, "
            f"starting {format_date_display(d1)}. This leaves at least {cur} {min_bal} available."
        )

    if chosen.method == "wait":
        d1, p1 = chosen.payments[0]
        return (
            f"Pay {cur} {format_money_display(p1)} in full on {format_date_display(d1)}. "
            f"Paying earlier would take the balance below the {cur} {min_bal} minimum."
        )

    return f"Pay {cur} {amt} per the recommended plan."
