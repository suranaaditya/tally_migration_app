"""Unit tests for the compute_next_pending pure-logic core.

Exercises ``rgi_migration.rgi_migration.page.md_review.query.compute_next_pending``
— the Frappe-free implementation of auto-advance "what's next"
per §1.7. The Frappe wrapper ``md_review.get_next_pending`` layers
on: session scope guard, filters merging, ``frappe.get_all`` call.

Covers (5 tests):

1. Normal advance — after-name is in the middle of the refreshed
   list → return the next name.
2. End of list — after-name is the last row → return None.
3. After-name dropped out of filter, with position hint — return
   the row now occupying the old position.
4. Empty refreshed list → return None regardless of inputs.
5. After-name not in list AND no position hint → return None
   (conservative: don't guess a random first-in-list).
"""

from __future__ import annotations

from rgi_migration.rgi_migration.page.md_review.query import (
    compute_next_pending,
)


def _decisions(*names: str) -> list[dict]:
    """Build a list of minimal decision dicts for tests."""
    return [{"name": n} for n in names]


def test_normal_advance_returns_next_name():
    """after-name is in the refreshed list at some middle position;
    compute_next_pending returns the name immediately after it."""
    decisions = _decisions("MD-01", "MD-02", "MD-03", "MD-04", "MD-05")

    result = compute_next_pending(
        decisions=decisions,
        after_name="MD-03",
    )
    assert result == "MD-04"

    # Edge — after=first row → returns second row.
    result_first = compute_next_pending(
        decisions=decisions,
        after_name="MD-01",
    )
    assert result_first == "MD-02"


def test_end_of_list_returns_none():
    """after-name is the LAST row in the refreshed list; there is
    no next row. Return None so the wrapper can drive the "all
    resolved" empty state (§1.9 case 3)."""
    decisions = _decisions("MD-01", "MD-02", "MD-03")

    result = compute_next_pending(
        decisions=decisions,
        after_name="MD-03",  # last row
    )
    assert result is None


def test_dropped_out_with_position_hint_returns_same_index_row():
    """after-name is NOT in the refreshed list (the save dropped it
    out of the filter — e.g. Approved on the Pending preset). The
    caller supplies the pre-save position; compute_next_pending
    returns the row now at that index — which semantically was "the
    next row" before the save removed after-name from the list.

    §1.7 rule 4b: "stay at the same index which now points to what
    was the next row before."
    """
    # Pre-save, MD-03 was at index 2. Reviewer Approved MD-03; it
    # dropped from the Pending filter. After refresh: MD-03 is gone,
    # everything below it shifted up by one position. The row now at
    # index 2 is what used to be MD-04.
    decisions = _decisions("MD-01", "MD-02", "MD-04", "MD-05", "MD-06")

    result = compute_next_pending(
        decisions=decisions,
        after_name="MD-03",
        after_position=2,
    )
    assert result == "MD-04"

    # Edge — position was the last in the pre-save list; after removal
    # the new list's length equals the old after_position, so the hint
    # points past the end → None.
    result_past_end = compute_next_pending(
        decisions=_decisions("MD-01", "MD-02"),
        after_name="MD-03",
        after_position=2,  # old pre-save index 2, but new list has only indices 0-1
    )
    assert result_past_end is None


def test_empty_list_returns_none():
    """Filter matches zero rows — all pending resolved. No next. The
    wrapper's response_count will also be 0; the frontend uses that
    to drive §1.9 case 3 (all-done empty state)."""
    result = compute_next_pending(
        decisions=[],
        after_name="MD-03",
    )
    assert result is None

    # Also None when the after-name happens to match nothing (which
    # is automatic for an empty list).
    result_with_position = compute_next_pending(
        decisions=[],
        after_name="MD-03",
        after_position=2,
    )
    assert result_with_position is None


def test_dropped_out_without_position_hint_returns_none():
    """Conservative fallback — if the client didn't pass
    after_position and the saved decision dropped out of the filter,
    we don't know where it was. Returning the first row would jump
    the reviewer backwards through already-reviewed work. Returning
    None lets the frontend fall back to "reselect by index using
    client cache" or just re-anchor on the first row intentionally.
    Either way, the pure function refuses to guess."""
    decisions = _decisions("MD-01", "MD-02", "MD-04", "MD-05")

    result = compute_next_pending(
        decisions=decisions,
        after_name="MD-03",  # not in list
        after_position=None,  # no hint
    )
    assert result is None
