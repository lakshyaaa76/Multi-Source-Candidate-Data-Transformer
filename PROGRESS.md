# PROGRESS.md — Implementation Phases

> Living document, updated at the end of every phase. Statuses: **Not Started** /
> **In Progress** / **Completed**.

---

## Phase 0 — Design
**Status: Completed**
- `PROJECT_CONTEXT.md` written and reviewed.
- This `PROGRESS.md` scaffold created.

---

## Phase 1 — Project scaffolding & data models
**Status: Completed**
- Created folder structure per §6 (`configs/`, `samples/`, `src/transformer/loaders/`,
  `tests/`, `output/`).
- `requirements.txt` added (pdfplumber, phonenumbers, jsonschema, pytest).
- `models.py` implemented with all dataclasses from §8 plus a couple of supporting
  ones not spelled out in the design doc but implied by it:
  - `Location`, `Links`, `Skill`, `ExperienceEntry`, `EducationEntry` — small typed
    sub-structures instead of raw dicts, each with `to_dict()`.
  - `Provenance` (field/source/method/confidence) — added `failed_normalize` as an
    explicit `ExtractionMethod` value (alongside `direct`/`regex`/`heuristic`/`merged`)
    so a value that was attempted-but-dropped during normalization (§11, §16) is
    distinguishable in provenance from a clean merge.
  - `PartialRecord` — mirrors `CanonicalRecord`'s shape, all fields optional, tagged
    with `source_id`/`source_type`, plus an `extraction_methods` dict so each
    loader/parser can record *how* it got each field at parse time, for `merge.py`
    to consume later without re-deriving it.
  - `CanonicalRecord` — matches the assignment's default schema; `to_dict()` produces
    the final nested JSON shape (used directly for the default-schema output path).
  - `LoadResult` — uniform `{ok, source_id, source_type, data, error}` contract every
    loader will return, per §9, so the pipeline can treat all four source types
    uniformly and skip a failed source without raising.
  - `OutputConfig` / `FieldSpec` — typed representation of the runtime projection
    config from §14 (`path`, `from`, `type`, `required`, `normalize`,
    `include_confidence`, `include_provenance`, `on_missing`), with `from_dict()`
    constructors so `projection.py` can build these directly from the JSON config
    files in Phase 7.
- Stub modules created for `normalize.py`, `identity.py`, `merge.py`, `validate.py`,
  `projection.py`, `pipeline.py`, `cli.py`, and the four loader modules under
  `loaders/`, each with a docstring pointing back to its design section — no logic
  yet, just so the package imports cleanly end-to-end.
- Smoke-tested: imported every module, instantiated `PartialRecord`/`CanonicalRecord`,
  and confirmed `CanonicalRecord.to_dict()` produces valid nested JSON.

**Files created:**
`src/transformer/models.py`, `src/transformer/normalize.py`, `identity.py`,
`merge.py`, `validate.py`, `projection.py`, `pipeline.py`, `cli.py`,
`src/transformer/loaders/csv_loader.py`, `ats_json_loader.py`, `resume_pdf_loader.py`,
`recruiter_notes_loader.py`, `requirements.txt`, plus `__init__.py` files and the
`configs/`, `samples/`, `tests/`, `output/` directories.

