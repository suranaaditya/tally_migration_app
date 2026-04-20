"""Tally XML slim preprocessor — CLI.

Reads a Tally ERP 9 "All Masters" XML export (any size, typically 150-300 MB
in UTF-16-LE), applies the whitelist-based filter from
``rgi_migration.parsers.tally_tag_whitelist``, and writes a UTF-8 slim
XML output that round-trips through ``rgi_migration``'s parser to an
identical ``ParsedTallyTB``.

Streaming design: ``lxml.etree.iterparse`` with ``tag=("REQUESTDESC",
"TALLYMESSAGE")`` plus aggressive element clearing keeps peak memory
bounded to a single TALLYMESSAGE payload, so the 220 MB CACSPU reference
export processes in ~20 seconds under 150 MB RSS.

Exit codes:
  0 — success (output written, strict validation passed if enabled)
  1 — input error (missing file, not a Tally All Masters XML, etc.)
  2 — strict re-parse validation failed (output written but parser rejected it)
  3 — unexpected exception (with traceback in sidecar log)

Usage:
  python -m tools.tally_slim.preprocessor --input FULL.xml --output SLIM.xml
  python -m tools.tally_slim.preprocessor --input FULL.xml --output SLIM.xml --no-strict
  python -m tools.tally_slim.preprocessor --input FULL.xml --output SLIM.xml --log custom.log --quiet

Not invoked directly by ``rgi_migration`` — bookkeepers run this via the
GUI wrapper (``tools/tally_slim/gui.py``) or CLI before uploading slim
files to the ERPNext Desk UI.  No Frappe dependency; ships as a
standalone PyInstaller ``.exe``.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from lxml import etree

from rgi_migration.parsers.tally_tag_whitelist import (
    BILLALLOC_CHILDREN_KEEP,
    GROUP_ATTRIBUTES_KEEP,
    GROUP_CHILDREN_KEEP,
    LEDGER_ATTRIBUTES_KEEP,
    LEDGER_CHILDREN_KEEP,
    TALLYMESSAGE_CHILDREN_KEEP,
    WHITELIST_VERSION,
)

from tools.tally_slim import __version__ as TOOL_VERSION

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_INPUT_ERROR = 1
EXIT_STRICT_FAILED = 2
EXIT_UNEXPECTED = 3


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class InputError(Exception):
    """Input file missing / unreadable / not a Tally All Masters XML."""


class StrictValidationError(Exception):
    """--strict re-parse of the slim output failed."""


# ---------------------------------------------------------------------------
# Run summary (populated during streaming, consumed by log writer)
# ---------------------------------------------------------------------------

@dataclass
class RunSummary:
    input_path: Path
    output_path: Path
    input_size: int = 0
    output_size: int = 0
    input_encoding: str = "unknown"
    kept_ledger: int = 0
    kept_group: int = 0
    dropped_by_type: dict[str, int] = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)
    strict_mode: bool = True
    strict_result: str = "not-run"   # "not-run" | "passed" | "failed: <msg>"
    strict_summary: str = ""          # ledger/group counts from re-parse, if passed


# ---------------------------------------------------------------------------
# Encoding detection (mirrors tally_xml_parser._detect_format)
# ---------------------------------------------------------------------------

def _detect_encoding(head_bytes: bytes) -> str:
    """Return 'UTF-16-LE', 'UTF-16-BE', or 'UTF-8' based on BOM / heuristics."""
    if head_bytes[:2] == b"\xff\xfe":
        return "UTF-16-LE"
    if head_bytes[:2] == b"\xfe\xff":
        return "UTF-16-BE"
    if head_bytes[:3] == b"\xef\xbb\xbf":
        return "UTF-8-BOM"
    return "UTF-8"


def _peek_text(path: Path, max_bytes: int = 16384) -> tuple[str, str]:
    """Read first ``max_bytes`` of ``path`` and decode to text.

    Returns ``(decoded_text, encoding_name)``.  Uses errors='replace' so
    a mostly-UTF-16 file with a garbled tail can still be sniffed.
    """
    raw = path.read_bytes()[:max_bytes]
    enc = _detect_encoding(raw)
    if enc.startswith("UTF-16"):
        return raw.decode(enc, errors="replace"), enc
    if enc == "UTF-8-BOM":
        return raw[3:].decode("utf-8", errors="replace"), enc
    return raw.decode("utf-8", errors="replace"), enc


# ---------------------------------------------------------------------------
# Pre-flight validation
# ---------------------------------------------------------------------------

def _validate_input(path: Path) -> str:
    """Raise ``InputError`` if ``path`` doesn't look like a Tally All Masters XML.

    Returns the detected encoding name.
    """
    if not path.exists():
        raise InputError(f"Input file not found: {path}")
    if not path.is_file():
        raise InputError(f"Input path is not a regular file: {path}")
    try:
        head, enc = _peek_text(path)
    except OSError as exc:
        raise InputError(f"Cannot read input file: {path} ({exc})") from exc

    if not head.strip():
        raise InputError(f"Input file is empty: {path}")

    # Reject display-report format (out of scope — see Work Item 8 design §8d)
    if "DSPACCNAME" in head and "All Masters" not in head:
        raise InputError(
            f"Input appears to be a Tally display-report XML "
            f"(DSPACCNAME/DSPACCINFO format). The slim preprocessor supports "
            f"All Masters format only — display-report exports are small "
            f"enough to upload directly."
        )

    # Require at least one Tally All Masters marker
    if "All Masters" not in head and "TALLYMESSAGE" not in head:
        raise InputError(
            f"Input does not look like a Tally All Masters XML export — "
            f"neither '<REPORTNAME>All Masters</REPORTNAME>' nor "
            f"'<TALLYMESSAGE>' found in first 16 KB of {path}. "
            f"Re-export from Tally with 'Export → Masters → All Masters'."
        )
    return enc


# ---------------------------------------------------------------------------
# Element filtering
# ---------------------------------------------------------------------------

def _filter_attributes(elem: etree._Element, keep: frozenset[str]) -> None:
    """Strip attributes not in ``keep`` (mutates in place)."""
    for k in list(elem.attrib.keys()):
        if k not in keep:
            del elem.attrib[k]


def _filter_children(elem: etree._Element, keep: frozenset[str]) -> None:
    """Remove child elements whose tags are not in ``keep`` (mutates in place)."""
    for child in list(elem):
        if child.tag not in keep:
            elem.remove(child)


def _filter_ledger(elem: etree._Element) -> None:
    """Apply LEDGER whitelist: attributes, children, and BILLALLOCATIONS.LIST recursion."""
    _filter_attributes(elem, LEDGER_ATTRIBUTES_KEEP)
    _filter_children(elem, LEDGER_CHILDREN_KEEP)
    # Descend one level into BILLALLOCATIONS.LIST — parser reads NAME, BILLDATE,
    # OPENINGBALANCE on each bill.  Strip ISADVANCE, BILLCREDITPERIOD, etc.
    for bill in elem.iterchildren("BILLALLOCATIONS.LIST"):
        _filter_children(bill, BILLALLOC_CHILDREN_KEEP)
        # BILLALLOCATIONS.LIST itself has no attributes the parser reads;
        # strip all for consistency.
        bill.attrib.clear()


def _filter_group(elem: etree._Element) -> None:
    """Apply GROUP whitelist: attributes + children."""
    _filter_attributes(elem, GROUP_ATTRIBUTES_KEEP)
    _filter_children(elem, GROUP_CHILDREN_KEEP)


# ---------------------------------------------------------------------------
# Output emission
# ---------------------------------------------------------------------------

_MINIMAL_REQUEST_DESC = (
    "<REQUESTDESC>"
    "<REPORTNAME>All Masters</REPORTNAME>"
    "<STATICVARIABLES><SVCURRENTCOMPANY/></STATICVARIABLES>"
    "</REQUESTDESC>"
)


def _write_prologue(
    out,                                  # noqa: ANN001 — file-like, text mode
    request_desc_xml: str | None,
) -> None:
    """Write <?xml?> + <ENVELOPE>/<HEADER>/... + <REQUESTDATA> opening.

    If ``request_desc_xml`` is None, emits a minimal REQUESTDESC stub
    (company name missing — parser will fall back to filename stem).
    """
    out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    out.write("<ENVELOPE>\n")
    out.write("<HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>\n")
    out.write("<BODY>\n<IMPORTDATA>\n")
    out.write(request_desc_xml if request_desc_xml else _MINIMAL_REQUEST_DESC)
    out.write("\n<REQUESTDATA>\n")


def _write_epilogue(out) -> None:                # noqa: ANN001
    out.write("</REQUESTDATA>\n</IMPORTDATA>\n</BODY>\n</ENVELOPE>\n")


def _write_tallymessage(out, serialized_child: str) -> None:   # noqa: ANN001
    """Wrap a filtered LEDGER/GROUP in a fresh <TALLYMESSAGE> and write it."""
    out.write("<TALLYMESSAGE>")
    out.write(serialized_child)
    out.write("</TALLYMESSAGE>\n")


# ---------------------------------------------------------------------------
# Streaming pass
# ---------------------------------------------------------------------------

def _stream_and_filter(
    input_path: Path,
    output_path: Path,
    summary: RunSummary,
) -> None:
    """Single streaming pass: iterparse REQUESTDESC + TALLYMESSAGE, filter, write.

    Side effects: populates ``summary.kept_*`` and ``summary.dropped_by_type``.
    Raises ``InputError`` on XML parse failure.
    """
    # Emit tags: REQUESTDESC (envelope metadata) + TALLYMESSAGE (entity wrappers).
    # Both tags fire end events; REQUESTDESC appears before any TALLYMESSAGE
    # in a well-formed Tally export.  If not, we emit a minimal REQUESTDESC stub
    # on first TALLYMESSAGE to keep the output structurally valid.
    prologue_written = False
    request_desc_xml: str | None = None

    with output_path.open("w", encoding="utf-8", newline="\n") as out:
        try:
            ctx = etree.iterparse(
                str(input_path),
                events=("end",),
                tag=("REQUESTDESC", "TALLYMESSAGE"),
                recover=True,
                huge_tree=True,
            )
            for _, elem in ctx:
                tag = elem.tag
                if tag == "REQUESTDESC":
                    if not prologue_written:
                        # Serialize the full REQUESTDESC subtree (includes
                        # REPORTNAME + STATICVARIABLES/SVCURRENTCOMPANY).
                        request_desc_xml = etree.tostring(elem, encoding="unicode")
                        _write_prologue(out, request_desc_xml)
                        prologue_written = True
                    # else: ignore duplicate REQUESTDESC (shouldn't happen; warn)
                    else:
                        summary.warnings.append(
                            "Duplicate REQUESTDESC element — ignoring subsequent occurrences"
                        )
                elif tag == "TALLYMESSAGE":
                    if not prologue_written:
                        summary.warnings.append(
                            "No REQUESTDESC found before first TALLYMESSAGE — "
                            "emitting minimal stub; company name will fall back to filename"
                        )
                        _write_prologue(out, None)
                        prologue_written = True
                    _process_tallymessage(elem, out, summary)

                # Aggressive clear — bound memory to one TALLYMESSAGE + ancestors.
                elem.clear()
                parent = elem.getparent()
                if parent is not None:
                    while elem.getprevious() is not None:
                        del parent[0]
        except etree.XMLSyntaxError as exc:
            # lxml's recover=True should eat most malformations, but truly
            # broken files still raise here.  Convert to InputError so the
            # main() handler writes a sidecar log before exiting.
            raise InputError(f"XML parse failed on {input_path}: {exc}") from exc

        # If we never wrote a prologue, the input had zero LEDGER/GROUP content.
        if not prologue_written:
            # Write a stub file so the sidecar log still makes sense, but flag.
            _write_prologue(out, None)
            summary.warnings.append(
                "Input contained no TALLYMESSAGE entities — slim output is empty"
            )
        _write_epilogue(out)

    # Post-flight: if zero entities kept, treat as input error.
    if summary.kept_ledger == 0 and summary.kept_group == 0:
        raise InputError(
            f"Input produced zero LEDGER or GROUP entities after filtering. "
            f"Verify the source is a Tally All Masters export with populated data."
        )


def _process_tallymessage(
    msg: etree._Element,
    out,                                   # noqa: ANN001
    summary: RunSummary,
) -> None:
    """Iterate TALLYMESSAGE children, filter + write keepers, count dropped."""
    for child in msg:
        ctag = child.tag
        if ctag not in TALLYMESSAGE_CHILDREN_KEEP:
            summary.dropped_by_type[ctag] = summary.dropped_by_type.get(ctag, 0) + 1
            continue
        if ctag == "LEDGER":
            _filter_ledger(child)
            _write_tallymessage(out, etree.tostring(child, encoding="unicode"))
            summary.kept_ledger += 1
        elif ctag == "GROUP":
            _filter_group(child)
            _write_tallymessage(out, etree.tostring(child, encoding="unicode"))
            summary.kept_group += 1
        else:
            # TALLYMESSAGE_CHILDREN_KEEP contains a tag we don't have handling
            # for — shouldn't happen given whitelist is just {LEDGER, GROUP}.
            summary.warnings.append(
                f"Whitelist includes {ctag!r} but preprocessor has no handler; dropped"
            )
            summary.dropped_by_type[ctag] = summary.dropped_by_type.get(ctag, 0) + 1


# ---------------------------------------------------------------------------
# Strict mode — re-parse via rgi_migration parser
# ---------------------------------------------------------------------------

def _run_strict_validation(output_path: Path, summary: RunSummary) -> None:
    """Re-parse the slim output via rgi_migration's parser.

    Lazy-imports the parser so ``--no-strict`` runs don't require the
    ``rgi_migration`` package to be importable (relevant for the
    PyInstaller .exe, which bundles only the whitelist module).

    Raises ``StrictValidationError`` on failure.
    """
    try:
        # Lazy import — see docstring
        from rgi_migration.parsers.tally_xml_parser import parse_xml
    except ImportError as exc:
        raise StrictValidationError(
            f"--strict mode requires rgi_migration package on sys.path "
            f"(import failed: {exc}). Pass --no-strict to skip validation."
        ) from exc

    try:
        tb = parse_xml(str(output_path))
    except Exception as exc:
        summary.strict_result = f"failed: parser raised {type(exc).__name__}: {exc}"
        raise StrictValidationError(
            f"Parser rejected slim output: {type(exc).__name__}: {exc}"
        ) from exc

    summary.strict_result = "passed"
    summary.strict_summary = (
        f"{len(tb.ledgers)} main ledgers, {len(tb.groups)} groups, "
        f"{len(tb.student_ledgers)} student ledgers, "
        f"{len(tb.system_ledgers)} system ledgers; "
        f"total_dr=Rs.{tb.total_dr:,.2f} total_cr=Rs.{tb.total_cr:,.2f} "
        f"is_balanced={tb.is_balanced}"
    )


# ---------------------------------------------------------------------------
# Sidecar log
# ---------------------------------------------------------------------------

def _format_size(n: int) -> str:
    """Human-readable byte count: 231_380_460 -> '220.7 MB'."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    return f"{n / 1024 / 1024 / 1024:.1f} GB"


