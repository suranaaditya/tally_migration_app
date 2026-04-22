"""Generator #1 — Main Opening Journal Entry (Draft).

Public entry points:

* :func:`generate_main_opening_je` — Frappe-aware. Loads a Tally Migration
  Session, parses + maps on demand, creates a Draft Journal Entry, links it
  back to the session. Returns the JE ``name``.
* :func:`build_je_payload` — pure core. Builds the JE dict payload from a
  ``ParsedTallyTB`` + list of ``MappedDecision``. Importable without
  Frappe; used by unit tests.

Scope (approved in the Work-Item-7 prose-design review):

    A Tally ledger becomes a JE account line iff ALL hold:

      * ``ledger.is_leaf``
      * ``not ledger.is_student_ledger``       (→ dux_voucher CSV)
      * ``not ledger.is_system_account``       (Tally internals)
      * ``ledger.root_type in {Asset, Liability, Equity}``  (P&L closes to 0)
      * decision tier in ``{tier1_rule, tier1_exact, tier1_pattern}``

    Supplier tiers (``tier1_supplier_*``, ``pending_supplier_creation``)
    are silently skipped — they're handled by generators #2 / #3.
    ``excluded_pnl`` decisions are skipped; any with non-zero balances
    emit a warning (diagnostic for a mis-configured Tally export).

Refusal contract (raises :class:`MainJEGenerationError` before any write):

    ``unmapped > 0``, ``pending_account_creation > 0``,
    ``group_refused > 0``, or ``anti_pattern_blocked > 0`` → refuse.
    Reviewer must resolve via the Week-4 UI before regeneration.

Idempotency:

    If ``session.generated_je_draft`` is set and the linked JE is Draft,
    it's deleted and regenerated. Submitted/Cancelled → refuse.

Grouping:

    One JE row per unique ERPNext account. Contributions from multiple
    Tally ledgers sum; per-row ``user_remark`` lists each contributor, with
    ``+combined`` suffix when the group has more than one member (reflecting
    the ``combine_amounts`` semantic of the source Mapping Rule).

Balancer:

    ``Temporary Opening - {ABBR}`` absorbs the net residual. Post-balancer
    imbalance > ₹1 (``_TOLERANCE_RUPEES``, per RGI_Migration_Rules.md §6.2)
    raises. The residual flows across generators — main JE + OIT + party-Dr
    JE + dux_voucher Ex Student JE all post to ``Temporary Opening`` and
    net to zero only after all four submit.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from rgi_migration.mapper.mapper import MappedDecision
from rgi_migration.parsers.normalized_schema import Ledger, ParsedTallyTB

LOG = logging.getLogger(__name__)

# RGI_Migration_Rules.md §6.2 — post-balancer tolerance on Dr=Cr.
_TOLERANCE_RUPEES = 1.00

_ELIGIBLE_TIERS = frozenset({"tier1_rule", "tier1_exact", "tier1_pattern"})
_REFUSAL_TIERS = frozenset({
    "unmapped",
    "pending_account_creation",
    "group_refused",
    "anti_pattern_blocked",
})


class MainJEGenerationError(Exception):
    """Raised when preconditions for main JE generation are not met."""


@dataclass(frozen=True)
class JEContribution:
    """One Tally ledger's contribution to a (possibly grouped) JE row."""

    tally_name: str
    tally_id: str | None
    erpnext_account: str
    opening_dr: float
    opening_cr: float
    tier: str
    matched_rule: str | None  # source_section (e.g. "sec 4.1") or None for tier1_exact


@dataclass
class JERow:
    """One row destined for the Journal Entry's ``accounts`` child table."""

    account: str
    debit: float
    credit: float
    user_remark: str


