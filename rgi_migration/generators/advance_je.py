"""Generator #3 — Party-wise Dr JE for vendor advances.

Per ``RGI_Migration_Rules.md §5.3``, vendors whose opening balance is net
Dr (advance paid — money out to the vendor before goods/services
received) post as a **separate** Opening Entry Journal Entry with
``is_advance="Yes"`` on every supplier line. Reference number is
``OB-{ABBR}-2026-02`` per §6.2.

Draft only — never auto-submit. Reviewer submits in ERPNext after preview.

Public API mirrors generator #1:

* :func:`generate_advance_je` — Frappe entry. Loads a session, parses +
  maps on demand, creates a Draft ``Journal Entry`` with the advance
  lines + Temp Opening balancer, writes back to the session.
* :func:`build_advance_je_payload` — pure core, importable without
  Frappe. Raises :class:`AdvanceJEGenerationError` on refusal.

Refusal contract — all-or-nothing, Order A:

* ``pending_supplier_creation > 0``
* Any resolved supplier deleted from ERPNext
* Any resolved supplier disabled
* Post-balancer |Dr − Cr| > ₹1.00 (mathematically can't happen; defensive)

All supplier issues are collected into one pass (Order A per design
notes §8.3) and surfaced together in a single refusal message.

Per-supplier aggregation mirrors gen #2 with the opposite sign
convention: a supplier's ``Σ opening_dr − Σ opening_cr > 0`` → one JE
line here. Net-Cr → gen #2 (OIT). Net-zero → skipped silently
(Decision 1 catches most; residual Cr+Dr-netting-to-zero caught here).
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from rgi_migration.mapper.mapper import MappedDecision

LOG = logging.getLogger(__name__)

_TOLERANCE_RUPEES = 1.00  # RGI §6.2

_SUPPLIER_RESOLVED_TIERS = frozenset({
    "tier1_supplier_exact",
    "tier1_supplier_alias",
    "tier1_supplier_fuzzy",
})


class AdvanceJEGenerationError(Exception):
    """Raised when Advance JE generation preconditions fail."""


@dataclass(frozen=True)
class SupplierInfo:
    """Snapshot of an ERPNext Supplier at generation time."""

    name: str
    supplier_name: str
    disabled: bool = False


@dataclass(frozen=True)
class SupplierIssue:
    supplier_id: str
    reason: str  # "deleted from Supplier master" | "disabled"


@dataclass(frozen=True)
class AdvanceLine:
    """One Dr line in the advance JE — one net-Dr supplier."""

    account: str
    party_type: str
    party: str
    is_advance: str
    debit: float
    credit: float
    user_remark: str


@dataclass
class AdvanceJEPayload:
    header: dict[str, Any]
    rows: list[dict[str, Any]]
    # Signed: positive = Temp Opening posted on Cr side (i.e. advance Dr
    # total > 0 → balancer adds Cr).
    temp_opening_amount: float
    supplier_count: int
    row_count: int  # includes balancer


@dataclass
class _SupplierAggregate:
    dr: float = 0.0
    cr: float = 0.0
    tiers: list[str] = field(default_factory=list)
    matched_rules: list[str | None] = field(default_factory=list)
    contributors: list[tuple[str, str | None]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pure core — no Frappe imports
# ---------------------------------------------------------------------------


def _aggregate_per_supplier(
    decisions: Iterable[MappedDecision],
) -> dict[str, _SupplierAggregate]:
    agg: dict[str, _SupplierAggregate] = {}
    for d in decisions:
        if d.tier not in _SUPPLIER_RESOLVED_TIERS:
            continue
        if not d.proposed_supplier:
            continue
        bucket = agg.setdefault(d.proposed_supplier, _SupplierAggregate())
        bucket.dr += d.opening_dr
        bucket.cr += d.opening_cr
        bucket.tiers.append(d.tier)
        bucket.matched_rules.append(d.matched_rule)
        bucket.contributors.append((d.tally_name, d.tally_id))
    return agg


def _collect_supplier_issues(
    aggregated: dict[str, _SupplierAggregate],
    supplier_index: dict[str, SupplierInfo],
) -> list[SupplierIssue]:
    issues: list[SupplierIssue] = []
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
    pending_count = sum(
        1 for d in decisions if d.tier == "pending_supplier_creation"
    )
    issues = _collect_supplier_issues(aggregated, supplier_index)

    if not pending_count and not issues:
        return

    parts: list[str] = [
        f"Cannot generate Advance JE for session {session_name}:"
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
    raise AdvanceJEGenerationError("\n".join(parts))


def _format_contributor_summary(agg: _SupplierAggregate) -> str:
    parts = []
    seen_tiers = []
    for t in agg.tiers:
        if t not in seen_tiers:
            seen_tiers.append(t)
    rule_refs: list[str] = []
    for r in agg.matched_rules:
        ref = r or "exact"
        if ref not in rule_refs:
            rule_refs.append(ref)
    ledger_names = ", ".join(
        f"{n} [tally_id={tid}]" for n, tid in agg.contributors
    )
    return (
        f"Resolved via "
        + "/".join(seen_tiers)
        + ":"
        + "/".join(rule_refs)
        + f". Tally sources: {ledger_names}"
    )


def _header_user_remark(
    *,
    reference_id: str,
    full_name: str,
    session_name: str,
    timestamp_iso: str,
    source_sha256: str,
    old_je_audit: str | None,
) -> str:
    sha_short = source_sha256[:12] if source_sha256 else "<unknown>"
    lines = [
        reference_id,
        "",
        f"Vendor advance JE (net-Dr supplier balances) — {full_name}",
        (
            f"Generated by rgi_migration from Tally Migration Session "
            f"{session_name} on {timestamp_iso}"
        ),
        f"Balances sourced from Tally export: {sha_short}",
    ]
    if old_je_audit:
        lines.append("")
        lines.append(old_je_audit)
    return "\n".join(lines)


def build_advance_je_payload(
    *,
    decisions: list[MappedDecision],
    supplier_index: dict[str, SupplierInfo],
    abbr: str,
    erpnext_company: str,
    full_name: str,
    fiscal_year: str,
    posting_date: str,
    session_name: str,
    source_sha256: str,
    reference_id: str,
    timestamp_iso: str,
    old_je_audit: str | None = None,
) -> AdvanceJEPayload:
    """Build the Draft Advance JE payload without touching Frappe.

    Raises :class:`AdvanceJEGenerationError` on refusal or post-balancer
    imbalance. Caller is responsible for invoking Frappe ORM (see
    :func:`generate_advance_je`).
    """
    aggregated = _aggregate_per_supplier(decisions)
    _enforce_preflight(decisions, aggregated, supplier_index, session_name)

    creditors_account = f"Sundry Creditors - {abbr}"
    temp_opening_account = f"Temporary Opening - {abbr}"

    lines: list[AdvanceLine] = []
    for sid in sorted(aggregated.keys()):
        agg = aggregated[sid]
        net_dr = agg.dr - agg.cr
        if net_dr <= 0:
            # net < 0 → goes to gen #2 (OIT). net == 0 → silently skipped.
            continue
        info = supplier_index[sid]  # preflight guarantees presence
        remark = (
            f"Advance to {info.supplier_name}: {net_dr:.2f}. "
            + _format_contributor_summary(agg)
        )
        lines.append(AdvanceLine(
            account=creditors_account,
            party_type="Supplier",
            party=info.name,
            is_advance="Yes",
            debit=round(net_dr, 2),
            credit=0.0,
            user_remark=remark,
        ))

    # Balancer — single Cr row on Temp Opening
    dr_total = sum(l.debit for l in lines)
    supplier_count = len(lines)
    balancer_remark = (
        f"Balancing leg for opening advances — {supplier_count} suppliers "
        f"totalling {dr_total:.2f}. Temporary Opening - {abbr} is shared "
        f"across main JE, OIT, advance JE, and dux_voucher's Ex Student JE; "
        f"nets to zero after all four submit."
    )
    balancer = AdvanceLine(
        account=temp_opening_account,
        party_type="",
        party="",
        is_advance="",
        debit=0.0,
        credit=round(dr_total, 2),
        user_remark=balancer_remark,
    )

    rows_with_balancer = lines + [balancer]

    # Balance assertion
    total_dr = sum(l.debit for l in rows_with_balancer)
    total_cr = sum(l.credit for l in rows_with_balancer)
    delta = abs(total_dr - total_cr)
    if delta > _TOLERANCE_RUPEES:
        raise AdvanceJEGenerationError(
            f"Advance JE is unbalanced after Temporary Opening balancer: "
            f"Dr={total_dr:.2f}, Cr={total_cr:.2f}, |delta|={delta:.2f} "
            f"(tolerance ₹{_TOLERANCE_RUPEES:.2f})."
        )

    header = {
        "doctype": "Journal Entry",
        "voucher_type": "Opening Entry",
        "is_opening": "Yes",
        "company": erpnext_company,
        "posting_date": posting_date,
        "title": f"Advance Entry - {abbr} - FY{fiscal_year}",
        "user_remark": _header_user_remark(
            reference_id=reference_id,
            full_name=full_name,
            session_name=session_name,
            timestamp_iso=timestamp_iso,
            source_sha256=source_sha256,
            old_je_audit=old_je_audit,
        ),
    }

    row_dicts: list[dict[str, Any]] = []
    for l in rows_with_balancer:
        d: dict[str, Any] = {
            "account": l.account,
            "debit_in_account_currency": l.debit,
            "credit_in_account_currency": l.credit,
            "user_remark": l.user_remark,
        }
        # Party fields + is_advance only populate on supplier lines; omit on
        # the balancer row so ERPNext doesn't flag a party-less balancer as
        # inconsistent.
        if l.party_type:
            d["party_type"] = l.party_type
            d["party"] = l.party
        if l.is_advance:
            d["is_advance"] = l.is_advance
        row_dicts.append(d)

    return AdvanceJEPayload(
        header=header,
        rows=row_dicts,
        temp_opening_amount=round(dr_total, 2),
        supplier_count=supplier_count,
        row_count=len(row_dicts),
    )


# ---------------------------------------------------------------------------
# Frappe-aware entry point
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_reference_id(abbr: str, fiscal_year: str) -> str:
    start_year = fiscal_year.split("-")[0] if fiscal_year else ""
    return f"OB-{abbr}-{start_year}-02"


def _append_error_log(session: Any, block: str) -> None:
    existing = session.error_log or ""
    separator = "\n\n" if existing else ""
    session.error_log = f"{existing}{separator}{block}"


def generate_advance_je(session_name: str) -> str:
    """Generate Draft Advance JE for a session. Returns JE.name."""
    import frappe  # type: ignore[import]

    session = frappe.get_doc("Tally Migration Session", session_name)

    # --- 1. Pre-condition ---
    if not session.company_abbr:
        raise AdvanceJEGenerationError(
            f"Session {session_name} has no company_abbr set."
        )
    abbr_doc = frappe.get_doc("Company Abbreviation", session.company_abbr)
    if not abbr_doc.is_active:
        raise AdvanceJEGenerationError(
            f"Company Abbreviation {session.company_abbr!r} is not active."
        )
    if not abbr_doc.erpnext_company:
        raise AdvanceJEGenerationError(
            f"Company Abbreviation {session.company_abbr!r} has no linked "
            f"ERPNext Company."
        )
    erpnext_company = abbr_doc.erpnext_company
    if not frappe.db.exists("Company", erpnext_company):
        raise AdvanceJEGenerationError(
            f"ERPNext Company {erpnext_company!r} does not exist."
        )
    if not session.fiscal_year:
        raise AdvanceJEGenerationError(
            f"Session {session_name} has no fiscal_year set."
        )
    if not session.tb_date:
        raise AdvanceJEGenerationError(
            f"Session {session_name} has no tb_date set."
        )

    creditors_account = f"Sundry Creditors - {session.company_abbr}"
    if not frappe.db.exists("Account", creditors_account):
        raise AdvanceJEGenerationError(
            f"Sundry Creditors account {creditors_account!r} does not "
            f"exist on Company {erpnext_company!r}."
        )
    temp_opening_account = f"Temporary Opening - {session.company_abbr}"
    if not frappe.db.exists("Account", temp_opening_account):
        raise AdvanceJEGenerationError(
            f"Temporary Opening account {temp_opening_account!r} does not "
            f"exist on Company {erpnext_company!r}."
        )

    # --- 2. Idempotency ---
    old_je_audit: str | None = None
    deletion_log_line: str | None = None
    if session.generated_advance_je:
        old_name = session.generated_advance_je
        if frappe.db.exists("Journal Entry", old_name):
            old_doc = frappe.get_doc("Journal Entry", old_name)
            if old_doc.docstatus == 0:
                ts = _iso_now()
                LOG.info(
                    "Replacing existing Draft Advance JE %s for session %s",
                    old_name, session_name,
                )
                old_je_audit = f"Regenerated from {old_name} (deleted at {ts})"
                deletion_log_line = (
                    f"[{ts}] advance-je regeneration: deleted previous Draft "
                    f"JE {old_name} before creating new Draft."
                )
                frappe.delete_doc("Journal Entry", old_name, force=1)
            elif old_doc.docstatus == 1:
                raise AdvanceJEGenerationError(
                    f"Linked Advance JE {old_name} is Submitted. Use "
                    f"ERPNext's amendment workflow (Amend button on the JE) "
                    f"to revise; do not regenerate via this session."
                )
            else:  # Cancelled
                raise AdvanceJEGenerationError(
                    f"Linked Advance JE {old_name} is Cancelled. Clear "
                    f"session.generated_advance_je manually and regenerate."
                )
        session.generated_advance_je = None

    # --- 3. Load persisted Mapping Decisions (Item 2 §9.1 architecture) ---
    from rgi_migration.session.parse_and_map import (
        load_decisions_from_session,
        require_generator_status,
    )

    require_generator_status(session, "generate_advance_je")
    decisions, _ = load_decisions_from_session(session_name)

    supplier_index = _load_supplier_index()

    # --- 4. Build payload ---
    reference_id = (
        session.generated_advance_je_reference
        or _default_reference_id(session.company_abbr, session.fiscal_year)
    )
    posting_date_val = session.tb_date
    posting_date_iso = (
        posting_date_val.isoformat() if hasattr(posting_date_val, "isoformat")
        else str(posting_date_val)
    )
    payload = build_advance_je_payload(
        decisions=decisions,
        supplier_index=supplier_index,
        abbr=session.company_abbr,
        erpnext_company=erpnext_company,
        full_name=abbr_doc.full_name or erpnext_company,
        fiscal_year=session.fiscal_year,
        posting_date=posting_date_iso,
        session_name=session_name,
        source_sha256=session.source_file_sha256 or "",
        reference_id=reference_id,
        timestamp_iso=_iso_now(),
        old_je_audit=old_je_audit,
    )

    # --- 5. Create Draft JE ---
    je = frappe.new_doc("Journal Entry")
    je.update(payload.header)
    for row in payload.rows:
        je.append("accounts", row)
    je.insert(ignore_permissions=True)
    LOG.info(
        "Created Draft Advance JE %s for session %s "
        "(%d supplier lines + 1 balancer, Temp Opening Cr %.2f)",
        je.name, session_name, payload.supplier_count, payload.temp_opening_amount,
    )

    # --- 6. Link back + persist logs ---
    session.generated_advance_je = je.name
    session.generated_advance_je_reference = reference_id

    log_blocks: list[str] = []
    if deletion_log_line:
        log_blocks.append(deletion_log_line)
    log_blocks.append(
        f"[{_iso_now()}] advance-je generated: {payload.supplier_count} "
        f"net-Dr supplier(s) + 1 balancer in {je.name}."
    )
    for block in log_blocks:
        _append_error_log(session, block)

    session.save(ignore_permissions=True)
    frappe.db.commit()
    return je.name


def _load_supplier_index() -> dict[str, SupplierInfo]:
    """Fetch live Supplier master — both enabled and disabled, so the
    re-check pass can distinguish 'deleted' from 'disabled'."""
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


