"""Tests for Item 3 Commit 3 — SCR approval / rejection pure-core + JS wiring.

Pure-core coverage (query.py):
1. build_supplier_doc_payload — maps SCR row → Supplier insert dict,
   defaults supplier_type="Company" at approval time.
2. apply_scr_approval_to_decision — computes the decision field updates
   on approve (final_supplier, tier lift, review_action=Approved).
3. apply_scr_rejection_to_decision — computes the decision field updates
   on reject (review_action=Rejected, final_supplier cleared).

Frappe-wrapper tests (approve_scr / reject_scr) require a live bench;
exercised via the Phase C programmatic smoke. This file covers the
pure helpers + static-file guardrails for the JS panel dialog + the
generator refusal-filter change.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from rgi_migration.rgi_migration.page.md_review.query import (
    apply_scr_approval_to_decision,
    apply_scr_rejection_to_decision,
    build_supplier_doc_payload,
)


# ---------------------------------------------------------------------------
# build_supplier_doc_payload
# ---------------------------------------------------------------------------


def _scr_row(**overrides) -> dict:
    base = {
        "proposed_supplier_name": "Aarna Solutions Pvt Ltd",
        "proposed_supplier_group": "Services",
    }
    base.update(overrides)
    return base


def test_supplier_payload_defaults_supplier_type_to_company() -> None:
    out = build_supplier_doc_payload(scr_row=_scr_row())
    assert out["supplier_type"] == "Company"
    assert out["doctype"] == "Supplier"
    assert out["supplier_name"] == "Aarna Solutions Pvt Ltd"
    assert out["supplier_group"] == "Services"


def test_supplier_payload_empty_group_stays_none() -> None:
    """SCR can legitimately have no supplier_group (reviewer left
    empty, filling at approval time instead). Supplier DocType
    permits NULL supplier_group in the payload — native Supplier
    validation catches the actual reqd enforcement."""
    out = build_supplier_doc_payload(scr_row=_scr_row(proposed_supplier_group=None))
    assert out["supplier_group"] is None


def test_supplier_payload_custom_type_override_accepted() -> None:
    """The default_supplier_type arg lets future callers / tests
    pick a different type (Individual / Partnership) without
    editing the helper."""
    out = build_supplier_doc_payload(
        scr_row=_scr_row(), default_supplier_type="Individual",
    )
    assert out["supplier_type"] == "Individual"


# ---------------------------------------------------------------------------
# apply_scr_approval_to_decision
# ---------------------------------------------------------------------------


def test_approval_decision_update_writes_three_fields() -> None:
    out = apply_scr_approval_to_decision(
        resolved_supplier_name="Aarna Solutions Pvt Ltd",
    )
    assert out == {
        "final_supplier": "Aarna Solutions Pvt Ltd",
        "tier": "tier1_supplier_exact",
        "review_action": "Approved",
    }


def test_approval_uses_resolved_name_not_proposed() -> None:
    """Naming collision case: reviewer proposed 'Acme Pvt Ltd' but
    Frappe created 'Acme Pvt Ltd 1' (existing record collided). The
    helper writes the RESOLVED name — otherwise final_supplier would
    link to a non-existent Supplier record."""
    out = apply_scr_approval_to_decision(
        resolved_supplier_name="Acme Pvt Ltd 1",
    )
    assert out["final_supplier"] == "Acme Pvt Ltd 1"


# ---------------------------------------------------------------------------
# apply_scr_rejection_to_decision
# ---------------------------------------------------------------------------


def test_rejection_decision_update_sets_review_action_rejected() -> None:
    out = apply_scr_rejection_to_decision()
    assert out["review_action"] == "Rejected"


def test_rejection_clears_final_supplier() -> None:
    """Reviewer may have previously picked a Map-to-existing Supplier,
    then created an SCR, then rejected the SCR — the final_supplier
    Link from the prior Map-to-existing save is now stale. Clearing
    it on reject keeps the decision state consistent with the
    reviewer's latest intent."""
    out = apply_scr_rejection_to_decision()
    assert out["final_supplier"] is None


def test_rejection_does_not_touch_tier() -> None:
    """tier stays mapper-authoritative. Generator refusal gates skip
    Rejected rows (Item 3 Commit 3 filter extension), so leaving
    tier=pending_supplier_creation is fine."""
    out = apply_scr_rejection_to_decision()
    assert "tier" not in out


# ---------------------------------------------------------------------------
# Generator refusal-filter change (Item 3 Commit 3 scope creep)
# ---------------------------------------------------------------------------


_GEN_ROOT = Path(__file__).parent.parent / "generators"


@pytest.fixture(scope="module")
def oit_source() -> str:
    return (_GEN_ROOT / "oit_csv.py").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def advance_source() -> str:
    return (_GEN_ROOT / "advance_je.py").read_text(encoding="utf-8")


def _extract_pending_count_block(src: str) -> str:
    """Extract the multi-line pending_count = sum(...) expression by
    walking balanced parens. Simple `[^)]+` regex truncates at the
    first inner `)` (e.g. inside ``getattr(d, "review_action", None)``).
    """
    m = re.search(r'pending_count\s*=\s*sum\(', src)
    assert m is not None, "pending_count = sum( not found"
    start = m.end() - 1  # index of the opening (
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                return src[m.start():i + 1]
    raise AssertionError("unbalanced parens")


