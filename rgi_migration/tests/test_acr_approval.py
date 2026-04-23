"""Tests for Item 4 Commit 3 — ACR approval/rejection + panel + reset sweep.

Pure-core coverage for the helpers used by ``approve_acr`` /
``reject_acr``:

1. ``build_account_doc_payload`` — shape + conditional account_type
   inclusion (AMB C3-13).
2. ``apply_acr_approval_to_decision`` — tier lift + final_account +
   review_action transitions (AMB C3-10).
3. ``apply_acr_rejection_to_decision`` — review_action flip +
   final_account clear, tier unchanged.
4. Static-file guardrails for the Session-form panel dialog +
   reset_parse sweep wiring.

The Frappe wrappers ``approve_acr``, ``reject_acr``,
``list_pending_acrs``, and ``reset_parse``'s sweep are ORM-bound;
exercised via Phase C bench smoke. Same philosophy as
``test_acr_creation.py`` and ``test_scr_approval.py``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from rgi_migration.rgi_migration.page.md_review.query import (
    apply_acr_approval_to_decision,
    apply_acr_rejection_to_decision,
    build_account_doc_payload,
)


# ---------------------------------------------------------------------------
# build_account_doc_payload — shape + conditional key
# ---------------------------------------------------------------------------


def _acr_row(**overrides) -> dict:
    """Default ACR child-row dict (Pending state, unmapped-path pre-fills
    for a bank account)."""
    base = {
        "status": "Pending",
        "proposed_account_name": "Bank of Maharashtra NSS Camp",
        "proposed_parent": "Bank Accounts - CACSPU",
        "proposed_root_type": "Asset",
        "proposed_is_group": 0,
        "account_type": "Bank",
        "reason": "Reviewer approved creation",
        "reviewer_notes": "From Tally migration smoke",
        "source_decisions": "MD-2026-00001",
    }
    base.update(overrides)
    return base


def test_account_payload_happy_path_with_account_type() -> None:
    out = build_account_doc_payload(
        acr_row=_acr_row(),
        company="GHR CACS Pune",
    )
    assert out == {
        "doctype": "Account",
        "account_name": "Bank of Maharashtra NSS Camp",
        "parent_account": "Bank Accounts - CACSPU",
        "root_type": "Asset",
        "is_group": 0,
        "company": "GHR CACS Pune",
        "account_type": "Bank",
    }


def test_account_payload_omits_account_type_when_empty() -> None:
    """AMB C3-13: empty account_type is dropped from the payload
    entirely rather than passed as ''. Frappe's Account validators
    distinguish absent-key from empty-string in some branches
    (default-detection, e.g.). Conditional inclusion keeps the payload
    clean."""
    out = build_account_doc_payload(
        acr_row=_acr_row(account_type=""),
        company="GHR CACS Pune",
    )
    assert "account_type" not in out
    # All other fields still present
    assert out["account_name"] == "Bank of Maharashtra NSS Camp"
    assert out["company"] == "GHR CACS Pune"


def test_account_payload_omits_account_type_when_whitespace_or_none() -> None:
    """Whitespace-only account_type is treated as empty; None ditto."""
    out1 = build_account_doc_payload(
        acr_row=_acr_row(account_type="   "),
        company="GHR CACS Pune",
    )
    assert "account_type" not in out1

    out2 = build_account_doc_payload(
        acr_row=_acr_row(account_type=None),
        company="GHR CACS Pune",
    )
    assert "account_type" not in out2


def test_account_payload_strips_whitespace_on_name_and_parent() -> None:
    """Reviewer may paste names with trailing whitespace. Normalise
    so Frappe's Link-exists check matches on the canonical form."""
    out = build_account_doc_payload(
        acr_row=_acr_row(
            proposed_account_name="  Padded Name  ",
            proposed_parent="  Parent - CACSPU ",
        ),
        company="GHR CACS Pune",
    )
    assert out["account_name"] == "Padded Name"
    assert out["parent_account"] == "Parent - CACSPU"


def test_account_payload_is_group_coerced_to_int() -> None:
    """Frappe stores Check fields as 0/1 ints. If the ACR row's
    proposed_is_group came back as bool True or None, coerce to int."""
    out_true = build_account_doc_payload(
        acr_row=_acr_row(proposed_is_group=True),
        company="GHR CACS Pune",
    )
    assert out_true["is_group"] == 1

    out_none = build_account_doc_payload(
        acr_row=_acr_row(proposed_is_group=None),
        company="GHR CACS Pune",
    )
    assert out_none["is_group"] == 0


# ---------------------------------------------------------------------------
# apply_acr_approval_to_decision
# ---------------------------------------------------------------------------


