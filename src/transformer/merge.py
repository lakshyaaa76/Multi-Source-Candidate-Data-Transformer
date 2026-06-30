"""
merge.py — field-by-field conflict resolution, confidence scoring, provenance
construction (PROJECT_CONTEXT.md §12, §13).

Consumes the output of identity.group_records() and produces one CanonicalRecord
per candidate. Reuses normalize.py so canonical values are normalized exactly once,
in one place.
"""

from __future__ import annotations

from collections import defaultdict

from transformer import normalize
from transformer.models import (
    CanonicalRecord,
    EducationEntry,
    ExperienceEntry,
    Links,
    PartialRecord,
    Provenance,
    Skill,
)

# MANUAL DECISION: default conflict-resolution ranking, confirmed during design
# (PROJECT_CONTEXT.md §12, §19). Pipeline-level constant, not exposed through the
# runtime OutputConfig (which reshapes *output*, not merge logic) -- see §15.
SOURCE_PRIORITY = ["ats_json", "csv", "resume_pdf", "recruiter_notes"]

# MANUAL DECISION: confidence-scoring constants (§13). Deliberately small/explicit
# so the formula can be stated and defended in two sentences during review, rather
# than an opaque scoring model.
_BASE_SCORE = {
    "direct": 0.9,
    "regex": 0.7,
    "heuristic": 0.5,
    "merged": 0.7,
    "failed_normalize": 0.1,
}
_SOURCE_TIER_WEIGHT = {
    "ats_json": 1.0,
    "csv": 1.0,
    "resume_pdf": 0.85,
    "recruiter_notes": 0.7,
}
_AGREEMENT_BONUS = 0.1  # per additional independent source agreeing on a value

# Required fields drag overall_confidence down more when missing; optional fields
# contribute less weight. MANUAL DECISION, see §13.
_REQUIRED_FIELDS = ["full_name", "emails", "skills"]
_OPTIONAL_FIELDS = ["phones", "location", "headline", "experience", "education"]


def _source_priority_rank(source_type: str) -> int:
    try:
        return SOURCE_PRIORITY.index(source_type)
    except ValueError:
        return len(SOURCE_PRIORITY)  # unknown source types sort last (lowest priority)


def _field_confidence(method: str, source_type: str, agreement_count: int = 0) -> float:
    base = _BASE_SCORE.get(method, 0.5)
    weight = _SOURCE_TIER_WEIGHT.get(source_type, 0.7)
    score = base * weight + _AGREEMENT_BONUS * agreement_count
    return round(min(score, 1.0), 3)


def _pick_scalar(records: list[PartialRecord], getter):
    """Pick the best value for a scalar field across records by source-priority,
    tie-broken by input order. Returns (winning_record, value) or (None, None)."""
    candidates = [(r, getter(r)) for r in records if getter(r)]
    if not candidates:
        return None, None
    candidates.sort(key=lambda rv: _source_priority_rank(rv[0].source_type))
    return candidates[0]


def _merge_emails(records: list[PartialRecord], provenance: list[Provenance]) -> list[str]:
    seen: dict[str, list[PartialRecord]] = defaultdict(list)
    order: list[str] = []
    for r in records:
        for e in r.emails:
            key = e.strip().lower()
            if not key:
                continue
            if key not in seen:
                order.append(key)
            seen[key].append(r)
    for key in order:
        contributors = seen[key]
        method = contributors[0].extraction_methods.get("emails", "direct")
        conf = _field_confidence(method, contributors[0].source_type, len(contributors) - 1)
        for r in contributors:
            provenance.append(Provenance(field=f"emails[{order.index(key)}]",
                                          source=r.source_id, method=method, confidence=conf))
    return order


