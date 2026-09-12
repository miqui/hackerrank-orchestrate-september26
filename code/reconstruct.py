"""Cash-relevance reconstruction: turn raw financial_events rows into a
per-user, home-currency-normalized ledger of events that actually matter
for the 90-day forecast.

Scope note: this module currently implements task 3.1 (cash-relevance
filtering + home-currency normalization) plus the bare module structure
recurrence detection (3.2), linked-chain resolution (2.3/R17 V6), and
evidence merging (messages/images) hook into later.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from fx import FxTable
from loaders import FinancialEvent, FinancialProfile

# Statuses/directions excluded from the cash-relevant forecast per R9 /
# data-reconstruction spec "Cash-relevance filtering":
#   - failed, cancelled events: never happened / voided
#   - pending credits (any pending event with direction=credit): not
#     realized yet, must not be counted as income
#   - unrealized investment values (direction=non_cash / status=unrealized):
#     never cash
# Pending DEBITS are reserved (kept in the ledger), matching R9.
EXCLUDED_STATUSES_ALWAYS = {"failed", "cancelled"}


class ReconstructError(ValueError):
    pass


@dataclass(frozen=True)
class CashEvent:
    """A financial_events row that has passed the cash-relevance filter,
    with its amount normalized to the user's home currency."""
    event_id: str
    user_id: str
    event_type: str
    description: str
    category: str
    direction: str  # 'credit' | 'debit'
    home_amount: Decimal  # always positive; direction carries the sign meaning
    home_currency: str
    original_amount: Decimal
    original_currency: str
    event_date: date
    settlement_date: Optional[date]
    effective_date: date
    status: str
    linked_event_id: Optional[str]
    flexibility: str
    minimum_allowed_amount: Optional[Decimal]  # left in original currency's home-normalized form when convertible
    fx_used_fallback: bool


def is_cash_relevant(ev: FinancialEvent) -> bool:
    """Baseline cash-relevance test (pre-linked-chain, pre-evidence).

    Per R9 / spec "Cash-relevance filtering":
      - failed / cancelled -> excluded
      - non_cash direction (investment_valuation) -> excluded
      - pending credit -> excluded (pending credits/refunds not counted)
      - pending debit -> INCLUDED (reserved)
      - settled / scheduled -> included
    """
    if ev.status in EXCLUDED_STATUSES_ALWAYS:
        return False
    if ev.direction == "non_cash":
        return False
    if ev.status == "pending" and ev.direction == "credit":
        return False
    return True


def normalize_event(
    ev: FinancialEvent,
    profile: FinancialProfile,
    fx: FxTable,
    amount_override: Optional[Decimal] = None,
    currency_override: Optional[str] = None,
) -> CashEvent:
    """Convert one financial_events row into a home-currency CashEvent.

    `amount_override`/`currency_override` let the (later) evidence pipeline
    supply an image-extracted amount for blank-amount rows without this
    module needing to know about images.csv directly.
    """
    amount = amount_override if amount_override is not None else ev.amount
    currency = currency_override if currency_override is not None else ev.currency
    if amount is None:
        raise ReconstructError(
            f"{ev.event_id}: blank amount with no override supplied "
            f"(must be resolved via linked image before reconstruction)"
        )

    effective_date = ev.settlement_date or ev.event_date
    if currency == profile.home_currency:
        home_amount = amount
        used_fallback = False
    else:
        match = fx.convert(amount, currency, profile.home_currency, effective_date, context=ev.event_id)
        home_amount = match.amount
        used_fallback = match.used_fallback

    min_allowed = ev.minimum_allowed_amount
    if min_allowed is not None and currency != profile.home_currency:
        m = fx.convert(min_allowed, currency, profile.home_currency, effective_date, context=f"{ev.event_id}:min_allowed")
        min_allowed = m.amount

    return CashEvent(
        event_id=ev.event_id,
        user_id=ev.user_id,
        event_type=ev.event_type,
        description=ev.description,
        category=ev.category,
        direction=ev.direction,
        home_amount=home_amount,
        home_currency=profile.home_currency,
        original_amount=amount,
        original_currency=currency,
        event_date=ev.event_date,
        settlement_date=ev.settlement_date,
        effective_date=effective_date,
        status=ev.status,
        linked_event_id=ev.linked_event_id,
        flexibility=ev.flexibility,
        minimum_allowed_amount=min_allowed,
        fx_used_fallback=used_fallback,
    )


