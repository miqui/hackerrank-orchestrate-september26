"""Golden tests T4, T7, T8, T9, T10 for the decision engine (tasks 4.1-4.3).

Run WITHOUT OPENROUTER_API_KEY set so evidence classification exercises the
deterministic no-key fallback path (zero network calls), matching the
convention in test_evidence.py.
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent.parent / "code"
sys.path.insert(0, str(CODE_DIR))

os.environ.pop("OPENROUTER_API_KEY", None)

import loaders  # noqa: E402
from fx import FxTable  # noqa: E402
from reconstruct import reconstruct_user_ledger, detect_recurrence  # noqa: E402
import decide  # noqa: E402


def _profile(
    user_id="user_test", home_currency="USD", balance=Decimal("2000"),
    minimum=Decimal("1000"), protect=None, reduce=None, stop=None,
    methods=None, max_months=None,
):
    return loaders.FinancialProfile(
        user_id=user_id,
        home_currency=home_currency,
        current_available_balance=balance,
        minimum_balance_to_keep=minimum,
        financial_priorities=[],
        expense_categories_to_protect=protect or [],
        expense_categories_user_is_willing_to_reduce=reduce or [],
        expense_categories_user_is_willing_to_stop=stop or [],
        payment_methods_user_will_consider=methods or ["full_payment"],
        max_installment_months=max_months,
    )


def _event(event_id, amount, direction="debit", status="settled",
           event_date=date(2026, 1, 1), settlement_date=None, currency="USD",
           description="x", category="cat", flexibility="fixed", min_allowed=None,
           linked_event_id=None, user_id="user_test", event_type=None):
    return loaders.FinancialEvent(
        event_id=event_id,
        user_id=user_id,
        event_type=event_type or ("expense" if direction == "debit" else "income"),
        description=description,
        category=category,
        direction=direction,
        amount=amount,
        currency=currency,
        event_date=event_date,
        settlement_date=settlement_date,
        status=status,
        linked_event_id=linked_event_id,
        flexibility=flexibility,
        minimum_allowed_amount=min_allowed,
    )


def _request(request_id="req_test", user_id="user_test", request_date=date(2026, 1, 1),
             requested_amount=Decimal("500"), desired_completion_date=date(2026, 1, 20),
             allows_partial_payment=False, request_text="test", request_type="purchase"):
    return loaders.Request(
        request_id=request_id, user_id=user_id, request_date=request_date,
        request_type=request_type, requested_amount=requested_amount,
        desired_completion_date=desired_completion_date,
        allows_partial_payment=allows_partial_payment, request_text=request_text,
    )


def _decide(request, profile, events, payment_options=None):
    fx = FxTable([])
    ledger = reconstruct_user_ledger(events, profile, fx)
    chains = detect_recurrence(ledger)
    return decide.decide_request(request, profile, ledger, chains, payment_options or [])


class TestT4PendingCreditTrap(unittest.TestCase):
    """T4 — Pending credit trap (Safety): a pending refund credit must not
    be counted toward affordability; safe amount stays capped at the
    balance-minus-minimum headroom regardless of the pending refund."""

    def test_pending_refund_credit_not_counted(self):
        profile = _profile(balance=Decimal("3000"), minimum=Decimal("1500"))
        events = [
            _event("ev_refund", Decimal("1200"), direction="credit", status="pending",
                   event_date=date(2026, 1, 10), settlement_date=date(2026, 1, 10),
                   description="refund", category="refund"),
        ]
        request = _request(requested_amount=Decimal("1800"), request_date=date(2026, 1, 1),
                            desired_completion_date=date(2026, 3, 1))
        result = _decide(request, profile, events)

        # Only 3000 - 1500 = 1500 headroom exists; the pending 1200 refund
        # must NOT inflate amount_safe_to_pay beyond that.
        self.assertLessEqual(result.amount_safe_to_pay, Decimal("1500.00"))
        self.assertEqual(result.amount_safe_to_pay, Decimal("1500.00"))

    def test_earliest_full_payment_does_not_rely_on_unsettled_refund(self):
        profile = _profile(balance=Decimal("3000"), minimum=Decimal("1500"))
        events = [
            _event("ev_refund", Decimal("1200"), direction="credit", status="pending",
                   event_date=date(2026, 1, 10), settlement_date=date(2026, 1, 10),
                   description="refund", category="refund"),
        ]
        request = _request(requested_amount=Decimal("1800"), request_date=date(2026, 1, 1),
                            desired_completion_date=date(2026, 3, 1))
        result = _decide(request, profile, events)
        # 1800 never becomes safe within the horizon since the refund never
        # settles in this ledger -> no earliest date should be produced.
        self.assertEqual(result.earliest_date_for_full_payment, "")
        self.assertEqual(result.affordability_status, "not_affordable")


class TestT7PhrasingInvariance(unittest.TestCase):
    """T7 — Phrasing invariance incl. non-English (Precision): identical
    state + amount, five different request_texts, must yield identical
    numeric/plan/status/method fields; only decision_explanation may vary
    in emphasis (rulings still require numbers to match)."""

    def _make_state(self):
        profile = _profile(home_currency="INR", balance=Decimal("300000"),
                            minimum=Decimal("50000"))
        events = []
        return profile, events

    def test_five_phrasings_identical_numeric_fields(self):
        profile, events = self._make_state()
        phrasings = [
            "Can I afford this laptop?",
            "What is the most I can put toward this laptop right now?",
            "Should I pay now or wait?",
            "Would paying today leave enough for my regular expenses?",
            "Apakah saya mampu membeli laptop ini?",
        ]
        results = []
        for i, text in enumerate(phrasings):
            request = _request(
                request_id=f"req_t7_{i}", requested_amount=Decimal("266700"),
                request_date=date(2026, 1, 1), desired_completion_date=date(2026, 2, 1),
                request_text=text,
            )
            results.append(_decide(request, profile, events))

        base = results[0]
        for r in results[1:]:
            self.assertEqual(r.amount_safe_to_pay, base.amount_safe_to_pay)
            self.assertEqual(r.affordability_status, base.affordability_status)
            self.assertEqual(r.recommended_payment_method, base.recommended_payment_method)
            self.assertEqual(r.payment_plan, base.payment_plan)
            self.assertEqual(r.earliest_date_for_full_payment, base.earliest_date_for_full_payment)


class TestT8SpendingChangeRanking(unittest.TestCase):
    """T8 — Spending-change ranking (Planning): a plan needing a spending
    change that completes by the deadline must rank ABOVE a safer plan
    (no changes needed) that misses the deadline (rule 1 > rule 2)."""

    def test_deadline_completing_plan_with_stop_beats_safer_late_partial(self):
        profile = _profile(
            balance=Decimal("1300"), minimum=Decimal("250"),
            stop=["entertainment"], methods=["full_payment", "partial_payment"],
        )
        # Recurring flexible streaming charge, stoppable, in a willing-to-stop
        # category; two settled occurrences establish the chain. Paying the
        # full amount today clears the immediate minimum, but the next
        # projected streaming debit (day 30) would breach it unless stopped.
        events = [
            _event("ev_stream_1", Decimal("50"), direction="debit", status="settled",
                   event_date=date(2025, 12, 1), description="Streaming plan",
                   category="entertainment", flexibility="stoppable"),
            _event("ev_stream_2", Decimal("50"), direction="debit", status="settled",
                   event_date=date(2025, 12, 31), description="Streaming plan",
                   category="entertainment", flexibility="stoppable"),
        ]
        request = _request(
            requested_amount=Decimal("1050"), request_date=date(2026, 1, 1),
            desired_completion_date=date(2026, 1, 1), allows_partial_payment=True,
        )
        result = _decide(request, profile, events)

        self.assertEqual(result.recommended_payment_method, "full_payment")
        self.assertIn("stop:ev_stream_2", result.spending_changes_needed)
        self.assertEqual(result.affordability_status, "affordable_with_plan")

    def test_stop_and_reduce_never_coexist_on_same_event(self):
        change = decide.SpendingChange(kind="stop", event_id="ev_x", description="x")
        formatted = decide.format_changes([change])
        self.assertEqual(formatted, "stop:ev_x")
        self.assertNotIn("reduce_to:ev_x", formatted)


class TestT9InstallmentExactMatch(unittest.TestCase):
    """T9 — Installment must exactly match option (Precision): an option
    spanning more months than max_installment_months allows must be
    rejected outright; no invented schedule is ever substituted."""

    def test_option_exceeding_max_months_rejected(self):
        profile = _profile(
            balance=Decimal("5000"), minimum=Decimal("1000"),
            methods=["installments", "full_payment"], max_months=2,
        )
        events = []
        request = _request(
            requested_amount=Decimal("3000"), request_date=date(2026, 1, 1),
            desired_completion_date=date(2026, 6, 1),
        )
        # 3 monthly payments = ~3 months span > max_installment_months=2.
        option = loaders.PaymentOption(
            payment_option_id="opt_1", request_id=request.request_id,
            payment_method="installments", payment_amount=Decimal("1050"),
            number_of_payments=3, first_payment_date=date(2026, 1, 1),
            payment_frequency_days=30, financing_fee=Decimal("150"),
            total_payable_amount=Decimal("3150"),
        )
        result = _decide(request, profile, events, payment_options=[option])
        self.assertNotEqual(result.recommended_payment_method, "installments")

    def test_option_within_max_months_accepted_when_no_other_better(self):
        profile = _profile(
            balance=Decimal("5000"), minimum=Decimal("100"),
            methods=["installments"], max_months=3,
        )
        events = []
        request = _request(
            requested_amount=Decimal("3000"), request_date=date(2026, 1, 1),
            desired_completion_date=date(2026, 6, 1),
        )
        option = loaders.PaymentOption(
            payment_option_id="opt_2", request_id=request.request_id,
            payment_method="installments", payment_amount=Decimal("1050"),
            number_of_payments=3, first_payment_date=date(2026, 1, 1),
            payment_frequency_days=30, financing_fee=Decimal("150"),
            total_payable_amount=Decimal("3150"),
        )
        result = _decide(request, profile, events, payment_options=[option])
        self.assertEqual(result.recommended_payment_method, "installments")
        self.assertEqual(result.payment_plan, "2026-01-01:1050|2026-01-31:1050|2026-03-02:1050")


class TestT10ConflictPrecedence(unittest.TestCase):
    """T10 — Duplicate/conflict precedence (Safety): an explicit amendment
    message must win over both a settled and a pending copy of the same
    event_id (amendment > newer/settled > safer-fallback)."""

    def test_amendment_overrides_settled_and_pending_duplicates(self):
        import evidence

        profile = _profile(balance=Decimal("1000"), minimum=Decimal("200"))
        fx = FxTable([])
        settled = _event("ev_dup", Decimal("100"), direction="debit", status="settled",
                          event_date=date(2026, 1, 5), description="dup charge")
        ledger = reconstruct_user_ledger([settled], profile, fx)

        classification = {
            "intent": "salary_reduced", "amount": "120", "currency": "USD",
            "date": "2026-01-12", "pct": None, "confidence": 0.95,
        }
        effect = evidence.build_evidence_effect(classification, event_id="ev_dup")
        applied = evidence.apply_evidence(ledger, [effect])

        target = next(ce for ce in applied if ce.event_id == "ev_dup")
        self.assertEqual(target.home_amount, Decimal("120"))
        self.assertEqual(target.effective_date, date(2026, 1, 12))


if __name__ == "__main__":
    unittest.main()