def _merge_phones(records: list[PartialRecord], provenance: list[Provenance]) -> list[str]:
    normalized: dict[str, list[PartialRecord]] = defaultdict(list)
    order: list[str] = []
    for r in records:
        for p in r.phones:
            e164 = normalize.normalize_phone(p)
            if e164 is None:
                # Attempted but unparseable -- record it as a failed normalization,
                # don't silently drop it without a trace (§11, §16).
                provenance.append(Provenance(field="phones", source=r.source_id,
                                              method="failed_normalize", confidence=0.1))
                continue
            if e164 not in normalized:
                order.append(e164)
            normalized[e164].append(r)
    for key in order:
        contributors = normalized[key]
        method = contributors[0].extraction_methods.get("phones", "direct")
        conf = _field_confidence(method, contributors[0].source_type, len(contributors) - 1)
        for r in contributors:
            provenance.append(Provenance(field=f"phones[{order.index(key)}]",
                                          source=r.source_id, method=method, confidence=conf))
    return order


def _merge_skills(records: list[PartialRecord], provenance: list[Provenance]) -> list[Skill]:
    by_name: dict[str, list[tuple[PartialRecord, str]]] = defaultdict(list)
    order: list[str] = []
    for r in records:
        method = r.extraction_methods.get("skills", "heuristic")
        for s in r.skills:
            canonical = normalize.normalize_skill(s.name)
            if not canonical:
                continue
            if canonical not in by_name:
                order.append(canonical)
            by_name[canonical].append((r, method))

    skills: list[Skill] = []
    for name in order:
        contributors = by_name[name]
        best_method = max((m for _, m in contributors), key=lambda m: _BASE_SCORE.get(m, 0.5))
        best_source_type = max(
            (r.source_type for r, _ in contributors),
            key=lambda st: _SOURCE_TIER_WEIGHT.get(st, 0.7),
        )
        conf = _field_confidence(best_method, best_source_type, len(contributors) - 1)
        sources = [r.source_id for r, _ in contributors]
        skills.append(Skill(name=name, confidence=conf, sources=sources))
        for r, m in contributors:
            provenance.append(Provenance(field=f"skills[{name}]", source=r.source_id,
                                          method=m, confidence=conf))
    return skills


def _merge_experience(records: list[PartialRecord], provenance: list[Provenance]) -> list[ExperienceEntry]:
    """Dedupe entries that are clearly the same job (same normalized company AND
    title); otherwise keep all distinct entries -- conflicting employer claims
    across sources surface as separate entries rather than one being silently
    discarded (§12: provenance stays fully inspectable, not just "the winner")."""
    seen: dict[tuple[str, str], ExperienceEntry] = {}
    order: list[tuple[str, str]] = []
    for r in sorted(records, key=lambda r: _source_priority_rank(r.source_type)):
        method = r.extraction_methods.get("experience", "direct")
        for e in r.experience:
            company_key = (e.company or "").strip().lower()
            title_key = (e.title or "").strip().lower()
            key = (company_key, title_key)
            if key not in seen:
                order.append(key)
                seen[key] = ExperienceEntry(
                    company=e.company,
                    title=e.title,
                    start=normalize.normalize_date(e.start),
                    end=normalize.normalize_date(e.end),
                    summary=e.summary,
                )
            elif e.summary and not seen[key].summary:
                # Fill in a missing summary from a lower-priority source rather
                # than dropping it -- more complete info is strictly useful here.
                seen[key].summary = e.summary
            provenance.append(Provenance(field=f"experience[{company_key}|{title_key}]",
                                          source=r.source_id, method=method,
                                          confidence=_field_confidence(method, r.source_type)))
    return [seen[k] for k in order]


def _merge_education(records: list[PartialRecord], provenance: list[Provenance]) -> list[EducationEntry]:
    seen: dict[tuple[str, str], EducationEntry] = {}
    order: list[tuple[str, str]] = []
    for r in sorted(records, key=lambda r: _source_priority_rank(r.source_type)):
        method = r.extraction_methods.get("education", "direct")
        for e in r.education:
            inst_key = (e.institution or "").strip().lower()
            degree_key = (e.degree or "").strip().lower()
            key = (inst_key, degree_key)
            if key not in seen:
                order.append(key)
                seen[key] = e
            provenance.append(Provenance(field=f"education[{inst_key}|{degree_key}]",
                                          source=r.source_id, method=method,
                                          confidence=_field_confidence(method, r.source_type)))
    return [seen[k] for k in order]


