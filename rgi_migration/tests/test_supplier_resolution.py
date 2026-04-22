"""Tests for Item 3 Commit 1b pure-core supplier resolution.

Covers:

1. ``build_supplier_autocomplete_results`` — formatted [name, description]
   pairs with supplier_group subtitle (or "(no group)" fallback).
2. ``apply_supplier_resolution`` — map-to-existing save: writes
   final_supplier, tier=tier1_supplier_exact, review_action=Approved,
   reviewer_notes (via shared chronology-header helper).
3. ``_apply_chronology_header`` — shared helper extracted from
   ``apply_decision_save`` in Commit 1b. Preserves existing account-save
   behaviour; supplier-save reuses the same rule.
4. Static-file guardrail for the JS SupplierResolutionDialog class
   presence and constants.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from rgi_migration.rgi_migration.page.md_review.query import (
    SUPPLIER_SAVE_FIELDS,
    _apply_chronology_header,
    apply_decision_save,
    apply_supplier_resolution,
    build_supplier_autocomplete_results,
)


# ---------------------------------------------------------------------------
# build_supplier_autocomplete_results
# ---------------------------------------------------------------------------


def test_autocomplete_formats_name_and_group() -> None:
    suppliers = [
        {"name": "Nilesh Traders", "supplier_name": "Nilesh Traders",
         "supplier_group": "Services"},
        {"name": "Gulab Hardware", "supplier_name": "Gulab Hardware",
         "supplier_group": "Hardware"},
    ]
    result = build_supplier_autocomplete_results(suppliers)
    assert result == [
        ["Nilesh Traders", "Group: Services"],
        ["Gulab Hardware", "Group: Hardware"],
    ]


def test_autocomplete_missing_group_falls_back() -> None:
    suppliers = [{"name": "Amit", "supplier_name": "Amit", "supplier_group": ""}]
    assert build_supplier_autocomplete_results(suppliers) == [
        ["Amit", "(no group)"]
    ]


def test_autocomplete_none_group_falls_back() -> None:
    suppliers = [{"name": "Abhi Tria", "supplier_name": "Abhi Tria",
                  "supplier_group": None}]
    assert build_supplier_autocomplete_results(suppliers) == [
        ["Abhi Tria", "(no group)"]
    ]


def test_autocomplete_empty_input_returns_empty() -> None:
    assert build_supplier_autocomplete_results([]) == []


def test_autocomplete_skips_rows_without_name() -> None:
    """Defensive parity with build_account_autocomplete_results — a
    name-less row would crash the Frappe autocomplete renderer."""
    suppliers = [
        {"name": "", "supplier_name": "Nameless", "supplier_group": "Services"},
        {"name": "Valid", "supplier_name": "Valid", "supplier_group": "Services"},
    ]
    assert build_supplier_autocomplete_results(suppliers) == [
        ["Valid", "Group: Services"]
    ]


# ---------------------------------------------------------------------------
# apply_supplier_resolution
# ---------------------------------------------------------------------------


def _current_doc(**overrides) -> dict:
    base = {
        "tally_name": "Aarna Solutions",
        "tally_id": "VA0290",
        "tier": "pending_supplier_creation",
        "review_action": "Pending Supplier Creation",
        "final_supplier": None,
        "reviewer_notes": "",
    }
    base.update(overrides)
    return base


def test_supplier_resolution_writes_all_four_fields() -> None:
    result = apply_supplier_resolution(
        current=_current_doc(),
        final_supplier="Nilesh Traders",
        reviewer_notes_input="Reviewer picked best match from picker",
        session_user="aditya@dux.in",
        now_str="2026-04-22 15:30",
    )
    assert set(result.keys()) == set(SUPPLIER_SAVE_FIELDS)
    assert result["final_supplier"] == "Nilesh Traders"
    assert result["tier"] == "tier1_supplier_exact"
    assert result["review_action"] == "Approved"
    # First-note case — no chronology header since stored was empty
    assert result["reviewer_notes"] == "Reviewer picked best match from picker"


def test_supplier_resolution_normalises_empty_final_supplier() -> None:
    """Defensive: if somehow an empty final_supplier reaches the pure
    function, normalise to None (Frappe wrapper guards this upstream
    too). Parallel to account-save's final_account normalisation."""
    result = apply_supplier_resolution(
        current=_current_doc(),
        final_supplier="",
        reviewer_notes_input="",
        session_user="aditya@dux.in",
        now_str="2026-04-22 15:30",
    )
    assert result["final_supplier"] is None


def test_supplier_resolution_chronology_appends_to_prior_notes() -> None:
    """Reviewer notes follow the same chronology-header rule as
    account-save: new content gets a [user, timestamp] header and
    sits above the prior notes, separated by a blank line."""
    result = apply_supplier_resolution(
        current=_current_doc(
            reviewer_notes="Prior reviewer flagged for investigation."
        ),
        final_supplier="Nilesh Traders",
        reviewer_notes_input="Confirmed — they split their invoicing accounts.",
        session_user="aditya@dux.in",
        now_str="2026-04-22 15:30",
    )
    notes = result["reviewer_notes"]
    assert "[aditya@dux.in, 2026-04-22 15:30]" in notes
    assert "Confirmed" in notes
    assert "Prior reviewer flagged" in notes
    # Header precedes the old note
    assert notes.index("Confirmed") < notes.index("Prior reviewer flagged")


