"""Generator #4 — Students CSV for dux_voucher handoff.

Per ``docs/dux_voucher_integration.md``, reads ``ParsedTallyTB.student_ledgers``
(populated by the parser's two student-routing mechanisms — per-student leaves
and aggregate control accounts), aggregates per unique student name, skips
zero-net aggregations, and writes a 4-column CSV consumed by
``dux_voucher.dux_voucher.api.ex_student_api.import_from_csv``.

Student ledgers bypass the mapper entirely — see integration doc §3 + §4.
Decision 1's zero-balance exclusion does NOT apply to this flow; generator
#4 enforces its own equivalent at the aggregation layer.

Public API:

* :func:`generate_students_csv` — Frappe entry. Returns the File doc's name.
* :func:`build_student_rows` — pure core, importable without Frappe.
* :func:`refuse_if_empty` — pure core, Q1 refusal check.
* :func:`format_csv` — pure core, RFC 4180 CSV render.

Refusal contract (raises :class:`StudentsCSVGenerationError`):

* Session pre-condition failures (missing company_abbr / ERPNext Company link /
  fiscal_year / tb_date / source path)
* Shared account existence (``Temporary Opening - {ABBR}`` or
  ``Ex-Students Receivable - {ABBR}`` missing)
* Parser contract violation (a ledger in ``tb.student_ledgers`` with
  ``is_student_ledger=False``)
* Zero rows after aggregation + zero-net-skip (Q1: GHRILS-shaped entities
  with no student receivables — reviewer consciously decides "no Phase 2
  needed" per RGI §5.4)
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from rgi_migration.generators._frappe_compat import whitelist
from rgi_migration.parsers.normalized_schema import Ledger, ParsedTallyTB

LOG = logging.getLogger(__name__)

# Floating-point-noise tolerance for per-student net comparison. NOT an
# accounting tolerance. Python float accumulation can produce tiny residuals
# (~1e-10) on sums that mathematically equal zero; treating < half-paisa as
# zero prevents these from leaking as bogus ₹0.00 rows while still letting a
# legitimate ₹0.01 student balance flow through to dux_voucher for the
# accounting team to decide on. Anything ≥ half-paisa is a real balance.
#
# Explicitly a *deliberate FP tolerance*, not an accounting tolerance —
# different semantic from generator #1's ₹1 after-balancer JE-wide check.
_ZERO_NET_TOLERANCE = 0.005

# 4-column CSV schema per integration doc §4 and dux_voucher's
# ex_student_api.import_from_csv expectations (case-insensitive, order-
# flexible; we pick canonical order for writer determinism).
_CSV_HEADER: list[str] = [
    "student_name",
    "debit_amount",
    "credit_amount",
    "remarks",
]

# Truncate parent_chain in remarks to keep CSV rows visually compact.
_CHAIN_MAX_LEN = 100


class StudentsCSVGenerationError(Exception):
    """Raised when Students CSV generation preconditions fail."""


@dataclass(frozen=True)
class StudentRow:
    """One CSV row — one unique student with a non-zero net balance."""

    student_name: str
    debit_amount: float
    credit_amount: float
    remarks: str


@dataclass
class _StudentAggregate:
    dr: float = 0.0
    cr: float = 0.0
    contributors: list[Ledger] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pure core — no Frappe imports
# ---------------------------------------------------------------------------


def _aggregate_per_student(
    student_ledgers: Iterable[Ledger],
) -> dict[str, _StudentAggregate]:
    """Group by ``Ledger.name`` (already suffix-stripped by the parser), sum
    Dr/Cr per group. Paranoid re-checks ``is_student_ledger`` per integration
    doc §3 — a False flag in this list indicates a parser contract
    violation."""
    agg: dict[str, _StudentAggregate] = {}
    for l in student_ledgers:
        if not l.is_student_ledger:
            raise StudentsCSVGenerationError(
                f"Parser contract violation: ledger {l.name!r} "
                f"(tally_id={l.tally_id}) is in tb.student_ledgers but "
                f"is_student_ledger=False."
            )
        bucket = agg.setdefault(l.name, _StudentAggregate())
        bucket.dr += l.opening_dr
        bucket.cr += l.opening_cr
        bucket.contributors.append(l)
    return agg


def _build_remarks(agg: _StudentAggregate) -> str:
    """Build the ``remarks`` cell for a student aggregate.

    Two formats:

    * **Normal** — ``{N} Tally ledger(s); tally_ids: {ids}; chain: {chain}``
    * **AUDIT_MERGED** — triggered when ≥2 contributors have ≥2 *distinct
      non-null* tally_ids (indicating the cleaned name may represent
      different Tally records, not a data-entry duplicate of the same
      student). Reviewers can grep/filter on ``AUDIT_MERGED:`` to triage.

    ``tally_ids`` slot:

    * 1 contributor w/ id: ``"112856"``
    * 1 contributor w/o id: ``"(none)"``
    * N contributors, all None: ``"(all none, N contributors)"`` — preserves
      count information even when no IDs are available.
    * N contributors, mixed: non-null IDs comma-joined (insertion order)
    """
    contributors = agg.contributors
    n = len(contributors)
    non_null_ids = [str(c.tally_id) for c in contributors if c.tally_id]
    distinct_non_null = sorted(set(non_null_ids))

    first_chain = (
        contributors[0].parent_chain
        if contributors and contributors[0].parent_chain
        else []
    )
    chain_str = " > ".join(first_chain) if first_chain else "<none>"
    if len(chain_str) > _CHAIN_MAX_LEN:
        chain_str = chain_str[: _CHAIN_MAX_LEN - 3] + "..."

    # AUDIT_MERGED: multi-contributor with ≥2 distinct non-null tally_ids
    if n > 1 and len(distinct_non_null) >= 2:
        ids_str = ", ".join(distinct_non_null)
        return (
            f"AUDIT_MERGED: {n} ledgers merged with distinct tally_ids "
            f"({ids_str}); chain: {chain_str}"
        )

    # Normal remarks
    if non_null_ids:
        ids_str = ", ".join(non_null_ids)
    elif n > 1:
        ids_str = f"(all none, {n} contributors)"
    else:
        ids_str = "(none)"
    label = "Tally ledger" if n == 1 else "Tally ledgers"
    return f"{n} {label}; tally_ids: {ids_str}; chain: {chain_str}"


def build_student_rows(
    student_ledgers: Iterable[Ledger],
) -> list[StudentRow]:
    """Pure core — aggregate student ledgers into CSV rows.

    Returns rows in alphabetical order by ``student_name`` (reviewer
    diff-stability). Empty input returns empty list; zero-row refusal
    (Q1) lives in :func:`refuse_if_empty`, called separately from the
    Frappe entry so pure-core testing doesn't require session context.
    """
    agg = _aggregate_per_student(student_ledgers)
    rows: list[StudentRow] = []
    for name in sorted(agg.keys()):
        bucket = agg[name]
        net = bucket.dr - bucket.cr
        # Zero-net skip — covers both "single zero-balance ledger"
        # (definitional noise) AND "multi-ledger netting to zero"
        # (fully-paid-up student) with one rule.
        if abs(net) < _ZERO_NET_TOLERANCE:
            continue
        remark = _build_remarks(bucket)
        if net > 0:
            rows.append(StudentRow(
                student_name=name,
                debit_amount=round(net, 2),
                credit_amount=0.0,
                remarks=remark,
            ))
        else:
            rows.append(StudentRow(
                student_name=name,
                debit_amount=0.0,
                credit_amount=round(-net, 2),
                remarks=remark,
            ))
    return rows


def refuse_if_empty(rows: list[StudentRow], session_name: str) -> None:
    """Q1 — refuse when no rows emit. GHRILS-shaped entities with no
    student receivables are valid per RGI §5.4; refusing loudly makes the
    reviewer consciously acknowledge "no Phase 2 needed" rather than
    silently missing an empty CSV."""
    if rows:
        return
    raise StudentsCSVGenerationError(
        f"No non-zero student balances in session {session_name}. "
        f"Generator #4 has nothing to emit for dux_voucher. "
        f"This is valid for entities without student receivables "
        f"(e.g., GHRILS per RGI §5.4). Update session.notes to record "
        f"'No Phase 2 needed' if this is the intended state."
    )


def format_csv(rows: list[StudentRow]) -> str:
    """Render rows as RFC 4180 CSV with the 4-column header.

    Python's ``csv.writer`` default is ``QUOTE_MINIMAL`` which matches RFC
    4180 — cells containing ``,``, ``"``, or a newline get quoted, and
    embedded ``"`` doubled. All other cells pass through unquoted.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(_CSV_HEADER)
    for r in rows:
        writer.writerow([
            r.student_name,
            f"{r.debit_amount:.2f}",
            f"{r.credit_amount:.2f}",
            r.remarks,
        ])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Frappe-aware entry point
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _timestamp_suffix() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _append_error_log(session: Any, block: str) -> None:
    existing = session.error_log or ""
    separator = "\n\n" if existing else ""
    session.error_log = f"{existing}{separator}{block}"


