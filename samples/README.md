# samples/ — synthetic fixture inputs

None of these were provided by the assignment; they're authored to exercise the pipeline
end-to-end and to deliberately hit the edge cases listed in `PROJECT_CONTEXT.md` §16.
Generated via `scripts/generate_sample_pdfs.py` (PDFs) and hand-authored (CSV/JSON/TXT).

## Candidates

| Candidate | Sources present | What it's testing |
|---|---|---|
| **Jane Doe** | CSV, ATS JSON, resume PDF, recruiter notes (all 4) | The "happy path": consistent identity (same email/phone) across all four source types, values agree closely enough to be a high-confidence merge. Skills come from all sources in different casings/spellings (e.g. "postgres" vs "PostgreSQL") to exercise skill canonicalization. |
| **John Smith** | CSV, ATS JSON, resume PDF, recruiter notes | Deliberate **3-way scalar conflict**: CSV says employer "Globex Corp" / title "Product Manager"; ATS JSON says employer "Soylent Corp" / title "Senior Product Manager"; resume says "Globex Corp (rebranded Globex Labs)" / "Group Product Manager". Exercises `merge.py`'s source-priority conflict resolution (§12). Notes file has no phone on file (tests partial source coverage) and openly admits the recruiter hasn't confirmed current info (good real-world flavor, not parsed specially). |
| **Alice Nguyen** | Resume PDF + recruiter notes only (**no CSV/ATS row**) | "Candidate with no structured source at all" (§16) — pipeline must still emit a valid, lower-confidence canonical record from unstructured sources alone. |
| **Bob Lee (×2)** | CSV only, two separate rows | Two genuinely **different people who share a full name**, with different emails/phones/companies. Exercises the identity-grouping guard (§10) that must NOT merge them just because the name matches. |
| **(unnamed) CSV row 5** | CSV only | Row with an empty `name` field but otherwise valid — tests per-row degradation (skip/flag one bad row, keep the rest of the file) vs. failing the whole source. |
| **(unnamed) ATS entry 3** | ATS JSON only | Empty `candidate_name` and an unparseable `mobile_number` ("not-a-real-number") — tests per-record degradation inside a structured JSON source, and the phone normalizer's "unparseable → dropped, not guessed" behavior (§11). |

## Deliberately broken / edge-case files

| File | Simulates | Expected pipeline behavior |
|---|---|---|
| `ats_blob_malformed.json` | Truncated/invalid JSON syntax | Whole source `ok=False`, error logged, rest of the run continues (§9, §16). |
| `recruiter_export_malformed.csv` | No real header row, garbage columns | Whole source `ok=False` (can't map columns), rest of the run continues. |
| `resume_scanned_no_text.pdf` | A scanned resume with no extractable text layer | `resume_pdf_loader` detects near-zero extracted text, source marked `ok=False` rather than emitting garbage (§9, §16). Verified via `pdfplumber` to extract exactly 0 characters. |
| `recruiter_notes_empty.txt` | An empty notes file (0 bytes) | Treated as "ok, zero content extracted" — not an error, just nothing to contribute (§16: "empty" is distinct from "malformed"). |

## Verified during creation
- `resume_jane_doe.pdf` → `pdfplumber` extracts 590 characters of real text on page 1.
- `resume_scanned_no_text.pdf` → `pdfplumber` extracts 0 characters, confirming it will
  correctly trip the "non-text PDF" detection to be implemented in `resume_pdf_loader.py`.

## Not yet covered by these fixtures (intentionally, revisit if time allows)
- A genuinely unparseable date string in a resume (e.g. "a while back") — easy to add a
  line for in Phase 3 testing once the date normalizer exists, no need to bake into the
  PDF fixture itself (can be covered by a focused unit test instead).
