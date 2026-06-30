"""
ats_json_loader.py — ATS JSON blob (structured source, foreign field names).

Per the assignment brief, the ATS uses its own field names that don't match ours;
the field-by-field mapping below documents the explicit remap. Treated as the same
confidence tier as CSV (method="direct" once remapped) since it's still structured
data, not free text.
"""

from __future__ import annotations

import json
import os

from transformer.models import (
    EducationEntry,
    ExperienceEntry,
    LoadResult,
    Links,
    Location,
    PartialRecord,
    Skill,
)


def load(path: str) -> LoadResult:
    source_id = f"ats_json:{os.path.basename(path)}"

    if not os.path.exists(path):
        return LoadResult(ok=False, source_id=source_id, source_type="ats_json",
                           error=f"file not found: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            blob = json.load(f)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return LoadResult(ok=False, source_id=source_id, source_type="ats_json",
                           error=f"failed to parse JSON: {exc}")

    candidates = blob.get("candidates") if isinstance(blob, dict) else None
    if not isinstance(candidates, list):
        return LoadResult(
            ok=False, source_id=source_id, source_type="ats_json",
            error="expected top-level 'candidates' list, found none",
        )

    return LoadResult(ok=True, source_id=source_id, source_type="ats_json", data=candidates)


def parse(result: LoadResult) -> list[PartialRecord]:
    """Convert a successful ATS JSON LoadResult's candidate entries into PartialRecords."""
    if not result.ok or not result.data:
        return []

    records: list[PartialRecord] = []
    for i, entry in enumerate(result.data):
        if not isinstance(entry, dict):
            continue

        name = (entry.get("candidate_name") or "").strip()
        email = (entry.get("contact_email") or "").strip()
        phone = (entry.get("mobile_number") or "").strip()
        employer = (entry.get("employer") or "").strip()
        title = (entry.get("job_title") or "").strip()
        city = entry.get("city") or None
        state = entry.get("state") or None
        country = entry.get("country_name") or None
        skill_tags = entry.get("skill_tags") or []

        if not name and not email:
            # Same rule as CSV: nothing to identify this candidate by, skip the entry.
            continue

        row_source_id = f"{result.source_id}#entry={i}"
        methods: dict = {}
        rec = PartialRecord(source_id=row_source_id, source_type="ats_json")

        if name:
            rec.full_name = name
            methods["full_name"] = "direct"
        if email:
            rec.emails = [email]
            methods["emails"] = "direct"
        if phone:
            rec.phones = [phone]  # raw; normalized later, "not-a-real-number" dropped then
            methods["phones"] = "direct"
        if employer or title:
            rec.experience = [ExperienceEntry(company=employer or None, title=title or None)]
            methods["experience"] = "direct"
        if city or state or country:
            rec.location = Location(city=city, region=state, country=country)
            methods["location"] = "direct"
        if skill_tags:
            rec.skills = [Skill(name=str(s)) for s in skill_tags if s]
            methods["skills"] = "direct"

        rec.extraction_methods = methods
        records.append(rec)

    return records