"""
normalize.py — shared normalization library (PROJECT_CONTEXT.md §11).

One function per concern, reused in two places: by merge.py when building the
canonical record, and by projection.py when a runtime OutputConfig requests a
`normalize` override on a projected field. Single implementation, two call sites,
so canonical normalization and config-time normalization never drift apart.

Guiding rule throughout (per the assignment brief): an unparseable value is dropped
to None, never guessed. "Wrong-but-confident is worse than honestly-empty."
"""

from __future__ import annotations

import re
from typing import Optional

import phonenumbers
from phonenumbers import NumberParseException

from transformer.models import Location

# ---------------------------------------------------------------------------
# MANUAL DECISION: default phone region fallback.
# Used only when a phone number has no country code and no location/country is
# otherwise inferable for that candidate. Documented in PROJECT_CONTEXT.md §17 as
# an assumption; surfaced again here since it directly affects parsing correctness
# and is the kind of default a reviewer should be able to spot and challenge.
# ---------------------------------------------------------------------------
DEFAULT_PHONE_REGION = "US"


def normalize_phone(raw: Optional[str], default_region: str = DEFAULT_PHONE_REGION) -> Optional[str]:
    """Parse a free-form phone string into E.164. Returns None if unparseable --
    never guesses a number into validity."""
    if not raw or not raw.strip():
        return None
    try:
        parsed = phonenumbers.parse(raw.strip(), default_region)
    except NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


# ---------------------------------------------------------------------------
# Dates -> YYYY-MM
# ---------------------------------------------------------------------------

_MONTHS = {
    "jan": "01", "january": "01",
    "feb": "02", "february": "02",
    "mar": "03", "march": "03",
    "apr": "04", "april": "04",
    "may": "05",
    "jun": "06", "june": "06",
    "jul": "07", "july": "07",
    "aug": "08", "august": "08",
    "sep": "09", "sept": "09", "september": "09",
    "oct": "10", "october": "10",
    "nov": "11", "november": "11",
    "dec": "12", "december": "12",
}

# MANUAL DECISION: words that mean "still ongoing" -> end date of None, rather than
# treating the literal word as unparseable garbage. Kept as a short explicit list
# rather than a fuzzy "looks like a present-tense word" heuristic, to stay
# predictable and easy to extend.
_PRESENT_WORDS = {"present", "current", "now", "ongoing"}

_RE_YYYY_MM = re.compile(r"^(\d{4})-(\d{2})$")
_RE_YYYY = re.compile(r"^(\d{4})$")
_RE_MONTH_YYYY = re.compile(r"^([A-Za-z.]+)\s+(\d{4})$")


def normalize_date(raw: Optional[str]) -> Optional[str]:
    """Best-effort parse of common resume date formats into 'YYYY-MM'.
    Returns None for "present/current" (an open-ended end date) and for anything
    unparseable -- never guesses a specific month it wasn't given."""
    if not raw:
        return None
    text = raw.strip().rstrip(".,")
    if not text:
        return None
    if text.lower() in _PRESENT_WORDS:
        return None

    if m := _RE_YYYY_MM.match(text):
        return f"{m.group(1)}-{m.group(2)}"

    if m := _RE_MONTH_YYYY.match(text):
        month_key = m.group(1).lower().rstrip(".")
        month_num = _MONTHS.get(month_key)
        if month_num:
            return f"{m.group(2)}-{month_num}"
        return None  # unrecognized month name -- don't guess

    if m := _RE_YYYY.match(text):
        # MANUAL DECISION: a bare year ("2020") has no month information. Rather
        # than guess "-01", we represent it as YYYY only (still a valid prefix of
        # YYYY-MM downstream consumers can choose to treat as "unknown month"),
        # documented here since it's a real product choice, not an obvious default.
        return text

    return None


# ---------------------------------------------------------------------------
# Skills -> canonical names
# ---------------------------------------------------------------------------

