#!/usr/bin/env python3
"""Task 5.2: backtest harness.

Runs the full pipeline (code/main.py's run_requests) on
dataset/sample_requests.csv and compares each output field against the
golden values embedded in that same file. Prints per-field accuracy and a
per-mismatch table with both values and the ruling most likely implicated.

Stdlib only. No LLM calls.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "code"))

import loaders  # noqa: E402
from main import load_dataset, run_requests, DATASET_DIR  # noqa: E402

FIELDS = [
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
]

# Heuristic mapping from field name to the ruling most likely implicated
# when it mismatches (used only to aid triage in the printed report).
FIELD_RULING = {
    "amount_safe_to_pay": "R2/R4/R5 (safe-amount baseline + projection)",
    "affordability_status": "R1/R4 (deadline classification + salary projection)",
    "recommended_payment_method": "R3 (plan ranking)",
    "payment_plan": "R3/R8 (plan construction/eligibility)",
    "earliest_date_for_full_payment": "R7/R4 (simulation window + projection)",
    "spending_changes_needed": "R6/R11 (spending-change discovery/floor)",
    "decision_explanation": "R12 (explanation synthesis)",
}


def load_golden(path: Path) -> dict:
    golden = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            golden[row["request_id"]] = row
    return golden


def _norm(value: str) -> str:
    return (value or "").strip()


def run_backtest(dataset_dir: Path = DATASET_DIR, verbose: bool = True) -> dict:
    dataset = load_dataset(dataset_dir)
    sample_path = dataset_dir / "sample_requests.csv"
    sample_requests = loaders.load_requests(sample_path)
    golden = load_golden(sample_path)

    results = run_requests(dataset, sample_requests)

    field_counts = {f: 0 for f in FIELDS}
    total = len(results)
    mismatches = []  # list of dicts

    for r in results:
        g = golden[r.request_id]
        row_vals = {
            "amount_safe_to_pay": str(r.amount_safe_to_pay),
            "affordability_status": r.affordability_status,
            "recommended_payment_method": r.recommended_payment_method,
            "payment_plan": r.payment_plan,
            "earliest_date_for_full_payment": r.earliest_date_for_full_payment,
            "spending_changes_needed": r.spending_changes_needed,
            "decision_explanation": r.decision_explanation,
        }
        for f in FIELDS:
            ours = _norm(row_vals[f])
            gold = _norm(g[f])
            if f == "amount_safe_to_pay":
                try:
                    match = abs(float(ours) - float(gold)) < 0.01
                except ValueError:
                    match = ours == gold
            else:
                match = ours == gold
            if match:
                field_counts[f] += 1
            else:
                mismatches.append({
                    "request_id": r.request_id,
                    "field": f,
                    "ours": ours,
                    "gold": gold,
                    "ruling": FIELD_RULING[f],
                })

    if verbose:
        print(f"=== Backtest report: {total} sample requests ===")
        for f in FIELDS:
            print(f"  {f:35s} {field_counts[f]:2d}/{total}")
        print()
        print("=== Mismatches ===")
        print(f"{'request_id':12s} {'field':30s} {'ours':30s} {'gold':30s} ruling")
        for m in mismatches:
            print(f"{m['request_id']:12s} {m['field']:30s} {m['ours'][:30]:30s} {m['gold'][:30]:30s} {m['ruling']}")

    return {"field_counts": field_counts, "total": total, "mismatches": mismatches}


if __name__ == "__main__":
    run_backtest()