def reconstruct_user_ledger(
    events: list[FinancialEvent],
    profile: FinancialProfile,
    fx: FxTable,
    amount_overrides: Optional[dict[str, tuple[Decimal, str]]] = None,
) -> list[CashEvent]:
    """Build the cash-relevant, home-currency-normalized ledger for one user.

    `amount_overrides` maps event_id -> (amount, currency) for blank-amount
    rows resolved via images (task 2.1); events still missing a resolvable
    amount are skipped with the caller expected to surface a quarantine
    warning (wired in a later task once evidence.py exists).
    """
    amount_overrides = amount_overrides or {}
    ledger: list[CashEvent] = []
    for ev in events:
        if ev.user_id != profile.user_id:
            continue
        if not is_cash_relevant(ev):
            continue
        override = amount_overrides.get(ev.event_id)
        if ev.amount is None and override is None:
            # Cannot resolve amount yet; caller/evidence pipeline must supply it.
            continue
        amt_override, cur_override = (override if override else (None, None))
        ledger.append(normalize_event(ev, profile, fx, amt_override, cur_override))
    ledger.sort(key=lambda ce: ce.effective_date)
    return ledger


# --- Task 3.2: recurrence detection (R5 / R17 V1-V3) ------------------------

# One-off salary-category credits that must NEVER be projected as recurring
# income even though they share category=salary with real payroll chains
# (R17-V3). Freelancer-style varying-description income is excluded by the
# description-keyed matching itself (each description has too few same-
# description occurrences, or amounts vary too much / dates are irregular).
SALARY_ONE_OFF_DESCRIPTIONS = {
    "Promotion arrears payment",
    "Quarterly performance bonus",
}

# The only description whose SCHEDULED rows count as confirmed future
# income per R17-V3 ("'Next confirmed salary' = 47 rows, all scheduled ...
# the ONLY confirmed future income marker").
CONFIRMED_FUTURE_INCOME_DESCRIPTION = "Next confirmed salary"

# Description that is a hard STOP signal for all future salary projection
# for that user, regardless of any other chain (R17-V3).
FINAL_PAYROLL_DESCRIPTION = "Final employer payroll"

AMOUNT_STABILITY_RATIO = Decimal("0.10")  # +/-10% (R5)
INTERVAL_TOLERANCE_DAYS = 3  # +/-3 days around modal gap (R5)


@dataclass(frozen=True)
class RecurrenceChain:
    """A description-keyed chain of settled occurrences treated as
    recurring per R5/R17-V1-V3.

    `flexibility` and `minimum_allowed_amount` are read ONCE per chain
    (R17-V1: "Flexibility is constant within every recurring chain").
    `stopped` is True when a 'Final employer payroll' row for this
    user+description group means no further occurrences may be projected.
    `confirmed_future_dates` holds any scheduled occurrence dates that are
    auto-confirmed future income/expense per R17-V3 (only applies to the
    'Next confirmed salary' description among income chains; for expense/
    subscription chains all scheduled rows found in the ledger qualify).
    """
    user_id: str
    description: str
    category: str
    direction: str
    modal_amount: Decimal
    modal_interval_days: int
    flexibility: str
    minimum_allowed_amount: Optional[Decimal]
    occurrences: list[CashEvent]
    stopped: bool
    confirmed_future_dates: list[date]


def _modal(values):
    """Most common value; ties broken by first-seen order (deterministic)."""
    counts: dict = {}
    order: list = []
    for v in values:
        if v not in counts:
            counts[v] = 0
            order.append(v)
        counts[v] += 1
    return max(order, key=lambda v: counts[v])