def test_apply_acr_approval_tier_lifts_to_tier1_exact() -> None:
    """AMB C3-10: approved ACRs transition source decision to
    tier1_exact regardless of origin (pending_account_creation OR
    unmapped). Post-approval, the decision points at a real Account —
    tier honestly describes the exact-name match in the COA."""
    out = apply_acr_approval_to_decision(
        resolved_account_name="Bank of Maharashtra NSS Camp - CACSPU",
    )
    assert out == {
        "final_account": "Bank of Maharashtra NSS Camp - CACSPU",
        "tier": "tier1_exact",
        "review_action": "Approved",
    }


def test_apply_acr_approval_uses_resolved_not_proposed_name() -> None:
    """If Frappe auto-suffixed the name on collision, the resolved name
    is what lands on the decision — NEVER the pre-insert proposed
    name. Exercised via the resolved_account_name arg carrying the
    suffixed form."""
    out = apply_acr_approval_to_decision(
        resolved_account_name="Cash - CACSPU 1",  # collision-suffixed
    )
    assert out["final_account"] == "Cash - CACSPU 1"


# ---------------------------------------------------------------------------
# apply_acr_rejection_to_decision
# ---------------------------------------------------------------------------


def test_apply_acr_rejection_clears_final_account_preserves_tier() -> None:
    """Rejection writes review_action=Rejected and clears any
    speculative final_account. tier is NOT in the output — caller
    doesn't touch mapper-authoritative tier on rejection
    (generator silent-skip covers the downstream)."""
    out = apply_acr_rejection_to_decision()
    assert out == {
        "review_action": "Rejected",
        "final_account": None,
    }
    # No 'tier' key — confirms mapper-authoritative tier is untouched.
    assert "tier" not in out


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


def test_acr_panel_button_registered(session_js: str) -> None:
    """refresh() must register an 'Process Account Creation Requests'
    custom button when the session has any Pending / Failed ACR rows.
    Parallel to the SCR button."""
    assert "Process Account Creation Requests" in session_js
    assert "openACRProcessingDialog" in session_js


def test_acr_panel_dispatches_to_whitelists(session_js: str) -> None:
    """The panel's Approve/Reject actions must call the new backend
    whitelists. Guards against a future refactor that forgets to
    rename after a method move."""
    assert "list_pending_acrs" in session_js
    assert "approve_acr" in session_js
    assert "reject_acr" in session_js


def test_acr_panel_classes_prefixed_distinct_from_scr(session_js: str) -> None:
    """SCR panel uses .scr-* class prefix; ACR must use .acr-* to
    avoid style collisions if both panels are open simultaneously
    (rare but possible if reviewer switches between them quickly)."""
    # Spot-check a few class-level identifiers
    assert ".acr-panel" in session_js
    assert ".acr-row" in session_js
    assert ".acr-btn-approve" in session_js
    assert ".acr-btn-reject" in session_js


def test_acr_reject_copy_mentions_opening_je(session_js: str) -> None:
    """Aditya's Phase A refinement: the Reject confirmation copy
    should mention "opening_je" specifically (not "generators" as a
    vague plural) so the reviewer knows which downstream pipeline is
    affected.

    Scope the search to the reject handler body specifically — the
    confirm() call is multi-line + string-concatenated, so we capture
    the whole handler span and string-search for 'opening_je' inside.
    """
    m = re.search(
        r"acr-btn-reject[\s\S]*?doACRAction\(frm,\s*row_name,\s*[\"']reject_acr[\"']",
        session_js,
    )
    assert m is not None, "acr-btn-reject handler body not found"
    assert "opening_je" in m.group(0), (
        "Reject confirmation copy must name the specific generator "
        "(opening_je) — generic 'generators will silent-skip' is "
        "too vague per Phase A refinement."
    )


# ---------------------------------------------------------------------------
# Reset Parse sweep — Session Python source guardrail
# ---------------------------------------------------------------------------


_SESSION_PY_PATH = (
    Path(__file__).parent.parent
    / "rgi_migration"
    / "doctype"
    / "tally_migration_session"
    / "tally_migration_session.py"
)


@pytest.fixture(scope="module")
def session_py() -> str:
    return _SESSION_PY_PATH.read_text(encoding="utf-8")


def test_reset_parse_sweeps_both_child_tables(session_py: str) -> None:
    """AMB-8 combined fix: reset_parse must clear BOTH
    supplier_creation_requests AND account_creation_requests. Guards
    against a future edit that accidentally drops one of the sweeps."""
    assert 'session.set("supplier_creation_requests", [])' in session_py
    assert 'session.set("account_creation_requests", [])' in session_py


