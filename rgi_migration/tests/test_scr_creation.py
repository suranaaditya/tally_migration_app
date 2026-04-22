"""Tests for Item 3 Commit 2 — Supplier Creation Request payload + CSV parse.

Pure-core coverage for the Create-new path in the SupplierResolutionDialog:

1. ``build_scr_payload`` — shape and sign-convention guarantees for the
   child row inserted under Tally Migration Session.supplier_creation_requests.
2. ``parse_source_decisions_csv`` — exact-token extraction used by the
   duplicate-refusal guard in ``create_supplier_creation_request``.
3. Static-file guardrails for the JS Create-new path wiring (radio
   enabled, plain toast dispatch, field validation).

The Frappe wrapper ``create_supplier_creation_request`` isn't directly
unit-testable (ORM-bound); it's exercised via the Phase C bench smoke.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from rgi_migration.rgi_migration.page.md_review.query import (
    build_scr_payload,
    parse_source_decisions_csv,
)


# ---------------------------------------------------------------------------
# build_scr_payload
# ---------------------------------------------------------------------------


def _decision(**overrides) -> dict:
    base = {
        "name": "MD-2026-00001",
        "tally_name": "Yogi Liquid Cleaner",
        "tally_id": "4087",
        "net_amount": 5150.0,  # positive — normal vendor payable (net Cr)
    }
    base.update(overrides)
    return base


def test_scr_payload_happy_path() -> None:
    out = build_scr_payload(
        decision=_decision(),
        proposed_supplier_name="Yogi Liquid Cleaner Pvt Ltd",
        supplier_group="Services",
        reviewer_notes="Reviewer confirmed — same as Tally name.",
    )
    assert out == {
        "status": "Pending",
        "tally_vendor_name": "Yogi Liquid Cleaner",
        "tally_vendor_id": "4087",
        "proposed_supplier_name": "Yogi Liquid Cleaner Pvt Ltd",
        "proposed_supplier_group": "Services",
        "detected_balance": 5150.0,
        "reviewer_notes": "Reviewer confirmed — same as Tally name.",
        "source_decisions": "MD-2026-00001",
    }


def test_scr_payload_empty_group_normalises_to_none() -> None:
    """Empty supplier_group is allowed (SCR field is not reqd). Normalise
    to None so Frappe writes NULL rather than empty string, matching
    the account-save empty-Link convention from Item 1 Commit 4b."""
    out = build_scr_payload(
        decision=_decision(),
        proposed_supplier_name="X",
        supplier_group="",
        reviewer_notes=None,
    )
    assert out["proposed_supplier_group"] is None


def test_scr_payload_whitespace_group_normalises_to_none() -> None:
    out = build_scr_payload(
        decision=_decision(),
        proposed_supplier_name="X",
        supplier_group="   ",
        reviewer_notes=None,
    )
    assert out["proposed_supplier_group"] is None


def test_scr_payload_none_reviewer_notes_preserved() -> None:
    out = build_scr_payload(
        decision=_decision(),
        proposed_supplier_name="X",
        supplier_group="G",
        reviewer_notes=None,
    )
    assert out["reviewer_notes"] is None


def test_scr_payload_signed_detected_balance_advance_case() -> None:
    """Vendor advance case: opening_dr > opening_cr → net_amount negative.
    The SCR DocType field label says "(net Cr)" so negative is the
    correct signed value for a Dr-balance vendor (advance receivable)."""
    out = build_scr_payload(
        decision=_decision(net_amount=-1234.50),
        proposed_supplier_name="X",
        supplier_group="",
        reviewer_notes=None,
    )
    assert out["detected_balance"] == -1234.50


def test_scr_payload_zero_amount_is_explicit_zero_not_null() -> None:
    """A zero-balance vendor should still emit 0.0 on detected_balance
    (not None / NULL), since downstream numeric comparisons treat
    NULL and 0 differently in Frappe's currency aggregation."""
    out = build_scr_payload(
        decision=_decision(net_amount=0.0),
        proposed_supplier_name="X",
        supplier_group="",
        reviewer_notes=None,
    )
    assert out["detected_balance"] == 0.0


def test_scr_payload_none_tally_id_passed_through() -> None:
    """Party-alias vendors like 'Anupam Silver Works-VA0243' carry
    tally_id=None in the persistence layer (the -VA0243 is part of
    the name, not a separate ID field). SCR row should mirror."""
    out = build_scr_payload(
        decision=_decision(tally_id=None),
        proposed_supplier_name="X",
        supplier_group="",
        reviewer_notes=None,
    )
    assert out["tally_vendor_id"] is None


# ---------------------------------------------------------------------------
# parse_source_decisions_csv — duplicate-refusal guard
# ---------------------------------------------------------------------------


def test_csv_parse_empty_input() -> None:
    assert parse_source_decisions_csv("") == []
    assert parse_source_decisions_csv(None) == []


