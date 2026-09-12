# Runbook

How to build, run, test, and validate the Buy-or-Wait financial agent.

## Requirements

- Python 3.11+, standard library only (no third-party packages required).
- Optional: `OPENROUTER_API_KEY` environment variable for real LLM evidence
  extraction. Without it, the solution runs fully deterministically using
  documented no-key fallbacks (see "Deterministic behavior" below).

## Running the solution

From the repo root:

```bash
# Full 250-request run -> writes output.csv at the repo root
python3 code/main.py --full

# 25-request public sample run -> writes sample_output.csv at the repo root
python3 code/main.py --sample

# Validate an existing output.csv for contract conformance
python3 code/main.py --validate
# equivalent, direct invocation of the validator:
python3 code/validate.py
```

Expected runtime: a few seconds for `--full` on this dataset size (250
requests, 18 images, 219 messages) with no network calls when no API key is
set; a few seconds to low minutes with a real key, dominated by LLM round
trips (cached after the first run per image_id/message_id).

## Deterministic behavior

- **No API key**: `code/evidence.py` returns a deterministic fallback result
  for every image/message lookup (`unresolved: true`, no amount override) and
  records the call as `unresolved` in `code/cache/telemetry.json`. No network
  call is made. The rest of the pipeline (ledger reconstruction, FX,
  decision logic, explanation synthesis) is 100% deterministic regardless of
  API key presence.
- **With `OPENROUTER_API_KEY` set**: image extraction and message
  classification call `anthropic/claude-haiku-4.5` (primary model), with
  escalation to `anthropic/claude-sonnet-5` on malformed JSON, low
  confidence, or a failed amount sanity check (see
  `code/models_config.json` and `code/evidence.py::_escalate`). Results are
  cached to `code/cache/{images,messages}/<id>.json` keyed by
  `image_id`/`message_id`, so re-runs against the same dataset make zero
  additional calls.
- **Explanation synthesis** (`decision_explanation` column) is always
  template-generated deterministically in `code/decide.py::_build_explanation`
  — it does not call an LLM.

## Where output lands

- `output.csv` at the repository root — the final submission artifact, one
  row per row in `dataset/requests.csv` (250 rows + header), in the exact
  column order required by the contract.
- `sample_output.csv` at the repository root — informal sample-run output,
  not part of the submission contract.

## Solution structure (`code/`)

| Module | Role |
|---|---|
| `main.py` | CLI entry point (`--full` / `--sample` / `--validate`); wires the pipeline and writes `output.csv`. |
| `loaders.py` | Reads and parses all `dataset/*.csv` files into typed records. |
| `fx.py` | Fixed-date exchange-rate lookups and currency conversion (`FxTable`). |
| `reconstruct.py` | Reconstructs each user's cash ledger from financial events; detects recurring commitments and resolves linked event chains. |
| `evidence.py` | LLM-backed (or deterministic-fallback) image amount extraction and message classification, with on-disk caching and telemetry in `code/cache/`. |
| `decide.py` | Core decision engine: affordability, payment plan selection, spending-change suggestions, and deterministic explanation synthesis. |
| `simulate.py` | Forward-simulates a candidate payment plan against the reconstructed ledger to verify feasibility before it's chosen. |
| `validate.py` | Standalone conformance validator for a generated `output.csv` (columns, types, referential integrity, spending-change bounds). |
| `models_config.json` | Pinned model slugs and OpenRouter list prices (no keys). |
| `prompts/` | LLM prompt templates used by `evidence.py`. |
| `cache/` | Per-run LLM call cache + `telemetry.json` (gitignored from `code.zip`; regenerated on demand, not required for reproducing `output.csv`'s deterministic-fallback content). |

## Tests

```bash
python3 -m unittest discover -s tests
```

Current status: 44/44 tests passing.

## Backtest against golden cases

```bash
python3 evaluation/backtest.py
```

Compares the pipeline's output against the hand-labeled golden cases in
`evaluation/golden_cases.md` / `evaluation/rulings.md`. Current status:
4/17/18/15/11/22/10 matches vs. gold across the tracked categories (see
`evaluation/backtest.py` output for the category breakdown).

## Usage / cost report

```bash
python3 evaluation/generate_usage_report.py
```

Regenerates `evaluation/usage_report.md` from `code/models_config.json`
(pinned prices) and `code/cache/` (telemetry + per-call cache files). Run
this after every full-dataset run so the report reflects the run that
produced the submitted `output.csv`.