def _write_log(
    log_path: Path,
    summary: RunSummary,
    error_text: str | None = None,
) -> None:
    """Write the sidecar log (overwrites any previous log at this path)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines: list[str] = []
    lines.append(
        f"Log generated at {now}, overwrote previous log if any"
    )
    lines.append("")
    lines.append(f"Tool: RGI Tally Preprocessor v{TOOL_VERSION}")
    lines.append(f"Whitelist version: {WHITELIST_VERSION}")
    lines.append("")
    lines.append("Input:")
    lines.append(f"  Path: {summary.input_path}")
    lines.append(f"  Size: {summary.input_size:,} bytes ({_format_size(summary.input_size)})")
    lines.append(f"  Encoding: {summary.input_encoding}")
    lines.append("")
    lines.append("Output:")
    lines.append(f"  Path: {summary.output_path}")
    if summary.output_size:
        lines.append(
            f"  Size: {summary.output_size:,} bytes ({_format_size(summary.output_size)})"
        )
        if summary.input_size > 0:
            pct = 100.0 * (1.0 - summary.output_size / summary.input_size)
            lines.append(f"  Size reduction: {pct:.1f}%")
    else:
        lines.append("  Size: (not written)")
    lines.append("  Encoding: UTF-8")
    lines.append("")
    lines.append("Entity counts:")
    lines.append("  Kept:")
    lines.append(f"    LEDGER: {summary.kept_ledger:,}")
    lines.append(f"    GROUP:  {summary.kept_group:,}")
    if summary.dropped_by_type:
        lines.append("  Dropped (by type):")
        for tag, n in sorted(summary.dropped_by_type.items(), key=lambda x: -x[1]):
            lines.append(f"    {tag}: {n:,}")
    else:
        lines.append("  Dropped: (none)")
    lines.append("")
    lines.append(f"Strict mode: {'enabled' if summary.strict_mode else 'disabled (--no-strict)'}")
    lines.append(f"Re-parse validation: {summary.strict_result}")
    if summary.strict_summary:
        lines.append(f"  {summary.strict_summary}")
    lines.append("")
    lines.append(f"Elapsed: {summary.elapsed_seconds:.1f} seconds")
    lines.append("")
    if summary.warnings:
        lines.append(f"Warnings ({len(summary.warnings)}):")
        for w in summary.warnings:
            lines.append(f"  - {w}")
    else:
        lines.append("Warnings: (none)")
    if error_text:
        lines.append("")
        lines.append("ERROR:")
        lines.append(error_text)
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API — callable from Python code (tests, GUI) as well as the CLI
# ---------------------------------------------------------------------------

def _resolve_log_path(output_path: Path, log_path: Path | str | None) -> Path:
    """Default log path is ``<output>.log`` next to the output file."""
    if log_path is None:
        return Path(str(output_path) + ".log").resolve()
    return Path(log_path).resolve()


def _write_log_safe(log_path: Path, summary: RunSummary, error_text: str | None) -> None:
    """Best-effort log write — swallow OSError so a missing log directory
    can't mask the underlying preprocessing error."""
    try:
        _write_log(log_path, summary, error_text)
    except OSError as exc:
        _log.warning("could not write sidecar log at %s: %s", log_path, exc)