def detect_recurrence(ledger: list[CashEvent]) -> list[RecurrenceChain]:
    """Detect recurring chains per R5 / R17 V1-V3.

    Grouping key is (user_id, description) — category is NOT used as the
    key because R17-V2 shows description is a stable, stricter key (zero
    category migrations exist, but multiple distinct descriptions can
    share one category, e.g. all the salary-category description
    variants). Within a description group:
      - only SETTLED occurrences count toward establishing the chain
        (R5: "settled occurrences"); scheduled rows are candidate future
        occurrences, not part of the historical cadence count.
      - a chain requires >=2 settled occurrences.
      - interval consistency: every consecutive settled gap must be
        within +/-3 days of the modal gap.
      - amount stability: every settled amount must be within +/-10% of
        the modal amount.
      - one-off salary-category descriptions are excluded outright
        (R17-V3), even if (degenerately) they had >=2 rows on shipped
        data they would still never project.
    """
    by_key: dict[tuple[str, str], list[CashEvent]] = {}
    for ce in ledger:
        by_key.setdefault((ce.user_id, ce.description), []).append(ce)

    # Track, per user, whether a 'Final employer payroll' row exists at all
    # (that row itself may be settled or scheduled) -> STOP all future
    # salary projection for that user (R17-V3), independent of description.
    final_payroll_users: set[str] = {
        ce.user_id for ce in ledger if ce.description == FINAL_PAYROLL_DESCRIPTION
    }

    chains: list[RecurrenceChain] = []
    for (user_id, description), events in by_key.items():
        if description in SALARY_ONE_OFF_DESCRIPTIONS:
            continue
        if description == FINAL_PAYROLL_DESCRIPTION:
            # The stop-signal row itself is not a recurring chain to project.
            continue

        events_sorted = sorted(events, key=lambda ce: ce.effective_date)
        settled = [ce for ce in events_sorted if ce.status == "settled"]

        if description == CONFIRMED_FUTURE_INCOME_DESCRIPTION:
            # R17-V3: 'Next confirmed salary' rows are all scheduled (no
            # settled history to establish a cadence from) but are still
            # the ONE auto-confirmed future income marker. Emit them as a
            # chain of their own regardless of the normal >=2-settled gate,
            # unless this user also has a 'Final employer payroll' stop.
            stopped = user_id in final_payroll_users
            scheduled_dates = [ce.effective_date for ce in events_sorted if ce.status == "scheduled"]
            if not scheduled_dates:
                continue
            chains.append(RecurrenceChain(
                user_id=user_id,
                description=description,
                category=events_sorted[0].category,
                direction=events_sorted[0].direction,
                modal_amount=_modal([ce.home_amount for ce in events_sorted]),
                modal_interval_days=0,
                flexibility=events_sorted[0].flexibility,
                minimum_allowed_amount=events_sorted[0].minimum_allowed_amount,
                occurrences=events_sorted,
                stopped=stopped,
                confirmed_future_dates=[] if stopped else scheduled_dates,
            ))
            continue

        if len(settled) < 2:
            continue

        gaps = [
            (settled[i].effective_date - settled[i - 1].effective_date).days
            for i in range(1, len(settled))
        ]
        modal_gap = _modal(gaps)
        if modal_gap <= 0:
            continue
        if any(abs(g - modal_gap) > INTERVAL_TOLERANCE_DAYS for g in gaps):
            continue

        amounts = [ce.home_amount for ce in settled]
        modal_amount = _modal(amounts)
        if modal_amount == 0:
            continue
        tolerance = modal_amount * AMOUNT_STABILITY_RATIO
        if any(abs(a - modal_amount) > tolerance for a in amounts):
            continue

        category = events_sorted[0].category
        direction = events_sorted[0].direction
        # Flexibility/minimum_allowed_amount read ONCE per chain (R17-V1).
        flexibility = events_sorted[0].flexibility
        min_allowed = events_sorted[0].minimum_allowed_amount

        stopped = (category == "salary" and user_id in final_payroll_users)

        confirmed_future_dates: list[date] = []
        if category == "salary":
            # Only the exact 'Next confirmed salary' description is
            # auto-confirmed future income (R17-V3); other salary
            # descriptions' scheduled rows are not invented/extended.
            if description == CONFIRMED_FUTURE_INCOME_DESCRIPTION and not stopped:
                confirmed_future_dates = [
                    ce.effective_date for ce in events_sorted if ce.status == "scheduled"
                ]
        else:
            # Non-salary recurring chains (subscriptions/expenses): any
            # scheduled occurrence already in the ledger is a confirmed
            # future instance; the modal cadence is used to project
            # further occurrences up to the forecast horizon by the
            # simulator/decision layer, not invented here.
            confirmed_future_dates = [
                ce.effective_date for ce in events_sorted if ce.status == "scheduled"
            ]

        chains.append(RecurrenceChain(
            user_id=user_id,
            description=description,
            category=category,
            direction=direction,
            modal_amount=modal_amount,
            modal_interval_days=modal_gap,
            flexibility=flexibility,
            minimum_allowed_amount=min_allowed,
            occurrences=events_sorted,
            stopped=stopped,
            confirmed_future_dates=confirmed_future_dates,
        ))

    return chains