# MANUAL DECISION: seed alias table, intentionally small (~30 entries) and focused
# on the skills that actually appear in our sample fixtures plus a few common
# adjacent ones, not an exhaustive industry taxonomy (see PROJECT_CONTEXT.md §17).
# Anything not in this table passes through title-cased rather than being dropped,
# since the skills vocabulary is open-ended and a real skill missing from our seed
# list is still more useful than no skill at all.
_SKILL_ALIASES = {
    "js": "JavaScript",
    "javascript": "JavaScript",
    "ts": "TypeScript",
    "typescript": "TypeScript",
    "node": "Node.js",
    "nodejs": "Node.js",
    "node.js": "Node.js",
    "py": "Python",
    "python": "Python",
    "golang": "Go",
    "go": "Go",
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "k8s": "Kubernetes",
    "kubernetes": "Kubernetes",
    "docker": "Docker",
    "aws": "AWS",
    "amazon web services": "AWS",
    "gcp": "GCP",
    "google cloud": "GCP",
    "terraform": "Terraform",
    "sql": "SQL",
    "graphql": "GraphQL",
    "react": "React",
    "react.js": "React",
    "reactjs": "React",
    "a/b testing": "A/B Testing",
    "ab testing": "A/B Testing",
    "roadmapping": "Roadmapping",
    "stakeholder management": "Stakeholder Management",
    "accessibility": "Accessibility",
    "wcag": "WCAG",
}


def normalize_skill(raw: Optional[str]) -> Optional[str]:
    """Map a free-form skill string to a canonical name via the alias table.
    Unknown skills pass through title-cased rather than being dropped."""
    if not raw or not raw.strip():
        return None
    key = raw.strip().lower()
    if key in _SKILL_ALIASES:
        return _SKILL_ALIASES[key]
    return raw.strip().title()


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

def normalize_name(raw: Optional[str]) -> Optional[str]:
    """Trim and collapse internal whitespace. Never re-cases a name -- guessing
    proper capitalization (e.g. for names like 'McDonald' or 'de la Cruz') is a
    common source of silent, wrong-but-confident errors, so original casing from
    the source is preserved as-is."""
    if not raw:
        return None
    collapsed = re.sub(r"\s+", " ", raw.strip())
    return collapsed or None


# ---------------------------------------------------------------------------
# Location / country
# ---------------------------------------------------------------------------

# MANUAL DECISION: small common-country lookup table (not a full ISO-3166 dataset
# pulled from a library/network resource, which isn't available in this offline
# environment). Covers the country names that plausibly show up in resumes/ATS
# data for this exercise; unrecognized country strings are intentionally left as
# country=None rather than guessed, consistent with the brief's core principle.
_COUNTRY_ALPHA2 = {
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "us": "US",
    "canada": "CA",
    "united kingdom": "GB",
    "uk": "GB",
    "india": "IN",
    "germany": "DE",
    "france": "FR",
    "australia": "AU",
    "ireland": "IE",
    "netherlands": "NL",
    "singapore": "SG",
}


def normalize_country(raw: Optional[str]) -> Optional[str]:
    """Map a free-form country name to ISO-3166 alpha-2. Already-valid 2-letter
    codes are upper-cased and passed through. Unrecognized values -> None."""
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    if len(text) == 2 and text.isalpha():
        return text.upper()
    return _COUNTRY_ALPHA2.get(text.lower())


def normalize_location(location: Optional[Location]) -> Optional[Location]:
    """Apply country normalization to a Location; city/region are trimmed as-is
    (no canonicalization attempted -- city/region naming conventions vary too much
    to safely normalize without a real geocoding source, which is out of scope)."""
    if location is None:
        return None
    city = (location.city or "").strip() or None
    region = (location.region or "").strip() or None
    country = normalize_country(location.country)
    if not (city or region or country):
        return None
    return Location(city=city, region=region, country=country)