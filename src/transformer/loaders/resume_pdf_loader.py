"""
resume_pdf_loader.py — Resume PDF (unstructured source).

Uses pdfplumber for text extraction. Single-column, text-based resumes only -- no
OCR, no multi-column layout parsing (see PROJECT_CONTEXT.md §9, §15 scope cuts).
A PDF with near-zero extractable text (e.g. a scanned image) is detected and the
whole source is marked ok=False rather than silently returning garbage (§16).
"""

from __future__ import annotations

import os
import re

import pdfplumber

from transformer.models import (
    EducationEntry,
    ExperienceEntry,
    LoadResult,
    PartialRecord,
    Skill,
)

# Below this many extracted characters, treat the PDF as having no usable text layer
# (e.g. a scanned resume) rather than attempting to parse near-empty/garbage content.
MIN_EXTRACTABLE_CHARS = 20

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(\+?\d[\d\-\s().]{7,}\d)")
SECTION_HEADERS = ("HEADLINE", "EXPERIENCE", "EDUCATION", "SKILLS")


def load(path: str) -> LoadResult:
    source_id = f"resume_pdf:{os.path.basename(path)}"

    if not os.path.exists(path):
        return LoadResult(ok=False, source_id=source_id, source_type="resume_pdf",
                           error=f"file not found: {path}")

    try:
        with pdfplumber.open(path) as pdf:
            text = "\n".join((page.extract_text() or "") for page in pdf.pages)
    except Exception as exc:  # pdfplumber can raise several distinct exception types
        return LoadResult(ok=False, source_id=source_id, source_type="resume_pdf",
                           error=f"failed to open/parse PDF: {exc}")

    if len(text.strip()) < MIN_EXTRACTABLE_CHARS:
        return LoadResult(
            ok=False, source_id=source_id, source_type="resume_pdf",
            error="no usable text extracted (likely a scanned/image-only PDF; OCR is out of scope)",
        )

    return LoadResult(ok=True, source_id=source_id, source_type="resume_pdf", data=text)


def _split_sections(text: str) -> dict[str, list[str]]:
    """Split resume text into named sections using the all-caps headers we control
    in our own sample format. Lines before the first header go under 'HEADER'
    (name/contact block)."""
    sections: dict[str, list[str]] = {"HEADER": []}
    current = "HEADER"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper() in SECTION_HEADERS:
            current = stripped.upper()
            sections.setdefault(current, [])
            continue
        if stripped:
            sections.setdefault(current, []).append(stripped)
    return sections


def parse(result: LoadResult) -> list[PartialRecord]:
    """Heuristic/regex extraction of a single candidate from resume text."""
    if not result.ok or not result.data:
        return []

    text = result.data
    sections = _split_sections(text)
    header_lines = sections.get("HEADER", [])

    methods: dict = {}
    rec = PartialRecord(source_id=result.source_id, source_type="resume_pdf")

    # Name: first non-empty line of the document, by convention (§9 assumption).
    if header_lines:
        rec.full_name = header_lines[0]
        methods["full_name"] = "heuristic"

    email_match = EMAIL_RE.search(text)
    if email_match:
        rec.emails = [email_match.group(0).rstrip(".,;:)")]
        methods["emails"] = "regex"

    phone_match = PHONE_RE.search(text)
    if phone_match:
        rec.phones = [phone_match.group(0).strip()]
        methods["phones"] = "regex"

    # Headline: first non-empty line under the HEADLINE section, if present.
    headline_lines = sections.get("HEADLINE", [])
    if headline_lines:
        rec.headline = headline_lines[0]
        methods["headline"] = "direct"

    # Experience: pairs of (title line, date-range line, optional summary lines) under
    # EXPERIENCE, separated by blank-line groups. Our sample format is:
    #   "Company -- Title"
    #   "Mon YYYY - Mon YYYY" / "Mon YYYY - Present"
    #   "summary sentence(s)"
    exp_lines = sections.get("EXPERIENCE", [])
    experience: list[ExperienceEntry] = []
    i = 0
    while i < len(exp_lines):
        company_title = exp_lines[i]
        company, _, title = company_title.partition("--")
        date_range = exp_lines[i + 1] if i + 1 < len(exp_lines) else ""
        summary = exp_lines[i + 2] if i + 2 < len(exp_lines) else None
        start, _, end = date_range.partition("-")
        experience.append(ExperienceEntry(
            company=company.strip() or None,
            title=title.strip() or None,
            start=start.strip() or None,
            end=None if "present" in end.lower() else (end.strip() or None),
            summary=summary,
        ))
        i += 3
    if experience:
        rec.experience = experience
        methods["experience"] = "heuristic"

    # Education: lines of "Institution -- Degree, YYYY" under EDUCATION.
    edu_lines = sections.get("EDUCATION", [])
    education: list[EducationEntry] = []
    for line in edu_lines:
        institution, _, rest = line.partition("--")
        degree_part, _, year_part = rest.rpartition(",")
        year_match = re.search(r"\d{4}", year_part)
        education.append(EducationEntry(
            institution=institution.strip() or None,
            degree=degree_part.strip() or rest.strip() or None,
            end_year=int(year_match.group(0)) if year_match else None,
        ))
    if education:
        rec.education = education
        methods["education"] = "heuristic"

    # Skills: comma-separated list under SKILLS.
    skill_lines = sections.get("SKILLS", [])
    if skill_lines:
        raw_skills = ", ".join(skill_lines).split(",")
        rec.skills = [Skill(name=s.strip()) for s in raw_skills if s.strip()]
        methods["skills"] = "direct"

    rec.extraction_methods = methods
    return [rec]