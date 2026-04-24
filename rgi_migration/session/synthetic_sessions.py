# Copyright (c) 2026, Dux Digitech and contributors
# For license information, please see license.txt

"""Synthetic Tally Migration Session factory for integration testing.

Creates real Frappe-backed `Tally Migration Session` rows with a
hand-built set of `Mapping Decision` rows — no Tally parse, no mapper
run — so downstream workflows (generators, reviewer UI, status
transitions) can be exercised against a predictable dataset.

Designed to be **reused across Stages 1-3** of Item 8.5 and any
future work needing a Frappe-backed session without going through
the full parse + map pipeline. Parameterized on:

* ``num_decisions`` — total decisions to create
* ``tier_distribution`` — dict like ``{"tier1_exact": 3, "tier1_rule": 2}``
  that overrides the default even-split (falls back to tier1_exact-heavy)
* ``all_approved`` — when True, sets ``review_action="Approved"`` and
  populates ``final_account`` on every actionable decision so
  generators pass their refusal gates. When False, leaves rows in
  ``Pending`` — useful for Stage 2 Deferred-state testing.
* ``attach_source_file`` — when True, sets ``source_file_server_path``
  to the committed sample_cacspu_masters_sample.xml fixture so the
  Students CSV generator can reparse it. Default True.

All synthetic rows use clearly-marked identifiers ("SYNTHETIC-…")
so a teardown sweep can recognise its own output. Paired with
``teardown_synthetic_session`` which drops the session + every linked
Mapping Decision + any artefacts written back to the session (JE
drafts, attached files).

Default company: ``CACSPU`` — it has the required COA entries
(``Temporary Opening - CACSPU``, ``Sundry Creditors - CACSPU``,
``Ex-Students Receivable - CACSPU``) already seeded on the dev bench.
Pass a different abbr only if those accounts exist for it.

Default fiscal year: ``2099-2100`` — chosen far from any realistic
migration period so synthetic sessions can't be confused with real
ones by their autoname (``TMS-CACSPU-99-00-#####``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


# Default tier split for a 5-decision session: 3 tier1_exact + 2 tier1_rule.
# Generators refuse on unmapped / pending_* tiers (see generators/README),
# so the default is deliberately refusal-free.
_DEFAULT_TIER_DISTRIBUTION = {
    "tier1_exact": 3,
    "tier1_rule": 2,
}


def _fixture_xml_path() -> str:
    """Absolute path to the committed sample XML used by the Students CSV
    generator when a synthetic session needs a reparse target.

    Resolves relative to this module so it works identically on the
    local worktree and on the bench (both have the fixture at the same
    package-relative path).
    """
    here = Path(__file__).resolve()
    fixture = here.parent.parent / "tests" / "fixtures" / "sample_cacspu_masters_sample.xml"
    return str(fixture)


def _build_tier_plan(
    num_decisions: int,
    tier_distribution: dict[str, int] | None,
) -> list[str]:
    """Return a list of tier strings of length ``num_decisions``.

    If ``tier_distribution`` is provided, expand it; counts are
    truncated/padded to reach ``num_decisions`` so the caller doesn't
    need to keep the two in sync. If omitted, apply
    ``_DEFAULT_TIER_DISTRIBUTION`` and scale proportionally.
    """
    dist = tier_distribution or _DEFAULT_TIER_DISTRIBUTION
    plan: list[str] = []
    for tier, count in dist.items():
        plan.extend([tier] * count)
    # Pad with the first key if under, truncate if over.
    if len(plan) < num_decisions:
        fallback = next(iter(dist.keys())) if dist else "tier1_exact"
        plan.extend([fallback] * (num_decisions - len(plan)))
    return plan[:num_decisions]


def _decision_row(
    *,
    index: int,
    tier: str,
    session_name: str,
    company_abbr: str,
    all_approved: bool,
) -> dict[str, Any]:
    """Build a Mapping Decision insert-dict for one synthetic row.

    Tally-side fields carry the ``SYNTHETIC-`` marker so teardown can
    identify its own rows without relying solely on the session FK.

    Account assignment: Dr-side rows post against a synthesised Asset-
    style leaf; Cr-side rows post against a Liability-style leaf. Split
    avoids Main JE aggregating to a row with both Dr+Cr on the same
    account (ERPNext refuses those as meaningless wash entries). Both
    chosen accounts are pre-seeded real leaf accounts on CACSPU.
    NEVER Temporary Opening — that's the balancer and must not carry
    contribution lines or the Main JE refuses.
    """
    tally_name = f"SYNTHETIC Ledger {index:03d}"
    # 2-out-of-3 Dr predicate: indices 0,1,3,4,6,7,... are Dr; 2,5,8,...
    # are Cr. Guarantees Dr count ≠ Cr count for any `num_decisions`,
    # which guarantees the Main JE's Temporary Opening balancer row
    # carries a non-zero amount. (ERPNext's Journal Entry validator
    # rejects rows where both debit and credit are zero — would bite
    # with a simple even/odd alternation at balanced totals.)
    is_dr_row = (index % 3) != 2
    if is_dr_row:
        account = f"Professional Tax - {company_abbr}"
        opening_dr = 100.0
        opening_cr = 0.0
    else:
        account = f"ICICI BANK - 624205021153 - {company_abbr}"
        opening_dr = 0.0
        opening_cr = 100.0
    net_amount = opening_cr - opening_dr
    net_side = "Cr" if net_amount > 0 else ("Dr" if net_amount < 0 else "Zero")

    row: dict[str, Any] = {
        "doctype": "Mapping Decision",
        "session": session_name,
        "tally_name": tally_name,
        "tally_id": f"SYN{index:04d}",
        "tally_parent_chain": "SYNTHETIC > Current Assets",
        "tally_root_type": "Asset",
        "opening_dr": opening_dr,
        "opening_cr": opening_cr,
        "net_amount": net_amount,
        "net_side": net_side,
        "is_pnl_closed_zero": 0,
        "is_student_ledger": 0,
        "is_system_account": 0,
        "tier": tier,
        "proposed_account": account,
        "confidence": 1.0,
        "review_action": "Approved" if all_approved else "Pending",
        "final_account": account if all_approved else None,
    }
    return row


def _supplier_advance_row(
    *,
    session_name: str,
    company_abbr: str,
    supplier_name: str,
) -> dict[str, Any]:
    """Build one ``tier1_supplier_exact`` Mapping Decision for a net-Dr
    vendor advance.

    Injected by ``create_synthetic_session`` when ``all_approved=True``
    and ``include_supplier_advance=True`` so the Advance JE generator
    has at least one supplier with a Dr (advance-paid) balance to
    aggregate. Advance JE refuses on an empty supplier set (ERPNext
    rejects a JE with zero-amount rows) — this row keeps the happy
    path green without changing generator semantics.
    """
    return {
        "doctype": "Mapping Decision",
        "session": session_name,
        "tally_name": f"SYNTHETIC Vendor Advance {supplier_name}",
        "tally_id": "SYNV0001",
        "tally_parent_chain": (
            "SYNTHETIC > Current Liabilities > Sundry Creditors"
        ),
        "tally_root_type": "Liability",
        "opening_dr": 500.0,  # Dr = advance paid to vendor (net-Dr)
        "opening_cr": 0.0,
        "net_amount": -500.0,
        "net_side": "Dr",
        "is_pnl_closed_zero": 0,
        "is_student_ledger": 0,
        "is_system_account": 0,
        "tier": "tier1_supplier_exact",
        "proposed_supplier": supplier_name,
        "final_supplier": supplier_name,
        "supplier_match_score": 1.0,
        "confidence": 1.0,
        "review_action": "Approved",
        "final_dr": 500.0,
        "final_cr": 0.0,
    }


def create_synthetic_session(
    *,
    num_decisions: int = 5,
    tier_distribution: dict[str, int] | None = None,
    all_approved: bool = True,
    company_abbr: str = "CACSPU",
    fiscal_year: str = "2099-2100",
    status: str = "Reviewing",
    attach_source_file: bool = True,
    include_supplier_advance: bool = True,
    advance_supplier_name: str = "Rites",
) -> str:
    """Create a Tally Migration Session + N Mapping Decisions for testing.

    Bypasses the parse+map pipeline entirely — the session is stamped
    directly into the desired status (default ``Reviewing``) with
    hand-built decisions. Suitable for exercising generators, UI
    workflows, and status transitions without the 4s mapper overhead
    per test.

    Returns the session's autoname (e.g. ``TMS-CACSPU-99-00-00042``).

    Raises ``frappe.ValidationError`` (via ``frappe.throw``) on company
    abbr / fiscal year format violations — matches the normal session
    creation contract.
    """
    import frappe  # lazy; module stays importable without a bench

    # Build session doc via the normal insert path — the before_insert
    # hook populates fiscal_year_short, the fetch_from JSON directive
    # fills erpnext_company from company_abbr.
    #
    # tb_date deliberately set to 2025-04-01 (start of FY 2025-2026) —
    # a known-good date for which the dev bench has an active Fiscal
    # Year doc for GHR CACS Pune. Using `nowdate()` can hit
    # FiscalYearError if the bench hasn't had the current FY activated
    # for the target Company. Callers testing against other companies
    # may need to override via a future parameter.
    session_doc = frappe.get_doc({
        "doctype": "Tally Migration Session",
        "company_abbr": company_abbr,
        "fiscal_year": fiscal_year,
        "tb_date": "2025-04-01",
        "source_format": "xml",
    })
    if attach_source_file:
        session_doc.source_file_server_path = _fixture_xml_path()
    session_doc.insert(ignore_permissions=True)

    # Stamp status directly — Frappe validates Select options but not
    # transitions between them, so we can jump straight to Reviewing
    # without running the parse+map pipeline.
    frappe.db.set_value(
        "Tally Migration Session", session_doc.name, "status", status,
    )

    # Insert N Mapping Decisions.
    plan = _build_tier_plan(num_decisions, tier_distribution)
    for i, tier in enumerate(plan):
        row = _decision_row(
            index=i,
            tier=tier,
            session_name=session_doc.name,
            company_abbr=company_abbr,
            all_approved=all_approved,
        )
        frappe.get_doc(row).insert(ignore_permissions=True)

    # Supplier-advance row — added only when all_approved so the happy-
    # path generate_all exercises Advance JE. On all_approved=False
    # (refusal-path tests) we don't want to pollute with a
    # would-succeed row; the caller is testing the unmapped/pending
    # refusals.
    if include_supplier_advance and all_approved:
        frappe.get_doc(_supplier_advance_row(
            session_name=session_doc.name,
            company_abbr=company_abbr,
            supplier_name=advance_supplier_name,
        )).insert(ignore_permissions=True)

    frappe.db.commit()
    return session_doc.name


def teardown_synthetic_session(session_name: str) -> dict[str, int]:
    """Delete a synthetic session and every artefact it touched.

    Order matters — Mapping Decisions link to the session via a
    Link field, so they must be deleted first. Frappe doesn't
    cascade-delete Link-field dependents automatically.

    Returns a dict summary: ``{"decisions": int, "draft_jes": int,
    "files": int}``. Counts are zero for artefacts that never got
    generated.
    """
    import frappe

    if not frappe.db.exists("Tally Migration Session", session_name):
        return {"decisions": 0, "draft_jes": 0, "files": 0}

    session = frappe.get_doc("Tally Migration Session", session_name)

    # Delete linked Draft JEs (idempotency-safe; Submitted won't be deleted).
    draft_jes = 0
    for field in ("generated_je_draft", "generated_advance_je"):
        je_name = getattr(session, field, None)
        if je_name and frappe.db.exists("Journal Entry", je_name):
            je_docstatus = frappe.db.get_value(
                "Journal Entry", je_name, "docstatus",
            )
            if je_docstatus == 0:  # Draft only
                frappe.delete_doc("Journal Entry", je_name, force=1)
                draft_jes += 1

    # Delete attached Files referenced by OIT + Students CSV links.
    files_deleted = 0
    for field in ("generated_oit_file", "student_ledger_file"):
        file_url = getattr(session, field, None)
        if not file_url:
            continue
        file_docs = frappe.get_all(
            "File", filters={"file_url": file_url}, fields=["name"],
        )
        for f in file_docs:
            frappe.delete_doc("File", f["name"], force=1)
            files_deleted += 1

    # Delete the Mapping Decisions — raw SQL for speed (bulk).
    decisions_count = frappe.db.count(
        "Mapping Decision", {"session": session_name},
    )
    if decisions_count:
        frappe.db.sql(
            "DELETE FROM `tabMapping Decision` WHERE session = %s",
            (session_name,),
        )

    # Finally the session itself.
    frappe.delete_doc(
        "Tally Migration Session", session_name,
        force=1, ignore_permissions=True,
    )
    frappe.db.commit()

    return {
        "decisions": decisions_count,
        "draft_jes": draft_jes,
        "files": files_deleted,
    }
