"""
csv_loader.py — Recruiter CSV export (structured source).

Implements : load() returns a uniform LoadResult and never
raises; parse() turns the raw rows into PartialRecords using direct column->field
mapping. Per-row degradation: a row with neither name nor email is unusable and is
skipped (noted, not raised); a row missing only one of them is still parsed as far
as possible,("one bad row inside an otherwise-good CSV -> skip that row,
keep the rest" / degrade gracefully rather than drop everything).
"""

from __future__ import annotations

import csv
import os

from transformer.models import (
    ExperienceEntry,
    LoadResult,
    PartialRecord,
)

# Recognized header columns for this source. If none of these appear in the file's
# header row, we treat the file as having no real header (malformed) rather than
# guessing at columns -- this is the "missing header" malformed-file case.
EXPECTED_COLUMNS = {"name", "email", "phone", "current_company", "title"}


def load(path: str) -> LoadResult:
    source_id = f"csv:{os.path.basename(path)}"

    if not os.path.exists(path):
        return LoadResult(ok=False, source_id=source_id, source_type="csv",
                           error=f"file not found: {path}")

    try:
        with open(path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            if not EXPECTED_COLUMNS.intersection(fieldnames):
                return LoadResult(
                    ok=False, source_id=source_id, source_type="csv",
                    error=f"no recognized header columns found; got {fieldnames!r}",
                )
            rows = list(reader)
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        return LoadResult(ok=False, source_id=source_id, source_type="csv",
                           error=f"failed to parse CSV: {exc}")

    return LoadResult(ok=True, source_id=source_id, source_type="csv", data=rows)


def parse(result: LoadResult) -> list[PartialRecord]:
    """Convert a successful CSV LoadResult's rows into PartialRecords."""
    if not result.ok or not result.data:
        return []

    records: list[PartialRecord] = []
    for i, row in enumerate(result.data):
        name = (row.get("name") or "").strip()
        email = (row.get("email") or "").strip()
        phone = (row.get("phone") or "").strip()
        company = (row.get("current_company") or "").strip()
        title = (row.get("title") or "").strip()

        if not name and not email:
            # No identifying information at all -- can't even group this row to a
            # candidate later. Skip it rather than fabricate an identity.
            continue

        row_source_id = f"{result.source_id}#row={i + 2}"  # +2: 1-indexed + header row
        methods: dict = {}
        rec = PartialRecord(source_id=row_source_id, source_type="csv")

        if name:
            rec.full_name = name
            methods["full_name"] = "direct"
        if email:
            rec.emails = [email]
            methods["emails"] = "direct"
        if phone:
            rec.phones = [phone]
            methods["phones"] = "direct"
        if company or title:
            rec.experience = [ExperienceEntry(company=company or None, title=title or None)]
            methods["experience"] = "direct"

        rec.extraction_methods = methods
        records.append(rec)

    return records