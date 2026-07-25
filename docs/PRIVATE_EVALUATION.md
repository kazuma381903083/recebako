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

The report contains case IDs and safe aggregate or outcome metadata:

- target count and processing success rate;
- schema success rate;
- confirmed, review, and failed rates;
- per-case and aggregate duration;
- safe error-code and validation-issue distributions;
- tax normalization adoption and rejection counts;
- date normalization and duplicate outcomes;
- aggregate human-ground-truth accuracy when available.

It never contains images, source filenames or paths, store or item text, amounts,
raw Ollama responses, hashes, EXIF, or receipt database identifiers.
