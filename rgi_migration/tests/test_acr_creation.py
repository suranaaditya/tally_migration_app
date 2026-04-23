"""Tests for Item 4 Commit 2 — Account Creation Request payload + wiring.

Pure-core coverage for the Create-new submit path in the
AccountResolutionDialog:

1. ``build_acr_payload`` — shape + server-owned-invariant guarantees
   (proposed_is_group locked to 0 per AMB C2-1; proposed_root_type
   read from source decision per AMB C2-B).
2. Static-file guardrails for the frontend wiring — submit calls the
   new whitelist method, REVIEW_ACTION_STATE maps the new enum to
   "done", review_action Select includes the new option, non-undoable
   informational note replaces the Commit 1 placeholder banner.

The Frappe wrapper ``create_account_creation_request`` isn't directly
unit-testable (ORM-bound); exercised via the Phase C bench smoke. Same
philosophy as ``test_scr_creation.py``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from rgi_migration.rgi_migration.page.md_review.query import build_acr_payload


# ---------------------------------------------------------------------------
# build_acr_payload — shape + invariants
# ---------------------------------------------------------------------------


def _decision(**overrides) -> dict:
    """Default source-decision dict for the pending_account_creation
    case. Override any key per test."""
    base = {
        "name": "MD-2026-00001",
        "tally_name": "Hostel Fee Advance",
        "tally_id": "H001",
        "tally_root_type": "Liability",
        "new_account_root_type": "Liability",
    }
    base.update(overrides)
    return base


def test_acr_payload_happy_path_pending_account_creation() -> None:
    """pending_account_creation row: both new_account_root_type and
    tally_root_type are set (same in practice for this branch).
    Payload carries all reviewer-filled fields plus the server-owned
    invariants (status, is_group=0)."""
    out = build_acr_payload(
        decision=_decision(),
        proposed_account_name="Hostel Fee Advance Payable",
        proposed_parent="Liability For Students - CACSPU",
        account_type="Payable",
        reason="Mapper flagged for creation; approver to verify.",
        reviewer_notes="Matches mapper suggestion verbatim.",
    )
    assert out == {
        "status": "Pending",
        "proposed_account_name": "Hostel Fee Advance Payable",
        "proposed_parent": "Liability For Students - CACSPU",
        "proposed_root_type": "Liability",
        "proposed_is_group": 0,
        "account_type": "Payable",
        "reason": "Mapper flagged for creation; approver to verify.",
        "reviewer_notes": "Matches mapper suggestion verbatim.",
        "source_decisions": "MD-2026-00001",
    }


def test_acr_payload_root_type_prefers_new_account_root_type() -> None:
    """AMB C2-B: for pending_account_creation rows, mapper's
    new_account_root_type wins over tally_root_type. In practice both
    should agree, but when they drift (e.g., mapper's rule
    redirected to a different root), the mapper-curated value is
    canonical."""
    out = build_acr_payload(
        decision=_decision(
            tally_root_type="Asset",
            new_account_root_type="Liability",  # mapper's redirect
        ),
        proposed_account_name="X",
        proposed_parent="Y - CACSPU",
        account_type=None,
        reason=None,
        reviewer_notes=None,
    )
    assert out["proposed_root_type"] == "Liability"


def test_acr_payload_root_type_falls_back_to_tally_root_type() -> None:
    """AMB C2-B + AMB-9 re-scope: unmapped rows have NO mapper
    suggestion — new_account_root_type is None. Payload falls back to
    the parser's tally_root_type so the ACR row still has a valid
    root_type for Commit 3's Account.insert()."""
    out = build_acr_payload(
        decision=_decision(
            tally_root_type="Asset",
            new_account_root_type=None,
        ),
        proposed_account_name="X",
        proposed_parent="Y - CACSPU",
        account_type=None,
        reason=None,
        reviewer_notes=None,
    )
    assert out["proposed_root_type"] == "Asset"


def test_acr_payload_is_group_always_zero_regardless_of_decision() -> None:
    """AMB C2-1: v1 locks reviewer-created accounts to leaves. Even if
    the source decision somehow carries new_account_is_group=True
    (which shouldn't happen per mapper semantics), the payload still
    writes 0. Defensive against future drift in the mapper's
    emit shape."""
    out = build_acr_payload(
        decision=_decision(new_account_is_group=True),
        proposed_account_name="X",
        proposed_parent="Y - CACSPU",
        account_type=None,
        reason=None,
        reviewer_notes=None,
    )
    assert out["proposed_is_group"] == 0