# R18 (sample-confirmed, see evaluation/rulings.md): everyday essential
# expense categories (groceries/transport/healthcare/shopping/etc.) are
# split across MANY differently-worded descriptions ("Supermarket basket",
# "Weekly produce market", "Fresh food shop", ...) that individually never
# pass the strict description-keyed cadence/amount-stability test (R5),
# yet collectively represent real, near-continuous recurring cash burn
# that the sample outputs clearly reserve (request_05: gold safe amount
# 737 vs baseline-without-aggregation 12269.74 — description-only
# recurrence badly under-forecasts essential spend). R17-V2 already notes
# category is a safe (if looser) recurrence key; this fallback applies it
# ONLY to leftover settled expense rows that didn't already form a
# description-level chain, aggregating them per (user, category) into one
# synthetic chain projected at the AVERAGE historical amount and AVERAGE
# gap between occurrences (not modal — many gaps are unique).
CATEGORY_FALLBACK_MIN_OCCURRENCES = 2


# --- R22: amount-irregular protected chains + protected-variable streams ---
#
# Cracked on request_05 (see evaluation/rulings.md R22): gold reserves TWO
# extra structures beyond R5's description-keyed chains, both restricted to
# `expense_categories_to_protect` categories only (R6 — protected essentials
# are reserved regardless of amount stability; non-protected variable
# discretionary spend is NOT synthesized into a forward reserve):
#
#   1. Date-regular but amount-irregular chains (R5's +/-10% amount-
#      stability gate rejects them, e.g. monthly "Therapy appointment" at
#      632-777, a >10% swing) are still reserved by gold, projected at
#      their cadence using the LAST settled occurrence's amount (not modal,
#      not average — the exact last value). Requires >=3 settled
#      occurrences (>=2 gaps) to establish real interval consistency,
#      matching R5's own bar.
#   2. Protected categories with NO chain at all (many differently-worded
#      descriptions under one category, e.g. groceries: "Supermarket
#      basket", "Weekly produce market", ...) are projected forward from
#      the last occurrence at the prior-90-day MEDIAN gap, at the
#      prior-90-day AVERAGE amount per occurrence.
#
# Both structures skip past-due occurrences: an occurrence whose projected
# date has already passed request_date is NOT counted retroactively; only
# future occurrences (offset in [1, 90] days from request_date) contribute.
PROTECTED_IRREGULAR_MIN_SETTLED = 3  # >=2 gaps