def test_reset_parse_reports_sweep_counts(session_py: str) -> None:
    """Return shape extended to include scr_swept + acr_swept so the
    frontend toast (and any future tests) can surface what happened.
    Regresses if someone reverts the return-dict extension."""
    assert '"scr_swept": scr_swept' in session_py
    assert '"acr_swept": acr_swept' in session_py


# ---------------------------------------------------------------------------
# md_review.py refactor guardrail — error_log helper + status constants
# ---------------------------------------------------------------------------


_MD_PY_PATH = (
    Path(__file__).parent.parent
    / "rgi_migration"
    / "page"
    / "md_review"
    / "md_review.py"
)


@pytest.fixture(scope="module")
def md_py() -> str:
    return _MD_PY_PATH.read_text(encoding="utf-8")


def test_generic_error_log_helper_exists_with_scr_alias(md_py: str) -> None:
    """Refactor: _append_scr_error_log -> _append_error_log_block.
    Alias preserved for backward compat with extant SCR call sites."""
    assert "def _append_error_log_block(" in md_py
    assert "_append_scr_error_log = _append_error_log_block" in md_py


def test_approval_status_constants_hoisted(md_py: str) -> None:
    """_APPROVAL_ALLOWED_STATUSES + _APPROVAL_TERMINAL_STATUSES replace
    the SCR-specific names; SCR aliases remain for backward compat.
    Both SCR and ACR approval paths key off the hoisted names."""
    assert "_APPROVAL_ALLOWED_STATUSES" in md_py
    assert "_APPROVAL_TERMINAL_STATUSES" in md_py
    # Aliases kept
    assert "_SCR_ACTION_ALLOWED_STATUSES = _APPROVAL_ALLOWED_STATUSES" in md_py
    assert "_SCR_ACTION_TERMINAL_STATUSES = _APPROVAL_TERMINAL_STATUSES" in md_py


def test_approve_acr_whitelist_exists(md_py: str) -> None:
    """Entry-point smoke: the new whitelist methods are defined."""
    assert "def approve_acr(" in md_py
    assert "def reject_acr(" in md_py
    assert "def list_pending_acrs(" in md_py
    # Idempotency guard presence
    assert "_APPROVAL_TERMINAL_STATUSES" in md_py


# ---------------------------------------------------------------------------
# Bulk ACR approve/reject — Phase D iteration (parallel to SCR bulk)
# ---------------------------------------------------------------------------


def test_bulk_acr_whitelists_exist(md_py: str) -> None:
    """Phase D reviewer feedback: ACR panel needs Select-All + bulk
    actions on par with SCR. bulk_approve_acrs / bulk_reject_acrs are
    the backend wrappers."""
    assert "def bulk_approve_acrs(" in md_py
    assert "def bulk_reject_acrs(" in md_py
    # They must use the shared _parse_whitelist_list_arg helper to
    # handle both JSON-string (HTTP) and list (pytest) input shapes.
    acr_bulk_block = md_py[md_py.index("def bulk_approve_acrs("):]
    assert "_parse_whitelist_list_arg(acr_row_names)" in acr_bulk_block


def test_acr_panel_has_bulk_bar_and_checkboxes(session_js: str) -> None:
    """Panel dialog must render a bulk-action bar (Select All +
    Approve/Reject Selected buttons) and per-row checkboxes. Mirror
    of the SCR panel's bulk UI."""
    # Bulk bar structural pieces
    assert ".acr-bulk-bar" in session_js
    assert ".acr-select-all" in session_js
    assert ".acr-bulk-approve" in session_js
    assert ".acr-bulk-reject" in session_js
    # Per-row checkbox
    assert ".acr-row-select" in session_js
    # Bulk RPC dispatch
    assert "bulk_approve_acrs" in session_js
    assert "bulk_reject_acrs" in session_js
    assert "doBulkACRAction" in session_js


# ---------------------------------------------------------------------------
# Account / Supplier delete-cascade hooks — Phase D iteration
# ---------------------------------------------------------------------------


_HOOKS_PATH = (
    Path(__file__).parent.parent
    / "hooks.py"
)

_HOOKS_IMPL_PATH = (
    Path(__file__).parent.parent
    / "hooks_impl.py"
)


@pytest.fixture(scope="module")
def hooks_src() -> str:
    return _HOOKS_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def hooks_impl_src() -> str:
    return _HOOKS_IMPL_PATH.read_text(encoding="utf-8")


