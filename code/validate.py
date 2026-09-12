#!/usr/bin/env python3
"""Task 5.1: conformance validator for an output.csv file.

Checks (stdlib only, no LLM calls, deterministic):
  - exactly one output row per request_id in the matching requests.csv
  - exact column order matches the contract
  - enum values (affordability_status, recommended_payment_method) are
    from the allowed sets
  - 0 <= amount_safe_to_pay <= requested_amount
  - payment_plan is chronological (dates strictly increasing) and every
    payment amount is positive
  - partial_payment plans have EXACTLY 2 payments summing to
    requested_amount, with the second payment dated on
    earliest_date_for_full_payment and on/before desired_completion_date
  - installment plans match the supplied request_payment_options.csv
    schedule verbatim (dates + amounts, in order) for SOME option row
    belonging to that request_id
  - affordable_now => earliest_date_for_full_payment == request_date
  - earliest_date_for_full_payment is empty iff affordability_status is
    not_affordable (never safe within the horizon)
  - spending_changes_needed entries reference event_ids that exist in
    financial_events.csv for that user, with reduce_to targets not below
    the event's minimum_allowed_amount when populated

Exits non-zero and prints every violation found when the output is not
conformant; exits 0 with a summary line when everything passes.
"""
from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "code"))

import loaders  # noqa: E402

OUTPUT_COLUMNS = [
    "request_id", "amount_safe_to_pay", "affordability_status",
    "recommended_payment_method", "payment_plan",
    "earliest_date_for_full_payment", "spending_changes_needed",
    "decision_explanation",
]

AFFORDABILITY_STATUSES = {
    "affordable_now", "affordable_later", "affordable_with_plan", "not_affordable",
}
RECOMMENDED_METHODS = {
    "full_payment", "partial_payment", "installments", "wait", "not_recommended",
}


@dataclass
class Violation:
    request_id: str
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.request_id}] {self.rule}: {self.detail}"


def _parse_date(value: str):
    import datetime
    value = (value or "").strip()
    if not value:
        return None
    return datetime.date.fromisoformat(value)


def _parse_decimal(value: str):
    value = (value or "").strip()
    if value == "":
        return None
    return Decimal(value)


def _parse_plan(plan_str: str, violations: list, request_id: str):
    """Returns list[(date, Decimal)] or None if plan_str == 'none'/empty."""
    plan_str = (plan_str or "").strip()
    if plan_str in ("", "none"):
        return None
    payments = []
    for part in plan_str.split("|"):
        if ":" not in part:
            violations.append(Violation(request_id, "payment_plan.format", f"malformed segment {part!r}"))
            continue
        d_str, amt_str = part.split(":", 1)
        try:
            d = _parse_date(d_str)
        except ValueError:
            violations.append(Violation(request_id, "payment_plan.format", f"bad date {d_str!r}"))
            continue
        try:
            amt = _parse_decimal(amt_str)
        except InvalidOperation:
            violations.append(Violation(request_id, "payment_plan.format", f"bad amount {amt_str!r}"))
            continue
        if amt is None or amt <= 0:
            violations.append(Violation(request_id, "payment_plan.amount_positive", f"non-positive amount {amt_str!r}"))
            continue
        payments.append((d, amt))
    return payments


def _parse_changes(changes_str: str, violations: list, request_id: str):
    changes_str = (changes_str or "").strip()
    if changes_str in ("", "none"):
        return []
    out = []
    for part in changes_str.split("|"):
        if part.startswith("stop:"):
            out.append(("stop", part[len("stop:"):], None))
        elif part.startswith("reduce_to:"):
            rest = part[len("reduce_to:"):]
            if ":" not in rest:
                violations.append(Violation(request_id, "spending_changes.format", f"malformed reduce_to {part!r}"))
                continue
            event_id, amt_str = rest.split(":", 1)
            try:
                amt = _parse_decimal(amt_str)
            except InvalidOperation:
                violations.append(Violation(request_id, "spending_changes.format", f"bad amount {amt_str!r}"))
                continue
            out.append(("reduce_to", event_id, amt))
        else:
            violations.append(Violation(request_id, "spending_changes.format", f"unrecognized entry {part!r}"))
    return out