@dataclass
class MainJEPayload:
    """JSON-serialisable JE payload ready for ``frappe.get_doc().insert()``."""

    header: dict[str, Any]
    rows: list[dict[str, Any]]
    # Signed residual absorbed by Temporary Opening.
    # Positive = Cr side on Temp Opening (main JE ledgers net Dr-heavy).
    # Negative = Dr side on Temp Opening (main JE ledgers net Cr-heavy).
    temp_opening_amount: float
    warnings: list[str]
    contributions_count: int       # number of Tally ledgers included
    row_count: int                 # number of JE rows (excludes header)


# ---------------------------------------------------------------------------
# Pure core — no Frappe imports
# ---------------------------------------------------------------------------


def _is_main_je_eligible(ledger: Ledger) -> bool:
    return (
        ledger.is_leaf
        and not ledger.is_student_ledger
        and not ledger.is_system_account
        and ledger.root_type in ("Asset", "Liability", "Equity")
    )


def _select_contributions(
    decisions: Iterable[MappedDecision],
    ledger_index: dict[tuple[str, str | None], Ledger],
) -> tuple[list[JEContribution], list[MappedDecision]]:
    contributions: list[JEContribution] = []
    refusals: list[MappedDecision] = []
    for d in decisions:
        ledger = ledger_index.get((d.tally_name, d.tally_id))
        if ledger is None or not _is_main_je_eligible(ledger):
            continue
        if d.tier in _REFUSAL_TIERS:
            refusals.append(d)
            continue
        if d.tier not in _ELIGIBLE_TIERS:
            continue
        if not d.proposed_account:
            refusals.append(d)
            continue
        contributions.append(JEContribution(
            tally_name=d.tally_name,
            tally_id=d.tally_id,
            erpnext_account=d.proposed_account,
            opening_dr=ledger.opening_dr,
            opening_cr=ledger.opening_cr,
            tier=d.tier,
            matched_rule=d.matched_rule,
        ))
    return contributions, refusals


def _enforce_refusals(refusals: list[MappedDecision], session_name: str) -> None:
    if not refusals:
        return
    by_tier: Counter[str] = Counter(d.tier for d in refusals)
    parts: list[str] = []
    if by_tier.get("unmapped"):
        parts.append(f"{by_tier['unmapped']} ledgers unmapped")
    if by_tier.get("pending_account_creation"):
        parts.append(f"{by_tier['pending_account_creation']} pending account creation")
    if by_tier.get("group_refused"):
        parts.append(f"{by_tier['group_refused']} refused by group validator")
    if by_tier.get("anti_pattern_blocked"):
        parts.append(f"{by_tier['anti_pattern_blocked']} anti-pattern blocked")
    raise MainJEGenerationError(
        f"Cannot generate main JE for session {session_name}: "
        + ", ".join(parts)
        + ". Resolve all in review UI before regeneration."
    )


def _pnl_corner_case_warnings(
    decisions: Iterable[MappedDecision],
    ledger_index: dict[tuple[str, str | None], Ledger],
) -> list[str]:
    """Warn when an ``excluded_pnl`` ledger carries a non-zero opening balance.

    P&L ledgers should close to zero on opening (Tally's "Export closing as
    opening" flag — see docs/tally_sign_convention.md §4). A non-zero
    balance on one signals either a misconfigured export or stale data.
    The ledger is still excluded from the main JE (P&L never goes in
    opening); the warning surfaces the anomaly for reviewer investigation.
    """
    warnings: list[str] = []
    for d in decisions:
        if d.tier != "excluded_pnl":
            continue
        ledger = ledger_index.get((d.tally_name, d.tally_id))
        if ledger is None:
            continue
        if ledger.opening_dr == 0 and ledger.opening_cr == 0:
            continue  # normal close-to-zero P&L
        warnings.append(
            f"P&L ledger {d.tally_name!r} [tally_id={d.tally_id}] carries "
            f"non-zero opening balance (Dr={ledger.opening_dr:.2f}, "
            f"Cr={ledger.opening_cr:.2f}); excluded from JE. "
            f"Check Tally 'Export closing as opening' flag."
        )
    return warnings