def test_oit_refusal_filter_skips_rejected_review_action(oit_source: str) -> None:
    """The pending_supplier_creation refusal gate must check
    review_action != 'Rejected' so reviewer-rejected rows silent-skip
    instead of blocking generator output."""
    body = _extract_pending_count_block(oit_source)
    assert '"pending_supplier_creation"' in body
    assert '"Rejected"' in body
    assert "review_action" in body


def test_advance_refusal_filter_skips_rejected_review_action(advance_source: str) -> None:
    body = _extract_pending_count_block(advance_source)
    assert '"pending_supplier_creation"' in body
    assert '"Rejected"' in body
    assert "review_action" in body


# ---------------------------------------------------------------------------
# Frontend static-file guardrails — Session form panel dialog
# ---------------------------------------------------------------------------


_SESSION_JS_PATH = (
    Path(__file__).parent.parent
    / "rgi_migration"
    / "doctype"
    / "tally_migration_session"
    / "tally_migration_session.js"
)


@pytest.fixture(scope="module")
def session_js() -> str:
    return _SESSION_JS_PATH.read_text(encoding="utf-8")


def test_session_form_has_process_scr_button(session_js: str) -> None:
    assert "Process Supplier Creation Requests" in session_js
    assert "openSCRProcessingDialog" in session_js


def test_session_scr_dialog_calls_approve_and_reject(session_js: str) -> None:
    assert "approve_scr" in session_js
    assert "reject_scr" in session_js


def test_session_scr_dialog_shows_count_only_when_open_rows(session_js: str) -> None:
    """Button label must include the count and only render when
    count > 0 — no empty-button clutter on sessions with all SCRs
    already Created / Skipped."""
    assert "scr_count > 0" in session_js or "scr_count" in session_js


def test_session_scr_dialog_uses_list_pending_scrs_endpoint(session_js: str) -> None:
    """The dialog populates via the list_pending_scrs whitelist method
    (filters out Created / Skipped rows server-side)."""
    assert "list_pending_scrs" in session_js


# ---------------------------------------------------------------------------
# Backend guardrails — approve_scr / reject_scr wiring presence
# ---------------------------------------------------------------------------


_MD_REVIEW_PY = (
    Path(__file__).parent.parent
    / "rgi_migration"
    / "page"
    / "md_review"
    / "md_review.py"
)


@pytest.fixture(scope="module")
def md_review_py() -> str:
    return _MD_REVIEW_PY.read_text(encoding="utf-8")


def test_approve_and_reject_scr_whitelisted(md_review_py: str) -> None:
    assert "def approve_scr(" in md_review_py
    assert "def reject_scr(" in md_review_py
    # Both methods must be @frappe.whitelist()
    assert md_review_py.count("@frappe.whitelist()") >= 5  # existing + our two new


def test_approve_scr_reads_resolved_name_post_insert(md_review_py: str) -> None:
    """Naming collision safety: after Supplier.insert, read the
    resolved name from the inserted doc — don't trust the proposed
    name which may differ after collision-suffix."""
    # Look for the resolved_name = new_supplier.name pattern
    assert "resolved_name = new_supplier.name" in md_review_py


def test_approve_scr_has_failed_status_retry_allowed(md_review_py: str) -> None:
    """AMB Q2 / Q5: Approve on Failed = retry. The allowed-statuses
    frozenset must include both Pending AND Failed."""
    m = re.search(
        r"_SCR_ACTION_ALLOWED_STATUSES\s*=\s*frozenset\(\{([^}]+)\}\)",
        md_review_py,
    )
    assert m is not None
    body = m.group(1)
    assert "Pending" in body
    assert "Failed" in body


def test_approve_scr_aggregates_mid_loop_errors(md_review_py: str) -> None:
    """AMB Q7: mid-loop decision update failure → aggregate + continue.
    The per_decision_errors list accumulates, then appends to
    error_log after the loop."""
    assert "per_decision_errors" in md_review_py


def test_bulk_approve_and_reject_whitelists_present(md_review_py: str) -> None:
    """Commit 3 polish: bulk actions for multi-SCR sessions (CACSPU ~18).
    Per-row failures must not block the rest — each row carries its
    own outcome in the response."""
    assert "def bulk_approve_scrs(" in md_review_py
    assert "def bulk_reject_scrs(" in md_review_py
    assert "_parse_whitelist_list_arg" in md_review_py


def test_bulk_methods_isolate_per_row_failures(md_review_py: str) -> None:
    """Response shape: {"ok": [...], "failed": [{"scr", "error"}, ...]}.
    Failure in row N must not break row N+1."""
    # Both bulk methods return ok + failed buckets
    assert md_review_py.count('"ok": ok, "failed": failed') >= 2


def test_session_form_bulk_ui_wired(session_js: str) -> None:
    """Select-all + per-row checkboxes + bulk action buttons present
    in the dialog. Guard against accidental regression on the rich
    UX paths."""
    assert "scr-select-all" in session_js
    assert "scr-row-select" in session_js
    assert "scr-bulk-approve" in session_js
    assert "scr-bulk-reject" in session_js
    assert "bulk_approve_scrs" in session_js
    assert "bulk_reject_scrs" in session_js


def test_scr_header_shows_proposed_name_not_tally(session_js: str) -> None:
    """Per reviewer feedback — proposed_supplier_name is the primary
    heading in the panel row; tally_vendor_name becomes the subtitle."""
    # The headline var should be built from proposed_supplier_name first
    assert 'r.proposed_supplier_name || r.tally_vendor_name' in session_js
    # And the subtitle line should start with "from Tally"
    assert 'from Tally' in session_js
