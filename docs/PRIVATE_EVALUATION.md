# Private evaluation

`recebako evaluate run` compares local Ollama models without placing receipt images,
ground truth, databases, or extracted values in Git.

## Data boundary

- Put only anonymized image copies directly below an absolute, Git-unmanaged source
  directory.
- Name images `case-0001`, `case-0002`, and so on, retaining a supported image
  suffix.
- Do not use nested entries or symbolic links.
- Use an absolute, Git-unmanaged output directory that does not overlap the source
  directory or the normal `data.root`.
- Treat the whole output directory as private. Model databases and copied inputs
  contain private extraction data even though the JSON report does not.
- The command copies source images into each isolated model runtime. It does not
  move, rename, edit, or remove source images.
- Runtime inference remains limited to the configured localhost Ollama endpoint.
- Run the evaluation only in a trusted local login session. Static symbolic links
  and directory replacement between processing stages are rejected, but a hostile
  process running concurrently as the same OS account is outside the isolation
  boundary because it already has equivalent access to the private source and
  runtime.

## Run

The default command evaluates both required models under the same mode and
reference date:

```bash
recebako evaluate run /absolute/git-unmanaged/cases \
  --output-root /absolute/git-unmanaged/evaluation-output \
  --reference-date 2026-01-31
```

The default model order is:

1. `qwen3-vl:8b`
2. `qwen3.5:9b`

Repeat `--model` to select one or both models explicitly. Each model receives its
own runtime and `ledger.db`, so one model's duplicate decisions cannot affect the
other. The normal configured `data.root` is never initialized or written by an
evaluation run.

Successful stdout is one JSON report. Operational errors use a fixed,
private-safe stderr message. A copy of the report is written as
`RUN_ID/evaluation-report.json` below the output root.

## Human ground truth

Ground truth is an optional UTF-8 CSV stored outside Git. Its header must match
exactly:

```text
case_id,human_verified,expected_store,expected_date,expected_total,expected_status,item_index,expected_item_name,expected_item_qty,expected_item_price
```

For a verified case:

- use one row per expected item;
- repeat the receipt-level expected fields consistently on every row;
- number `item_index` contiguously from `0`;
- use `true` for `human_verified`;
- use `confirmed`, `review`, or `failed` for `expected_status`.
- record `expected_item_qty` as the printed quantity and
  `expected_item_price` as that item's tax-inclusive integer line total, not its
  unit price and not a value to multiply by quantity again;
- record a separately printed discount as its own item row with quantity `1` and
  a negative tax-inclusive line total.

For a case that has not been verified by a person, use exactly one row with
`human_verified` set to `false` and leave every expected field empty. AI output
must never be copied into this CSV as ground truth.

Pass the CSV with:

```bash
recebako evaluate run /absolute/git-unmanaged/cases \
  --output-root /absolute/git-unmanaged/evaluation-output \
  --ground-truth /absolute/git-unmanaged/human-ground-truth.csv
```

Without any human-verified cases, `accuracy.status` is `unknown` and its reason is
`no_human_verified_ground_truth`. Confirmed rate remains an operational outcome,
not an accuracy measurement. When verified cases exist, the report contains only
aggregate comparison counts and rates.

## Report contents

Successful stdout and `RUN_ID/evaluation-report.json` retain schema version `1`
unchanged. The report contains case IDs and safe aggregate or outcome metadata:

- target count and processing success rate;
- schema success rate;
- confirmed, review, and failed rates;
- per-case and aggregate duration;
- safe error-code and validation-issue distributions;
- tax normalization adoption and rejection counts;
- date normalization and duplicate outcomes;
- aggregate human-ground-truth accuracy when available.

It never contains images, source filenames or paths, store or item text, amounts,
raw Ollama responses, receipt or image hashes, EXIF, or receipt database
identifiers.

Every run also writes the aggregate-only
`RUN_ID/quality-baseline-report.json` sidecar. Its report schema version is `1`
and its metric version is `quality-v1`. For each model the sidecar contains:

- model, prompt, and extraction-schema provenance;
- the existing aggregate summary and aggregate human-ground-truth accuracy;
- target and human-verified counts;
- aggregate NFR-Q1 through Q5 counts, rates, fixed thresholds, and assessments;
- metric version.

The sidecar does not contain case IDs or any per-case entry. Prompt and schema
provenance hashes cover repository-owned extraction contracts only and never
include private inputs, ground truth, model output, or an evaluation database.
The stdout contract is not extended with the sidecar. Without human-verified
ground truth, Q1 through Q4 rates are `null`; Q5 can still have an observed rate
over all target cases. All assessments remain `unknown` while the golden set is
incomplete.

## Quality baseline

`quality-v1` uses only `human_verified=true` cases for Q1 through Q4. Processing
or schema failures for those cases count as mismatches instead of disappearing
from the denominator. Q5 remains an operational rate over every target case.

- Q1 is exact total matches divided by verified cases.
- Q2 requires both normalized store matches and exact normalized-date matches.
- Q3 aligns exact `(raw item name, quantity, tax-inclusive line price)` tuples
  with a sequence-preserving LCS. Its denominator is the sum of
  `max(expected item count, actual item count)` for each verified case.
- Q4 is confirmed results with an incorrect total divided by confirmed results
  among verified cases. A zero confirmed denominator is `unknown`, not zero.
- Q5 is review results divided by all target cases.

Store comparison normalization is deliberately conservative: Unicode NFKC,
Unicode case folding, then removal of Unicode whitespace. It does not infer
aliases or semantic equivalence. Item names are not normalized: the raw item name,
quantity, and tax-inclusive line price must all match exactly.

Rates can be reported for an incomplete set, but Q1 through Q5 assessments remain
`unknown` unless the same run has exactly 30 target cases and all 30 are human
verified. The fixed `quality-v1` thresholds are:

- Q1: at least 98%;
- Q2 store and date: each at least 95%;
- Q3: at least 80%;
- Q4: at most 2%;
- Q5: at most 30%.

Changing a denominator, comparison rule, or threshold requires a new metric
version. See `docs/adr/002-quality-baseline-metrics.md`.
