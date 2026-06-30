"""
validate.py — schema validation for pipeline outputs (PROJECT_CONTEXT.md §15).

Two validation passes (both via jsonschema):

1. validate_canonical (Phase 6) — sanity-checks a CanonicalRecord right after
   merge, before it reaches the projection layer. Catches bugs in our own pipeline
   (wrong types, missing candidate_id, confidence out of range, orphaned provenance
   references), NOT user input errors.

2. validate_projection (Phase 7) — validates each projected dict against the
   schema *derived from the OutputConfig itself*, enforcing required fields and
   declared types. Implemented in Phase 7.

Design note: both validators are pure functions with no side effects. They raise
CanonicalValidationError / ProjectionValidationError on failure rather than
returning a bool, so callers get a structured, informative exception they can log
and recover from (the pipeline catches these and records them as per-candidate
errors without aborting the full run — see pipeline.py).
"""

from __future__ import annotations

from typing import Optional

import jsonschema
import jsonschema.exceptions

from transformer.models import CanonicalRecord


# ---------------------------------------------------------------------------
# Custom exception types
# ---------------------------------------------------------------------------

class CanonicalValidationError(ValueError):
    """Raised when a CanonicalRecord fails internal schema validation.

    Carries the `candidate_id` of the failing record and a human-readable
    `reason` so the pipeline can log it without exposing raw jsonschema
    internals to callers.
    """
    def __init__(self, candidate_id: str, reason: str) -> None:
        self.candidate_id = candidate_id
        self.reason = reason
        super().__init__(f"[{candidate_id}] canonical validation failed: {reason}")


class ProjectionValidationError(ValueError):
    """Raised when a projected output dict fails the OutputConfig-derived schema.

    Implemented in Phase 7 alongside projection.py and validate_projection().
    """
    def __init__(self, candidate_id: str, reason: str) -> None:
        self.candidate_id = candidate_id
        self.reason = reason
        super().__init__(f"[{candidate_id}] projection validation failed: {reason}")


# ---------------------------------------------------------------------------
# Internal (canonical) JSON schema
# ---------------------------------------------------------------------------

# The schema below reflects the CanonicalRecord dataclass (models.py §8) exactly.
# It is used to verify structural correctness of what our own merge engine produces,
# not to validate arbitrary external data.  This means the schema can be strict
# about types that the merge engine must always get right (e.g. candidate_id must
# be a non-empty string, overall_confidence must be a number) while still allowing
# optional fields to be null or absent (since many candidates won't fill every field).
#
# Sub-schemas are defined at the top level and referenced via $ref to avoid
# repetition and to keep them individually readable.

