"""Generator #2 — OIT CSV for net-Cr vendors.

Produces the 12-column CSV specified in ``RGI_Migration_Rules.md §5.2``,
attached to a ``Tally Migration Session``. Each net-Cr vendor becomes one
row; the reviewer uploads the CSV via ERPNext's Opening Invoice Creation
Tool (OICT) → Make Invoices, producing Draft ``Purchase Invoice`` docs
with ``is_opening=Yes``.

Net-Dr vendors are deferred to generator #3 (party-advance JE,
``is_advance=Yes``). Net-zero vendors are skipped (Decision 1's
zero-balance exclusion already catches most, but per-supplier
aggregation here catches the residual Cr+Dr-netting-to-zero case).

Public API:

* :func:`generate_oit_csv` — Frappe entry. Loads a session, parses + maps
  on demand (reparse-and-remap per ``docs/mapper_design_notes.md §8.2``),
  builds and attaches the CSV. Returns the ``File`` doc's name.
* :func:`build_oit_rows` — pure core. Importable without Frappe.
* :func:`format_csv` — pure core. RFC 4180 quoting via stdlib ``csv``.

Refusal contract (``OITGenerationError`` before any file write):

* ``pending_supplier_creation > 0`` — resolve Supplier Creation Requests first.
* Resolved supplier deleted from master — refuse with supplier ID.
* Resolved supplier disabled — refuse with supplier ID.

Order A (``mapper_design_notes.md §8.3``): all supplier issues are
collected into one pass and surfaced together in a single refusal
message; no first-encountered-fail short-circuit.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from rgi_migration.generators._frappe_compat import whitelist
from rgi_migration.mapper.mapper import MappedDecision

LOG = logging.getLogger(__name__)

# Mapper tiers where proposed_supplier is set to a resolved ERPNext
# Supplier ID. Decisions in other tiers are ignored by this generator.
_SUPPLIER_RESOLVED_TIERS = frozenset({
    "tier1_supplier_exact",
    "tier1_supplier_alias",
    "tier1_supplier_fuzzy",
})

# RGI §5.2 literal constants
_OIT_ITEM_NAME = "Opening Invoice Item"
_OIT_QTY = "1"  # DocType field is Data, not Int; string literal per §8.1
_PARTY_TYPE = "Supplier"

# 12-column schema, order exactly per RGI §5.2
_CSV_HEADER: list[str] = [
    "Invoice Number",
    "Party Type",
    "Party ID",
    "Party Name",
    "Temporary Opening Account",
    "Posting Date",
    "Due Date",
    "Supplier Invoice Date",
    "Item Name",
    "Outstanding Amount",
    "Quantity",
    "Cost Center",
]


class OITGenerationError(Exception):
    """Raised when OIT CSV generation preconditions fail."""


@dataclass(frozen=True)
class SupplierInfo:
    """Snapshot of an ERPNext Supplier at generation time.

    The re-check pass (Order A) compares every ``proposed_supplier`` from
    the mapper against this index; missing keys → deleted from master,
    ``disabled=True`` → disabled.
    """

    name: str            # Supplier doc ID
    supplier_name: str   # Display name
    disabled: bool = False


@dataclass(frozen=True)
class OITRow:
    """One CSV row — one net-Cr vendor."""

    invoice_number: str
    party_type: str
    party_id: str
    party_name: str
    temporary_opening_account: str
    posting_date: str
    due_date: str
    supplier_invoice_date: str
    item_name: str
    outstanding_amount: float
    quantity: str
    cost_center: str


@dataclass(frozen=True)
class SupplierIssue:
    """A single supplier that failed the re-check pass (Order A)."""

    supplier_id: str
    reason: str  # "deleted from Supplier master" | "disabled"


@dataclass
class _SupplierAggregate:
    cr: float = 0.0
    dr: float = 0.0
    contributors: list[tuple[str, str | None]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pure core — no Frappe imports
# ---------------------------------------------------------------------------


def _aggregate_per_supplier(
    decisions: Iterable[MappedDecision],
) -> dict[str, _SupplierAggregate]:
    """Sum opening Dr/Cr per resolved ``proposed_supplier``.

    Multiple Tally ledgers resolving to the same ERPNext supplier collapse
    into one aggregate. Decisions outside the supplier-resolved tiers (and
    supplier-less party tiers like ``pending_supplier_creation``) are
    filtered out — they're handled by separate preflight checks in
    :func:`_enforce_preflight`.
    """
    agg: dict[str, _SupplierAggregate] = {}
    for d in decisions:
        # Production hotfix 2026-04-25 — supplier-side reviewer rescue.
        # Rescued tier=pending_supplier_creation MDs (reviewer picked
        # an existing Supplier instead of creating new) get
        # final_supplier set, which decision_from_doc_row promotes onto
        # MappedDecision.proposed_supplier. Treat these as resolved-
        # supplier contributions even though the persisted tier is
        # pending_supplier_creation. Mirror of the account-side rescue
        # in opening_je._select_contributions.
        is_rescued = (
            d.tier == "pending_supplier_creation"
            and getattr(d, "review_action", None) in ("Approved", "Manual Override")
            and d.proposed_supplier
        )
        if d.tier not in _SUPPLIER_RESOLVED_TIERS and not is_rescued:
            continue
        if not d.proposed_supplier:
            continue
        # Item 8.5 Stage 2: reviewer-Rejected and reviewer-Deferred
        # decisions drop out of the aggregation the same way they
        # drop out of the refusal gate. Prior to Stage 2, the
        # refusal gate filtered review_action but the aggregator did
        # not — a latent leak where Rejected resolved-supplier rows
        # would still contribute an OIT row. Fixed symmetrically for
        # Rejected and Deferred here.
        if getattr(d, "review_action", None) in ("Rejected", "Deferred"):
            continue
        bucket = agg.setdefault(d.proposed_supplier, _SupplierAggregate())
        bucket.cr += d.opening_cr
        bucket.dr += d.opening_dr
        bucket.contributors.append((d.tally_name, d.tally_id))
    return agg


def _collect_supplier_issues(
    aggregated: dict[str, _SupplierAggregate],
    supplier_index: dict[str, SupplierInfo],
) -> list[SupplierIssue]:
    """Order A — iterate once, collect every missing/disabled supplier."""
    issues: list[SupplierIssue] = []
    # Deterministic order so error messages are reproducible
    for sid in sorted(aggregated.keys()):
        info = supplier_index.get(sid)
        if info is None:
            issues.append(SupplierIssue(sid, "deleted from Supplier master"))
        elif info.disabled:
            issues.append(SupplierIssue(sid, "disabled"))
    return issues


def _enforce_preflight(
    decisions: list[MappedDecision],
    aggregated: dict[str, _SupplierAggregate],
    supplier_index: dict[str, SupplierInfo],
    session_name: str,
) -> None:
    """Refuse if any supplier-side preconditions fail.

    Both pending-creation and supplier-master issues are collected first
    (Order A) — the exception message surfaces every problem at once so
    the reviewer fixes the full set in one trip.
    """
    # Item 3 Commit 3: reviewer-Rejected decisions silent-skip the
    # refusal gate. The mapper tier stays pending_supplier_creation
    # (mapper-authoritative) but the reviewer's intent (review_action
    # == "Rejected") means no Supplier should be created + no OIT row
    # contributed. Such rows are effectively dropped from the OIT
    # pipeline.
    #
    # Item 8.5 Stage 2: Deferred decisions behave identically to
    # Rejected at this gate — the row stays on record for a future
    # pass but doesn't block Pass 1. See creation_request_sync.py for
    # how the SCR child row follows the MD's Deferred transition.
    # Production hotfix 2026-04-25: also exclude reviewer-rescued MDs
    # (Approved/Manual Override with final_supplier set). Those are
    # contributed via the rescue path in _aggregate_per_supplier; they
    # are NOT pending creation operationally despite the tier label.
    pending_count = sum(
        1 for d in decisions
        if d.tier == "pending_supplier_creation"
        and getattr(d, "review_action", None) not in ("Rejected", "Deferred")
        and not (
            getattr(d, "review_action", None) in ("Approved", "Manual Override")
            and d.proposed_supplier
        )
    )
    issues = _collect_supplier_issues(aggregated, supplier_index)

    if not pending_count and not issues:
        return

    parts: list[str] = [
        f"Cannot generate OIT CSV for session {session_name}:"
    ]
    if pending_count:
        parts.append(
            f"  - {pending_count} suppliers pending creation "
            f"(resolve Supplier Creation Requests in review UI before regeneration)"
        )
    if issues:
        parts.append(f"  - {len(issues)} supplier resolution issues:")
        for issue in issues:
            parts.append(f"      - {issue.supplier_id}: {issue.reason}")
    parts.append("Re-run mapping or restore/enable suppliers, then regenerate.")
    raise OITGenerationError("\n".join(parts))


def build_oit_rows(
    *,
    decisions: list[MappedDecision],
    supplier_index: dict[str, SupplierInfo],
    abbr: str,
    posting_date: str,
    session_name: str,
    reference_prefix: str | None = None,
) -> list[OITRow]:
    """Build OIT rows from mapper decisions + live supplier master snapshot.

    Raises :class:`OITGenerationError` if preflight fails (pending
    creations, missing suppliers, disabled suppliers).

    Rows are ordered alphabetically by ERPNext supplier ID for
    deterministic output; ``Invoice Number`` sequences 0001, 0002…
    follow that order.
    """
    aggregated = _aggregate_per_supplier(decisions)
    _enforce_preflight(decisions, aggregated, supplier_index, session_name)

    temp_account = f"Temporary Opening - {abbr}"
    prefix = reference_prefix if reference_prefix is not None else f"OB-{abbr}-OIT-"

    rows: list[OITRow] = []
    for sid in sorted(aggregated.keys()):
        bucket = aggregated[sid]
        net = bucket.cr - bucket.dr
        if net <= 0:
            # net == 0 (both sides balance out) or net < 0 (advance → gen #3)
            continue
        info = supplier_index[sid]  # preflight guarantees presence
        rows.append(OITRow(
            invoice_number=f"{prefix}{len(rows) + 1:04d}",
            party_type=_PARTY_TYPE,
            party_id=info.name,
            party_name=info.supplier_name,
            temporary_opening_account=temp_account,
            posting_date=posting_date,
            due_date=posting_date,
            supplier_invoice_date=posting_date,
            item_name=_OIT_ITEM_NAME,
            outstanding_amount=round(net, 2),
            quantity=_OIT_QTY,
            cost_center="",
        ))
    return rows


def format_csv(rows: list[OITRow]) -> str:
    """Render rows as RFC 4180 CSV with the 12-column header.

    Python's ``csv.writer`` default is ``QUOTE_MINIMAL`` which matches
    RFC 4180 — only values containing ``,``, ``"``, or a newline are
    quoted, and embedded ``"`` characters are doubled.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(_CSV_HEADER)
    for r in rows:
        writer.writerow([
            r.invoice_number,
            r.party_type,
            r.party_id,
            r.party_name,
            r.temporary_opening_account,
            r.posting_date,
            r.due_date,
            r.supplier_invoice_date,
            r.item_name,
            f"{r.outstanding_amount:.2f}",
            r.quantity,
            r.cost_center,
        ])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Frappe-aware entry point
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _timestamp_suffix() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def _append_error_log(session: Any, block: str) -> None:
    existing = session.error_log or ""
    separator = "\n\n" if existing else ""
    session.error_log = f"{existing}{separator}{block}"