def _format_contribution_line(c: JEContribution, *, combined: bool) -> str:
    rule_ref = c.matched_rule or "exact"
    suffix = "+combined" if combined else ""
    return (
        f"Tally: {c.tally_name} [tally_id={c.tally_id}] "
        f"via {c.tier}/{rule_ref}{suffix}"
    )


def _group_by_account(contributions: list[JEContribution]) -> list[JERow]:
    """Sum contributions per ERPNext account, preserving first-seen order."""
    order: list[str] = []
    buckets: dict[str, list[JEContribution]] = {}
    for c in contributions:
        if c.erpnext_account not in buckets:
            order.append(c.erpnext_account)
            buckets[c.erpnext_account] = []
        buckets[c.erpnext_account].append(c)

    rows: list[JERow] = []
    for account in order:
        group = buckets[account]
        combined = len(group) > 1
        remark = "\n".join(
            _format_contribution_line(c, combined=combined) for c in group
        )
        rows.append(JERow(
            account=account,
            debit=sum(c.opening_dr for c in group),
            credit=sum(c.opening_cr for c in group),
            user_remark=remark,
        ))
    return rows


def _balancer_row(
    rows: list[JERow], temp_opening_account: str, reference_id: str,
) -> JERow:
    total_dr = sum(r.debit for r in rows)
    total_cr = sum(r.credit for r in rows)
    delta = total_dr - total_cr  # >0 → credit side needed to balance
    debit = -delta if delta < 0 else 0.0
    credit = delta if delta > 0 else 0.0
    remark = (
        f"Opening balance residual ({reference_id}) — Temporary Opening is "
        f"shared across main JE, OIT, party-Dr JE, and dux_voucher's Ex "
        f"Student JE; nets to zero after all four submit."
    )
    return JERow(
        account=temp_opening_account, debit=debit, credit=credit, user_remark=remark,
    )


