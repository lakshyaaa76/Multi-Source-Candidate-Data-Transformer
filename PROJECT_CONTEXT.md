# PROJECT_CONTEXT.md — Multi-Source Candidate Data Transformer

> Living design document. Updated as decisions are made/changed during implementation.
> Status: **Design finalized, implementation not started.**

---

## 1. Problem Understanding

Eightfold receives candidate data from multiple, structurally different sources. The same
human may show up in several sources with different field names, formats, and sometimes
contradictory values. Downstream hiring products need exactly **one canonical profile per
candidate**, in a fixed shape, where:

- every value is normalized (dates, phones, skills, etc.),
- duplicate source records for the same person are merged into one,
- every field carries **provenance** (which source(s) it came from) and a **confidence**,
- the system prefers an honestly-empty field over a wrong-but-confident one (silent bad
  data is worse than no data — explicitly called out in the brief),
- the output shape itself is **not hardcoded** — a runtime config can select, rename, and
  re-normalize fields without touching code.

So there are really two layered problems:
1. **Extraction + normalization + merge** → one internal canonical record per candidate.
2. **Projection** → render that canonical record into whatever shape a config asks for.

These two must stay decoupled (the brief explicitly asks for "a clean separation between
your internal canonical record and a projection layer").

---

## 2. Scope of This Implementation

Given the deadline (tomorrow morning), this is a **correct, well-reasoned partial solution**,
not a production system. We optimize for: clear pipeline boundaries, correct core logic,
honest edge-case handling, and a result we can explain and defend — not breadth of features.

### In scope
- 4 source parsers (chosen to exceed the "at least one structured + one unstructured" bar,
  since all four are listed in the brief and are tractable in the time available):
  - **Structured**: Recruiter CSV export, ATS JSON blob.
  - **Unstructured**: Resume PDF (text-based, not scanned/OCR), Recruiter notes (.txt).
- Canonical merge engine with provenance + confidence.
- Config-driven projection layer (field selection, renaming via `from`, per-field
  normalization, provenance/confidence toggle, `on_missing` policy).
- Schema validation of the final projected output.
- CLI entrypoint.
- A handful of targeted unit/integration tests, including at least one edge case
  (conflicting sources, malformed input, missing source).
- Small synthetic sample fixtures for all 4 source types (none were provided in the
  assignment, so we author minimal realistic ones ourselves).

### Out of scope (see §15 for explicit reasoning)
- GitHub/LinkedIn live API integration.
- DOCX resume parsing (PDF only).
- OCR / scanned PDF support.
- A real web UI (CLI only).
- ML-based entity resolution / fuzzy name matching across sources (we use a simpler
  deterministic key strategy — see §10).
- Persistence layer / database (in-memory + file I/O only).
- Multi-candidate batch dedup across *different* people with similar names (we dedupe
  records that are *already grouped* per candidate — see Assumptions).

---

## 3. Functional Requirements (extracted from assignment)

1. Accept inputs from ≥2 source types, ≥1 structured and ≥1 unstructured.
2. Handle any source being missing, empty, or malformed without crashing.
3. Handle the same person appearing in multiple sources with conflicting values.
4. Produce one canonical profile per candidate matching the fixed internal schema.
5. Normalize: phone → E.164, dates → `YYYY-MM`, skills → canonical names.
6. Populate `provenance` (field, source, method) and `overall_confidence`.
7. Accept a runtime **projection config** (JSON) that can:
   - select a subset of fields,
   - rename/remap a field from a canonical path (`from`),
   - set per-field normalization,
   - toggle provenance/confidence on/off,
   - choose missing-value behavior: `null` | `omit` | `error`.
8. Validate output against the requested (projected) schema before returning it.
9. Run end-to-end via CLI on sample inputs, emit valid JSON for default schema + ≥1 custom
   config.
10. Degrade gracefully on missing/garbage sources (never hard-crash the whole pipeline
    because one source is bad).

---

## 4. High-Level Pipeline

```
            ┌──────────────┐
 raw files  │   Loaders    │  one per source type — read file → raw dict/text, tagged
 ─────────► │ (per source) │  with source_type + source_id; never raises on bad input,
            └──────┬───────┘  returns a LoadResult{ok, data, error}
                   │
                   ▼
            ┌──────────────┐
            │   Parsers /   │  source-specific extraction into a *partial* candidate
            │  Extractors   │  record using the canonical field names (PartialRecord)
            └──────┬───────┘
                   │  list[PartialRecord], each tagged with its source
                   ▼
            ┌──────────────┐
            │  Normalizers  │  shared, field-keyed normalization functions applied to
            │ (per field)   │  every PartialRecord before merge (phones, dates, skills,
            └──────┬───────┘  locations, names) — same normalizer used later by the
                   │           projection layer for custom `normalize` directives
                   ▼
            ┌──────────────┐
            │  Identity /   │  groups PartialRecords that refer to the same candidate
            │   Grouping    │  (email/phone match → same candidate_id)
            └──────┬───────┘
                   │  dict[candidate_id -> list[PartialRecord]]
                   ▼
            ┌──────────────┐
            │    Merger     │  field-by-field conflict resolution across a candidate's
            │ (conflict res)│  PartialRecords → CanonicalRecord + Provenance +
            └──────┬───────┘  confidence per field + overall_confidence
                   │  list[CanonicalRecord]
                   ▼
            ┌──────────────┐
            │  Validator    │  validates CanonicalRecord against the internal schema
            │  (internal)   │  (sanity checks: well-formed, no orphan refs, etc.)
            └──────┬───────┘
                   │
                   ▼
            ┌──────────────┐
            │  Projection   │  applies the runtime OutputConfig: select / rename /
            │    Engine     │  re-normalize / drop provenance / on_missing policy
            └──────┬───────┘
                   │  list[dict]  (shape defined by config)
                   ▼
            ┌──────────────┐
            │  Validator    │  validates the *projected* output against the schema
            │  (output)     │  implied by OutputConfig (required fields, types)
            └──────┬───────┘
                   │
                   ▼
              JSON output (stdout / file)
```

Each stage is a pure function/class with a narrow input/output contract, which is what
makes it possible to unit-test stages independently and to explain the design later.

---

## 5. System Architecture

- **Pipeline orchestration**: a single `Pipeline` class wires the stages above; the CLI
  is a thin wrapper around it.
- **Per-stage isolation**: a loader/parser failure for one source produces a `LoadResult`/
  `PartialRecord` with `ok=False` and an error note instead of raising — the pipeline logs
  it and continues with whatever sources succeeded. This directly implements "degrade
  gracefully on a missing/garbage source."
- **Shared normalization library**: normalizers live in one module (`normalize.py`) and
  are reused in two places — (a) during merge, to build the canonical record, and (b)
  inside the projection engine, when a config requests a `normalize` directive on a
  projected field. One implementation, two call sites — no drift between "canonical
  normalization" and "config-time normalization."
- **Confidence is computed, not hardcoded per source**: a small deterministic scoring
  function combines source reliability tier + field-extraction method + cross-source
  agreement (see §11). This keeps confidence explainable rather than a magic number.
- **No global mutable state**: every stage returns new objects; makes the pipeline
  trivially re-runnable and testable.

---

## 6. Folder Structure

```
candidate-transformer/
├── PROJECT_CONTEXT.md
├── PROGRESS.md
├── README.md                      # how to run, sample commands
├── requirements.txt
├── configs/
│   ├── default_config.json        # mirrors the full canonical schema
│   └── example_custom_config.json # the example from the assignment, adapted
├── samples/                       # synthetic sample inputs (authored by us)
│   ├── recruiter_export.csv
│   ├── ats_blob.json
│   ├── resume_jane_doe.pdf
│   ├── recruiter_notes_jane_doe.txt
│   └── ... (a couple more candidates incl. one malformed file on purpose)
├── src/
│   └── transformer/
│       ├── __init__.py
│       ├── models.py              # PartialRecord, CanonicalRecord, Provenance, dataclasses
│       ├── loaders/
│       │   ├── csv_loader.py
│       │   ├── ats_json_loader.py
│       │   ├── resume_pdf_loader.py
│       │   └── recruiter_notes_loader.py
│       ├── normalize.py           # phone, date, skill, name, location normalizers
│       ├── identity.py            # grouping/dedup logic
│       ├── merge.py               # conflict resolution + confidence scoring
│       ├── validate.py            # internal + projected schema validation
│       ├── projection.py          # config-driven projection engine
│       ├── pipeline.py            # orchestrates all stages
│       └── cli.py                 # argparse entrypoint
├── tests/
│   ├── test_normalize.py
│   ├── test_merge_conflicts.py
│   ├── test_projection.py
│   └── test_pipeline_end_to_end.py
└── output/                        # generated JSON lands here (gitignored in spirit)
```

---

## 7. Technology Stack & Libraries

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | Fast to write, readable for review, strong stdlib for CSV/JSON. |
| PDF text extraction | `pdfplumber` | Reliable text extraction for text-based resumes; small dependency footprint vs. heavier OCR stacks (which are explicitly out of scope). |
| Phone normalization | `phonenumbers` (Google's libphonenumber port) | Correct E.164 formatting is genuinely hard to hand-roll (country code inference, validity); this is the standard, well-tested library for it. |
| Schema validation | `jsonschema` | Declarative, lets us validate both the internal canonical schema and the dynamically-generated projected schema from the same library without writing a hand-rolled validator. |
| Everything else (CSV, JSON, regex, dataclasses, argparse) | stdlib | No need to add dependency weight for things Python already does well; keeps the project easy to set up and review (one `requirements.txt` with 2 real entries). |
| Tests | `pytest` | Standard, minimal ceremony. |

We deliberately avoid an NLP/NER library for resume/notes parsing (e.g. spaCy) — given
the time budget, a well-scoped set of regex + heuristic extractors over realistic sample
text is more defensible and explainable than a half-tuned ML extractor. This is called
out explicitly in §15.

---

## 8. Canonical Data Model

Matches the assignment's default schema, expressed as Python dataclasses (illustrative,
not final code):

```python
@dataclass
class Provenance:
    field: str            # canonical field path, e.g. "phones[0]" or "skills[2].name"
    source: str            # source_id, e.g. "csv:recruiter_export.csv" or "pdf:jane_doe_resume.pdf"
    method: str             # "direct" | "regex" | "heuristic" | "merged"
    confidence: float       # 0..1, field-level

@dataclass
class CanonicalRecord:
    candidate_id: str
    full_name: str | None
    emails: list[str]
    phones: list[str]                       # E.164
    location: dict | None                   # {city, region, country}
    links: dict                             # {linkedin, github, portfolio, other: []}
    headline: str | None
    years_experience: float | None
    skills: list[dict]                      # [{name, confidence, sources: [source_id]}]
    experience: list[dict]                  # [{company, title, start, end, summary}]
    education: list[dict]                   # [{institution, degree, field, end_year}]
    provenance: list[Provenance]            # one entry per populated field/sub-field
    overall_confidence: float
```

A `PartialRecord` is structurally the same shape but every field is allowed to be
`None`/empty, plus it carries `source_id` and `source_type`. This is what each
loader/parser produces, before merge.

---

## 9. Parsing Strategy Per Source

| Source | Approach | Notes / honesty about limits |
|---|---|---|
| **Recruiter CSV** | `csv.DictReader`, direct column → canonical field mapping (`name→full_name`, `email→emails[0]`, `phone→phones[0]`, `current_company`+`title`→one `experience` entry with `end=null`). | High-confidence, `method="direct"`. Malformed rows (missing required columns) are skipped per-row, not per-file; file-level parse errors (bad encoding, no header) downgrade the whole source to `ok=False`. |
| **ATS JSON blob** | `json.load`, explicit field-name remap table (their schema → ours), since the brief states field names do not match ours. | Same confidence tier as CSV (`method="direct"` once remapped) since it's still structured. Unknown/extra ATS fields are ignored, not errored. |
| **Resume PDF** | `pdfplumber` extracts raw text; section-aware heuristics (regex on headers like "EXPERIENCE", "EDUCATION", "SKILLS"; email/phone via regex; name via first non-empty line heuristic). | `method="heuristic"`/`"regex"`, lower confidence than structured sources. We explicitly do **not** attempt layout-based / multi-column resume parsing — flat single-column resumes only, called out as an assumption. Non-text PDFs (scanned images) are detected (near-zero extracted text) and the source is marked `ok=False` rather than silently returning garbage. |
| **Recruiter notes (.txt)** | Free text; regex for email/phone if present; light keyword heuristics for skills (match against a small canonical skills list) and company/title mentions. | Lowest confidence tier — notes are the least structured and most likely to be opinion/commentary rather than fact. We bias toward extracting *little but correct* over *a lot but guessed*, per the brief's "wrong-but-confident is worse than honestly-empty" principle. |

All four loaders share a `LoadResult(ok: bool, source_id: str, data: Any, error: str | None)`
contract so the pipeline treats them uniformly.

---

## 10. Identity / Grouping Strategy (cross-source dedup)

Given the scope (a handful of synthetic sample candidates, not a production identity
graph), we use a simple, explainable deterministic strategy rather than fuzzy matching:

1. Normalize email (lowercase, trim) — if two PartialRecords share a normalized email,
   they're the same candidate.
2. Else, normalize phone to E.164 — shared E.164 phone ⇒ same candidate.
3. Else, fall back to normalized full name as a weak signal **only** if no other
   PartialRecord already claims that exact normalized name with a *different* email/phone
   (to avoid false-merging two different people with the same common name) — this fallback
   is logged as a low-confidence grouping decision.
4. If nothing matches, treat as a new candidate.

`candidate_id` is generated deterministically (e.g. `slugify(full_name) + short hash`) the
first time a candidate group is formed.

This is explicitly **not** real entity resolution (no Jaro-Winkler, no ML matching) — see
§15 for why that's a reasonable cut given the time budget.

---

## 11. Normalization Strategy

One module, one function per concern, reused by both merge and projection:

- **Phones** → `phonenumbers.parse` + `format_number(..., E164)`. Default region inferred
  from `location.country` if present, else a configurable fallback (default `"US"`,
  documented as an assumption). Unparseable numbers are dropped (not guessed), with a
  provenance note `method="failed_normalize"` so we can see it was attempted.
- **Dates** → tolerant parser handling common resume formats (`"Jan 2020"`, `"2020-01"`,
  `"2020"`, `"Present"/"Current"` → `end=null`) into `YYYY-MM`. Unparseable → `null`, never
  a guessed date.
- **Skills** → lowercase, trim, map through a small canonical alias table (e.g.
  `"js"/"javascript"/"Javascript"` → `"JavaScript"`, `"nodejs"` → `"Node.js"`). Anything not
  in the alias table passes through title-cased as-is rather than being dropped (skills
  vocab is open-ended; we don't want to silently lose real skills not in our seed list).
- **Names** → trim, collapse whitespace, preserve original casing (no guessing of proper
  casing).
- **Location** → best-effort split of free text into city/region/country; country coerced
  to ISO-3166 alpha-2 via a small lookup table for common country names; unrecognized
  → `country=null` rather than a wrong guess.

---

## 12. Merge / Conflict Resolution Strategy

For each candidate's group of `PartialRecord`s, merge field by field:

- **List-type fields** (`emails`, `phones`, `links.other`): **union** (deduplicated, order
  by source priority) — more contact info is strictly useful, low risk of harm.
- **Scalar fields with conflicts** (`full_name`, `headline`, `location`,
  `years_experience`): resolved by **source-priority ranking**, in this default order:

  `ats_json > csv > resume_pdf > recruiter_notes`

  Rationale: ATS/CSV are recruiter/system-entered structured data (closest to "ground
  truth" at intake); resume is candidate-self-reported and more current for things like
  headline/experience but more error-prone to parse; recruiter notes are the least
  structured and most likely to contain opinion or stale info. This ranking is a
  **default, not hardcoded** — it's a config-level constant (`SOURCE_PRIORITY` list) that
  could be overridden, but we don't expose it through the runtime `OutputConfig` (that
  config reshapes *output*, not merge logic) — that distinction is intentional and noted
  as a scope boundary in §15.
- **Tie-break within the same priority tier**: prefer the value from the source with
  higher per-field confidence (see §13); if still tied, prefer the more recently-seen
  source (stable, deterministic order of inputs as given to the CLI).
- **`experience`/`education` (list of structured entries)**: merged by approximate entry
  identity (same `company`+overlapping date range, or same `institution`+`degree`) rather
  than naive concatenation, so the same job mentioned in both CSV and resume becomes one
  entry with the more complete `summary` field filled in from whichever source has it.
- Every merge decision producing the final value for a field appends a `Provenance` entry
  (or one per source if multiple sources agreed, since agreement boosts confidence — see
  §13) — so a field's full sourcing is always inspectable, not just the "winner."

---

## 13. Provenance & Confidence Approach

**Provenance** — list of `{field, source, method}` entries. Multiple sources contributing
or agreeing on the same field each get an entry, so a reviewer can see "this email came
from CSV and was confirmed by the resume" vs. "this email only came from recruiter notes."

**Field-level confidence** — a simple, explainable score, not a black box:

```
base_score(method)            # direct=0.9, regex=0.7, heuristic=0.5, failed_normalize=0.1
  × source_tier_weight(source) # ats/csv=1.0, resume=0.85, notes=0.7
  + agreement_bonus            # +0.1 per additional independent source agreeing,
                                # capped at 1.0
```

**`overall_confidence`** — weighted average of populated required-field confidences
(missing optional fields don't drag the score down; missing *required* fields, like name/
email/skills, drag it down more, since those matter most downstream).

This keeps the scoring rule small enough to state and defend in two sentences during
review, rather than an opaque ML-flavored heuristic.

---

## 14. Projection / Configuration Layer Design

The projection layer takes a `CanonicalRecord` + an `OutputConfig` and produces a plain
dict matching the requested shape. Pure, side-effect-free, and reused for both the
default config and any custom config.

`OutputConfig` shape (matches the assignment's example):
```json
{
  "fields": [
    { "path": "full_name", "type": "string", "required": true },
    { "path": "primary_email", "from": "emails[0]", "type": "string", "required": true },
    { "path": "phone", "from": "phones[0]", "type": "string", "normalize": "E164" },
    { "path": "skills", "from": "skills[].name", "type": "string[]", "normalize": "canonical" }
  ],
  "include_confidence": true,
  "include_provenance": false,
  "on_missing": "null"
}
```

- `path` — the field name in the *output*.
- `from` — optional dotted/bracketed path into the canonical record (defaults to `path`
  if omitted). Supports simple indexing (`emails[0]`) and list-projection
  (`skills[].name` → maps `name` over the `skills` list).
- `type` — declared output type, used to build the JSON-schema for output validation.
- `normalize` — optional override; if present, re-runs the shared normalizer from §11 on
  the extracted value at projection time (so a config can ask for a field to be
  normalized differently than how it's stored canonically — though in this implementation
  canonical storage is already normalized, so this is mostly idempotent; the hook exists
  to satisfy the requirement and support future divergence).
- `on_missing`: `"null"` (emit field with `null`), `"omit"` (drop the key entirely),
  `"error"` (raise `MissingRequiredFieldError`, surfaced as a per-candidate error in the
  CLI output rather than crashing the whole batch).

The engine builds a JSON-schema on the fly from `fields[].type` + `required`, and runs
`jsonschema.validate` on every projected record before returning it — satisfying
"validate the result against the requested schema."

---

## 15. Validation Approach

Two validation passes, both via `jsonschema`:

1. **Internal/canonical validation** (`validate.py: validate_canonical`) — sanity-checks
   the `CanonicalRecord` right after merge: correct types, `candidate_id` present,
   `overall_confidence` in `[0,1]`, no `Provenance` entry referencing a field that doesn't
   exist. This catches bugs in our own pipeline, not user input.
2. **Output/projected validation** (`validate.py: validate_projection`) — validates each
   projected dict against the schema *derived from the `OutputConfig` itself*
   (auto-generated, not hand-maintained), enforcing `required` fields and declared
   `type`s. A record that fails `on_missing="error"` surfaces as a structured per-record
   error in the CLI's final report rather than aborting the run.

---

## 16. Edge Cases To Handle

- Source file missing entirely (path doesn't exist) → that source skipped, logged, rest
  of pipeline proceeds.
- Source file present but empty / zero rows → treated as "ok, zero records," not an error.
- Malformed CSV (missing header, wrong delimiter) / malformed JSON (invalid syntax) →
  whole source marked `ok=False`, run continues with other sources.
- One bad row/record inside an otherwise-good CSV → skip that row, keep the rest.
- Scanned/image-only PDF (no extractable text) → detected via near-empty extracted text,
  source marked `ok=False` rather than emitting garbage.
- Same candidate across sources with **conflicting** scalar values (e.g. two different
  current companies) → resolved per §12, both values still visible in provenance, not
  silently lost.
- Phone number that can't be parsed into E.164 → field omitted from `phones`, *not*
  guessed; logged via `failed_normalize` provenance method.
- Date strings that can't be parsed (`"a while back"`) → `null`, not guessed.
- Candidate with **no structured source at all** (e.g. resume + notes only) — should
  still produce a valid (lower-confidence) canonical record, since the brief requires
  ≥1 structured + ≥1 unstructured *across the whole pipeline run*, not necessarily per
  candidate.
- `OutputConfig` references a `from` path that doesn't exist on the canonical record →
  treated as "missing," subject to `on_missing` policy, not a crash.
- Two different people who happen to share a common full name but no shared email/phone
  → must **not** be merged (tested explicitly — this is the trap the naive
  "group by name" approach would fall into).
- Empty `fields` list in `OutputConfig` → produces an (almost) empty but still
  schema-valid record per candidate, not an error.

---

## 17. Assumptions

- Default phone region fallback is `"US"` when no location/country is otherwise inferable
  (documented, overridable in code/config, not silently invisible).
- Resumes are single-column, text-extractable PDFs in English; no OCR, no complex layout
  parsing.
- "Same candidate" grouping is decided primarily by exact email/phone match; common-name
  collisions across genuinely different people are assumed rare in the sample set and are
  explicitly tested as a non-merge case.
- The small canonical-skills alias table is illustrative (~30–40 common tech skills), not
  an exhaustive industry taxonomy — acceptable given time constraints; unknown skills pass
  through rather than being dropped.
- `SOURCE_PRIORITY` for conflict resolution is a pipeline-level default, not exposed
  through the runtime `OutputConfig` (which governs projection, not merge logic).
- Sample input files are authored by us (synthetically), since the assignment did not
  attach actual sample data; they're designed to exercise the edge cases in §16, including
  at least one deliberately malformed file.

---

## 18. Features Intentionally Left Out (and why)

| Feature | Why left out |
|---|---|
| GitHub/LinkedIn live API integration | Requires network/auth handling and rate-limit logic that's orthogonal to the core merge/projection problem the assignment is actually testing; CSV+ATS JSON+PDF+notes already satisfies "≥1 structured + ≥1 unstructured" with margin, and adding two more network-dependent parsers risks flaky demo behavior right before a deadline. |
| DOCX resume parsing | PDF parsing already demonstrates the unstructured-extraction pattern; adding a second resume format multiplies parsing edge cases without adding new pipeline-design insight. |
| OCR for scanned PDFs | Genuinely a different (image processing) problem; explicitly out of scope per the brief's spirit of "core, not exhaustive." We detect and gracefully skip such files instead of mishandling them. |
| Fuzzy/ML entity resolution across candidates | Given the small sample set and time budget, a deterministic email/phone-based grouping is more defensible and testable than a half-tuned fuzzy matcher; a fuzzy matcher introduces false-merge risk, which is the exact "wrong-but-confident" failure mode the brief warns against. |
| Web UI | Brief explicitly says CLI is fine and lower priority; CLI lets us spend the saved time on correctness and tests instead. |
| Persistent storage / DB | No requirement for it; file-in/file-out is sufficient and simpler to review. |
| Configurable `SOURCE_PRIORITY` via `OutputConfig` | Conflated concerns — merge-time conflict resolution and output-time projection are different layers; keeping `SOURCE_PRIORITY` as a pipeline constant (still easily editable in one place) keeps the projection config focused on what the assignment actually asks it to do. |

---

## 19. Open Questions / Decisions Already Made

(For traceability — answered during design discussion, recorded here so we don't
re-litigate mid-implementation.)

- **Source set**: CSV + ATS JSON (structured) and Resume PDF + Recruiter notes .txt
  (unstructured) — all four, exceeding the "≥1 each" minimum. *Decided.*
- **Stack**: Python, stdlib-first, + `pdfplumber`, `phonenumbers`, `jsonschema`, `pytest`.
  *Decided.*
- **Default conflict resolution**: source-priority ranking
  (`ats_json > csv > resume_pdf > recruiter_notes`) with confidence/order tie-breaks.
  *Decided — simple, explainable default per assignment guidance.*