def test_acr_payload_empty_account_type_normalises_to_none() -> None:
    """Frappe's Data fields distinguish NULL (absent) from '' (empty
    string) in some validation paths. Normalise empty / whitespace to
    None so the stored ACR row is unambiguously "no value" rather than
    "reviewer left it blank on purpose". Parallel to SCR payload's
    supplier_group handling."""
    out = build_acr_payload(
        decision=_decision(),
        proposed_account_name="X",
        proposed_parent="Y - CACSPU",
        account_type="   ",  # whitespace
        reason=None,
        reviewer_notes=None,
    )
    assert out["account_type"] is None


def test_acr_payload_empty_reason_normalises_to_none() -> None:
    """Same rationale as account_type — empty reason is stored as
    NULL, not ''. Lets downstream (approver UI, queries) use
    ``is None`` checks consistently."""
    out = build_acr_payload(
        decision=_decision(),
        proposed_account_name="X",
        proposed_parent="Y - CACSPU",
        account_type="Bank",
        reason="",
        reviewer_notes=None,
    )
    assert out["reason"] is None


def test_acr_payload_reviewer_notes_preserved_verbatim() -> None:
    """reviewer_notes is the ONE field that stays verbatim on the ACR
    (no chronology header). Exact match assertion — regresses if a
    future refactor accidentally runs the chronology helper on it."""
    out = build_acr_payload(
        decision=_decision(),
        proposed_account_name="X",
        proposed_parent="Y - CACSPU",
        account_type=None,
        reason=None,
        reviewer_notes="Reviewer sees\nmulti-line verbatim.",
    )
    assert out["reviewer_notes"] == "Reviewer sees\nmulti-line verbatim."


def test_acr_payload_missing_both_root_types_falls_back_to_empty() -> None:
    """Defensive — a degenerate decision with no root_type anywhere
    should not crash the pure function. Empty string returned; Commit
    3's approve_acr will catch this at Account.insert() time."""
    out = build_acr_payload(
        decision={
            "name": "MD-2026-00001",
            "tally_name": "Degenerate",
        },
        proposed_account_name="X",
        proposed_parent="Y - CACSPU",
        account_type=None,
        reason=None,
        reviewer_notes=None,
    )
    assert out["proposed_root_type"] == ""


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


def test_dialog_submit_calls_create_account_creation_request(js_source: str) -> None:
    """AMB C2-D: guard the wiring — AccountResolutionDialog._on_submit
    must reference the new whitelist method. Commit 1 placeholder
    (``Save wiring ships in Commit 2``) must be gone."""
    assert "create_account_creation_request" in js_source
    assert "Save wiring ships in Commit 2" not in js_source


def test_review_action_state_includes_account_creation_requested(
    js_source: str,
) -> None:
    """REVIEW_ACTION_STATE must classify 'Account Creation Requested' as
    'done' so the master pane indicator renders green and the Pending
    filter excludes these rows (mirrors Item 3 Commit 2's treatment of
    'Supplier Creation Requested')."""
    m = re.search(
        r"static\s+REVIEW_ACTION_STATE\s*=\s*\{([^}]+)\}",
        js_source,
        re.DOTALL,
    )
    assert m is not None, "REVIEW_ACTION_STATE static map missing"
    pairs = dict(re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', m.group(1)))
    assert pairs.get("Account Creation Requested") == "done", (
        f"Account Creation Requested must map to 'done' (green); got "
        f"{pairs.get('Account Creation Requested')!r}"
    )


def test_review_action_select_includes_account_creation_requested(
    js_source: str,
) -> None:
    """Section 4's review_action Select control hardcodes the full enum
    list. Missing 'Account Creation Requested' would render the value
    inconsistently when a row carrying it is loaded (Frappe would show
    blank or the raw string). Guard its presence."""
    # Match the review_action control's options array within the
    # _mount_reviewer_action_controls method.
    m = re.search(
        r'fieldname:\s*"review_action"[^}]*?options:\s*\[([^\]]+)\]',
        js_source,
        re.DOTALL,
    )
    assert m is not None, "review_action Select options block not found"
    assert '"Account Creation Requested"' in m.group(1), (
        "'Account Creation Requested' missing from Section 4's "
        "review_action Select options list."
    )


def test_dialog_replaces_commit1_placeholder_with_info_note(
    js_source: str,
) -> None:
    """Commit 1 shipped an orange warning banner saying submit was
    placeholder. Commit 2 replaces with a blue informational note
    ("Account Creation Request will be added … does NOT participate in
    Undo"). Regresses if someone restores the placeholder."""
    assert "Commit 2 wires backend" not in js_source
    assert "Account Creation Request will be added" in js_source
    assert "does NOT participate in Undo" in js_source
