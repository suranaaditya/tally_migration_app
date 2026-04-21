"""Unit tests for the apply_decision_save pure-logic core.

Exercises ``rgi_migration.rgi_migration.page.md_review.query.apply_decision_save``
— the Frappe-free implementation that computes the field-value dict
to write back onto a Mapping Decision on save.

Covers (5 tests):

1. Full happy-path save of a first-time decision (empty stored notes,
   approver picks an account) — no chronology header, final_dr/cr
   derived from opening values, final_account normalised from empty.
2. Chronology header prepends when appending to non-empty stored
   notes — new content gets `[user, timestamp] ` prefix and joins
   with `\\n\\n<stored>`.
3. Empty reviewer_notes input preserves stored notes verbatim (no
   accidental wipe).
4. Identical reviewer_notes input (reviewer didn't change anything)
   leaves stored unchanged — no redundant header for a no-op.
5. Clearing final_account (empty string) normalises to None so
   Frappe writes NULL, not an empty-string Link.
"""

from __future__ import annotations

from rgi_migration.rgi_migration.page.md_review.query import (
    SAVE_DECISION_FIELDS,
    apply_decision_save,
)


def _base_current(**overrides) -> dict:
    """Minimal Mapping Decision dict for tests."""
    base = {
        "name": "MD-2026-00001",
        "session": "TMS-CACSPU--00001",
        "tally_name": "Test Ledger",
        "opening_dr": 1000.0,
        "opening_cr": 0.0,
        "review_action": "Pending",
        "final_account": None,
        "final_dr": 0.0,
        "final_cr": 0.0,
        "reviewer_notes": "",
    }
    base.update(overrides)
    return base


def test_happy_path_first_save():
    """First-time save on an unmapped decision: no prior notes, reviewer
    picks an account + writes an initial note. Verifies the full
    shape of the returned dict."""

    current = _base_current()

    result = apply_decision_save(
        current=current,
        review_action="Approved",
        final_account="Bank of Maharashtra - CACSPU",
        reviewer_notes_input="Confirmed target — CACSPU Bank Accounts.",
        session_user="aditya@jewonline.in",
        now_str="2026-04-22 10:14",
    )

    # Exact shape of the output
    assert set(result.keys()) == set(SAVE_DECISION_FIELDS)
    assert result["review_action"] == "Approved"
    assert result["final_account"] == "Bank of Maharashtra - CACSPU"
    # final_dr / final_cr mirror opening values (refinement 2)
    assert result["final_dr"] == 1000.0
    assert result["final_cr"] == 0.0
    # First-note case — no header prepended
    assert result["reviewer_notes"] == "Confirmed target — CACSPU Bank Accounts."


def test_chronology_header_on_append_to_existing_notes():
    """When stored notes are non-empty and the reviewer submits new
    content, the new content gets a `[user, timestamp] ` header and
    is concatenated above the stored content with a blank line."""

    existing = (
        "[priya@jewonline.in, 2026-04-20 15:47] First-pass: tier unmapped, "
        "Tally parent chain suggests Branch / Divisions."
    )
    current = _base_current(reviewer_notes=existing)

    result = apply_decision_save(
        current=current,
        review_action="Approved",
        final_account="Some Account - CACSPU",
        reviewer_notes_input="Confirmed as inter-entity receivable.",
        session_user="aditya@jewonline.in",
        now_str="2026-04-22 10:14",
    )

    expected = (
        "[aditya@jewonline.in, 2026-04-22 10:14] Confirmed as inter-entity receivable.\n"
        "\n"
        f"{existing}"
    )
    assert result["reviewer_notes"] == expected
    # Header appears exactly once — guards against double-prepending if
    # the logic accidentally ran twice.
    assert result["reviewer_notes"].count("[aditya@jewonline.in, 2026-04-22 10:14]") == 1


def test_empty_notes_input_preserves_stored_value():
    """Submitting empty reviewer_notes must NOT wipe existing stored
    notes — it means "no new note this save". Critical: the UI
    sends empty when the reviewer only edits the Select / Link
    fields, and we must not lose chronology because of it."""

    existing = "[priya@jewonline.in, 2026-04-20 15:47] Prior note."
    current = _base_current(reviewer_notes=existing)

    result_empty_str = apply_decision_save(
        current=current,
        review_action="Approved",
        final_account="Acct - CACSPU",
        reviewer_notes_input="",
        session_user="aditya@jewonline.in",
        now_str="2026-04-22 10:14",
    )
    result_none = apply_decision_save(
        current=current,
        review_action="Approved",
        final_account="Acct - CACSPU",
        reviewer_notes_input=None,
        session_user="aditya@jewonline.in",
        now_str="2026-04-22 10:14",
    )

    assert result_empty_str["reviewer_notes"] == existing
    assert result_none["reviewer_notes"] == existing


def test_identical_notes_input_skips_header():
    """If the reviewer's input is byte-identical to stored content
    (e.g. they selected-all/copy-pasted the existing value and re-
    submitted), don't treat that as a "new note" — no header, no
    concatenation. Preserves history exactly."""

    existing = "[priya@jewonline.in, 2026-04-20 15:47] Prior note body."
    current = _base_current(reviewer_notes=existing)

    result = apply_decision_save(
        current=current,
        review_action="Approved",
        final_account="Acct - CACSPU",
        reviewer_notes_input=existing,
        session_user="aditya@jewonline.in",
        now_str="2026-04-22 10:14",
    )

    assert result["reviewer_notes"] == existing
    # Specifically: no second header for aditya
    assert "aditya@jewonline.in" not in result["reviewer_notes"]


def test_empty_final_account_normalises_to_none():
    """Clearing the account picker sends an empty string to the
    endpoint. The pure function normalises to None so the Frappe
    wrapper writes a true NULL on the Link column — an empty string
    would fail Account-existence validation."""

    current = _base_current(final_account="Old Acct - CACSPU")

    result_empty = apply_decision_save(
        current=current,
        review_action="Pending",
        final_account="",
        reviewer_notes_input=None,
        session_user="aditya@jewonline.in",
        now_str="2026-04-22 10:14",
    )
    result_none = apply_decision_save(
        current=current,
        review_action="Pending",
        final_account=None,
        reviewer_notes_input=None,
        session_user="aditya@jewonline.in",
        now_str="2026-04-22 10:14",
    )

    assert result_empty["final_account"] is None
    assert result_none["final_account"] is None