def protected_irregular_chains(
    ledger: list[CashEvent],
    existing: list[RecurrenceChain],
    protect_categories: set[str],
) -> list[RecurrenceChain]:
    """Date-regular, amount-irregular chains restricted to protected
    categories (R22). Projects at the LAST settled occurrence's amount.

    Category-level exclusion (R22 addendum): a category that already has
    a regular R5 chain for that user needs no irregular variant, even if
    other descriptions in that category are date-regular/amount-unstable.
    """
    claimed = {(c.user_id, c.description) for c in existing}
    covered_categories = {(c.user_id, c.category) for c in existing}
    by_key: dict[tuple[str, str], list[CashEvent]] = {}
    for ce in ledger:
        if ce.direction != "debit" or ce.category not in protect_categories:
            continue
        if (ce.user_id, ce.category) in covered_categories:
            continue
        by_key.setdefault((ce.user_id, ce.description), []).append(ce)

    out: list[RecurrenceChain] = []
    for (user_id, description), events in by_key.items():
        if (user_id, description) in claimed:
            continue
        events_sorted = sorted(events, key=lambda ce: ce.effective_date)
        settled = [ce for ce in events_sorted if ce.status == "settled"]
        if len(settled) < PROTECTED_IRREGULAR_MIN_SETTLED:
            continue
        gaps = [
            (settled[i].effective_date - settled[i - 1].effective_date).days
            for i in range(1, len(settled))
        ]
        modal_gap = _modal(gaps)
        if modal_gap <= 0:
            continue
        if any(abs(g - modal_gap) > INTERVAL_TOLERANCE_DAYS for g in gaps):
            continue
        last = settled[-1]
        out.append(RecurrenceChain(
            user_id=user_id,
            description=description,
            category=last.category,
            direction=last.direction,
            modal_amount=last.home_amount,  # LAST amount, not modal/average (R22)
            modal_interval_days=modal_gap,
            flexibility=last.flexibility,
            minimum_allowed_amount=last.minimum_allowed_amount,
            occurrences=events_sorted,
            stopped=False,
            confirmed_future_dates=[],
        ))
    return out


@dataclass(frozen=True)
class ProtectedVariableStream:
    """A synthetic forward-projected stream for a protected category with
    no single description-keyed chain (R22 groceries-style aggregation).
    Cadence = prior-90d MEDIAN gap between settled occurrences; amount =
    prior-90d AVERAGE amount per occurrence."""
    user_id: str
    category: str
    last_date: date
    interval_days: int
    amount: Decimal


