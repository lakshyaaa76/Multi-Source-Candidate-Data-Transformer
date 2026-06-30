# Multi-Source Candidate Data Transformer

A Python pipeline that ingests candidate data from multiple source types (structured and unstructured), normalizes and merges conflicting fields into one canonical profile per candidate, and produces a config-driven JSON output with full provenance and confidence tracking.

Built for [Eightfold.ai](https://eightfold.ai) — see `PROJECT_CONTEXT.md` for the full design rationale.

---

## Setup

**Requirements:** Python 3.11+

```bash
# Create and activate a virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1   # Windows PowerShell
# source venv/bin/activate    # macOS/Linux

# Install dependencies
pip install -r requirements.txt
```

**Dependencies** (`requirements.txt`):
| Package | Purpose |
|---|---|
| `pdfplumber` | Text extraction from resume PDFs |
| `phonenumbers` | E.164 phone normalization (Google's libphonenumber) |
| `jsonschema` | Canonical + projected output schema validation |

---

## Running the Pipeline

All commands are run from the project root. Set `PYTHONPATH=src` so Python finds the `transformer` package:

```powershell
# Windows PowerShell
$env:PYTHONPATH = "src"
python -m transformer.cli [options]
```

```bash
# macOS / Linux
PYTHONPATH=src python -m transformer.cli [options]
```

---

## CLI Usage

```
python -m transformer.cli [--csv FILE...] [--ats FILE...] [--pdf FILE...] [--notes FILE...]
                          [--files FILE...]
                          [--config FILE] [--output FILE]
                          [--no-metadata] [--indent N] [--verbose]
```

### Source file arguments

| Flag | Source type | Expected format |
|---|---|---|
| `--csv FILE...` | Recruiter CSV export | `recruiter_export.csv` |
| `--ats FILE...` | ATS JSON blob | `ats_blob.json` |
| `--pdf FILE...` | Resume PDF | `resume_*.pdf` |
| `--notes FILE...` | Recruiter notes | `recruiter_notes_*.txt` |
| `--files FILE...` | Any mix | Type inferred from extension |

### Other flags

| Flag | Default | Description |
|---|---|---|
| `--config FILE` | `configs/default_config.json` | Output projection config (see §14 of `PROJECT_CONTEXT.md`) |
| `--output FILE` | stdout | Write JSON to a file instead of printing |
| `--no-metadata` | off | Suppress the `run_metadata` block from output |
| `--indent N` | `2` | JSON indentation level |
| `--verbose` / `-v` | off | Enable DEBUG logging to stderr |

---

## Example Commands

### Default schema — all sample sources

Produces the full canonical output (all fields, provenance, confidence) for all candidates:

```powershell
$env:PYTHONPATH = "src"
python -m transformer.cli `
  --csv   samples/recruiter_export.csv `
  --ats   samples/ats_blob.json `
  --pdf   samples/resume_jane_doe.pdf samples/resume_john_smith.pdf samples/resume_alice_nguyen.pdf `
  --notes samples/recruiter_notes_jane_doe.txt samples/recruiter_notes_john_smith.txt samples/recruiter_notes_alice_nguyen.txt `
  --output output/default_run.json
```

### Custom config (assignment example) — to file

Field-renamed output (`primary_email` from `emails[0]`, `phone` from `phones[0]` normalized to E.164, `skills` as a flat list of canonical names), confidence included, provenance suppressed:

```powershell
$env:PYTHONPATH = "src"
python -m transformer.cli `
  --csv   samples/recruiter_export.csv `
  --ats   samples/ats_blob.json `
  --pdf   samples/resume_jane_doe.pdf samples/resume_john_smith.pdf samples/resume_alice_nguyen.pdf `
  --notes samples/recruiter_notes_jane_doe.txt samples/recruiter_notes_john_smith.txt samples/recruiter_notes_alice_nguyen.txt `
  --config configs/example_custom_config.json `
  --output output/custom_run.json
```

### Stdout, no metadata, compact view

```powershell
$env:PYTHONPATH = "src"
python -m transformer.cli `
  --csv   samples/recruiter_export.csv `
  --ats   samples/ats_blob.json `
  --config configs/example_custom_config.json `
  --no-metadata --indent 2
```

### Extension-inferred sources

```powershell
$env:PYTHONPATH = "src"
python -m transformer.cli `
  --files samples/recruiter_export.csv samples/ats_blob.json `
          samples/resume_jane_doe.pdf samples/recruiter_notes_jane_doe.txt
```

### Graceful degradation — malformed inputs

The pipeline never crashes on bad files. Source errors appear in `run_metadata.source_errors`:

```powershell
$env:PYTHONPATH = "src"
python -m transformer.cli `
  --csv   samples/recruiter_export_malformed.csv `
  --ats   samples/ats_blob_malformed.json `
  --pdf   samples/resume_scanned_no_text.pdf
# → Done: 0 candidate(s) in output, 3 source error(s)
# → exit code 1 (non-zero when nothing produced)
```

---

## Output Format

```json
{
  "candidates": [
    {
      "full_name": "Jane Doe",
      "primary_email": "jane.doe@example.com",
      "phone": "+14155550142",
      "skills": ["Python", "AWS", "PostgreSQL", "Docker", "Go", "Kubernetes", "Terraform"],
      "overall_confidence": 0.882
    }
  ],
  "run_metadata": {
    "partial_records_loaded": 8,
    "canonical_records_merged": 4,
    "candidates_in_output": 4,
    "source_errors": [],
    "candidate_errors": []
  }
}
```

- **`candidates`** — one entry per candidate, shaped by the active `--config`.
- **`run_metadata`** — informational; includes all non-fatal errors. Suppressed by `--no-metadata`.

---

## Configuration Files

### `configs/default_config.json`
Mirrors the full canonical schema: all fields, full provenance + confidence metadata, no field renaming.

### `configs/example_custom_config.json`
The assignment example from the design brief (`PROJECT_CONTEXT.md §14`): selective fields, renaming, normalize overrides, provenance suppressed.

### Writing your own config

```json
{
  "fields": [
    { "path": "full_name",     "type": "string",   "required": true },
    { "path": "primary_email", "from": "emails[0]","type": "string",   "required": true },
    { "path": "phone",         "from": "phones[0]","type": "string",   "normalize": "E164" },
    { "path": "skills",        "from": "skills[].name", "type": "string[]", "normalize": "canonical" },
    { "path": "location",      "type": "object" }
  ],
  "include_confidence": true,
  "include_provenance": false,
  "on_missing": "null"
}
```

| Key | Values | Description |
|---|---|---|
| `path` | string | Output field name |
| `from` | dotted/bracketed path | Canonical field to read from; defaults to `path` |
| `type` | `string`, `number`, `boolean`, `object`, `array`, `string[]`, `number[]` | Declared output type (used to build the validation schema) |
| `normalize` | `E164`, `canonical`, `YYYY-MM` | Re-run the shared normalizer on the extracted value |
| `required` | `true` / `false` | Whether the field is required in the output schema |
| `include_confidence` | `true` / `false` | Append `overall_confidence` to every record |
| `include_provenance` | `true` / `false` | Append full `provenance` list to every record |
| `on_missing` | `"null"`, `"omit"`, `"error"` | What to do when the source field is absent |

---

## Project Structure

```
candidate-transformer/
├── PROJECT_CONTEXT.md          # Full design doc (architecture, decisions, rationale)
├── PROGRESS.md                 # Phase-by-phase implementation log
├── README.md                   # This file
├── requirements.txt
├── configs/
│   ├── default_config.json
│   └── example_custom_config.json
├── samples/                    # Synthetic sample inputs (all four source types)
│   ├── recruiter_export.csv
│   ├── ats_blob.json
│   ├── resume_jane_doe.pdf     # + john_smith, alice_nguyen
│   ├── recruiter_notes_jane_doe.txt  # + john_smith, alice_nguyen, empty
│   ├── recruiter_export_malformed.csv   # deliberately bad inputs
│   ├── ats_blob_malformed.json
│   └── resume_scanned_no_text.pdf
├── src/transformer/
│   ├── models.py               # PartialRecord, CanonicalRecord, OutputConfig, …
│   ├── normalize.py            # Phone (E.164), date (YYYY-MM), skill, name, location
│   ├── identity.py             # Cross-source candidate grouping (email/phone/name)
│   ├── merge.py                # Conflict resolution + provenance + confidence scoring
│   ├── validate.py             # validate_canonical + validate_projection
│   ├── projection.py           # Config-driven projection engine
│   ├── pipeline.py             # Orchestrates all stages; error collection
│   ├── cli.py                  # argparse entrypoint
│   └── loaders/
│       ├── csv_loader.py
│       ├── ats_json_loader.py
│       ├── resume_pdf_loader.py
│       └── recruiter_notes_loader.py
└── output/                     # Generated JSON output (git-ignored in spirit)
```

---

## Design Highlights

| Property | Implementation |
|---|---|
| **Graceful degradation** | Every loader returns `LoadResult(ok=False, error=...)` instead of raising; pipeline continues with remaining sources |
| **Provenance** | Every merged field records which source(s) contributed, extraction method, and field-level confidence |
| **Confidence scoring** | `base_score(method) × source_tier + agreement_bonus` — deterministic formula, stated in two sentences |
| **Conflict resolution** | Source priority `ats_json > csv > resume_pdf > recruiter_notes`; list fields are unioned |
| **No false merges** | Identity grouping by email → phone → name-only-if-unambiguous; two people named "Bob Lee" with different emails stay separate |
| **Config-driven output** | Projection layer is fully decoupled from canonical storage; `from`, `normalize`, `on_missing`, and include toggles are all runtime config |
| **Output validation** | JSON Schema is auto-generated from `OutputConfig.fields[].type` + `required`; no hand-maintained output schema |
