#!/usr/bin/env python3
"""Buy-or-Wait financial agent — entry point.

Stdlib-only (Python 3.11+). `python3 code/main.py --sample` runs on the
25 public sample requests; `--full` runs on all 250 requests in
dataset/requests.csv; `--validate` runs the conformance validator against
an existing output.csv (validator lands in task 5.1; this only checks row
counts/column order here).
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import loaders  # noqa: E402
from fx import FxTable  # noqa: E402
from reconstruct import CashEvent, detect_recurrence, reconstruct_user_ledger, resolve_linked_chains  # noqa: E402
from evidence import (  # noqa: E402
    apply_evidence,
    build_evidence_effect,
    classify_message,
    resolve_image_amount_overrides,
)
from decide import DecisionResult, decide_request  # noqa: E402

DATASET_DIR = Path(__file__).resolve().parent.parent / "dataset"
OUTPUT_COLUMNS = [
    "request_id", "amount_safe_to_pay", "affordability_status",
    "recommended_payment_method", "payment_plan",
    "earliest_date_for_full_payment", "spending_changes_needed",
    "decision_explanation",
]


def load_dataset(dataset_dir: Path = DATASET_DIR) -> dict:
    return loaders.load_all(dataset_dir)


def build_fx_table(dataset: dict) -> FxTable:
    return FxTable(dataset["rates"])


def build_amount_overrides(dataset: dict) -> dict:
    """Resolve blank-amount events via images (task 2.1/2.2)."""
    return resolve_image_amount_overrides(dataset["images"])


def build_ledgers_and_chains(dataset: dict, fx: FxTable, amount_overrides: dict) -> tuple[dict, dict]:
    events_by_user: dict = {}
    for ev in dataset["events"]:
        events_by_user.setdefault(ev.user_id, []).append(ev)

    ledgers: dict = {}
    chains: dict = {}
    for user_id, profile in dataset["profiles"].items():
        user_events = events_by_user.get(user_id, [])
        ledger = reconstruct_user_ledger(user_events, profile, fx, amount_overrides)

        # Apply message-based evidence effects for this user's events.
        user_messages = [m for m in dataset["messages"] if m.user_id == user_id]
        effects = []
        for m in user_messages:
            context = {"event_currency": profile.home_currency}
            classification = classify_message(m.message_text, context=context, message_id=m.message_id)
            effects.append(build_evidence_effect(classification, event_id=m.related_event_id))
        if effects:
            ledger = apply_evidence(ledger, effects)

        ledgers[user_id] = ledger
        chains[user_id] = detect_recurrence(ledger)
    return ledgers, chains


def run_requests(dataset: dict, requests: list) -> list:
    fx = build_fx_table(dataset)
    amount_overrides = build_amount_overrides(dataset)
    ledgers, chains = build_ledgers_and_chains(dataset, fx, amount_overrides)

    results = []
    for req in requests:
        profile = dataset["profiles"][req.user_id]
        ledger = ledgers.get(req.user_id, [])
        chain_list = chains.get(req.user_id, [])
        result = decide_request(req, profile, ledger, chain_list, dataset["payment_options"])
        results.append(result)
    return results


def write_output(results: list, output_path: Path) -> None:
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(OUTPUT_COLUMNS)
        for r in results:
            writer.writerow([
                r.request_id, r.amount_safe_to_pay, r.affordability_status,
                r.recommended_payment_method, r.payment_plan,
                r.earliest_date_for_full_payment, r.spending_changes_needed,
                r.decision_explanation,
            ])


def run_sample(dataset_dir: Path, output_path: Path) -> int:
    dataset = load_dataset(dataset_dir)
    sample_requests = loaders.load_requests(dataset_dir / "sample_requests.csv")
    results = run_requests(dataset, sample_requests)
    sample_out = output_path.parent / "sample_output.csv"
    write_output(results, sample_out)
    print(f"Wrote {len(results)} sample rows to {sample_out}")
    return 0


def run_full(dataset_dir: Path, output_path: Path) -> int:
    dataset = load_dataset(dataset_dir)
    results = run_requests(dataset, dataset["requests"])
    write_output(results, output_path)
    print(f"Wrote {len(results)} rows to {output_path}")
    return 0


def run_validate(output_path: Path) -> int:
    print(f"Validator not yet implemented (task 5.1); would check {output_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Buy-or-Wait financial agent")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--sample", action="store_true", help="run on the 25 public sample requests")
    mode.add_argument("--full", action="store_true", help="run over all 250 requests")
    mode.add_argument("--validate", action="store_true", help="validate an existing output.csv")
    parser.add_argument("--dataset-dir", type=Path, default=DATASET_DIR)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent.parent / "output.csv")
    args = parser.parse_args(argv)

    if args.sample:
        return run_sample(args.dataset_dir, args.output)
    if args.full:
        return run_full(args.dataset_dir, args.output)
    if args.validate:
        return run_validate(args.output)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