@whitelist()
def generate_oit_csv(session_name: str) -> str:
    """Generate OIT CSV for the given session. Returns the File doc's name.

    Order of operations:
      1. Pre-condition: resolve company_abbr → Company Abbreviation → Company.
      2. Idempotency: delete any existing OIT File attachment on this session.
      3. Parse + map (ad-hoc reparse-and-remap; §8.2).
      4. Load live Supplier master snapshot from Frappe.
      5. Build rows via pure :func:`build_oit_rows` — raises on refusal.
      6. Format CSV, attach as File, link via ``session.generated_oit_file``.
      7. Persist deletion + generation event lines to ``session.error_log``.
    """
    import frappe  # type: ignore[import]

    session = frappe.get_doc("Tally Migration Session", session_name)

    # --- 1. Pre-condition ---
    if not session.company_abbr:
        raise OITGenerationError(
            f"Session {session_name} has no company_abbr set."
        )
    abbr_doc = frappe.get_doc("Company Abbreviation", session.company_abbr)
    if not abbr_doc.is_active:
        raise OITGenerationError(
            f"Company Abbreviation {session.company_abbr!r} is not active."
        )
    if not abbr_doc.erpnext_company:
        raise OITGenerationError(
            f"Company Abbreviation {session.company_abbr!r} has no linked "
            f"ERPNext Company."
        )
    erpnext_company = abbr_doc.erpnext_company
    if not frappe.db.exists("Company", erpnext_company):
        raise OITGenerationError(
            f"ERPNext Company {erpnext_company!r} does not exist."
        )
    if not session.tb_date:
        raise OITGenerationError(
            f"Session {session_name} has no tb_date set."
        )
    if not session.fiscal_year:
        raise OITGenerationError(
            f"Session {session_name} has no fiscal_year set."
        )

    # --- 2. Idempotency ---
    deletion_log_line: str | None = None
    if session.generated_oit_file:
        prior_url = session.generated_oit_file
        prior_file_name = frappe.db.get_value("File", {"file_url": prior_url}, "name")
        if prior_file_name:
            ts = _iso_now()
            LOG.info(
                "Replacing existing OIT CSV File %s for session %s",
                prior_file_name, session_name,
            )
            deletion_log_line = (
                f"[{ts}] oit-csv regeneration: deleted previous File "
                f"{prior_file_name} ({prior_url}) before creating new CSV."
            )
            frappe.delete_doc("File", prior_file_name, force=1)
        session.generated_oit_file = None

    # --- 3. Load persisted Mapping Decisions (Item 2 §9.1 architecture) ---
    # Generators no longer reparse-and-remap — they read the DocType
    # that run_mapper() populated. Reviewer overrides on final_account /
    # final_supplier are picked up transparently by decision_from_doc_row.
    from rgi_migration.session.parse_and_map import (
        load_pass_pending_decisions_from_session,
        require_generator_status,
    )
    from rgi_migration.generators.pass_tracking import (
        build_filename_suffix,
        determine_current_pass_number,
    )

    require_generator_status(session, "generate_oit_csv")
    pass_number = determine_current_pass_number(session)
    decisions, _ = load_pass_pending_decisions_from_session(
        session_name, current_pass_number=pass_number,
    )

    # --- 4. Supplier master snapshot ---
    supplier_index = _load_supplier_index()

    # --- 5. Build rows (pure; may raise OITGenerationError) ---
    posting_date_val = session.tb_date
    posting_date_iso = (
        posting_date_val.isoformat()
        if hasattr(posting_date_val, "isoformat")
        else str(posting_date_val)
    )
    rows = build_oit_rows(
        decisions=decisions,
        supplier_index=supplier_index,
        abbr=session.company_abbr,
        posting_date=posting_date_iso,
        session_name=session_name,
    )

    # --- 6. Format + attach ---
    csv_content = format_csv(rows)
    # Item 8.5 Stage 3: Pass 2+ filenames get a _p{N} suffix before the
    # timestamp so the OIT CSV for Pass 1 and Pass 2 are distinguishable
    # by reviewer visually and by dux_voucher's file-consumer script.
    filename = (
        f"oit-{session.company_abbr}-{session.fiscal_year}"
        f"{build_filename_suffix(pass_number)}-"
        f"{_timestamp_suffix()}.csv"
    )
    file_doc = frappe.get_doc({
        "doctype": "File",
        "file_name": filename,
        "attached_to_doctype": "Tally Migration Session",
        "attached_to_name": session_name,
        "attached_to_field": "generated_oit_file",
        "content": csv_content.encode("utf-8"),
        "is_private": 1,
    }).insert(ignore_permissions=True)
    session.generated_oit_file = file_doc.file_url

    # --- 7. Persist event log ---
    log_blocks: list[str] = []
    if deletion_log_line:
        log_blocks.append(deletion_log_line)
    log_blocks.append(
        f"[{_iso_now()}] oit-csv generated: {len(rows)} net-Cr vendor "
        f"row(s) in {filename}."
    )
    for block in log_blocks:
        _append_error_log(session, block)

    session.save(ignore_permissions=True)
    frappe.db.commit()
    return file_doc.name


def _load_supplier_index() -> dict[str, SupplierInfo]:
    """Fetch the current Supplier master into a SupplierInfo index.

    Does NOT filter disabled — the re-check pass needs to distinguish
    "deleted" from "disabled", so both states must be visible.
    """
    import frappe  # type: ignore[import]

    rows = frappe.get_all(
        "Supplier",
        fields=["name", "supplier_name", "disabled"],
        limit_page_length=0,
    )
    return {
        r["name"]: SupplierInfo(
            name=r["name"],
            supplier_name=r.get("supplier_name") or r["name"],
            disabled=bool(r.get("disabled")),
        )
        for r in rows
    }