def validate_output(output_path: Path, requests_path: Path, dataset_dir: Path) -> list:
    violations: list[Violation] = []

    with open(output_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        if header != OUTPUT_COLUMNS:
            violations.append(Violation("<file>", "column_order", f"expected {OUTPUT_COLUMNS}, got {header}"))
        rows = list(csv.DictReader(open(output_path, newline="", encoding="utf-8")))

    requests = {r.request_id: r for r in loaders.load_requests(requests_path)}
    events_by_id = {}
    events_path = dataset_dir / "financial_events.csv"
    if events_path.exists():
        with open(events_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                events_by_id[row["event_id"]] = row

    options_by_request = {}
    options_path = dataset_dir / "request_payment_options.csv"
    if options_path.exists():
        with open(options_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                options_by_request.setdefault(row["request_id"], []).append(row)

    seen_ids = set()
    for row in rows:
        rid = row.get("request_id", "")
        if rid in seen_ids:
            violations.append(Violation(rid, "duplicate_row", "request_id appears more than once in output"))
        seen_ids.add(rid)

        req = requests.get(rid)
        if req is None:
            violations.append(Violation(rid, "unknown_request_id", "not present in requests.csv"))
            continue

        status = row["affordability_status"]
        if status not in AFFORDABILITY_STATUSES:
            violations.append(Violation(rid, "affordability_status.enum", f"{status!r} not in {sorted(AFFORDABILITY_STATUSES)}"))

        method = row["recommended_payment_method"]
        if method not in RECOMMENDED_METHODS:
            violations.append(Violation(rid, "recommended_payment_method.enum", f"{method!r} not in {sorted(RECOMMENDED_METHODS)}"))

        try:
            amount_safe = _parse_decimal(row["amount_safe_to_pay"])
        except InvalidOperation:
            violations.append(Violation(rid, "amount_safe_to_pay.format", f"bad decimal {row['amount_safe_to_pay']!r}"))
            amount_safe = None
        if amount_safe is not None:
            if amount_safe < 0:
                violations.append(Violation(rid, "amount_safe_to_pay.range", f"{amount_safe} < 0"))
            if amount_safe > req.requested_amount:
                violations.append(Violation(rid, "amount_safe_to_pay.range", f"{amount_safe} > requested_amount {req.requested_amount}"))

        earliest_str = (row["earliest_date_for_full_payment"] or "").strip()
        try:
            earliest = _parse_date(earliest_str)
        except ValueError:
            violations.append(Violation(rid, "earliest_date.format", f"bad date {earliest_str!r}"))
            earliest = None

        if status == "not_affordable":
            if earliest_str != "":
                violations.append(Violation(rid, "earliest_date.not_affordable_must_be_blank", f"got {earliest_str!r}"))
        else:
            if earliest_str == "":
                violations.append(Violation(rid, "earliest_date.required_when_affordable", f"status={status} but earliest is blank"))

        if status == "affordable_now" and earliest is not None and earliest != req.request_date:
            violations.append(Violation(rid, "affordable_now.earliest_equals_request_date", f"earliest={earliest} request_date={req.request_date}"))

        payments = _parse_plan(row["payment_plan"], violations, rid)
        if payments:
            dates = [d for d, _ in payments]
            if dates != sorted(dates) or len(set(dates)) != len(dates):
                violations.append(Violation(rid, "payment_plan.chronological", f"dates not strictly increasing: {dates}"))

            if method == "partial_payment":
                if len(payments) != 2:
                    violations.append(Violation(rid, "partial_payment.two_payments", f"expected 2 payments, got {len(payments)}"))
                else:
                    total = payments[0][1] + payments[1][1]
                    if total != req.requested_amount:
                        violations.append(Violation(rid, "partial_payment.sum", f"{total} != requested_amount {req.requested_amount}"))
                    second_date = payments[1][0]
                    if earliest is not None and second_date != earliest:
                        violations.append(Violation(rid, "partial_payment.second_date_is_earliest", f"second payment date {second_date} != earliest {earliest}"))
                    if second_date > req.desired_completion_date:
                        violations.append(Violation(rid, "partial_payment.within_deadline", f"second payment {second_date} > deadline {req.desired_completion_date}"))

            if method == "installments":
                opts = options_by_request.get(rid, [])
                install_opts = [o for o in opts if o.get("payment_method") == "installments"]
                matched = False
                for opt in install_opts:
                    freq = int(opt["payment_frequency_days"]) if opt.get("payment_frequency_days") else 0
                    n = int(opt["number_of_payments"])
                    first_date = _parse_date(opt["first_payment_date"])
                    amt = _parse_decimal(opt["payment_amount"])
                    expected = []
                    d = first_date
                    for _ in range(n):
                        expected.append((d, amt))
                        d = d + __import__("datetime").timedelta(days=freq) if freq else d
                    if expected == payments:
                        matched = True
                        break
                if not matched:
                    violations.append(Violation(rid, "installments.matches_option_schedule", f"payment_plan {payments} matches no supplied option for {rid}"))

        changes = _parse_changes(row["spending_changes_needed"], violations, rid)
        for kind, event_id, amt in changes:
            ev = events_by_id.get(event_id)
            if ev is None:
                violations.append(Violation(rid, "spending_changes.unknown_event", f"event_id {event_id!r} not found"))
                continue
            if kind == "reduce_to":
                floor_str = (ev.get("minimum_allowed_amount") or "").strip()
                if floor_str:
                    floor = _parse_decimal(floor_str)
                    if amt is not None and floor is not None and amt < floor:
                        violations.append(Violation(rid, "spending_changes.below_floor", f"{event_id}: reduce_to {amt} < floor {floor}"))

    for rid in requests:
        if rid not in seen_ids:
            violations.append(Violation(rid, "missing_row", "no output row for this request_id"))

    return violations


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Validate an output.csv for conformance")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--dataset-dir", type=Path, default=REPO_ROOT / "dataset")
    args = parser.parse_args(argv)

    violations = validate_output(args.output, args.requests, args.dataset_dir)
    if violations:
        print(f"FAILED: {len(violations)} conformance violation(s)")
        for v in violations:
            print(f"  {v}")
        return 1
    print("OK: output.csv is conformant")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