def preprocess(
    input_path: str | Path,
    output_path: str | Path,
    *,
    strict: bool = True,
    log_path: str | Path | None = None,
) -> RunSummary:
    """Run one preprocessing pass; return the summary or raise on failure.

    Always writes the sidecar log before returning or re-raising — the log
    is the canonical run record whether success or failure.

    Args:
        input_path:  Full Tally All Masters XML to read.
        output_path: Slim UTF-8 XML to write.
        strict:      If True, re-parse the output via rgi_migration's parser
                     and raise ``StrictValidationError`` if parsing fails.
        log_path:    Sidecar log path (defaults to ``<output>.log``; always
                     overwrite mode).

    Returns:
        ``RunSummary`` populated with input/output sizes, kept/dropped
        entity counts, elapsed seconds, and strict-mode result.

    Raises:
        InputError:             Input file missing/unreadable, not a Tally
                                All Masters XML, or produced zero entities.
        StrictValidationError:  ``strict=True`` and the parser rejected the
                                slim output.
        Exception:              Unexpected failure — the sidecar log captures
                                the traceback before the exception propagates.
    """
    input_p = Path(input_path).resolve()
    output_p = Path(output_path).resolve()
    log_p = _resolve_log_path(output_p, log_path)

    summary = RunSummary(
        input_path=input_p,
        output_path=output_p,
        strict_mode=strict,
    )
    started = time.monotonic()

    try:
        enc = _validate_input(input_p)
        summary.input_encoding = enc
        summary.input_size = input_p.stat().st_size

        _stream_and_filter(input_p, output_p, summary)
        summary.output_size = output_p.stat().st_size if output_p.exists() else 0

        if strict:
            _run_strict_validation(output_p, summary)
        else:
            summary.strict_result = "skipped (--no-strict)"
    except (InputError, StrictValidationError) as exc:
        summary.elapsed_seconds = time.monotonic() - started
        _write_log_safe(log_p, summary, str(exc))
        raise
    except Exception:
        summary.elapsed_seconds = time.monotonic() - started
        _write_log_safe(log_p, summary, traceback.format_exc())
        raise

    summary.elapsed_seconds = time.monotonic() - started
    _write_log_safe(log_p, summary, None)
    return summary


