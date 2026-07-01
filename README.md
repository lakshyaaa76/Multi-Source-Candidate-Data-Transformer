# Multi-Source Candidate Data Transformer

A Python pipeline that ingests candidate data from multiple structured and unstructured sources, merges conflicting information into a single canonical candidate profile, and produces a configurable JSON output with provenance and confidence tracking.

---

# Prerequisites

- Python 3.11+

---

# Setup

Create a virtual environment and install the required dependencies.

```bash
python -m venv venv

# Windows PowerShell
.\venv\Scripts\Activate.ps1

# macOS / Linux
# source venv/bin/activate

pip install -r requirements.txt
```

Set the Python source path.

### Windows PowerShell

```powershell
$env:PYTHONPATH = "src"
```

### macOS / Linux

```bash
export PYTHONPATH=src
```

---

# Running the Project

Follow the steps below to verify all major features of the pipeline.

---

## Step 1 – Run the complete pipeline

```powershell
python -m transformer.cli --dir samples --output output/demo_full.json
```

Expected result:

- `output/demo_full.json` is created.
- 7 merged candidate records are produced.
- 3 malformed source files are reported as warnings.
- The pipeline completes successfully.

---

## Step 2 – Run with the custom output configuration

```powershell
python -m transformer.cli --dir samples --config configs/example_custom_config.json --output output/demo_custom.json
```

Expected result:

- Output is generated using the custom projection configuration.
- `primary_email` replaces `emails[0]`.
- Phone numbers are normalized to E.164 format.
- Skills are projected as a string array.
- Provenance is omitted while confidence scores are retained.

---

## Step 3 – Verify graceful degradation

```powershell
python -m transformer.cli --files samples/recruiter_export_malformed.csv samples/ats_blob_malformed.json samples/resume_scanned_no_text.pdf
```

Expected result:

- The pipeline does not crash.
- All malformed files are reported in `source_errors`.
- No candidate records are produced.
- The program exits with a non-zero status code.

---

## Step 4 – Verify edge cases

```powershell
python -m transformer.cli --dir samples --config configs/example_custom_config.json --no-metadata
```

Verify the following:

- Two candidates named **Bob Lee** remain separate because they have different email addresses.
- **Alice Nguyen** is successfully created using only unstructured sources.
- Missing values are preserved as `null` where applicable.

---

# Running Individual Files

The pipeline also supports processing specific input files instead of the entire sample directory. This is useful for quickly testing individual candidates or verifying a particular source type.

```powershell
# Process specific source files
python -m transformer.cli `
    --csv samples/recruiter_export.csv `
    --ats samples/ats_blob.json `
    --pdf samples/resume_jane_doe.pdf `
    --notes samples/recruiter_notes_jane_doe.txt `
    --output output/jane_doe.json
```

You can also use the `--files` option to provide any combination of supported input files, with the pipeline automatically detecting the source type from each file extension.

---

# Submission Checklist

The above steps demonstrate:

- Multi-source data ingestion
- Data normalization
- Identity resolution
- Conflict resolution
- Configurable output projection
- Provenance tracking
- Confidence scoring
- Graceful degradation
- Edge case handling