def _assert_balanced(rows: list[JERow]) -> None:
    dr = sum(r.debit for r in rows)
    cr = sum(r.credit for r in rows)
    delta = abs(dr - cr)
    if delta > _TOLERANCE_RUPEES:
        raise MainJEGenerationError(
            f"Main JE is unbalanced after Temporary Opening balancer: "
            f"Dr={dr:.2f}, Cr={cr:.2f}, |delta|={delta:.2f} "
            f"(tolerance ₹{_TOLERANCE_RUPEES:.2f})."
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
        f"Opening balance migration — {full_name}",
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


def build_je_payload(
    *,
    tb: ParsedTallyTB,
    decisions: list[MappedDecision],
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
) -> MainJEPayload:
    """Build the Draft JE payload without touching Frappe.

    Raises :class:`MainJEGenerationError` on refusal or post-balancer
    imbalance. Caller is responsible for invoking Frappe ORM (see
    :func:`generate_main_opening_je`).
    """
    ledger_index: dict[tuple[str, str | None], Ledger] = {
        (l.name, l.tally_id): l for l in tb.ledgers
    }
    contributions, refusals = _select_contributions(decisions, ledger_index)
    _enforce_refusals(refusals, session_name)
    warnings = _pnl_corner_case_warnings(decisions, ledger_index)

    rows = _group_by_account(contributions)
    temp_opening_account = f"Temporary Opening - {abbr}"
    balancer = _balancer_row(rows, temp_opening_account, reference_id)
    rows_with_balancer = rows + [balancer]
    _assert_balanced(rows_with_balancer)

    header = {
        "doctype": "Journal Entry",
        "voucher_type": "Opening Entry",
        "is_opening": "Yes",
        "company": erpnext_company,
        "posting_date": posting_date,
        "title": f"Opening Entry - {abbr} - FY{fiscal_year}",
        "user_remark": _header_user_remark(
            reference_id=reference_id, full_name=full_name,
            session_name=session_name, timestamp_iso=timestamp_iso,
            source_sha256=source_sha256, old_je_audit=old_je_audit,
        ),
    }
    row_dicts = [
        {
            "account": r.account,
            "debit_in_account_currency": round(r.debit, 2),
            "credit_in_account_currency": round(r.credit, 2),
            "user_remark": r.user_remark,
        }
        for r in rows_with_balancer
    ]
    temp_opening_amount = round(balancer.credit - balancer.debit, 2)
    return MainJEPayload(
        header=header,
        rows=row_dicts,
        temp_opening_amount=temp_opening_amount,
        warnings=warnings,
        contributions_count=len(contributions),
        row_count=len(row_dicts),
    )


# ---------------------------------------------------------------------------
# Frappe-aware entry point
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_reference_id(abbr: str, fiscal_year: str) -> str:
    start_year = fiscal_year.split("-")[0] if fiscal_year else ""
    return f"OB-{abbr}-{start_year}-01"


def _append_error_log(session: Any, block: str) -> None:
    existing = session.error_log or ""
    separator = "\n\n" if existing else ""
    session.error_log = f"{existing}{separator}{block}"


def generate_main_opening_je(session_name: str) -> str:
    """Generate a Draft Main Opening JE for the given session. Returns JE.name.

    Order of operations (matches the §3 pre-condition-first contract):

      1. Resolve session + Company Abbreviation + ERPNext Company links.
      2. Idempotency — delete existing Draft JE if present; refuse Submitted.
      3. Parse Tally source + run Tier-1 mapper (ad-hoc; Week-4 will persist).
      4. Build payload via :func:`build_je_payload`.
      5. Create ``Journal Entry`` via ``frappe.new_doc``, insert Draft.
      6. Link ``session.generated_je_draft`` + persist ``temp_opening_amount``
         + append warnings / deletion audit to ``session.error_log``.

    Raises :class:`MainJEGenerationError` on any pre-condition or refusal
    failure; nothing is written to the session or database in that case.
    """
    import frappe  # type: ignore[import]

    session = frappe.get_doc("Tally Migration Session", session_name)

    # --- 1. Pre-condition ---------------------------------------------------
    if not session.company_abbr:
        raise MainJEGenerationError(
            f"Session {session_name} has no company_abbr set."
        )
    abbr_doc = frappe.get_doc("Company Abbreviation", session.company_abbr)
    if not abbr_doc.is_active:
        raise MainJEGenerationError(
            f"Company Abbreviation {session.company_abbr!r} is not active."
        )
    if not abbr_doc.erpnext_company:
        raise MainJEGenerationError(
            f"Company Abbreviation {session.company_abbr!r} has no linked "
            f"ERPNext Company. Set erpnext_company on the Company "
            f"Abbreviation row before generating the JE."
        )
    erpnext_company = abbr_doc.erpnext_company
    if not frappe.db.exists("Company", erpnext_company):
        raise MainJEGenerationError(
            f"ERPNext Company {erpnext_company!r} referenced by "
            f"Company Abbreviation {session.company_abbr!r} does not exist."
        )
    if not session.fiscal_year:
        raise MainJEGenerationError(
            f"Session {session_name} has no fiscal_year set."
        )
    if not session.tb_date:
        raise MainJEGenerationError(
            f"Session {session_name} has no tb_date set."
        )

    # --- 2. Idempotency -----------------------------------------------------
    old_je_audit: str | None = None
    deletion_log_line: str | None = None
    if session.generated_je_draft:
        old_name = session.generated_je_draft
        if frappe.db.exists("Journal Entry", old_name):
            old_doc = frappe.get_doc("Journal Entry", old_name)
            if old_doc.docstatus == 0:
                ts = _iso_now()
                LOG.info(
                    "Replacing existing Draft JE %s with new generation from "
                    "session %s", old_name, session_name,
                )
                old_je_audit = f"Regenerated from {old_name} (deleted at {ts})"
                deletion_log_line = (
                    f"[{ts}] main-je regeneration: deleted previous Draft JE "
                    f"{old_name} before creating new Draft."
                )
                frappe.delete_doc("Journal Entry", old_name, force=1)
            elif old_doc.docstatus == 1:
                raise MainJEGenerationError(
                    f"Linked JE {old_name} is Submitted. Use ERPNext's "
                    f"amendment workflow (Amend button on the JE) to revise; "
                    f"do not regenerate via this session."
                )
            else:  # 2 = Cancelled
                raise MainJEGenerationError(
                    f"Linked JE {old_name} is Cancelled. Clear "
                    f"session.generated_je_draft manually and regenerate."
                )
        session.generated_je_draft = None

    # --- 3. Load persisted Mapping Decisions (Item 2 §9.1 architecture) ---
    # Reads from the DocType that run_mapper() populated, preferring the
    # reviewer's final_account / final_supplier over mapper proposals.
    # See rgi_migration/session/parse_and_map.py for the loader.
    from rgi_migration.session.parse_and_map import (
        load_decisions_from_session,
        require_generator_status,
        synthesize_tb_from_ledger_index,
    )

    require_generator_status(session, "generate_main_opening_je")
    decisions, ledger_index = load_decisions_from_session(session_name)

    # build_je_payload's signature still takes ``tb: ParsedTallyTB`` because
    # pure-core tests construct hand-crafted TBs against it. Synthesize a
    # minimal TB from the reconstructed ledger_index so the signature
    # stays stable; only tb.ledgers is read downstream.
    tb = synthesize_tb_from_ledger_index(
        ledger_index,
        company_name=session.parsed_company_name or "",
        tb_date=(
            session.tb_date.isoformat() if hasattr(session.tb_date, "isoformat")
            else str(session.tb_date or "")
        ),
        source_format=session.source_format or "xml",
    )

    zero_count = sum(1 for d in decisions if d.tier == "excluded_zero_balance")
    if zero_count:
        LOG.info(
            "Skipped %d zero-balance ledgers (Tally definitional noise — no "
            "migration impact) for session %s", zero_count, session_name,
        )

    # --- 4. Build payload ---------------------------------------------------
    reference_id = (
        session.generated_je_reference
        or _default_reference_id(session.company_abbr, session.fiscal_year)
    )
    tb_date_val = session.tb_date
    posting_date = (
        tb_date_val.isoformat() if hasattr(tb_date_val, "isoformat")
        else str(tb_date_val)
    )
    payload = build_je_payload(
        tb=tb, decisions=decisions,
        abbr=session.company_abbr, erpnext_company=erpnext_company,
        full_name=abbr_doc.full_name or erpnext_company,
        fiscal_year=session.fiscal_year, posting_date=posting_date,
        session_name=session_name,
        source_sha256=session.source_file_sha256 or "",
        reference_id=reference_id, timestamp_iso=_iso_now(),
        old_je_audit=old_je_audit,
    )

    # --- 5. Create Draft JE -------------------------------------------------
    je = frappe.new_doc("Journal Entry")
    je.update(payload.header)
    for row in payload.rows:
        je.append("accounts", row)
    je.insert(ignore_permissions=True)
    LOG.info(
        "Created Draft Journal Entry %s for session %s (%d rows, "
        "%d Tally contributors, Temp Opening residual %.2f)",
        je.name, session_name, payload.row_count,
        payload.contributions_count, payload.temp_opening_amount,
    )

    # --- 6. Link back + persist logs ----------------------------------------
    session.generated_je_draft = je.name
    session.generated_je_reference = reference_id
    session.temp_opening_amount = payload.temp_opening_amount

    log_blocks: list[str] = []
    if deletion_log_line:
        log_blocks.append(deletion_log_line)
    if payload.warnings:
        ts = _iso_now()
        warn_body = "\n".join(f"  - {w}" for w in payload.warnings)
        log_blocks.append(
            f"[{ts}] main-je warnings ({len(payload.warnings)}):\n{warn_body}"
        )
    for block in log_blocks:
        _append_error_log(session, block)

    session.save(ignore_permissions=True)
    frappe.db.commit()
    return je.name