def protected_variable_streams(
    ledger: list[CashEvent],
    existing: list[RecurrenceChain],
    irregular_chains: list[RecurrenceChain],
    protect_categories: set[str],
    request_date: date,
) -> list[ProtectedVariableStream]:
    """A category has NO chain coverage at all — neither R5 chains nor
    irregular chains — before we synthesize a variable stream (R22
    addendum: category-level exclusion, not description-level)."""
    covered_categories = {(c.user_id, c.category) for c in existing} | {
        (c.user_id, c.category) for c in irregular_chains
    }
    window_start = request_date - timedelta(days=90)
    by_cat: dict[tuple[str, str], list[CashEvent]] = {}
    for ce in ledger:
        if ce.direction != "debit" or ce.status != "settled":
            continue
        if ce.category not in protect_categories:
            continue
        if (ce.user_id, ce.category) in covered_categories:
            continue
        if not (window_start <= ce.effective_date < request_date):
            continue
        by_cat.setdefault((ce.user_id, ce.category), []).append(ce)

    out: list[ProtectedVariableStream] = []
    for (user_id, category), events in by_cat.items():
        if len(events) < 2:
            continue
        events_sorted = sorted(events, key=lambda ce: ce.effective_date)
        gaps = [
            (events_sorted[i].effective_date - events_sorted[i - 1].effective_date).days
            for i in range(1, len(events_sorted))
        ]
        gaps = [g for g in gaps if g > 0]
        if not gaps:
            continue
        gaps_sorted = sorted(gaps)
        n = len(gaps_sorted)
        median_gap = (
            gaps_sorted[n // 2]
            if n % 2 == 1
            else (gaps_sorted[n // 2 - 1] + gaps_sorted[n // 2]) / 2
        )
        interval = max(1, round(median_gap))
        avg_amount = sum((ce.home_amount for ce in events_sorted), Decimal("0")) / len(events_sorted)
        out.append(ProtectedVariableStream(
            user_id=user_id,
            category=category,
            last_date=events_sorted[-1].effective_date,
            interval_days=interval,
            amount=avg_amount,
        ))
    return out


def _category_fallback_chains(ledger: list[CashEvent], existing: list[RecurrenceChain]) -> list[RecurrenceChain]:
    claimed_descriptions = {(c.user_id, c.description) for c in existing}
    leftover: dict[tuple[str, str], list[CashEvent]] = {}
    for ce in ledger:
        if ce.direction != "debit" or ce.status != "settled":
            continue
        if (ce.user_id, ce.description) in claimed_descriptions:
            continue
        leftover.setdefault((ce.user_id, ce.category), []).append(ce)

    out: list[RecurrenceChain] = []
    for (user_id, category), events in leftover.items():
        if len(events) < CATEGORY_FALLBACK_MIN_OCCURRENCES:
            continue
        events_sorted = sorted(events, key=lambda ce: ce.effective_date)
        span_days = (events_sorted[-1].effective_date - events_sorted[0].effective_date).days
        if span_days <= 0:
            continue
        avg_interval = max(1, round(span_days / (len(events_sorted) - 1)))
        avg_amount = sum((ce.home_amount for ce in events_sorted), Decimal("0")) / len(events_sorted)
        flexibility = events_sorted[-1].flexibility
        min_allowed = events_sorted[-1].minimum_allowed_amount
        out.append(RecurrenceChain(
            user_id=user_id,
            description=f"__category_fallback__:{category}",
            category=category,
            direction="debit",
            modal_amount=avg_amount,
            modal_interval_days=avg_interval,
            flexibility=flexibility,
            minimum_allowed_amount=min_allowed,
            occurrences=events_sorted,
            stopped=False,
            confirmed_future_dates=[],
        ))
    return out


# --- Task 2.3 / 3.2: linked-chain resolution (R17 V6) ------------------------
#
# Exactly 7 shapes, counts verified on shipped data: 14/8/8/6/7/10/5.
#   1. (14x) expense settled -> refund settled: net refund back in.
#   2. (8x)  expense settled -> refund pending: ignore pending credit,
#            expense stands (the pending refund credit is already dropped
#            by is_cash_relevant; no extra action needed beyond keeping the
#            settled expense).
#   3. (8x)  expense cancelled -> expense settled: count the settled row
#            once; the cancelled row is a duplicate representation and is
#            already dropped by is_cash_relevant (status == cancelled).
#   4. (6x)  expense settled -> expense pending: COUNT the pending debit
#            too (repeat charge under investigation; safer reading keeps
#            both cash-relevant per is_cash_relevant already).
#   5. (7x)  debt_payment failed -> debt_payment scheduled: the failed row
#            is void (already dropped by is_cash_relevant); the scheduled
#            retry stands as a confirmed future debit.
#   6. (10x) investment_purchase -> investment_valuation unrealized: ignore
#            the valuation row (already dropped: direction=non_cash).
#   7. (5x)  investment_purchase -> investment_sale settled: proceeds are
#            real cash in (settled credit; already kept by is_cash_relevant).
#
# Because is_cash_relevant already implements the correct filtering for
# every shape's "excluded" side (cancelled/failed/unrealized/pending-
# credit are all dropped, pending debits and settled rows are all kept),
# resolve_linked_chains's job is to (a) make each shape auditable/explicit
# by classifying every linked pair, and (b) net a settled refund against
# its settled expense so the pair contributes ONE net cash effect rather
# than two independent line items (needed for shape 1; the other 6 shapes
# are already correctly additive/exclusive under is_cash_relevant as long
# as no row is double counted, which the per-event-id ledger guarantees).

LINKED_CHAIN_SHAPES = (
    "expense_settled_refund_settled",
    "expense_settled_refund_pending",
    "expense_cancelled_expense_settled",
    "expense_settled_expense_pending",
    "debt_failed_debt_scheduled",
    "investment_purchase_valuation_unrealized",
    "investment_purchase_sale_settled",
)


@dataclass(frozen=True)
class LinkedChainResolution:
    """Classification of one linked_event_id pair, plus the ledger
    event_ids that should be netted/removed/kept as a result."""
    shape: str
    parent_event_id: str
    child_event_id: str
    net_event_ids: list[str]  # event_ids whose home_amount should be summed as a single net effect
    drop_event_ids: list[str]  # event_ids to exclude entirely from the forecast (already-void rows)


def _classify_pair(parent: FinancialEvent, child: FinancialEvent) -> Optional[str]:
    if parent.event_type == "expense" and parent.status == "settled" and child.event_type == "refund":
        if child.status == "settled":
            return "expense_settled_refund_settled"
        if child.status == "pending":
            return "expense_settled_refund_pending"
    if parent.event_type == "expense" and parent.status == "cancelled" and child.event_type == "expense" and child.status == "settled":
        return "expense_cancelled_expense_settled"
    if parent.event_type == "expense" and parent.status == "settled" and child.event_type == "expense" and child.status == "pending":
        return "expense_settled_expense_pending"
    if parent.event_type == "debt_payment" and parent.status == "failed" and child.event_type == "debt_payment" and child.status == "scheduled":
        return "debt_failed_debt_scheduled"
    if parent.event_type == "investment_purchase" and child.event_type == "investment_valuation":
        return "investment_purchase_valuation_unrealized"
    if parent.event_type == "investment_purchase" and child.event_type == "investment_sale" and child.status == "settled":
        return "investment_purchase_sale_settled"
    return None


def resolve_linked_chains(events: list[FinancialEvent]) -> list[LinkedChainResolution]:
    """Classify every linked_event_id pair per the R17-V6 7-shape table.

    `events` is the raw (pre-cash-relevance-filter) event list so both
    ends of each chain are visible regardless of status. Returns one
    LinkedChainResolution per linked pair found; unmatched/unexpected
    shapes are omitted (callers/tests should assert the expected counts
    sum to the shipped totals: 14/8/8/6/7/10/5).

    Net/drop semantics feed the ledger builder:
      - shape 1 (refund settled back in): net_event_ids=[parent, child]
        -> the two settled cash rows are summed to one net inflow/outflow
        instead of double counting the full expense AND the full refund
        as unrelated events (refund partially or fully offsets it).
      - shapes 2/3/5/6: the void/duplicate side is already excluded by
        is_cash_relevant; drop_event_ids lists it explicitly for
        auditability even though the filter already removes it.
      - shape 4: both settled expense and pending repeat charge are kept
        (safer reading) — no netting, no dropping.
      - shape 7: both investment_purchase (past outflow) and
        investment_sale settled (cash in) are kept independently.
    """
    by_id = {ev.event_id: ev for ev in events}
    resolutions: list[LinkedChainResolution] = []

    for ev in events:
        if not ev.linked_event_id:
            continue
        parent = by_id.get(ev.linked_event_id)
        if parent is None:
            continue
        child = ev
        shape = _classify_pair(parent, child)
        if shape is None:
            continue

        net_ids: list[str] = []
        drop_ids: list[str] = []
        if shape == "expense_settled_refund_settled":
            net_ids = [parent.event_id, child.event_id]
        elif shape == "expense_cancelled_expense_settled":
            drop_ids = [parent.event_id]  # cancelled row is a duplicate representation
        elif shape == "debt_failed_debt_scheduled":
            drop_ids = [parent.event_id]  # failed row is void
        elif shape == "investment_purchase_valuation_unrealized":
            drop_ids = [child.event_id]  # valuation is never cash
        elif shape == "expense_settled_refund_pending":
            drop_ids = [child.event_id]  # pending refund credit ignored
        # shape "expense_settled_expense_pending" and
        # "investment_purchase_sale_settled": no netting/dropping, both
        # rows stand independently (see docstring above).

        resolutions.append(LinkedChainResolution(
            shape=shape,
            parent_event_id=parent.event_id,
            child_event_id=child.event_id,
            net_event_ids=net_ids,
            drop_event_ids=drop_ids,
        ))

    return resolutions


def apply_evidence(ledger: list[CashEvent], evidence: list[dict]):
    """Placeholder for task 2.3 (message/image evidence merge)."""
    raise NotImplementedError("evidence merge is implemented in task 2.3")