def test_supplier_resolution_empty_new_input_preserves_stored_notes() -> None:
    result = apply_supplier_resolution(
        current=_current_doc(reviewer_notes="Existing notes"),
        final_supplier="Nilesh Traders",
        reviewer_notes_input="",
        session_user="aditya@dux.in",
        now_str="2026-04-22 15:30",
    )
    assert result["reviewer_notes"] == "Existing notes"


# ---------------------------------------------------------------------------
# _apply_chronology_header — shared helper (regression guard against
# the Commit 1b extraction changing behaviour)
# ---------------------------------------------------------------------------


def test_chronology_helper_first_note_verbatim() -> None:
    out = _apply_chronology_header(
        stored_notes="",
        new_input="first note",
        session_user="u@x",
        now_str="2026-04-22 10:00",
    )
    assert out == "first note"


def test_chronology_helper_empty_input_preserves_stored() -> None:
    out = _apply_chronology_header(
        stored_notes="already there",
        new_input="",
        session_user="u@x",
        now_str="2026-04-22 10:00",
    )
    assert out == "already there"


def test_chronology_helper_idempotent_same_content() -> None:
    """Reviewer re-opens row, submits without editing — no spurious
    chronology header is added."""
    out = _apply_chronology_header(
        stored_notes="existing line",
        new_input="existing line",
        session_user="u@x",
        now_str="2026-04-22 10:00",
    )
    assert out == "existing line"


def test_chronology_helper_prepends_header_on_divergence() -> None:
    out = _apply_chronology_header(
        stored_notes="old note",
        new_input="new note",
        session_user="u@x",
        now_str="2026-04-22 10:00",
    )
    assert out.startswith("[u@x, 2026-04-22 10:00] new note")
    assert out.endswith("old note")


# ---------------------------------------------------------------------------
# Back-compat guard: apply_decision_save still emits the same shape
# post-extraction. Tests in test_save_decision.py cover the detailed
# semantics; this is a quick sanity anchor.
# ---------------------------------------------------------------------------


def test_apply_decision_save_still_produces_save_decision_fields() -> None:
    """After extracting _apply_chronology_header, the account save must
    still return a dict keyed on SAVE_DECISION_FIELDS exactly."""
    from rgi_migration.rgi_migration.page.md_review.query import SAVE_DECISION_FIELDS

    result = apply_decision_save(
        current={
            "opening_dr": 1000.0,
            "opening_cr": 0.0,
            "reviewer_notes": "",
        },
        review_action="Approved",
        final_account="Cash - CACSPU",
        reviewer_notes_input="test note",
        session_user="u@x",
        now_str="2026-04-22 10:00",
    )
    assert set(result.keys()) == set(SAVE_DECISION_FIELDS)
    assert result["reviewer_notes"] == "test note"  # first-note verbatim


# ---------------------------------------------------------------------------
# Static-file guardrail — JS dialog class + constants present
# ---------------------------------------------------------------------------


_JS_PATH = (
    Path(__file__).parent.parent
    / "rgi_migration"
    / "page"
    / "md_review"
    / "md_review.js"
)


@pytest.fixture(scope="module")
def js_source() -> str:
    return _JS_PATH.read_text(encoding="utf-8")


def test_supplier_resolution_dialog_class_exists(js_source: str) -> None:
    assert "class SupplierResolutionDialog" in js_source


def test_dialog_wired_to_request_creation_and_approve(js_source: str) -> None:
    """Both a-shortcut (_onApprove) and c-shortcut (_onRequestCreation)
    must route supplier rows through the dialog per Sub-AMB-8/9."""
    # _onApprove branches on _isVendorRow() before the account validation
    approve_match = re.search(
        r"async\s+_onApprove\s*\(\s*\)[^{]*\{[^}]*this\._isVendorRow\(\)[^}]*"
        r"this\._openSupplierResolutionDialog\(\)",
        js_source,
        re.DOTALL,
    )
    assert approve_match, "_onApprove does not open the dialog on supplier rows"

    # _onRequestCreation same pattern
    request_match = re.search(
        r"async\s+_onRequestCreation\s*\(\s*\)[^{]*\{[^}]*this\._isVendorRow\(\)[^}]*"
        r"this\._openSupplierResolutionDialog\(\)",
        js_source,
        re.DOTALL,
    )
    assert request_match, "_onRequestCreation does not open the dialog on supplier rows"


def test_dialog_save_calls_save_supplier_resolution(js_source: str) -> None:
    assert "save_supplier_resolution" in js_source


def test_create_new_radio_is_disabled_per_commit_1b_scope(js_source: str) -> None:
    """Sub-AMB-8 β: Create-new path is visible but submit is blocked
    in Commit 1b. The dialog's _update_submit_state must disable the
    primary button when Create-new is selected."""
    assert "Create new supplier (ships in next commit)" in js_source
    assert "Create-new workflow ships in next commit" in js_source


def test_button_text_polymorphism_by_row_type(js_source: str) -> None:
    """action-request-creation-btn text swaps between 'Resolve Supplier'
    (supplier rows) and 'Request Creation' (account rows)."""
    assert 'is_vendor ? __("Resolve Supplier") : __("Request Creation")' in js_source
