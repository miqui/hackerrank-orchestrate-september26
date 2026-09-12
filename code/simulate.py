"""Day-grain 90-day balance simulator + safe-amount binary search.

Implements tasks 3.3/3.4 of the buy-or-wait-financial-agent change.
Stdlib only (Decimal, datetime.date, dataclasses).

Per R7 (balance-simulation spec "90-day forecast" + "Same-day ordering"):
  - Day 0 = request_date; window is [request_date, request_date + 89].
  - Within a day, apply in this order: starting balance (day 0 only) ->
    pending debits -> recurring debits -> income (credits) -> the
    request/candidate payment LAST (money must exist before spending).
  - A plan is safe only if balance never falls below minimum_balance_to_keep
    on any day in the window (R "Safety invariant").

Per R2: amount_safe_to_pay is computed on the NO-spending-changes baseline;
callers apply spending changes (stop/reduce) separately by adjusting the
`payment_events` list they pass in, not inside this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

HORIZON_DAYS = 90  # day 0 .. day 89 inclusive

# Same-day ordering priority (lower sorts first); the request/candidate
# payment always gets the highest number so it applies last regardless of
# what kind string a caller passes for it.
_KIND_ORDER = {
    "pending_debit": 0,
    "recurring_debit": 1,
    "income": 2,
    "credit": 2,
    "request_payment": 99,
}


@dataclass(frozen=True)
class PaymentEvent:
    """One dated cash movement to feed into the simulator.

    `amount` is always positive; `direction` carries the sign meaning.
    `kind` controls same-day ordering (see _KIND_ORDER above); unknown
    kinds sort after debits/income but before the request payment.
    """
    event_date: date
    amount: Decimal
    direction: str  # 'debit' | 'credit'
    kind: str = "other"
    label: str = ""


@dataclass(frozen=True)
class SimulationResult:
    ok: bool
    first_breach_day: Optional[date]
    min_balance_day_value: Decimal


def _ordering_key(ev_kind: str) -> int:
    return _KIND_ORDER.get(ev_kind, 50)


def simulate(
    starting_balance: Decimal,
    minimum_balance_to_keep: Decimal,
    request_date: date,
    payment_events: list[PaymentEvent],
    request_payment: Optional[PaymentEvent] = None,
    horizon_days: int = HORIZON_DAYS,
) -> SimulationResult:
    """Day-grain simulate [request_date, request_date + horizon_days - 1].

    Returns (ok, first_breach_day, min_balance_day_value) where ok is True
    iff the balance never falls below minimum_balance_to_keep on any day
    in the window. `min_balance_day_value` is the lowest balance observed
    (useful for diagnostics even when ok is True).
    """
    end_date = request_date + timedelta(days=horizon_days - 1)

    events_by_day: dict[date, list[PaymentEvent]] = {}
    for ev in payment_events:
        if request_date <= ev.event_date <= end_date:
            events_by_day.setdefault(ev.event_date, []).append(ev)
    if request_payment is not None and request_date <= request_payment.event_date <= end_date:
        events_by_day.setdefault(request_payment.event_date, []).append(request_payment)

    balance = starting_balance
    min_balance = balance
    min_balance_day = request_date
    first_breach_day: Optional[date] = None

    # Breach detection is end-of-day (once every event for that day has
    # been applied in R7 order), not per-individual-event: same-day debit
    # + credit pairs (e.g. rent and salary both landing "day 35") net out
    # to a single day-end balance, matching normal ledger/statement
    # semantics. The R7 ordering still matters for producing the correct
    # day-end sum (it guarantees the request payment only succeeds once
    # that day's income has already landed) but does not itself create an
    # artificial mid-day dip when debits happen to be listed before
    # same-day credits.
    if balance < minimum_balance_to_keep:
        first_breach_day = request_date
        min_balance = balance
        min_balance_day = request_date

    day = request_date
    while day <= end_date:
        todays = events_by_day.get(day)
        if todays:
            todays_sorted = sorted(todays, key=lambda e: _ordering_key(e.kind))
            for ev in todays_sorted:
                if ev.direction == "debit":
                    balance -= ev.amount
                else:
                    balance += ev.amount
            if balance < min_balance:
                min_balance = balance
                min_balance_day = day
            if balance < minimum_balance_to_keep and first_breach_day is None:
                first_breach_day = day
        day += timedelta(days=1)

    ok = first_breach_day is None
    return SimulationResult(ok=ok, first_breach_day=first_breach_day, min_balance_day_value=min_balance)


def safe_amount(
    starting_balance: Decimal,
    minimum_balance_to_keep: Decimal,
    request_date: date,
    payment_events: list[PaymentEvent],
    requested_amount: Decimal,
    horizon_days: int = HORIZON_DAYS,
) -> Decimal:
    """Largest X in [0, requested_amount] such that paying X on
    request_date (baseline, no spending changes per R2) passes the 90-day
    safety check. Binary search to the cent, rounded DOWN so the returned
    value never breaches (never round up past a passing boundary).
    """
    if requested_amount <= 0:
        return Decimal("0.00")

    def passes(x: Decimal) -> bool:
        req_ev = PaymentEvent(request_date, x, "debit", kind="request_payment")
        result = simulate(starting_balance, minimum_balance_to_keep, request_date, payment_events, req_ev, horizon_days)
        return result.ok

    if passes(requested_amount):
        return requested_amount
    if not passes(Decimal("0.00")):
        # Even paying nothing breaches (already below minimum) -> 0 is the floor.
        return Decimal("0.00")

    lo = Decimal("0.00")
    hi = requested_amount
    cent = Decimal("0.01")
    # Binary search until the interval collapses to one cent.
    while hi - lo > cent:
        mid = ((lo + hi) / 2).quantize(cent)
        if mid <= lo:
            mid = lo + cent
        if passes(mid):
            lo = mid
        else:
            hi = mid
    return lo


def earliest_date_for_full_payment(
    starting_balance: Decimal,
    minimum_balance_to_keep: Decimal,
    request_date: date,
    payment_events: list[PaymentEvent],
    requested_amount: Decimal,
    horizon_days: int = HORIZON_DAYS,
    search_days: Optional[int] = None,
) -> Optional[date]:
    """First day in [request_date, request_date+search_days-1] (default:
    the full `horizon_days` window) where a single full payment on that
    day passes the safety check across the full `horizon_days`-day window
    starting at request_date (per R7/R "Earliest safe full-payment date").
    None if no such day exists in the search range.

    `search_days` lets a caller restrict WHICH candidate days are tried
    (e.g. R18-sample-confirmed: only days up to the request's own
    desired_completion_date count as a usable "earliest full payment")
    while the underlying safety check still simulates the full 90-day
    horizon — shrinking `horizon_days` itself would incorrectly also
    shrink the safety window being checked.
    """
    search_end = request_date + timedelta(days=(search_days if search_days is not None else horizon_days) - 1)
    day = request_date
    while day <= search_end:
        req_ev = PaymentEvent(day, requested_amount, "debit", kind="request_payment")
        result = simulate(starting_balance, minimum_balance_to_keep, request_date, payment_events, req_ev, horizon_days)
        if result.ok:
            return day
        day += timedelta(days=1)
    return None