@whitelist()
def generate_students_csv(session_name: str) -> str:
    """Generate Students CSV for the given session. Returns File doc name.

    Order of operations (fail-fast; no DB writes before all pre-conditions
    pass):

      1. Session + Company Abbreviation + ERPNext Company resolution.
      2. Fiscal year + tb_date + source path presence.
      3. Shared-account existence check (Q2 Option A) — Temporary Opening
         and Ex-Students Receivable on target Company.
      4. Parse Tally source → ParsedTallyTB.
      5. build_student_rows (pure; raises on parser contract violation).
      6. refuse_if_empty (pure; Q1 refusal).
      7. Idempotency — delete existing File attachment if any.
      8. Format CSV + attach via File doc + link session.student_ledger_file.
      9. Log deletion + generation events to session.error_log.
    """
    import frappe  # type: ignore[import]

    session = frappe.get_doc("Tally Migration Session", session_name)

    # --- 1. Company / abbr / ERPNext Company ---
    if not session.company_abbr:
        raise StudentsCSVGenerationError(
            f"Session {session_name} has no company_abbr set."
        )
    abbr_doc = frappe.get_doc("Company Abbreviation", session.company_abbr)
    if not abbr_doc.is_active:
        raise StudentsCSVGenerationError(
            f"Company Abbreviation {session.company_abbr!r} is not active."
        )
    if not abbr_doc.erpnext_company:
        raise StudentsCSVGenerationError(
            f"Company Abbreviation {session.company_abbr!r} has no linked "
            f"ERPNext Company."
        )
    erpnext_company = abbr_doc.erpnext_company
    if not frappe.db.exists("Company", erpnext_company):
        raise StudentsCSVGenerationError(
            f"ERPNext Company {erpnext_company!r} does not exist."
        )

    # --- 2. Fiscal year / tb_date / source path ---
    if not session.fiscal_year:
        raise StudentsCSVGenerationError(
            f"Session {session_name} has no fiscal_year set."
        )
    if not session.tb_date:
        raise StudentsCSVGenerationError(
            f"Session {session_name} has no tb_date set."
        )
    source_path = session.source_file_server_path or session.source_file
    if not source_path:
        raise StudentsCSVGenerationError(
            f"Session {session_name} has no source file path "
            f"(source_file_server_path and source_file are both empty)."
        )

    # --- 3. Shared-account existence (Q2) ---
    temp_opening_account = f"Temporary Opening - {session.company_abbr}"
    ex_receivable_account = f"Ex-Students Receivable - {session.company_abbr}"
    missing = [
        a for a in (temp_opening_account, ex_receivable_account)
        if not frappe.db.exists("Account", a)
    ]
    if missing:
        raise StudentsCSVGenerationError(
            f"Missing account(s) on Company {erpnext_company!r} required "
            f"by dux_voucher's Ex Student Opening Batch: "
            f"{', '.join(missing)}. Create these accounts before "
            f"generating the Students CSV."
        )

    # --- 4. Parse ---
    tb = _parse_source(str(source_path), session.source_format or "xml")

    # --- 5. Build rows ---
    rows = build_student_rows(tb.student_ledgers)

    # --- 6. Q1 refusal ---
    refuse_if_empty(rows, session_name)

    # --- 7. Idempotency ---
    deletion_log_line: str | None = None
    if session.student_ledger_file:
        prior_url = session.student_ledger_file
        prior_file_name = frappe.db.get_value(
            "File", {"file_url": prior_url}, "name"
        )
        if prior_file_name:
            ts = _iso_now()
            LOG.info(
                "Replacing existing Students CSV File %s for session %s",
                prior_file_name, session_name,
            )
            deletion_log_line = (
                f"[{ts}] students-csv regeneration: deleted previous File "
                f"{prior_file_name} ({prior_url}) before creating new CSV."
            )
            frappe.delete_doc("File", prior_file_name, force=1)
        session.student_ledger_file = None

    # --- 8. Format + attach ---
    csv_content = format_csv(rows)
    filename = (
        f"students-{session.company_abbr}-{session.fiscal_year}-"
        f"{_timestamp_suffix()}.csv"
    )
    file_doc = frappe.get_doc({
        "doctype": "File",
        "file_name": filename,
        "attached_to_doctype": "Tally Migration Session",
        "attached_to_name": session_name,
        "attached_to_field": "student_ledger_file",
        "content": csv_content.encode("utf-8"),
        "is_private": 1,
    }).insert(ignore_permissions=True)
    session.student_ledger_file = file_doc.file_url

    # --- 9. Persist event log ---
    log_blocks: list[str] = []
    if deletion_log_line:
        log_blocks.append(deletion_log_line)
    log_blocks.append(
        f"[{_iso_now()}] students-csv generated: {len(rows)} non-zero "
        f"student row(s) in {filename} "
        f"(from {len(tb.student_ledgers)} raw student ledgers "
        f"— {len(tb.student_ledgers) - len(rows)} zero-net skipped)."
    )
    for block in log_blocks:
        _append_error_log(session, block)

    session.save(ignore_permissions=True)
    frappe.db.commit()
    return file_doc.name


def _parse_source(source_path: str, source_format: str) -> ParsedTallyTB:
    """Parse the Tally source file. No mapper invocation — student ledgers
    are produced by the parser directly via ``is_student_ledger=True``
    flagging (see integration doc §3)."""
    if source_format == "excel":
        from rgi_migration.parsers.tally_excel_parser import parse_excel  # type: ignore[import]
        return parse_excel(source_path)
    from rgi_migration.parsers.tally_xml_parser import parse_xml  # type: ignore[import]
    return parse_xml(source_path)
