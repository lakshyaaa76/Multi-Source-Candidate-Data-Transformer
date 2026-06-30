"""
recruiter_notes_loader.py — Recruiter notes .txt (unstructured, free text).

Lowest-confidence source tier (§9, §13): regex for email/phone if present, light
keyword matching against a small known-skills list. We bias toward extracting
little-but-correct over a lot-but-guessed, per the brief's "wrong-but-confident is
worse than honestly-empty" principle -- e.g. we do NOT attempt to guess a current
company/title from free text here, since notes are commentary, not a structured
fact sheet, and a wrong guess there is exactly the kind of silent bad data the
brief warns against.
"""

from __future__ import annotations

import os
import re

from transformer.models import LoadResult, PartialRecord, Skill

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(\+?\d[\d\-\s().]{7,}\d)")
# Free text often contains ISO dates (e.g. "2026-06-18") in note headers, which the
# loose PHONE_RE above can mistake for a phone number. Exclude obvious date-shaped
# matches rather than tightening PHONE_RE itself (which still needs to tolerate
# spaced/parenthesized real phone formats like "415-555-0142" or "+1 503 555 0177").
DATE_LIKE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Small seed vocabulary for keyword-based skill detection in free text. Matching is
# case-insensitive substring search; anything not here is simply not picked up from
# notes (acceptable -- notes are a low-confidence, supplementary source, and the
# resume/CSV/ATS sources are expected to carry the bulk of skill data).
KNOWN_SKILLS = [
    "Python", "Go", "JavaScript", "TypeScript", "React", "Node.js", "GraphQL",
    "AWS", "PostgreSQL", "Docker", "Kubernetes", "Terraform", "SQL",
    "Roadmapping", "A/B Testing", "Accessibility", "WCAG",
]


def load(path: str) -> LoadResult:
    source_id = f"recruiter_notes:{os.path.basename(path)}"

    if not os.path.exists(path):
        return LoadResult(ok=False, source_id=source_id, source_type="recruiter_notes",
                           error=f"file not found: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as exc:
        return LoadResult(ok=False, source_id=source_id, source_type="recruiter_notes",
                           error=f"failed to read file: {exc}")

    # An empty file is "ok, nothing to contribute" -- not an error (§16 distinguishes
    # empty from malformed).
    return LoadResult(ok=True, source_id=source_id, source_type="recruiter_notes", data=text)


def parse(result: LoadResult) -> list[PartialRecord]:
    if not result.ok or not result.data or not result.data.strip():
        return []

    text = result.data
    methods: dict = {}
    rec = PartialRecord(source_id=result.source_id, source_type="recruiter_notes")

    email_match = EMAIL_RE.search(text)
    if email_match:
        rec.emails = [email_match.group(0).rstrip(".,;:)")]
        methods["emails"] = "regex"

    for phone_match in PHONE_RE.finditer(text):
        candidate = phone_match.group(0).strip()
        if DATE_LIKE_RE.match(candidate):
            continue
        rec.phones = [candidate]
        methods["phones"] = "regex"
        break

    found_skills = [s for s in KNOWN_SKILLS if s.lower() in text.lower()]
    if found_skills:
        rec.skills = [Skill(name=s) for s in found_skills]
        methods["skills"] = "heuristic"

    # No identifying info at all (no email/phone found) -- nothing for identity
    # grouping to key off later, so this record isn't useful on its own. We still
    # return it; the pipeline's grouping stage will simply be unable to attach it
    # to a candidate and will log that, rather than the loader silently dropping
    # potentially-useful skills/notes data.
    rec.extraction_methods = methods
    return [rec]