def _print_ok_summary(summary: RunSummary) -> None:
    """Short stdout summary on successful runs."""
    pct = (
        100.0 * (1.0 - summary.output_size / summary.input_size)
        if summary.input_size > 0 else 0.0
    )
    print(
        f"OK: {_format_size(summary.input_size)} -> "
        f"{_format_size(summary.output_size)} ({pct:.1f}% reduction) in "
        f"{summary.elapsed_seconds:.1f}s  "
        f"[{summary.kept_ledger:,} ledgers, {summary.kept_group:,} groups]"
    )
    if summary.strict_mode:
        print(f"Strict re-parse: {summary.strict_result}")


def main(argv: list[str] | None = None) -> int:
    """Thin argparse wrapper around ``preprocess()``.

    ``preprocess()`` does all the work and writes the sidecar log; this
    function just translates argparse kwargs to the callable API, catches
    the three documented exception types, and maps each to the
    appropriate exit code.
    """
    ap = argparse.ArgumentParser(
        prog="python -m tools.tally_slim.preprocessor",
        description=(
            "Convert a full Tally All Masters XML export into a slim XML "
            "containing only the fields rgi_migration's parser reads."
        ),
    )
    ap.add_argument("--input", required=True, help="Path to the full Tally XML")
    ap.add_argument("--output", required=True, help="Path for the slim output XML")
    ap.add_argument(
        "--log",
        default=None,
        help="Path for the sidecar log (default: <output>.log, overwrite mode)",
    )
    # --strict / --no-strict pair — default True.  argparse BooleanOptionalAction
    # gives us both flags from one declaration.
    ap.add_argument(
        "--strict",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Re-parse the slim output via rgi_migration's parser to verify "
            "structural correctness (default: enabled). Use --no-strict to skip."
        ),
    )
    ap.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the stdout summary (log is still written)",
    )
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        summary = preprocess(
            args.input,
            args.output,
            strict=args.strict,
            log_path=args.log,
        )
    except InputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    except StrictValidationError as exc:
        print(f"ERROR: strict re-parse failed: {exc}", file=sys.stderr)
        return EXIT_STRICT_FAILED
    except Exception as exc:
        print(f"UNEXPECTED: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("(see sidecar log for traceback)", file=sys.stderr)
        return EXIT_UNEXPECTED

    if not args.quiet:
        _print_ok_summary(summary)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
