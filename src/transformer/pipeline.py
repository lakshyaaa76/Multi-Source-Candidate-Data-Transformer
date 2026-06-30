"""
pipeline.py — end-to-end orchestration.

The Pipeline class wires every stage in order:
  1. Load   — one LoadResult per source file (never raises; ok=False on failure)
  2. Parse  — source-specific extraction → list[PartialRecord]
  3. Group  — identity.group_records → dict[candidate_id → list[PartialRecord]]
  4. Merge  — merge.merge_all → list[CanonicalRecord]
  5. Validate (internal) — validate.validate_canonical per record
  6. Project — projection.project_record per record + OutputConfig
  7. Validate (output) — validate.validate_projection per projected dict

Failures at every stage are collected into a structured PipelineReport rather
than raising — consistent with the design principle "degrade gracefully on a
missing/garbage source".  The CLI then decides how to surface the report.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from transformer import identity, merge
from transformer import projection as proj_engine
from transformer import validate
from transformer.loaders import (
    ats_json_loader,
    csv_loader,
    recruiter_notes_loader,
    resume_pdf_loader,
)
from transformer.models import (
    CanonicalRecord,
    LoadResult,
    OutputConfig,
    PartialRecord,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Source-type routing
# ---------------------------------------------------------------------------

# Map source_type strings to (loader_module) with load() and parse() functions.
# Each loader exposes exactly: load(path) -> LoadResult, parse(result) -> list[PartialRecord].
_LOADERS = {
    "csv":              csv_loader,
    "ats_json":         ats_json_loader,
    "resume_pdf":       resume_pdf_loader,
    "recruiter_notes":  recruiter_notes_loader,
}

# Convenience: infer source_type from a file extension when the caller doesn't
# specify it explicitly.  The CLI uses this so users can just pass file paths
# without --source-type flags.
_EXT_TO_SOURCE_TYPE: dict[str, str] = {
    ".csv":  "csv",
    ".json": "ats_json",
    ".pdf":  "resume_pdf",
    ".txt":  "recruiter_notes",
}


def infer_source_type(path: str) -> Optional[str]:
    """Infer a source_type string from a file's extension.  Returns None if
    the extension is not recognised — the pipeline will skip the file with a
    warning rather than guessing incorrectly."""
    _, ext = os.path.splitext(path.lower())
    return _EXT_TO_SOURCE_TYPE.get(ext)


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

@dataclass
class SourceError:
    """A non-fatal error from loading or parsing one source file."""
    path: str
    source_type: str
    stage: str          # "load" | "parse"
    message: str


@dataclass
class CandidateError:
    """A non-fatal error from processing one candidate (validation / projection)."""
    candidate_id: str
    stage: str          # "validate_canonical" | "project" | "validate_projection"
    message: str


@dataclass
class PipelineReport:
    """Structured result of one full pipeline run.

    `candidates` holds every successfully projected output dict.
    `source_errors` and `candidate_errors` hold non-fatal failures; they are
    always present (possibly empty) so callers can include them in reports
    without having to check for None.
    `partial_records_loaded` and `canonical_records_merged` are informational
    counts useful for debugging and demo output.
    """
    candidates: list[dict] = field(default_factory=list)
    source_errors: list[SourceError] = field(default_factory=list)
    candidate_errors: list[CandidateError] = field(default_factory=list)
    partial_records_loaded: int = 0
    canonical_records_merged: int = 0


# ---------------------------------------------------------------------------
# Pipeline class
# ---------------------------------------------------------------------------

class Pipeline:
    """Wires all pipeline stages; each stage can fail independently.

    Usage:
        config = projection.load_config("configs/default_config.json")
        pipeline = Pipeline(config)
        report = pipeline.run([
            ("path/to/recruiter_export.csv", "csv"),
            ("path/to/ats_blob.json",        "ats_json"),
            ("path/to/resume_jane.pdf",      "resume_pdf"),
            ("path/to/notes_jane.txt",       "recruiter_notes"),
        ])
        # report.candidates  → list of projected dicts ready for JSON output
        # report.source_errors / report.candidate_errors → non-fatal failures

    Or use the convenience classmethod for the common case:
        report = Pipeline.from_files(file_paths, config)
    """

    def __init__(self, config: OutputConfig) -> None:
        self.config = config

    # ------------------------------------------------------------------
    # Stage 1+2: load + parse
    # ------------------------------------------------------------------

    def _load_and_parse(
        self,
        sources: list[tuple[str, str]],   # [(path, source_type), ...]
        report: PipelineReport,
    ) -> list[PartialRecord]:
        """Load every source file, collect errors, return all PartialRecords."""
        all_partial: list[PartialRecord] = []

        for path, source_type in sources:
            loader = _LOADERS.get(source_type)
            if loader is None:
                report.source_errors.append(SourceError(
                    path=path, source_type=source_type, stage="load",
                    message=f"unknown source_type '{source_type}'; "
                            f"supported: {list(_LOADERS)}",
                ))
                logger.warning("unknown source_type '%s' for path '%s'", source_type, path)
                continue

            # Load
            try:
                result: LoadResult = loader.load(path)
            except Exception as exc:  # belt-and-suspenders; loaders should never raise
                report.source_errors.append(SourceError(
                    path=path, source_type=source_type, stage="load",
                    message=f"unexpected exception during load: {exc}",
                ))
                logger.exception("unexpected exception loading '%s'", path)
                continue

            if not result.ok:
                report.source_errors.append(SourceError(
                    path=path, source_type=source_type, stage="load",
                    message=result.error or "load returned ok=False (no detail)",
                ))
                logger.warning("load failed for '%s': %s", path, result.error)
                continue

            logger.debug("loaded '%s' (%s)", path, source_type)

            # Parse
            try:
                records = loader.parse(result)
            except Exception as exc:  # belt-and-suspenders; parsers should never raise
                report.source_errors.append(SourceError(
                    path=path, source_type=source_type, stage="parse",
                    message=f"unexpected exception during parse: {exc}",
                ))
                logger.exception("unexpected exception parsing '%s'", path)
                continue

            logger.debug("parsed %d record(s) from '%s'", len(records), path)
            all_partial.extend(records)

        return all_partial

    # ------------------------------------------------------------------
    # Stages 3+4: group + merge
    # ------------------------------------------------------------------

    def _group_and_merge(self, partial: list[PartialRecord]) -> list[CanonicalRecord]:
        grouped = identity.group_records(partial)
        logger.debug("grouped %d partial records into %d candidate(s)",
                     len(partial), len(grouped))
        canonical = merge.merge_all(grouped)
        logger.debug("merged into %d canonical record(s)", len(canonical))
        return canonical

    # ------------------------------------------------------------------
    # Stage 5: internal validation
    # ------------------------------------------------------------------

    def _validate_canonical(
        self,
        canonical: list[CanonicalRecord],
        report: PipelineReport,
    ) -> list[CanonicalRecord]:
        """Validate each CanonicalRecord.  Records that fail are excluded from
        further processing and logged as CandidateErrors (this would indicate
        a bug in our merge engine, not a user data problem)."""
        valid: list[CanonicalRecord] = []
        for rec in canonical:
            try:
                validate.validate_canonical(rec)
                valid.append(rec)
            except validate.CanonicalValidationError as e:
                report.candidate_errors.append(CandidateError(
                    candidate_id=rec.candidate_id,
                    stage="validate_canonical",
                    message=e.reason,
                ))
                logger.error("canonical validation failed for '%s': %s",
                             rec.candidate_id, e.reason)
        return valid

    # ------------------------------------------------------------------
    # Stages 6+7: project + validate output
    # ------------------------------------------------------------------

    def _project_and_validate(
        self,
        canonical: list[CanonicalRecord],
        report: PipelineReport,
    ) -> list[dict]:
        """Project each CanonicalRecord with the OutputConfig, then validate
        the projected dict.  Per-candidate errors are collected; the record
        is skipped from the final output but the run continues."""
        results: list[dict] = []
        for rec in canonical:
            # Project
            try:
                projected = proj_engine.project_record(rec, self.config)
            except validate.ProjectionValidationError as e:
                # on_missing="error" fires here for a missing required field
                report.candidate_errors.append(CandidateError(
                    candidate_id=rec.candidate_id,
                    stage="project",
                    message=e.reason,
                ))
                logger.warning("projection error for '%s': %s", rec.candidate_id, e.reason)
                continue
            except Exception as exc:
                report.candidate_errors.append(CandidateError(
                    candidate_id=rec.candidate_id,
                    stage="project",
                    message=f"unexpected exception: {exc}",
                ))
                logger.exception("unexpected projection error for '%s'", rec.candidate_id)
                continue

            # Validate projected output
            try:
                validate.validate_projection(rec.candidate_id, projected, self.config)
            except validate.ProjectionValidationError as e:
                report.candidate_errors.append(CandidateError(
                    candidate_id=rec.candidate_id,
                    stage="validate_projection",
                    message=e.reason,
                ))
                logger.warning("projection validation failed for '%s': %s",
                               rec.candidate_id, e.reason)
                continue

            results.append(projected)

        return results

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self, sources: list[tuple[str, str]]) -> PipelineReport:
        """Run the full pipeline for the given list of (path, source_type) tuples.

        Args:
            sources: list of (file_path, source_type) pairs.  source_type must
                     be one of: "csv", "ats_json", "resume_pdf", "recruiter_notes".

        Returns:
            A PipelineReport with .candidates (projected dicts ready for JSON
            output) and .source_errors / .candidate_errors for reporting.
        """
        report = PipelineReport()

        # Stages 1+2: load + parse
        partial = self._load_and_parse(sources, report)
        report.partial_records_loaded = len(partial)
        logger.info("loaded %d partial record(s) from %d source file(s)",
                    len(partial), len(sources))

        if not partial:
            logger.warning("no partial records loaded — all sources failed or were empty")
            return report

        # Stages 3+4: group + merge
        canonical = self._group_and_merge(partial)
        report.canonical_records_merged = len(canonical)

        # Stage 5: internal validation
        canonical = self._validate_canonical(canonical, report)

        # Stages 6+7: project + validate output
        report.candidates = self._project_and_validate(canonical, report)
        logger.info("pipeline complete: %d candidate(s) in output, "
                    "%d source error(s), %d candidate error(s)",
                    len(report.candidates),
                    len(report.source_errors),
                    len(report.candidate_errors))

        return report

    # ------------------------------------------------------------------
    # Convenience classmethod
    # ------------------------------------------------------------------

    @classmethod
    def from_files(
        cls,
        file_paths: list[str],
        config: OutputConfig,
    ) -> PipelineReport:
        """Convenience entry point: infer source_type from each file's extension
        and run the pipeline.  Files with unrecognised extensions are skipped
        with a SourceError rather than raising.

        Args:
            file_paths: list of absolute or relative paths to source files.
            config:     OutputConfig to use for projection.
        """
        sources: list[tuple[str, str]] = []
        # We'll collect unknown-extension paths via a temporary report, then fold
        # those errors into the real report inside run().
        unknown_ext: list[str] = []

        for path in file_paths:
            st = infer_source_type(path)
            if st is None:
                unknown_ext.append(path)
            else:
                sources.append((path, st))

        pipeline = cls(config)
        report = pipeline.run(sources)

        for path in unknown_ext:
            _, ext = os.path.splitext(path)
            report.source_errors.append(SourceError(
                path=path, source_type="unknown", stage="load",
                message=f"cannot infer source_type from extension '{ext}'; "
                        f"recognised extensions: {list(_EXT_TO_SOURCE_TYPE)}",
            ))

        return report