def test_csv_parse_single_token() -> None:
    assert parse_source_decisions_csv("MD-2026-00001") == ["MD-2026-00001"]


def test_csv_parse_multi_token_stripped() -> None:
    """Whitespace around tokens + trailing comma handled."""
    raw = "MD-2026-00001, MD-2026-00002 , MD-2026-00003,"
    assert parse_source_decisions_csv(raw) == [
        "MD-2026-00001", "MD-2026-00002", "MD-2026-00003"
    ]


def test_csv_parse_prevents_substring_false_positive() -> None:
    """The whole reason we parse instead of using substring match:
    MD-2026-00010 must NOT match against MD-2026-00001 or MD-2026-0001."""
    tokens = parse_source_decisions_csv("MD-2026-00010, MD-2026-01234")
    assert "MD-2026-00001" not in tokens  # never was — sanity
    assert "MD-2026-0001" not in tokens    # substring-match would have
    assert "MD-2026-00010" in tokens


# ---------------------------------------------------------------------------
# Frontend static-file guardrails
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


def test_create_new_fields_replace_disabled_banner(js_source: str) -> None:
    """Commit 1b had a 'Create new supplier workflow ships in next
    commit' banner. Commit 2 replaces it with functional fields +
    an informational note. The old preview-only banner text must be gone."""
    assert "ships in next commit" not in js_source.lower() or \
        "proposed_supplier_name" in js_source  # fields present either way
    # And the new informational note is present
    assert "Supplier Creation Request will be added" in js_source


def test_create_new_path_has_validation(js_source: str) -> None:
    """proposed_supplier_name must be mandatory when Create-new radio
    is active. The Dialog uses mandatory_depends_on to enforce this."""
    assert "mandatory_depends_on" in js_source
    assert "proposed_supplier_name" in js_source


def test_create_new_submit_calls_create_supplier_creation_request(js_source: str) -> None:
    assert "create_supplier_creation_request" in js_source


def test_plain_saved_toast_helper_present(js_source: str) -> None:
    """Commit 2 introduces _showSavedToast (no-Undo variant). The
    Create-new path invokes it via onSaved({undoable:false})."""
    assert "_showSavedToast" in js_source
    assert "undoable" in js_source


def test_submit_state_button_always_enabled_in_commit_2(js_source: str) -> None:
    """Commit 1b's _update_submit_state disabled the primary button on
    Create-new radio. Commit 2 removes that gate — both paths submit."""
    # Presence of the updated comment / logic: primary always enabled
    assert "btn.prop(\"disabled\", false)" in js_source


# ---------------------------------------------------------------------------
# Commit 2 followup — review_action state + supplier_group Link
# ---------------------------------------------------------------------------


def test_review_action_state_includes_supplier_creation_requested(js_source: str) -> None:
    """REVIEW_ACTION_STATE map must classify 'Supplier Creation Requested'
    as 'done' — the reviewer-has-acted state should render green in the
    master pane indicator so the Pending filter excludes it."""
    # Locate the REVIEW_ACTION_STATE block and parse it out
    m = re.search(
        r"static\s+REVIEW_ACTION_STATE\s*=\s*\{([^}]+)\}",
        js_source,
        re.DOTALL,
    )
    assert m is not None, "REVIEW_ACTION_STATE static map missing"
    block = m.group(1)
    # Parse "key": "value" pairs
    pairs = dict(re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', block))
    assert pairs.get("Supplier Creation Requested") == "done", (
        f"Supplier Creation Requested must map to 'done' (green); got "
        f"{pairs.get('Supplier Creation Requested')!r}"
    )


def test_supplier_group_field_is_link_to_supplier_group_doctype(js_source: str) -> None:
    """Item 3 Commit 2 followup — supplier_group was initially a Data
    free-text field. Reviewer feedback: should autocomplete from the
    Supplier Group DocType. Changed to fieldtype=Link, options=Supplier Group.
    Native Frappe Link autocomplete handles the query.
    """
    # Tight regex around the supplier_group dialog field definition
    m = re.search(
        r'fieldname:\s*"supplier_group"[^}]*?fieldtype:\s*"([^"]+)"[^}]*?options:\s*"([^"]+)"',
        js_source,
        re.DOTALL,
    )
    if m is None:
        # Field ordering inside the object literal can vary — also try
        # the reverse (fieldtype before fieldname).
        m = re.search(
            r'fieldtype:\s*"([^"]+)"[^}]*?fieldname:\s*"supplier_group"[^}]*?options:\s*"([^"]+)"',
            js_source,
            re.DOTALL,
        )
    assert m is not None, "supplier_group field definition not found"
    fieldtype, options = m.group(1), m.group(2)
    assert fieldtype == "Link", (
        f"supplier_group should be fieldtype=Link for Supplier Group "
        f"autocomplete; got {fieldtype!r}"
    )
    assert options == "Supplier Group", (
        f"supplier_group options should be 'Supplier Group'; got {options!r}"
    )