def test_hooks_registers_account_supplier_on_trash(hooks_src: str) -> None:
    """Phase D reviewer feedback: Account / Supplier deletion must not
    be blocked by legacy Mapping Decision / ACR / SCR Link references.
    hooks.py registers on_trash handlers that nullify our Links
    before Frappe's check_if_doc_is_linked runs."""
    # doc_events declaration present
    assert "doc_events" in hooks_src
    # Account + Supplier both hooked
    assert '"Account"' in hooks_src
    assert '"Supplier"' in hooks_src
    # on_trash specifically (not on_update / before_delete — on_trash
    # is the one Frappe runs BEFORE check_if_doc_is_linked)
    assert '"on_trash"' in hooks_src
    assert "rgi_migration.hooks_impl.clear_account_links" in hooks_src
    assert "rgi_migration.hooks_impl.clear_supplier_links" in hooks_src


def test_hooks_impl_cascades_revert_on_account_delete(hooks_impl_src: str) -> None:
    """Option D cascade-revert: clear_account_links deletes ACR rows +
    reverts Mapping Decisions back to Pending with tier restored.
    Guard the structural shape so a future simplification that
    accidentally drops the revert / cascade behavior is caught."""
    assert "def clear_account_links(" in hooks_impl_src
    # Fields that must be handled
    assert '"proposed_account"' in hooks_impl_src
    assert '"final_account"' in hooks_impl_src
    assert '"created_account"' in hooks_impl_src
    # The function targets Account + ACR + Session DocTypes
    assert '"Mapping Decision"' in hooks_impl_src
    assert '"Account Creation Request"' in hooks_impl_src
    assert '"Tally Migration Session"' in hooks_impl_src
    # Cascade-revert markers: ACR row deletion + review_action revert
    # + tier restoration via new_account_name heuristic
    assert "account_creation_requests" in hooks_impl_src
    assert '"Pending"' in hooks_impl_src
    assert '"pending_account_creation"' in hooks_impl_src
    assert '"unmapped"' in hooks_impl_src
    assert "new_account_name" in hooks_impl_src
    # Chronology note helper invoked
    assert "_append_chronology_note" in hooks_impl_src


def test_hooks_impl_cascades_revert_on_supplier_delete(hooks_impl_src: str) -> None:
    """Parallel guard for clear_supplier_links. Cascade-revert behavior
    mirrors the Account side: delete SCR rows, revert Mapping
    Decisions to Pending with supplier-tier restored."""
    assert "def clear_supplier_links(" in hooks_impl_src
    assert '"proposed_supplier"' in hooks_impl_src
    assert '"final_supplier"' in hooks_impl_src
    assert '"created_supplier"' in hooks_impl_src
    assert '"Supplier Creation Request"' in hooks_impl_src
    assert "supplier_creation_requests" in hooks_impl_src
    # Supplier tier revert markers
    assert '"pending_supplier_creation"' in hooks_impl_src
    assert "new_supplier_name" in hooks_impl_src


def test_hooks_impl_approved_like_actions_scope(hooks_impl_src: str) -> None:
    """Only approved-like review_actions should flip to Pending on
    cascade-revert. Rejected/Deferred/Skipped must NOT flip —
    those are reviewer-owned terminal intents that don't depend on
    a live Link target. Guards the _APPROVED_LIKE_ACTIONS set."""
    assert "_APPROVED_LIKE_ACTIONS" in hooks_impl_src
    # Approved-family values that DO flip
    assert '"Approved"' in hooks_impl_src
    assert '"Manual Override"' in hooks_impl_src
    assert '"Account Creation Requested"' in hooks_impl_src
    assert '"Supplier Creation Requested"' in hooks_impl_src


def test_hooks_impl_lazy_imports_frappe(hooks_impl_src: str) -> None:
    """Module-level ``import frappe`` can bite during app-install when
    the Frappe namespace is warming up. hooks_impl imports frappe
    INSIDE each function body instead.

    Check: no line at module scope (4+ leading spaces doesn't count —
    that's inside a function body) matches ``^import frappe``. Using
    line-start anchors avoids false-positives from the module
    docstring mentioning the name.
    """
    top_level = hooks_impl_src.split("\ndef ", 1)[0]
    # Scan line-by-line so docstring text mentioning "import frappe"
    # doesn't trip the guard — only actual module-scope import statements
    # (unindented, first non-comment token = "import") should fail.
    for line in top_level.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("import frappe") and not line.startswith((" ", "\t")):
            pytest.fail(
                f"Module-level 'import frappe' found in hooks_impl — "
                f"should be inside each function body instead:\n  {line}"
            )
    # And each function body does import it — count actual import
    # statements (not docstring mentions) by requiring leading
    # indent.
    function_body_imports = sum(
        1 for line in hooks_impl_src.splitlines()
        if line.lstrip().startswith("import frappe")
        and line.startswith((" ", "\t"))
    )
    assert function_body_imports >= 2, (
        f"Expected >=2 function-body 'import frappe' statements; "
        f"found {function_body_imports}."
    )
