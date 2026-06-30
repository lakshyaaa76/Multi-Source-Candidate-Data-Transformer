"""
projection.py — config-driven projection engine (PROJECT_CONTEXT.md §14).

Takes a CanonicalRecord + OutputConfig and produces a plain dict whose shape is
entirely determined by the config — no hardcoded output schema.  Pure, side-effect-
free: same inputs always produce the same output dict.

Two call sites in the pipeline:
  1. default config  → produces the full canonical shape
  2. custom config   → produces whatever subset/renaming the config requests

Key capabilities implemented here (per §14):
  - `path`/`from` path resolution, including:
      * simple key access       ("full_name")
      * list index access       ("emails[0]")
      * list projection         ("skills[].name")
      * nested key access       ("location.city")
  - Per-field `normalize` override, reusing the shared normalize.py library so
    canonical and projected normalization never drift (§11 / §5).
  - `on_missing` policy: "null" | "omit" | "error".
  - `include_confidence` / `include_provenance` top-level toggles.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from transformer import normalize as _norm
from transformer.models import CanonicalRecord, FieldSpec, OutputConfig
from transformer.validate import ProjectionValidationError


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

# A "path token" is one of:
#   ("key", name)   → dict key access:  data[name]
#   ("index", n)    → list index access: data[n]
#   ("wildcard",)   → list projection:   [elem for elem in data]
#                      (must be followed by more tokens to be useful)

_Token = tuple  # ("key", str) | ("index", int) | ("wildcard",)


def _tokenize(path: str) -> list[_Token]:
    """Parse a path string into a list of access tokens.

    Examples:
        "full_name"         → [("key", "full_name")]
        "emails[0]"         → [("key", "emails"), ("index", 0)]
        "skills[].name"     → [("key", "skills"), ("wildcard",), ("key", "name")]
        "location.city"     → [("key", "location"), ("key", "city")]
        "experience[0].company" → [("key","experience"),("index",0),("key","company")]
    """
    tokens: list[_Token] = []
    # Split on '.' but not inside brackets; then process each segment
    # We process character-by-character for full generality.
    i = 0
    while i < len(path):
        if path[i] == ".":
            i += 1
            continue

        # Collect a plain key segment (up to '[' or '.')
        m_key = re.match(r"([^\.\[\]]+)", path[i:])
        if m_key:
            tokens.append(("key", m_key.group(1)))
            i += len(m_key.group(1))
            continue

        # Handle bracket: "[N]" or "[]"
        m_bracket = re.match(r"\[(\d*)\]", path[i:])
        if m_bracket:
            inner = m_bracket.group(1)
            if inner == "":
                tokens.append(("wildcard",))
            else:
                tokens.append(("index", int(inner)))
            i += len(m_bracket.group(0))
            continue

        # Unrecognised character — stop (returns what we have)
        break

    return tokens


def _apply_tokens(data: Any, tokens: list[_Token]) -> tuple[Any, bool]:
    """Walk `data` following the token list.  Returns (value, found).

    `found=False` means the path was valid but the data didn't have the
    requested key/index — callers apply the on_missing policy.
    `found=True, value=None` means the key exists but its value is None.

    For wildcard tokens, the result is a list of values obtained by applying
    the remaining tokens to each element in the list at that position.
    """
    current = data
    for i, token in enumerate(tokens):
        kind = token[0]
        if kind == "key":
            key = token[1]
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return None, False

        elif kind == "index":
            idx = token[1]
            if isinstance(current, list) and 0 <= idx < len(current):
                current = current[idx]
            else:
                return None, False

        elif kind == "wildcard":
            # Collect remaining tokens, apply recursively to each list element
            if not isinstance(current, list):
                return None, False
            remaining = tokens[i + 1:]
            if not remaining:
                # bare "field[]" with no sub-path → just return the list
                return current, True
            result = []
            for elem in current:
                val, ok = _apply_tokens(elem, remaining)
                if ok and val is not None:
                    result.append(val)
            # Wildcard always returns a (possibly empty) list — always "found"
            return result, True

    return current, True


def _resolve_path(canonical_dict: dict, path: str) -> tuple[Any, bool]:
    """Top-level path resolver.  Returns (value, found).

    `found=False` → key/index did not exist in data (missing).
    `found=True, value=None` → key exists, value is null/None.
    """
    tokens = _tokenize(path)
    if not tokens:
        return None, False
    return _apply_tokens(canonical_dict, tokens)


# ---------------------------------------------------------------------------
# Normalize override
# ---------------------------------------------------------------------------

def _apply_normalize(value: Any, directive: str) -> Any:
    """Apply the named normalizer to `value`.

    If `value` is a list, applies element-wise (for list-projected fields like
    `skills[].name` with normalize="canonical").  None elements are kept as None
    rather than normalizing them (consistent with the rest of the pipeline's
    "never-guess" principle).
    """
    if isinstance(value, list):
        return [_normalize_scalar(v, directive) for v in value]
    return _normalize_scalar(value, directive)


def _normalize_scalar(value: Any, directive: str) -> Any:
    """Apply a normalize directive to a single scalar value.

    Supported directives (case-insensitive):
      "E164"       → normalize_phone  (phone → E.164 or None)
      "canonical"  → normalize_skill  (skill name → canonical form)
      "YYYY-MM"    → normalize_date   (date string → YYYY-MM or None)
    Unknown directives are passed through unchanged — forward-compatible and
    not silently lossy.
    """
    if value is None:
        return None
    s = str(value)
    d = directive.lower()
    if d == "e164":
        return _norm.normalize_phone(s)
    if d == "canonical":
        return _norm.normalize_skill(s)
    if d in ("yyyy-mm", "date"):
        return _norm.normalize_date(s)
    # Unknown directive: pass through as-is (documented choice, not a silent drop)
    return value


# ---------------------------------------------------------------------------
# Core projection function
# ---------------------------------------------------------------------------

def project_record(record: CanonicalRecord, config: OutputConfig) -> dict:
    """Project one CanonicalRecord into a plain dict per the OutputConfig.

    For each FieldSpec in config.fields:
      1. Resolve `source_path` (spec.from_ or spec.path) against the canonical dict.
      2. Apply `normalize` override if present.
      3. Apply `on_missing` policy if the value is absent/None.
      4. Set the output key to `spec.path`.

    Appends `overall_confidence` and/or `provenance` if the corresponding
    include_ flags are True.

    Raises:
        ProjectionValidationError: when a field is missing and on_missing="error".
            The pipeline should catch this and record it as a per-candidate error
            rather than aborting the whole batch.
    """
    canonical = record.to_dict()
    result: dict = {}

    for spec in config.fields:
        raw_value, found = _resolve_path(canonical, spec.source_path)

        # Treat "found but None" the same as "not found" for on_missing purposes
        is_missing = not found or raw_value is None

        if is_missing:
            policy = config.on_missing
            if policy == "omit":
                continue  # drop the key from the output entirely
            elif policy == "error":
                raise ProjectionValidationError(
                    record.candidate_id,
                    f"field '{spec.path}' (from '{spec.source_path}') is missing "
                    f"and on_missing=\"error\" is set",
                )
            else:  # "null"
                result[spec.path] = None
                continue

        # Apply normalize directive if present
        if spec.normalize:
            raw_value = _apply_normalize(raw_value, spec.normalize)

        result[spec.path] = raw_value

    # Top-level metadata toggles
    if config.include_confidence:
        result["overall_confidence"] = record.overall_confidence
    if config.include_provenance:
        result["provenance"] = [p.to_dict() for p in record.provenance]

    return result


def project_all(records: list[CanonicalRecord], config: OutputConfig) -> list[dict]:
    """Project every record in the list.  Per-record ProjectionValidationErrors
    are re-raised as-is; callers (the pipeline) decide whether to skip or abort.

    Returns a list of projected dicts in the same order as `records`.
    Records that raise ProjectionValidationError are omitted from the result
    and their errors are collected in the returned error list.

    Returns:
        (results, errors) where `results` is list[dict] and `errors` is
        list[ProjectionValidationError].
    """
    results: list[dict] = []
    errors: list[ProjectionValidationError] = []
    for rec in records:
        try:
            results.append(project_record(rec, config))
        except ProjectionValidationError as e:
            errors.append(e)
    return results, errors


# ---------------------------------------------------------------------------
# Config loading from JSON file
# ---------------------------------------------------------------------------

def load_config(path: str) -> OutputConfig:
    """Load an OutputConfig from a JSON file.

    Raises:
        FileNotFoundError: if the file doesn't exist.
        ValueError: if the file is not valid JSON or is missing the 'fields' key.
    """
    import json
    import os

    if not os.path.exists(path):
        raise FileNotFoundError(f"config file not found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"failed to load config from '{path}': {exc}") from exc
    return OutputConfig.from_dict(data)