def _compute_overall_confidence(canonical: CanonicalRecord, provenance: list[Provenance]) -> float:
    by_field_prefix: dict[str, list[float]] = defaultdict(list)
    for p in provenance:
        prefix = p.field.split("[")[0]
        by_field_prefix[prefix].append(p.confidence)

    def field_score(name: str, populated: bool) -> float:
        if not populated:
            return 0.0
        scores = by_field_prefix.get(name, [])
        return sum(scores) / len(scores) if scores else 0.5

    required_scores = [
        field_score("full_name", bool(canonical.full_name)),
        field_score("emails", bool(canonical.emails)),
        field_score("skills", bool(canonical.skills)),
    ]
    optional_present = [
        field_score("phones", bool(canonical.phones)),
        field_score("location", bool(canonical.location)),
        field_score("headline", bool(canonical.headline)),
        field_score("experience", bool(canonical.experience)),
        field_score("education", bool(canonical.education)),
    ]
    optional_present = [s for s in optional_present if s > 0]

    required_avg = sum(required_scores) / len(required_scores)
    if optional_present:
        optional_avg = sum(optional_present) / len(optional_present)
        overall = 0.7 * required_avg + 0.3 * optional_avg
    else:
        overall = required_avg
    return round(overall, 3)


def merge_candidate(candidate_id: str, records: list[PartialRecord]) -> CanonicalRecord:
    """Merge one candidate's grouped PartialRecords into a single CanonicalRecord."""
    provenance: list[Provenance] = []

    canonical = CanonicalRecord(candidate_id=candidate_id)

    name_record, name_value = _pick_scalar(records, lambda r: r.full_name)
    if name_record:
        canonical.full_name = normalize.normalize_name(name_value)
        method = name_record.extraction_methods.get("full_name", "direct")
        provenance.append(Provenance(field="full_name", source=name_record.source_id,
                                      method=method,
                                      confidence=_field_confidence(method, name_record.source_type)))

    canonical.emails = _merge_emails(records, provenance)
    canonical.phones = _merge_phones(records, provenance)

    loc_record, loc_value = _pick_scalar(records, lambda r: r.location)
    if loc_record:
        canonical.location = normalize.normalize_location(loc_value)
        method = loc_record.extraction_methods.get("location", "direct")
        provenance.append(Provenance(field="location", source=loc_record.source_id,
                                      method=method,
                                      confidence=_field_confidence(method, loc_record.source_type)))

    headline_record, headline_value = _pick_scalar(records, lambda r: r.headline)
    if headline_record:
        canonical.headline = headline_value
        method = headline_record.extraction_methods.get("headline", "direct")
        provenance.append(Provenance(field="headline", source=headline_record.source_id,
                                      method=method,
                                      confidence=_field_confidence(method, headline_record.source_type)))

    # NOTE (known limitation, not computed in this implementation): years_experience
    # is left as None. Deriving it would mean summing/estimating spans across
    # already-conflicting experience entries, which risks exactly the kind of
    # confidently-wrong derived number the brief warns against; left for a future
    # pass rather than guessed. Flagged here and in PROGRESS.md.

    canonical.skills = _merge_skills(records, provenance)
    canonical.experience = _merge_experience(records, provenance)
    canonical.education = _merge_education(records, provenance)

    canonical.links = Links()  # no loader currently populates links; left empty

    canonical.provenance = provenance
    canonical.overall_confidence = _compute_overall_confidence(canonical, provenance)
    return canonical


def merge_all(grouped: dict[str, list[PartialRecord]]) -> list[CanonicalRecord]:
    return [merge_candidate(cid, recs) for cid, recs in grouped.items()]