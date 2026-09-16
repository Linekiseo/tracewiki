# Code Golden v1

This directory contains the immutable, treatment-blinded C0-02 Code Golden
dataset. It is data and validation material only; it does not contain or claim a
C0-03 baseline.

Files:

- `code-golden-v1.manifest.json` — version, counts, slices, review record,
  checksums, and C0-01 field mapping.
- `code-golden-v1.cases.yaml` — 50 annotated cases and the 15-case smoke subset.
- `annotation-contract-v1.schema.json` — machine-readable per-case contract.

The deterministic multilingual repository is rooted at
`tests/fixtures/code_golden/repository`. Historical snapshots, diffs, stack
traces, and validation results are separate fixture assets under
`tests/fixtures/code_golden/history`; no developer-home path is required.

Run the independent validation with:

```text
pytest tests/test_code_golden_dataset.py
```

Changing any case, judgment, fixture, or contract requires a new Golden version
and refreshed checksums. Results from future baselines or treatments must never
be used to silently edit v1.