**Implementation notes / no deviations from design:**
- All dataclass field names match §8's table exactly; no renames.
- `Location`/`Links`/`Skill`/`ExperienceEntry`/`EducationEntry` as separate dataclasses
  (rather than raw dicts, as §8's pseudocode loosely implied) is a clarification, not a
  deviation — `to_dict()` on each still serializes to exactly the dict shapes shown in
  the design doc's schema table.
- **Known environment constraint**: this sandbox has no network access, so
  `pip install -r requirements.txt` could not be run/verified here (`phonenumbers`
  unavailable offline). `models.py` has zero third-party imports, so it was smoke-tested
  standalone successfully. `pdfplumber`/`phonenumbers`/`jsonschema` will be exercised
  starting Phase 3/4 — flagging now in case local installs need to happen on your end
  before then; the actual usage is small and isolated per module, so this is easy to
  verify in your local environment, or to mock/stub if needed.

---

## Phase 2 — Sample fixtures
**Status: Completed**
- Authored synthetic fixtures covering 4 candidates + several deliberately broken files,
  exceeding the original plan of "2–3 candidates" since the extra cases were cheap to add
  and each maps directly to an edge case in §16:
  - **Jane Doe** — present in all 4 sources, consistent identity, values agree (happy
    path); skills given in different casings across sources to test canonicalization.
  - **John Smith** — present in all 4 sources with a deliberate **3-way conflicting**
    employer/title (CSV vs. ATS JSON vs. resume), to properly exercise source-priority
    conflict resolution in Phase 5.
  - **Alice Nguyen** — resume + notes only, **no CSV/ATS row at all** — covers "candidate
    with no structured source."
  - **Bob Lee × 2** — two CSV rows, same full name, different email/phone/company —
    covers the no-false-merge identity guard.
  - One CSV row with a blank `name` field, and one ATS JSON entry with blank name +
    unparseable phone — cover per-row/per-record degradation within an otherwise-good
    structured source.
  - `ats_blob_malformed.json` (truncated JSON) and `recruiter_export_malformed.csv` (no
    real header) — whole-source malformed-input cases.
  - `resume_scanned_no_text.pdf` — generated with reportlab using only shape-drawing (no
    text calls); verified with `pdfplumber` to extract exactly 0 characters, confirming
    it will correctly trip "non-text PDF" detection in Phase 3.
  - `recruiter_notes_empty.txt` — 0-byte file, covers "empty but not malformed."
- `samples/README.md` documents every fixture and which design-doc edge case it maps to,
  so reviewers (and future-us in Phase 3) don't have to reverse-engineer intent from data.
- `scripts/generate_sample_pdfs.py` added (one-off generator, not part of the pipeline
  package) using `reportlab`, which was available in this environment; reused for the
  blank/no-text PDF as well by drawing only filled rectangles, no text operations.

**Files created:** `samples/recruiter_export.csv`, `samples/recruiter_export_malformed.csv`,
`samples/ats_blob.json`, `samples/ats_blob_malformed.json`,
`samples/resume_jane_doe.pdf`, `samples/resume_john_smith.pdf`,
`samples/resume_alice_nguyen.pdf`, `samples/resume_scanned_no_text.pdf`,
`samples/recruiter_notes_jane_doe.txt`, `samples/recruiter_notes_john_smith.txt`,
`samples/recruiter_notes_alice_nguyen.txt`, `samples/recruiter_notes_empty.txt`,
`samples/README.md`, `scripts/generate_sample_pdfs.py`.

**Implementation notes / no deviations from design:**
- Used `reportlab` (not in `requirements.txt`, since it's only a fixture-generation tool,
  not a pipeline dependency) — noted here rather than silently added; not needed again
  unless fixtures are regenerated.
- Text extraction and the "0 characters" no-text case were both verified directly against
  `pdfplumber` during fixture creation (not just asserted) — see `samples/README.md`
  "Verified during creation" section.
- One item deferred to Phase 3 testing rather than baked into a fixture: an unparseable
  free-text date (e.g. "a while back"); noted in `samples/README.md` as intentionally
  left for a focused unit test instead of a fixture file.

---

## Phase 3 — Loaders (per source)
**Status: Completed**
- Implemented all four loader modules, each exposing `load(path) -> LoadResult` and
  `parse(result) -> list[PartialRecord]`, per the uniform contract in §9:
  - **`csv_loader.py`** — `csv.DictReader`; treats the file as malformed (`ok=False`)
    if none of the expected columns (`name`, `email`, `phone`, `current_company`,
    `title`) appear in the header, rather than guessing column meaning. Per-row
    degradation: a row is skipped only if it has neither `name` nor `email` (no way
    to identify anyone); a row missing just one is still parsed.
  - **`ats_json_loader.py`** — `json.load` + explicit field-name remap (`candidate_name`
    → `full_name`, `contact_email` → `emails`, `mobile_number` → `phones`, `employer`/
    `job_title` → an `ExperienceEntry`, `city`/`state`/`country_name` → `Location`,
    `skill_tags` → `Skill` list). Whole file marked `ok=False` if it can't be parsed as
    JSON or lacks a top-level `candidates` list; per-entry skip uses the same
    no-name-and-no-email rule as CSV.
  - **`resume_pdf_loader.py`** — `pdfplumber` text extraction; if extracted text is
    under `MIN_EXTRACTABLE_CHARS` (20), the source is marked `ok=False` as a likely
    scanned/image-only PDF rather than parsed as garbage. `parse()` splits text into
    sections via our own all-caps headers (`HEADLINE`/`EXPERIENCE`/`EDUCATION`/`SKILLS`)
    and extracts name (first line), email/phone (regex), and structured
    experience/education/skills via positional/heuristic rules matched to our sample
    resume format.
  - **`recruiter_notes_loader.py`** — plain text read; empty file is `ok=True` with
    empty data (not an error, per §16's empty-vs-malformed distinction). `parse()`
    does regex email/phone extraction plus keyword matching against a small known-skills
    list (`KNOWN_SKILLS`), deliberately not attempting to guess company/title from free
    text, per the brief's "wrong-but-confident is worse than honestly-empty" principle.
- **Bugs found and fixed during smoke-testing against the Phase 2 fixtures:**
  1. The loose phone regex was matching ISO dates in note headers (e.g. "2026-06-18")
     as phone numbers — fixed by excluding date-shaped matches (`DATE_LIKE_RE`) in
     `recruiter_notes_loader.py`.
  2. Email regex was capturing a trailing period at the end of a sentence (e.g.
     "...example.com.") — fixed by stripping trailing punctuation from email matches
     in both `recruiter_notes_loader.py` and `resume_pdf_loader.py`.
- Smoke-tested all four loaders against every Phase 2 fixture (good/malformed/missing/
  empty/scanned cases) directly via Python, confirming `ok`/`error` behavior and
  extracted field values matched expectations.

**Files created:** `src/transformer/loaders/csv_loader.py`, `ats_json_loader.py`,
`resume_pdf_loader.py`, `recruiter_notes_loader.py` (overwriting the Phase 1 stubs).

**Implementation notes / no deviations from design:**
- All four loaders follow the `LoadResult` contract from `models.py` exactly; no
  raised exceptions escape `load()` or `parse()`.
- Per-row/per-entry skip behavior (CSV, ATS JSON) and the no-text-layer PDF detection
  both directly implement the edge cases called out in §16.
- Normalization (E.164 phones, canonical skills, parsed dates) is deliberately **not**
  done at this stage — loaders extract raw strings only; normalization happens once,
  centrally, in Phase 4/5 (`normalize.py` + `merge.py`), per §11's "one implementation,
  two call sites" principle.

---

## Phase 4 — Normalization library
**Status: Completed**
- Implemented `normalize.py` with one function per concern (§11), each returning
  `None` for anything unparseable rather than guessing a value:
  - `normalize_phone()` — via `phonenumbers`; default region fallback `"US"`
    (`DEFAULT_PHONE_REGION`) when no country is otherwise known.
  - `normalize_date()` — hand-rolled parser (no `dateutil` dependency) supporting
    `YYYY-MM`, `Month YYYY`/`Mon YYYY`, bare `YYYY`, and present/current/now/ongoing
    → `None` (open-ended end date).
  - `normalize_skill()` — small seed alias table (~30 entries covering the sample
    fixtures plus common adjacents); unmatched skills pass through title-cased rather
    than being dropped, since the skills vocabulary is open-ended.
  - `normalize_name()` — trims/collapses whitespace only; deliberately never re-cases
    a name, to avoid guessing capitalization for names like "McDonald."
  - `normalize_country()` / `normalize_location()` — small hand-rolled
    country-name → ISO-3166 alpha-2 lookup table (~14 entries); unrecognized country
    strings become `None` rather than guessed; city/region are trimmed only, not
    canonicalized (no geocoding source available/in scope).
- Wrote `tests/test_normalize.py` (20 tests) covering happy paths and the
  unparseable-returns-`None` cases for phone and date specifically, per the design's
  explicit emphasis on that failure behavior.
- **Environment constraint:** this sandbox has no network access, so the real
  `phonenumbers` package could not be installed/imported here. To still verify the
  rest of the module's logic, a minimal local stand-in for `phonenumbers` was written
  and used only for this sandbox's test run (not part of the shipped project); all 20
  tests passed against it. The real `phonenumbers` library should be installed and
  the tests re-run locally before relying on phone normalization.

**Files created:** `src/transformer/normalize.py` (overwriting the Phase 1 stub),
`tests/test_normalize.py`.

**Manual decisions made (flagged inline in code comments too):**
1. Default phone region fallback = `"US"`.
2. Bare-year dates (`"2020"`) are returned as-is (`"2020"`), not guessed to `"2020-01"`.
3. Skill alias table is a small, hand-curated seed list, not an industry taxonomy.
4. Country lookup table is a small hand-rolled dict, not a full ISO-3166 dataset
   (no network access to pull one).
5. Used a hand-rolled date parser instead of adding `dateutil` as a project dependency,
   to avoid drifting from the dependency list agreed in `PROJECT_CONTEXT.md` §7.

---

## Phase 5 — Identity grouping + merge engine
**Status: Completed**
- **`identity.py`** — deterministic candidate grouping per §10, implemented as a
  union-find over all `PartialRecord`s:
  - Pass 1/2: union records sharing a normalized email key or a loose
    digits-only phone key (last 10 digits, so differing formatting/punctuation
    still matches without needing a region).
  - Pass 3 (name fallback): for clusters with **no** email/phone at all, attach to an
    existing identity-bearing cluster by name **only if exactly one** such cluster
    has that name; ambiguous or zero matches are left as their own cluster rather
    than guessed — this is the guard against false-merging two different people who
    share a name.
  - `candidate_id` generated deterministically as `{slugified-name}-{6-char hash of
    sorted source_ids}`.
- **`merge.py`** — field-by-field conflict resolution and confidence scoring (§12,
  §13):
  - `SOURCE_PRIORITY = [ats_json, csv, resume_pdf, recruiter_notes]` used to pick
    winners for scalar fields (`full_name`, `location`, `headline`) and to order
    experience/education merging.
  - List fields (`emails`, `phones`) are unioned and deduplicated; phones are run
    through `normalize.normalize_phone()` during merge, with unparseable numbers
    recorded as `failed_normalize` provenance entries rather than silently dropped.
  - Skills are deduplicated by canonical name (via `normalize.normalize_skill()`)
    with sources/confidence aggregated across every contributing record.
  - Experience/education entries are deduplicated only when company+title (or
    institution+degree) match exactly; **genuinely conflicting** entries (different
    employer names across sources) are kept as separate entries rather than one
    being silently chosen as "the" answer — preserves full information per §12's
    provenance-stays-inspectable principle.
  - `overall_confidence` = weighted average (`0.7 × required` + `0.3 × optional`)
    over per-field confidence scores, where required fields
    (`full_name`/`emails`/`skills`) missing entirely count as 0.
  - `years_experience` is intentionally left `None` — not computed in this
    implementation (see Manual Decisions below).
- Smoke-tested end-to-end across all four loaders' output combined: 14 raw
  PartialRecords grouped into 7 candidates. Confirmed: Jane Doe and John Smith
  correctly merge across all 4 sources each; the two Bob Lee CSV rows correctly stay
  **separate** (no false merge); Alice Nguyen merges correctly from resume+notes only
  (no CSV/ATS source) with a visibly lower `overall_confidence` (0.622) than
  fully-sourced candidates (~0.88–0.90); John Smith's 3-way conflicting employer data
  surfaces as 4 distinct experience entries rather than one source's value silently
  overwriting another's.

**Files created:** `src/transformer/identity.py`, `src/transformer/merge.py`
(overwriting the Phase 1 stubs).

**Manual decisions made:**
1. `years_experience` not computed — deriving it from already-conflicting experience
   spans risked producing a confidently-wrong derived number; left as a known gap
   rather than guessed.
2. Conflicting experience entries (e.g. different employer names across sources for
   the same candidate) are kept as separate entries rather than collapsed to one
   "winner" — preserves all source information but means downstream consumers may
   see multiple "current job" entries.
3. Minor known cosmetic issue, not fixed in this phase: a resume skill with a
   parenthetical (e.g. "Accessibility (WCAG)") isn't split further and can appear
   as a separately-cased duplicate alongside a plain "Accessibility" picked up from
   notes text. Low priority, candidate for a Phase 7 cleanup pass if time allows.

---

## Phase 6 — Internal validation
**Status: Completed**
- Implemented `src/transformer/validate.py` with `validate_canonical()`, which runs
  two verification passes against every `CanonicalRecord` produced by the merge engine:

  **Pass 1 — JSON schema validation** (via `jsonschema`):
  - Enforces structural correctness of the full `CanonicalRecord.to_dict()` output:
    correct types on every field (`candidate_id` is a non-empty string, `emails`/
    `phones`/etc. are arrays, all numeric fields are numbers, etc.).
  - `candidate_id` must be a non-empty string (`minLength: 1`).
  - `overall_confidence` constrained to `[0.0, 1.0]` by the schema.
  - Every `Skill.confidence` and `Provenance.confidence` similarly constrained to
    `[0.0, 1.0]`.
  - `Provenance.method` validated against the known enum of allowed extraction
    methods (`direct`, `regex`, `heuristic`, `merged`, `failed_normalize`),
    matching `ExtractionMethod` in `models.py` — any future method added to one
    must be added to the other.
  - Sub-schemas defined under `definitions` and referenced via `$ref`, avoiding
    repetition and making each sub-structure independently readable.

  **Pass 2 — Semantic sanity checks** (things JSON schema alone can't express):
  - `overall_confidence` range re-checked explicitly so the error message names
    the field directly (rather than requiring the caller to decode jsonschema output).
  - **Orphan provenance check**: every `Provenance` entry's `field` prefix
    (the part before any `[`) must correspond to a real top-level field on
    `CanonicalRecord`. A typo in `merge.py` like `Provenance(field="skilz[...]")`
    would silently produce unreachable provenance; this check surfaces it immediately
    as a `CanonicalValidationError` so it's caught at pipeline time, not later during
    debugging. Known valid field prefixes are maintained in the module-level
    `_CANONICAL_FIELD_PREFIXES` frozenset.

- Added two typed exception classes:
  - `CanonicalValidationError(candidate_id, reason)` — raised by `validate_canonical`;
    carries `candidate_id` and human-readable `reason` as attributes so the pipeline
    (Phase 8) can log it per-candidate without exposing raw jsonschema internals.
  - `ProjectionValidationError(candidate_id, reason)` — defined here for completeness
    (Phase 7 will raise it), so Phase 8 can import and handle both from one location.

- `validate_projection()` left as a documented stub (raises `NotImplementedError`)
  pointing to Phase 7 for implementation, keeping the module importable and the
  future Phase 7 interface defined without blocking anything.

- Smoke-tested against the full Phase 2–5 pipeline output:
  - All 7 `CanonicalRecord`s produced from the sample fixtures passed
    `validate_canonical` cleanly (covering Jane Doe, John Smith, Alice Nguyen,
    the two separate Bob Lee records, and the two unknown-identity records).
  - Error-detection verified for 5 distinct failure modes: empty `candidate_id`,
    `overall_confidence > 1.0`, skill `confidence > 1.0`, orphan provenance field
    prefix (`"skilz"` instead of `"skills"`), and invalid provenance method enum
    value (`"guessed"`).
  - A valid minimal record confirmed to pass with no exception.

**Files modified:** `src/transformer/validate.py` (overwriting the Phase 1 stub).

**Implementation notes / deviations:**
- No deviations from the design (§15). All behaviours described there are implemented.
- The `→` Unicode arrow in error-message path strings was replaced with plain ASCII
  `>` to avoid `cp1252` encoding errors when printing on Windows consoles —
  cosmetic-only change, does not affect error content or structure.
- `Optional` import from `typing` is present in the module (carried forward from stub)
  but not used in the final implementation; left in to avoid a noisy one-line diff;
  no functional impact.

---

## Phase 7 — Projection engine + output validation
**Status: Completed**

### `projection.py`
- Implemented `project_record(record, config) -> dict` and `project_all(records, config) -> (list[dict], list[ProjectionValidationError])`.
- **Path resolution** (`_tokenize` + `_apply_tokens`): tokenizes any `from`/`path` string into a sequence of typed access operations and walks the canonical dict:
  - Plain key: `"full_name"` → `data["full_name"]`
  - List index: `"emails[0]"` → `data["emails"][0]`
  - List wildcard / projection: `"skills[].name"` → `[s["name"] for s in data["skills"]]`
  - Nested key: `"location.city"` → `data["location"]["city"]`
  - These can be composed, e.g. `"experience[0].company"`.
- **`on_missing` policy** applied field-by-field:
  - `"null"` — emit `{field: None}` for missing/null values.
  - `"omit"` — drop the key from the output dict entirely.
  - `"error"` — raise `ProjectionValidationError(candidate_id, reason)`; caught by the pipeline per-candidate, not per-batch.
- **`normalize` override** (`_normalize_scalar` + `_apply_normalize`): dispatches to the shared `normalize.py` functions (same implementation as merge.py uses — no drift). Supported directives: `"E164"` → `normalize_phone`, `"canonical"` → `normalize_skill`, `"YYYY-MM"` / `"date"` → `normalize_date`. Applied element-wise for list-projected values. Unknown directives pass through unchanged.
- **`include_confidence`** — appends `overall_confidence` (from `record.overall_confidence`) to every projected dict.
- **`include_provenance`** — appends `provenance` (full serialized provenance list) to every projected dict.
- **`load_config(path)`** — loads an `OutputConfig` from a JSON file, raising `FileNotFoundError` / `ValueError` on missing/malformed files.
- `project_all` returns `(results, errors)` so the pipeline can collect per-record `ProjectionValidationError`s without aborting the batch.

### `validate.py: validate_projection`
- Replaced the Phase 6 stub with a full implementation.
- **`_type_str_to_schema(type_str, on_missing)`** converts FieldSpec type strings to JSON Schema fragments:
  - Primitive types: `"string"`, `"number"`, `"integer"`, `"boolean"`, `"object"`, `"array"`.
  - Array shorthand: `"string[]"`, `"number[]"` → `{type: "array", items: {type: "string/number"}}`.
  - `on_missing="null"` → types are `["T", "null"]` (nullable); `"omit"` / `"error"` → strict `"T"`.
  - Unknown type strings → `{}` (no constraint — forward-compatible, not silently lossy).
- **`_build_projection_schema(config)`** assembles the full projected record schema:
  - `properties` for every FieldSpec (`path` → type schema).
  - `required` list for specs with `required=True` and `on_missing != "omit"`.
  - Adds `overall_confidence` (number, [0,1]) when `include_confidence=True`.
  - Adds `provenance` (array) when `include_provenance=True`.
  - Does NOT use `additionalProperties: false` to avoid rejecting records with future metadata keys.
- **`validate_projection(candidate_id, projected, config)`** runs `jsonschema.validate` against the generated schema and raises `ProjectionValidationError` on failure, with a clean `"field > key"` path in the reason string.

### Config files
- **`configs/default_config.json`** — mirrors the full canonical schema; all 10 data fields included (no renaming), `include_confidence=true`, `include_provenance=true`, `on_missing="null"`.
- **`configs/example_custom_config.json`** — exactly the assignment example from §14: `full_name` (required), `primary_email` from `emails[0]` (required), `phone` from `phones[0]` with `normalize="E164"`, `skills` from `skills[].name` with `normalize="canonical"`. `include_confidence=true`, `include_provenance=false`, `on_missing="null"`.

### Smoke-tested end-to-end
- Default config: all 7 candidates projected and validated cleanly; `provenance` present, `overall_confidence` present.
- Custom config: field renaming (`primary_email` ← `emails[0]`), list-projection (`skills[].name`), E.164 phone normalization, and canonical skill normalization all verified on real sample data (e.g. Jane Doe: `phone=+14155550142`, `skills=["Python","AWS","PostgreSQL",...]`).
- `on_missing` policies: `null` (missing fields → `None`), `omit` (missing fields → key absent), `error` (raises `ProjectionValidationError`) all verified.
- `validate_projection` required-field check: confirmed raises `ProjectionValidationError` when a required field is absent from the projected dict.

**Files modified/created:**
- `src/transformer/projection.py` (overwriting Phase 1 stub)
- `src/transformer/validate.py` (replacing the `validate_projection` stub from Phase 6)
- `configs/default_config.json` [NEW]
- `configs/example_custom_config.json` [NEW]

**Implementation notes / deviations:**
- No deviations from §14/§15 of the design doc.
- `project_all` returns `(results, errors)` tuple (not just `list[dict]`) — a small addition over the stub signature, necessary so the pipeline can collect per-candidate errors without try/except wrapping every call. This is consistent with Phase 8's error-collection pattern.
- No tests/ folder per project instruction change (live demo instead of automated tests).
- The `_comment` key in both JSON config files is an informal annotation (not a FieldSpec) and is ignored by `OutputConfig.from_dict` (which only reads `fields`, `include_confidence`, `include_provenance`, `on_missing`) — confirmed this does not interfere with loading.

---

## Phase 8 — Pipeline orchestration + CLI
**Status: Not Started**
- `pipeline.py`: wires loaders → parse → normalize → group → merge → validate →
  project → validate, collecting per-source and per-candidate errors instead of
  crashing.
- `cli.py`: argparse — input source paths/dirs, `--config`, `--output`.
- `README.md`: run instructions + example commands.

**Files expected:** `src/transformer/pipeline.py`, `src/transformer/cli.py`,
`README.md`.

---

## Phase 9 — End-to-end test + edge-case pass
**Status: Not Started**
- `tests/test_pipeline_end_to_end.py`: full run over `samples/`, default config and
  custom config, asserting schema-valid output and that the edge cases from §16/Phase 2
  fixtures behave as designed (malformed file skipped, conflict resolved correctly,
  no false-merge of same-name candidates, missing source handled).
- Run CLI manually end-to-end, capture sample output JSON into `output/` for the
  submission.
- Final read-through of `PROJECT_CONTEXT.md` for any deviations made during
  implementation; reconcile.

**Files expected:** `tests/test_pipeline_end_to_end.py`, `output/*.json`.

---

## Deviations Log
*(Append here as they happen — none yet.)*

## Remaining Work
*(Updated per phase — currently: everything from Phase 1 onward.)*