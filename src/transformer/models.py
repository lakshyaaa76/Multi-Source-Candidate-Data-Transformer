"""
Core data models for the candidate data transformer.

These dataclasses are the contract between pipeline stages (see PROJECT_CONTEXT.md
§4 High-Level Pipeline and §8 Canonical Data Model). Keeping them in one module avoids
drift between what loaders produce, what merge consumes, and what projection reads.

Design notes:
- All "record" dataclasses are plain data containers with `to_dict()` helpers — no
  business logic lives here. Normalization, merging, and projection logic live in
  their own modules (normalize.py, merge.py, projection.py).
- `PartialRecord` mirrors `CanonicalRecord`'s shape but every field is optional/empty
  by default, since a single source rarely populates the whole profile.
- Sub-structures (Location, Links, Skill, ExperienceEntry, EducationEntry) are kept as
  separate small dataclasses rather than raw dicts so field names are enforced and
  typos surface early (e.g. during development/tests), while still being trivially
  convertible to dicts for JSON output.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Literal, Optional


# ---------------------------------------------------------------------------
# Shared sub-structures
# ---------------------------------------------------------------------------

@dataclass
class Location:
    city: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None  # ISO-3166 alpha-2

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Links:
    linkedin: Optional[str] = None
    github: Optional[str] = None
    portfolio: Optional[str] = None
    other: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Skill:
    name: str
    confidence: float = 0.0
    sources: list[str] = field(default_factory=list)  # source_ids

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExperienceEntry:
    company: Optional[str] = None
    title: Optional[str] = None
    start: Optional[str] = None  # YYYY-MM
    end: Optional[str] = None    # YYYY-MM or None for "current"
    summary: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EducationEntry:
    institution: Optional[str] = None
    degree: Optional[str] = None
    field: Optional[str] = None
    end_year: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

# How a field's value was obtained. "failed_normalize" marks a value that was
# attempted but dropped because it couldn't be normalized (see PROJECT_CONTEXT.md §11),
# kept distinct from "merged" so reviewers can tell a clean merge from a lossy one.
ExtractionMethod = Literal["direct", "regex", "heuristic", "merged", "failed_normalize"]


@dataclass
class Provenance:
    field: str               # canonical field path, e.g. "phones[0]" or "skills[2].name"
    source: str               # source_id, e.g. "csv:recruiter_export.csv"
    method: ExtractionMethod
    confidence: float = 0.0   # field-level confidence, 0..1

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Partial record — produced by a single source's loader/parser
# ---------------------------------------------------------------------------

SourceType = Literal["csv", "ats_json", "resume_pdf", "recruiter_notes"]


@dataclass
class PartialRecord:
    """
    One source's view of one candidate, before merge. Every field is optional —
    a source is expected to populate only what it has. Always tagged with the
    source it came from, so identity/merge/provenance can trace it back.
    """
    source_id: str          # e.g. "csv:recruiter_export.csv#row=3"
    source_type: SourceType

    full_name: Optional[str] = None
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    location: Optional[Location] = None
    links: Links = field(default_factory=Links)
    headline: Optional[str] = None
    years_experience: Optional[float] = None
    skills: list[Skill] = field(default_factory=list)
    experience: list[ExperienceEntry] = field(default_factory=list)
    education: list[EducationEntry] = field(default_factory=list)

    # Per-field extraction method, keyed by canonical field name, set by the parser
    # that produced this PartialRecord. Used by merge.py to build Provenance/confidence
    # without re-deriving "how was this extracted" later.
    extraction_methods: dict[str, ExtractionMethod] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# ---------------------------------------------------------------------------
# Canonical record — one fully-merged profile per candidate
# ---------------------------------------------------------------------------

@dataclass
class CanonicalRecord:
    candidate_id: str
    full_name: Optional[str] = None
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    location: Optional[Location] = None
    links: Links = field(default_factory=Links)
    headline: Optional[str] = None
    years_experience: Optional[float] = None
    skills: list[Skill] = field(default_factory=list)
    experience: list[ExperienceEntry] = field(default_factory=list)
    education: list[EducationEntry] = field(default_factory=list)
    provenance: list[Provenance] = field(default_factory=list)
    overall_confidence: float = 0.0

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "full_name": self.full_name,
            "emails": list(self.emails),
            "phones": list(self.phones),
            "location": self.location.to_dict() if self.location else None,
            "links": self.links.to_dict(),
            "headline": self.headline,
            "years_experience": self.years_experience,
            "skills": [s.to_dict() for s in self.skills],
            "experience": [e.to_dict() for e in self.experience],
            "education": [e.to_dict() for e in self.education],
            "provenance": [p.to_dict() for p in self.provenance],
            "overall_confidence": self.overall_confidence,
        }


# ---------------------------------------------------------------------------
# Loader result — uniform contract every source loader returns (see §9)
# ---------------------------------------------------------------------------

@dataclass
class LoadResult:
    """
    Uniform result every loader returns, regardless of source type. `ok=False`
    means the *whole source* failed to load (missing file, malformed JSON/CSV with
    no header, non-text PDF, etc.) — the pipeline logs `error` and continues with
    other sources rather than raising. `data` holds source_type-specific raw content
    (e.g. list[dict] for CSV rows, dict for ATS JSON, str for PDF/notes text) for the
    parser stage to consume; it is not yet a PartialRecord.
    """
    ok: bool
    source_id: str
    source_type: SourceType
    data: Any = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Output config — runtime projection config (see §14)
# ---------------------------------------------------------------------------

OnMissingPolicy = Literal["null", "omit", "error"]


@dataclass
class FieldSpec:
    path: str                       # field name in the OUTPUT
    type: str                       # "string" | "number" | "boolean" | "string[]" | "object" | ...
    required: bool = False
    from_: Optional[str] = None     # canonical path to read from; defaults to `path`
    normalize: Optional[str] = None  # e.g. "E164", "canonical", "YYYY-MM"

    @staticmethod
    def from_dict(d: dict) -> "FieldSpec":
        return FieldSpec(
            path=d["path"],
            type=d.get("type", "string"),
            required=d.get("required", False),
            from_=d.get("from"),
            normalize=d.get("normalize"),
        )

    @property
    def source_path(self) -> str:
        return self.from_ if self.from_ is not None else self.path


@dataclass
class OutputConfig:
    fields: list[FieldSpec]
    include_confidence: bool = True
    include_provenance: bool = True
    on_missing: OnMissingPolicy = "null"

    @staticmethod
    def from_dict(d: dict) -> "OutputConfig":
        return OutputConfig(
            fields=[FieldSpec.from_dict(f) for f in d.get("fields", [])],
            include_confidence=d.get("include_confidence", True),
            include_provenance=d.get("include_provenance", True),
            on_missing=d.get("on_missing", "null"),
        )
