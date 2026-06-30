"""
cli.py — argparse entrypoint for the candidate transformer (PROJECT_CONTEXT.md §5).

A thin wrapper around Pipeline: parses arguments, loads the OutputConfig, runs
the pipeline, and writes results to stdout or a file.

Usage examples (from the project root):

  # Default config, all sample sources, output to stdout:
  python -m transformer.cli \\
    --csv   samples/recruiter_export.csv \\
    --ats   samples/ats_blob.json \\
    --pdf   samples/resume_jane_doe.pdf samples/resume_john_smith.pdf samples/resume_alice_nguyen.pdf \\
    --notes samples/recruiter_notes_jane_doe.txt samples/recruiter_notes_john_smith.txt samples/recruiter_notes_alice_nguyen.txt

  # Custom config, output to file:
  python -m transformer.cli \\
    --csv   samples/recruiter_export.csv \\
    --ats   samples/ats_blob.json \\
    --pdf   samples/resume_jane_doe.pdf \\
    --config configs/example_custom_config.json \\
    --output output/custom_run.json

  # Or just pass all files — source type is inferred from extension:
  python -m transformer.cli --files samples/recruiter_export.csv samples/ats_blob.json ...
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from transformer import projection as proj_engine
from transformer.pipeline import Pipeline, PipelineReport


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s  %(name)s  %(message)s",
        stream=sys.stderr,
    )


# ---------------------------------------------------------------------------
# Output serialisation
# ---------------------------------------------------------------------------

def _build_output(report: PipelineReport, include_run_metadata: bool) -> dict:
    """Assemble the final JSON output dict from the pipeline report.

    Structure:
      {
        "candidates": [ ... ],         # projected dicts, one per candidate
        "run_metadata": {              # always present (useful for debugging/demo)
          "partial_records_loaded": N,
          "canonical_records_merged": N,
          "candidates_in_output": N,
          "source_errors": [ ... ],
          "candidate_errors": [ ... ]
        }
      }
    """
    output: dict = {"candidates": report.candidates}

    if include_run_metadata:
        output["run_metadata"] = {
            "partial_records_loaded": report.partial_records_loaded,
            "canonical_records_merged": report.canonical_records_merged,
            "candidates_in_output": len(report.candidates),
            "source_errors": [
                {"path": e.path, "source_type": e.source_type,
                 "stage": e.stage, "message": e.message}
                for e in report.source_errors
            ],
            "candidate_errors": [
                {"candidate_id": e.candidate_id, "stage": e.stage,
                 "message": e.message}
                for e in report.candidate_errors
            ],
        }

    return output


def _write_output(output: dict, output_path: Optional[str], indent: int) -> None:
    json_str = json.dumps(output, indent=indent, ensure_ascii=False)
    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(json_str)
            f.write("\n")
        print(f"Output written to: {output_path}", file=sys.stderr)
    else:
        print(json_str)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m transformer.cli",
        description=(
            "Transform candidate data from multiple source formats into a "
            "single canonical profile per candidate, with provenance and "
            "confidence tracking."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Source file arguments (specify at least one):
  --dir  scans a directory for .csv/.json/.pdf/.txt files (no recursion).
  --csv, --ats, --pdf, --notes accept one or more file paths each.
  --files accepts any mix; source type is inferred from the file extension.

Examples:
  # Scan the whole samples/ folder (shortest demo command):
  python -m transformer.cli --dir samples/

  # Custom config, write to file:
  python -m transformer.cli --dir samples/ --config configs/example_custom_config.json --output output/demo.json

  # Specific files only:
  python -m transformer.cli --files samples/recruiter_export.csv samples/ats_blob.json
""",
    )

    # Source file arguments — typed by flag
    src = parser.add_argument_group("source files (by type)")
    src.add_argument("--csv", dest="csv_files", nargs="+", metavar="FILE",
                     default=[], help="Recruiter CSV export file(s)")
    src.add_argument("--ats", dest="ats_files", nargs="+", metavar="FILE",
                     default=[], help="ATS JSON blob file(s)")
    src.add_argument("--pdf", dest="pdf_files", nargs="+", metavar="FILE",
                     default=[], help="Resume PDF file(s)")
    src.add_argument("--notes", dest="notes_files", nargs="+", metavar="FILE",
                     default=[], help="Recruiter notes .txt file(s)")

    # Or pass all files together and let the pipeline infer the type
    parser.add_argument(
        "--files", nargs="+", metavar="FILE", default=[],
        help="Any source files; source type is inferred from extension "
             "(.csv -> csv, .json -> ats_json, .pdf -> resume_pdf, .txt -> recruiter_notes).",
    )

    # Scan an entire directory (non-recursive) — ideal for demos
    parser.add_argument(
        "--dir", nargs="+", metavar="DIR", default=[],
        help="Scan DIR for source files (.csv, .json, .pdf, .txt); "
             "source type is inferred from extension. Non-recursive. "
             "Combine with --files/--csv/etc. for mixed runs.",
    )

    # Config
    parser.add_argument(
        "--config", metavar="FILE",
        default=None,
        help="Path to an OutputConfig JSON file. Defaults to "
             "configs/default_config.json (relative to CWD).",
    )

    # Output
    parser.add_argument(
        "--output", metavar="FILE",
        default=None,
        help="Write JSON output to FILE instead of stdout.",
    )

    # Formatting
    parser.add_argument(
        "--indent", type=int, default=2, metavar="N",
        help="JSON indentation level (default: 2). Use 0 for compact output.",
    )
    parser.add_argument(
        "--no-metadata", action="store_true",
        help="Omit the run_metadata block from the output JSON.",
    )

    # Verbosity
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable DEBUG logging to stderr.",
    )

    return parser


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

