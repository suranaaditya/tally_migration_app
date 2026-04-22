"""Unit tests for the apply_decision_undo pure-logic core.

Exercises ``rgi_migration.rgi_migration.page.md_review.query.apply_decision_undo``
— the Frappe-free implementation that reads a pre-save snapshot
and returns the field-value dict to restore. The Frappe wrapper
``md_review.undo_decision`` layers on: cache read/delete,
permission check, doc save. Those are exercised via the bench-
console smoke test, not here.

Covers (5 tests):

1. Happy path — snapshot contains all expected fields; returned
   dict mirrors snapshot values exactly.
2. Snapshot has ``None`` for ``final_account`` — restored as None
   (verifies the "clear the account on undo" case where the
   pre-save state was unmapped).
3. Snapshot missing a required field — raises KeyError rather than
   partial-restoring (defensive against malformed cache entries).
4. Multi-field restore — review_action, final_account, reviewer_notes
   all carry through independently; no cross-contamination.
5. Shape contract — return dict's keys exactly equal
   ``UNDO_RESTORE_FIELDS``, no more, no less (so the wrapper's
   ``doc.set`` loop doesn't accidentally write unexpected fields).
"""

from __future__ import annotations

import pytest

from rgi_migration.rgi_migration.page.md_review.query import (
    UNDO_RESTORE_FIELDS,
    apply_decision_undo,
)


def _current_doc(**overrides) -> dict:
    """Current (post-save) Mapping Decision dict — what undo is reverting FROM."""
    base = {
        "name": "MD-2026-00001",
        "session": "TMS-CACSPU--00001",
        "tally_name": "Test Ledger",
        "opening_dr": 1000.0,
        "opening_cr": 0.0,
        # Post-save (Approved) state
        "review_action": "Approved",
        "final_account": "Bank of Maharashtra - CACSPU",
        "final_dr": 1000.0,
        "final_cr": 0.0,
        "reviewer_notes": (
            "[aditya@jewonline.in, 2026-04-22 10:14] Confirmed.\n"
            "\n"
            "[priya@jewonline.in, 2026-04-20 15:47] First-pass."
        ),
    }
    base.update(overrides)
    return base


def _snapshot(**overrides) -> dict:
    """Pre-save snapshot — what undo is reverting TO. Matches the
    shape written by :func:`save_decision` in Commit 4b."""
    base = {
        "review_action": "Pending",
        "final_account": None,
        "final_dr": 1000.0,
        "final_cr": 0.0,
        "reviewer_notes": "[priya@jewonline.in, 2026-04-20 15:47] First-pass.",
    }
    base.update(overrides)
    return base


def test_happy_path_restores_all_fields():
    """Snapshot has all expected fields; return dict mirrors it."""
    current = _current_doc()
    snapshot = _snapshot()

    result = apply_decision_undo(current=current, snapshot=snapshot)

    assert result["review_action"] == "Pending"
    assert result["final_account"] is None
    assert result["final_dr"] == 1000.0
    assert result["final_cr"] == 0.0
    assert result["reviewer_notes"] == (
        "[priya@jewonline.in, 2026-04-20 15:47] First-pass."
    )


def test_none_final_account_preserved():
    """The pre-save state for an unmapped row has final_account=None.
    Undo must restore None (not coerce to "" or any sentinel) so the
    Frappe wrapper writes a true NULL back to the column and the
    frontend re-renders the row as unmapped."""
    current = _current_doc(final_account="Some Account - CACSPU")
    snapshot = _snapshot(final_account=None)

    result = apply_decision_undo(current=current, snapshot=snapshot)

    assert result["final_account"] is None


def test_partial_snapshot_restores_only_its_keys():
    """Item 3 Commit 1b made undo shape-agnostic: a snapshot with a
    subset of the original SAVE_DECISION_FIELDS (e.g. a supplier-save
    snapshot that contains ``final_supplier`` + ``tier`` but not
    ``final_account``) restores exactly those keys, nothing more,
    nothing less.

    Pre-Commit-1b behavior raised ``KeyError`` on missing keys,
    guarding against partial restores of the strict 5-field contract.
    The new contract accepts any non-empty snapshot dict — the
    empty-snapshot guard still catches the obvious failure mode
    (zero fields → no-op save with no audit value → refuses)."""
    current = _current_doc()
    partial_snapshot = {
        "review_action": "Pending",
        "final_account": None,
    }
    result = apply_decision_undo(current=current, snapshot=partial_snapshot)
    assert result == partial_snapshot


def test_empty_snapshot_raises_valueerror():
    """Zero-field snapshot is defensively refused — restoring nothing
    would yield a no-op save that still calls doc.save() and still
    invalidates the cache entry, producing audit noise without
    meaningful revert semantics."""
    current = _current_doc()
    with pytest.raises(ValueError, match="empty"):
        apply_decision_undo(current=current, snapshot={})


def test_multi_field_restore_no_cross_contamination():
    """When all three writable fields changed in the save being
    undone, each reverts independently. Specifically guards against
    a buggy implementation that restored, say, review_action from
    the current doc's value instead of the snapshot."""
    current = _current_doc(
        review_action="Manual Override",
        final_account="Petty Cash - CACSPU",
        reviewer_notes="edited notes post-save",
    )
    snapshot = _snapshot(
        review_action="Pending",
        final_account="Cash - CACSPU",
        reviewer_notes="original notes",
    )

    result = apply_decision_undo(current=current, snapshot=snapshot)

    # Each field comes from snapshot, NOT from current.
    assert result["review_action"] == "Pending"
    assert result["final_account"] == "Cash - CACSPU"
    assert result["reviewer_notes"] == "original notes"
    # Sanity — verify we didn't accidentally return any of the
    # current doc's post-save values.
    assert result["review_action"] != current["review_action"]
    assert result["final_account"] != current["final_account"]
    assert result["reviewer_notes"] != current["reviewer_notes"]


def test_return_shape_matches_snapshot_keys_exactly():
    """Shape-agnostic contract (Item 3 Commit 1b): the returned dict
    contains exactly the keys present in the snapshot — caller is
    now responsible for building a well-formed snapshot. The Frappe
    wrapper's snapshot-shape contract (SAVE_DECISION_FIELDS for
    account saves, SUPPLIER_SAVE_FIELDS for supplier saves) is
    enforced at snapshot-build time, not at restore time.

    Sanity bound: account-save snapshots still contain all the
    UNDO_RESTORE_FIELDS keys since SAVE_DECISION_FIELDS is unchanged."""
    current = _current_doc()
    snapshot = _snapshot()

    result = apply_decision_undo(current=current, snapshot=snapshot)

    assert set(result.keys()) == set(snapshot.keys())
    assert set(result.keys()) >= set(UNDO_RESTORE_FIELDS)
