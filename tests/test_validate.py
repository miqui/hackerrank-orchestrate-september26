import csv
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

REPO = Path(__file__).resolve().parents[1]
VALIDATE = REPO / "code" / "validate.py"
DATASET = REPO / "dataset"


def run_validator(output_path: Path, requests_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(VALIDATE), "--output", str(output_path),
         "--requests", str(requests_path), "--dataset-dir", str(DATASET)],
        capture_output=True, text=True,
    )


class TestValidateConformant(unittest.TestCase):
    def test_sample_output_is_conformant(self):
        subprocess.run([sys.executable, str(REPO / "code" / "main.py"), "--sample"],
                        cwd=REPO, check=True, capture_output=True, text=True)
        result = run_validator(DATASET / "sample_output.csv", DATASET / "sample_requests.csv")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("conformant", result.stdout)


class TestValidateViolations(unittest.TestCase):
    def _write_rows(self, tmpdir: Path, rows: list[dict]) -> Path:
        out = tmpdir / "output.csv"
        fieldnames = [
            "request_id", "affordability_status", "recommended_payment_method",
            "amount_safe_to_pay", "earliest_date_for_full_payment", "payment_plan",
            "spending_changes_needed", "decision_explanation",
        ]
        with out.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for r in rows:
                w.writerow(r)
        return out

    def _base_row(self, request_id="request_01"):
        return {
            "request_id": request_id,
            "affordability_status": "affordable_now",
            "recommended_payment_method": "full_payment",
            "amount_safe_to_pay": "25256",
            "earliest_date_for_full_payment": "2024-03-03",
            "payment_plan": "2024-03-03:25256",
            "spending_changes_needed": "",
            "decision_explanation": "Pay in full.",
        }

    def test_missing_request_id_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            rows = [self._base_row("request_01")]
            out = self._write_rows(tmpdir, rows)
            result = run_validator(out, DATASET / "sample_requests.csv")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing", result.stdout.lower())

    def test_bad_enum_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            rows = []
            for rid in [f"request_{i:02d}" for i in range(1, 26)]:
                row = self._base_row(rid)
                if rid == "request_01":
                    row["affordability_status"] = "totally_fine"
                rows.append(row)
            out = self._write_rows(tmpdir, rows)
            result = run_validator(out, DATASET / "sample_requests.csv")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("enum", result.stdout.lower())

    def test_amount_out_of_range_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            rows = []
            for rid in [f"request_{i:02d}" for i in range(1, 26)]:
                row = self._base_row(rid)
                if rid == "request_01":
                    row["amount_safe_to_pay"] = "999999999"
                rows.append(row)
            out = self._write_rows(tmpdir, rows)
            result = run_validator(out, DATASET / "sample_requests.csv")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("amount", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()