_CANONICAL_SCHEMA: dict = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "CanonicalRecord",
    "type": "object",
    "required": ["candidate_id", "emails", "phones", "skills", "experience",
                 "education", "provenance", "overall_confidence"],
    "additionalProperties": False,

    # Sub-schemas used via $ref below
    "definitions": {
        "location": {
            "type": ["object", "null"],
            "properties": {
                "city":    {"type": ["string", "null"]},
                "region":  {"type": ["string", "null"]},
                "country": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "links": {
            "type": "object",
            "properties": {
                "linkedin":  {"type": ["string", "null"]},
                "github":    {"type": ["string", "null"]},
                "portfolio": {"type": ["string", "null"]},
                "other":     {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
        "skill": {
            "type": "object",
            "required": ["name", "confidence", "sources"],
            "properties": {
                "name":       {"type": "string", "minLength": 1},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "sources":    {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
        "experience_entry": {
            "type": "object",
            "properties": {
                "company": {"type": ["string", "null"]},
                "title":   {"type": ["string", "null"]},
                "start":   {"type": ["string", "null"]},
                "end":     {"type": ["string", "null"]},
                "summary": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "education_entry": {
            "type": "object",
            "properties": {
                "institution": {"type": ["string", "null"]},
                "degree":      {"type": ["string", "null"]},
                "field":       {"type": ["string", "null"]},
                "end_year":    {"type": ["integer", "null"]},
            },
            "additionalProperties": False,
        },
        "provenance_entry": {
            "type": "object",
            "required": ["field", "source", "method", "confidence"],
            "properties": {
                "field":      {"type": "string", "minLength": 1},
                "source":     {"type": "string", "minLength": 1},
                "method":     {
                    "type": "string",
                    "enum": ["direct", "regex", "heuristic", "merged", "failed_normalize"],
                },
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            },
            "additionalProperties": False,
        },
    },

    "properties": {
        "candidate_id": {
            "type": "string",
            "minLength": 1,
            "description": "Non-empty deterministic identifier generated by identity.py",
        },
        "full_name":    {"type": ["string", "null"]},
        "emails":       {"type": "array", "items": {"type": "string"}},
        "phones":       {"type": "array", "items": {"type": "string"}},
        "location":     {"$ref": "#/definitions/location"},
        "links":        {"$ref": "#/definitions/links"},
        "headline":     {"type": ["string", "null"]},
        "years_experience": {"type": ["number", "null"]},
        "skills":       {
            "type": "array",
            "items": {"$ref": "#/definitions/skill"},
        },
        "experience":   {
            "type": "array",
            "items": {"$ref": "#/definitions/experience_entry"},
        },
        "education":    {
            "type": "array",
            "items": {"$ref": "#/definitions/education_entry"},
        },
        "provenance":   {
            "type": "array",
            "items": {"$ref": "#/definitions/provenance_entry"},
        },
        "overall_confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "Weighted average of per-field confidences (§13)",
        },
    },
}


# ---------------------------------------------------------------------------
# Known top-level field prefixes in a CanonicalRecord, used by the orphan-
# provenance check.  A Provenance entry whose `field` doesn't start with one
# of these prefixes is treated as an orphan (refers to a field that doesn't
# exist on the canonical record — a bug in the merge engine).
# ---------------------------------------------------------------------------
_CANONICAL_FIELD_PREFIXES: frozenset[str] = frozenset({
    "candidate_id",
    "full_name",
    "emails",
    "phones",
    "location",
    "links",
    "headline",
    "years_experience",
    "skills",
    "experience",
    "education",
    # note: "provenance" and "overall_confidence" do not appear as provenance
    # entries themselves (the provenance list doesn't track its own construction)
})


# ---------------------------------------------------------------------------
# Public API — Phase 6
# ---------------------------------------------------------------------------

def validate_canonical(record: CanonicalRecord) -> None:
    """Validate a CanonicalRecord produced by the merge engine.

    Runs two checks:

    1. JSON schema validation — enforces structural correctness: correct types,
       required keys present, enum values on provenance method, confidence
       values in [0, 1] for every skill and provenance entry.

    2. Semantic sanity checks — catches pipeline bugs that JSON schema alone
       cannot express:
       - `overall_confidence` in [0.0, 1.0] (also enforced by schema, but
         checked again explicitly so the error message names the field clearly).
       - No Provenance entry whose `field` prefix refers to a field that
         doesn't exist on a CanonicalRecord ("orphan provenance"). This catches
         a merge.py typo like `Provenance(field="skilz[Python]", ...)` which
         would otherwise silently pollute provenance with an unreachable path.

    Raises:
        CanonicalValidationError: with a descriptive reason. The pipeline should
            catch this, log it, and continue with other candidates rather than
            aborting the whole run — a single bad canonical record is a pipeline
            bug, not a fatal system failure.
    """
    cid = record.candidate_id or "<unknown>"

    # ------------------------------------------------------------------
    # Pass 1: structural JSON-schema check
    # ------------------------------------------------------------------
    record_dict = record.to_dict()
    try:
        jsonschema.validate(instance=record_dict, schema=_CANONICAL_SCHEMA)
    except jsonschema.exceptions.ValidationError as exc:
        # exc.message gives the first failing constraint; json_path gives the
        # location in the document. Together they're more useful than the full
        # schema path, which tends to be noisy for reviewers.
        path = " > ".join(str(p) for p in exc.absolute_path) or "root"
        raise CanonicalValidationError(
            cid,
            f"schema violation at '{path}': {exc.message}",
        ) from exc

    # ------------------------------------------------------------------
    # Pass 2: semantic sanity checks
    # (These are catches for bugs in *our own* pipeline, not user errors.)
    # ------------------------------------------------------------------

    # 2a. overall_confidence range — also in schema, but explicit here so
    #     the error message is clear without having to decode schema output.
    conf = record.overall_confidence
    if not (0.0 <= conf <= 1.0):
        raise CanonicalValidationError(
            cid,
            f"overall_confidence {conf!r} is outside [0.0, 1.0] — this is a "
            "merge.py bug; check _compute_overall_confidence()",
        )

    # 2b. Orphan provenance check — every Provenance entry's field prefix must
    #     correspond to a real field on CanonicalRecord.
    orphans: list[str] = []
    for prov in record.provenance:
        prefix = prov.field.split("[")[0].split(".")[0]  # e.g. "skills" from "skills[Python]"
        if prefix not in _CANONICAL_FIELD_PREFIXES:
            orphans.append(prov.field)
    if orphans:
        raise CanonicalValidationError(
            cid,
            f"provenance entries reference unknown field prefix(es): "
            f"{orphans!r}. This is a merge.py bug — check Provenance(field=...) "
            "call sites and compare against _CANONICAL_FIELD_PREFIXES in validate.py",
        )


# ---------------------------------------------------------------------------
# validate_projection — Phase 7
# ---------------------------------------------------------------------------

# Maps FieldSpec.type strings to their JSON-schema equivalents.
# "string[]", "number[]", etc. are handled by the _type_str_to_schema helper.
_JSON_PRIMITIVE_TYPES: dict[str, str] = {
    "string": "string",
    "number": "number",
    "integer": "integer",
    "boolean": "boolean",
    "object": "object",
    "array": "array",
}


def _type_str_to_schema(type_str: str, on_missing: str) -> dict:
    """Convert a FieldSpec type string to a JSON Schema fragment.

    on_missing="null" → type allows null (["T", "null"]).
    on_missing="omit" / "error" → type is strict (just "T").

    Handles "T[]" shorthand for arrays of primitive type T.
    Unknown type strings fall back to {} (no type constraint) rather than
    erroring — forward-compatible and not silently lossy.
    """
    nullable = on_missing == "null"

    # Array shorthand: "string[]", "number[]", etc.
    if type_str.endswith("[]"):
        item_type_str = type_str[:-2]
        item_json_type = _JSON_PRIMITIVE_TYPES.get(item_type_str, "string")
        if nullable:
            return {"type": ["array", "null"], "items": {"type": item_json_type}}
        return {"type": "array", "items": {"type": item_json_type}}

    json_type = _JSON_PRIMITIVE_TYPES.get(type_str)
    if json_type is None:
        return {}  # unknown type — no constraint, forward-compatible

    if nullable:
        return {"type": [json_type, "null"]}
    return {"type": json_type}


def _build_projection_schema(config) -> dict:
    """Build a JSON Schema from an OutputConfig.

    The generated schema:
      - Has a `properties` entry for every FieldSpec (using the spec's `path`
        as the output key and its `type` as the constraint).
      - Has a `required` array containing all FieldSpec paths where
        `required=True` and on_missing is not "omit" (omit means the key may be
        absent, so requiring it in the schema would contradict the policy).
      - Includes `overall_confidence` (number [0,1]) if include_confidence=True.
      - Includes `provenance` (array) if include_provenance=True.

    Does NOT use `additionalProperties: false` because the two optional metadata
    keys (overall_confidence, provenance) are always potentially present regardless
    of the fields list, and we don't want to reject records that happen to have
    extra keys introduced by future pipeline stages.
    """
    properties: dict = {}
    required: list[str] = []

    for spec in config.fields:
        properties[spec.path] = _type_str_to_schema(spec.type, config.on_missing)
        # A field is schema-required only if it's marked required AND the policy
        # won't omit it when missing (omit means absent is OK by design).
        if spec.required and config.on_missing != "omit":
            required.append(spec.path)

    if config.include_confidence:
        properties["overall_confidence"] = {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
        }

    if config.include_provenance:
        properties["provenance"] = {"type": "array"}

    schema: dict = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "ProjectedRecord",
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required

    return schema


def validate_projection(candidate_id: str, projected: dict, config) -> None:
    """Validate a projected output dict against the schema derived from OutputConfig.

    Builds a JSON Schema on the fly from `config.fields[].type` + `required` flags,
    then runs `jsonschema.validate` on `projected`.  This satisfies the design
    requirement (§15) that every projected record is validated against "the schema
    implied by the OutputConfig" before being returned — auto-generated, not
    hand-maintained.

    NOTE: `on_missing="error"` violations are caught *during projection* in
    projection.py (before this validator is called).  This validator catches type
    mismatches and required-field absences that survive projection (e.g. a field
    that was projected to null but was marked required with on_missing="null").

    Args:
        candidate_id: identifier used in error messages for traceability.
        projected:    the dict produced by projection.project_record().
        config:       the OutputConfig instance that produced `projected`; its
                      `fields`, `include_confidence`, `include_provenance`, and
                      `on_missing` drive schema generation.

    Raises:
        ProjectionValidationError: with a descriptive reason string containing
            the offending field path and the constraint that failed.
    """
    schema = _build_projection_schema(config)
    try:
        jsonschema.validate(instance=projected, schema=schema)
    except jsonschema.exceptions.ValidationError as exc:
        path = " > ".join(str(p) for p in exc.absolute_path) or "root"
        raise ProjectionValidationError(
            candidate_id,
            f"schema violation at '{path}': {exc.message}",
        ) from exc