# Fix the Optional import that _write_output uses
from typing import Optional  # noqa: E402 (after stdlib, before third-party — acceptable here)


def main(argv: list[str] | None = None) -> int:
    """Parse args, run pipeline, write output.  Returns exit code (0 = success)."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    # Assemble source list
    sources: list[tuple[str, str]] = []
    for path in args.csv_files:
        sources.append((path, "csv"))
    for path in args.ats_files:
        sources.append((path, "ats_json"))
    for path in args.pdf_files:
        sources.append((path, "resume_pdf"))
    for path in args.notes_files:
        sources.append((path, "recruiter_notes"))

    # --files: infer types from extensions
    inferred_sources = list(args.files)

    # --dir: scan each directory for recognised file types (non-recursive)
    for dir_path in args.dir:
        if not os.path.isdir(dir_path):
            print(f"Warning: --dir '{dir_path}' is not a directory, skipping.",
                  file=sys.stderr)
            continue
        for entry in sorted(os.scandir(dir_path), key=lambda e: e.name):
            if entry.is_file() and os.path.splitext(entry.name)[1].lower() in \
                    {".csv", ".json", ".pdf", ".txt"}:
                inferred_sources.append(entry.path)

    if not sources and not inferred_sources:
        parser.error(
            "No source files specified. Use --dir, --files, --csv, --ats, --pdf, or --notes."
        )

    # Load config
    config_path = args.config
    if config_path is None:
        # Default: look for configs/default_config.json relative to CWD
        default_path = os.path.join("configs", "default_config.json")
        if os.path.exists(default_path):
            config_path = default_path
        else:
            parser.error(
                f"No --config specified and default '{default_path}' not found. "
                "Please provide a config file path with --config."
            )

    try:
        config = proj_engine.load_config(config_path)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        return 1

    # Run pipeline
    if inferred_sources:
        # Use from_files for the inferred batch; merge with any typed sources
        if sources:
            # Run typed sources through the normal path first, then merge reports
            report_typed = Pipeline(config).run(sources)
        else:
            report_typed = None

        from transformer.pipeline import PipelineReport as _PR
        report_inferred = Pipeline.from_files(inferred_sources, config)

        if report_typed is not None:
            # Merge the two reports
            report_inferred.candidates.extend(report_typed.candidates)
            report_inferred.source_errors.extend(report_typed.source_errors)
            report_inferred.candidate_errors.extend(report_typed.candidate_errors)
            report_inferred.partial_records_loaded += report_typed.partial_records_loaded
            report_inferred.canonical_records_merged += report_typed.canonical_records_merged

        report = report_inferred
    else:
        report = Pipeline(config).run(sources)

    # Build and write output
    output = _build_output(report, include_run_metadata=not args.no_metadata)
    _write_output(output, args.output, indent=args.indent)

    # Print a summary to stderr so it's always visible
    n_cands = len(report.candidates)
    n_src_err = len(report.source_errors)
    n_cand_err = len(report.candidate_errors)
    print(
        f"Done: {n_cands} candidate(s) in output"
        + (f", {n_src_err} source error(s)" if n_src_err else "")
        + (f", {n_cand_err} candidate error(s)" if n_cand_err else ""),
        file=sys.stderr,
    )

    # Exit non-zero if every source failed (nothing was produced and errors exist)
    if n_cands == 0 and (n_src_err > 0 or n_cand_err > 0):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
