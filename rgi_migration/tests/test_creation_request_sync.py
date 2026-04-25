"""Pure-logic tests for `compute_sync_target` — the Q4 resolution
engine that decides whether an SCR/ACR row should transition to/from
``Deferred`` based on its parent Mapping Decisions' ``review_action``.

The Frappe-coupled halves (`sync_creation_requests_for_decision`
and `restore_creation_requests_from_snapshot`) are exercised via
Phase C bench smoke on synthetic sessions.
"""

from __future__ import annotations

import pytest

from rgi_migration.rgi_migration.page.md_review.creation_request_sync import (
    DEFERRED_STATUS,
    UNDEFER_DEFAULT_STATUS,
    compute_sync_target,
)


class TestAllParentsDeferred:
    """Q4 rule: all parents Deferred → SCR/ACR → Deferred."""

    def test_single_parent_all_deferred(self) -> None:
        assert compute_sync_target(["Deferred"], "Pending") == DEFERRED_STATUS

    def test_multiple_parents_all_deferred(self) -> None:
        assert compute_sync_target(
            ["Deferred", "Deferred", "Deferred"], "Pending",
        ) == DEFERRED_STATUS

    def test_all_deferred_but_already_at_target(self) -> None:
        # Idempotent — no-op when already Deferred.
        assert compute_sync_target(["Deferred"], "Deferred") is None


class TestAnyParentNotDeferred:
    """Q4 rule: any non-Deferred parent → SCR keeps prior status OR
    reverts to Pending if currently Deferred."""

    def test_mixed_parents_row_pending(self) -> None:
        # Row at Pending, mixed parents: no transition.
        assert compute_sync_target(["Approved", "Deferred"], "Pending") is None

    def test_mixed_parents_row_deferred(self) -> None:
        # Row at Deferred + any non-Deferred parent: revert to Pending.
        assert compute_sync_target(
            ["Approved", "Deferred"], "Deferred",
        ) == UNDEFER_DEFAULT_STATUS

    def test_all_approved_no_transition(self) -> None:
        # Row at Pending, all parents Approved: nothing changes.
        assert compute_sync_target(["Approved", "Approved"], "Pending") is None

    def test_all_rejected_no_transition_corner_case(self) -> None:
        """Q4 corner case: all parents Rejected → SCR stays Pending.
        Documented in the helper module: sits dormant, harmless. The
        helper does NOT auto-transition Rejected-only rows."""
        assert compute_sync_target(["Rejected", "Rejected"], "Pending") is None


class TestUndeferTransitions:
    """Row currently Deferred, transitioning based on parent state changes."""

    @pytest.mark.parametrize(
        "parents",
        [
            ["Pending"],
            ["Approved"],
            ["Rejected"],
            ["Approved", "Deferred"],
            ["Pending", "Deferred", "Approved"],
        ],
    )
    def test_reverts_to_pending_default(self, parents: list[str]) -> None:
        assert compute_sync_target(parents, "Deferred") == UNDEFER_DEFAULT_STATUS

    def test_still_all_deferred_stays(self) -> None:
        assert compute_sync_target(["Deferred", "Deferred"], "Deferred") is None


class TestEdgeCases:
    def test_empty_parent_list(self) -> None:
        """Orphan row with no parents — helper doesn't interpret, returns None."""
        assert compute_sync_target([], "Pending") is None
        assert compute_sync_target([], "Deferred") is None

    def test_whitespace_in_status_strings(self) -> None:
        # Defensive: exact string match, no whitespace normalization.
        # A "  Deferred" with spaces won't match — surface as bug if we see it.
        assert compute_sync_target([" Deferred"], "Pending") is None

    def test_unknown_parent_action(self) -> None:
        # A parent_action we don't know about (e.g., a future enum
        # value) should be treated as "not Deferred" — row at Pending
        # stays at Pending.
        assert compute_sync_target(["SomeUnknownAction"], "Pending") is None

    def test_unknown_parent_action_currently_deferred(self) -> None:
        # Same unknown parent + row currently Deferred → revert
        # (because at-least-one-not-Deferred triggers).
        assert compute_sync_target(
            ["SomeUnknownAction"], "Deferred",
        ) == UNDEFER_DEFAULT_STATUS


class TestTerminalStatusProtection:
    """Created and Skipped are terminal audit states — sync must NOT
    transition away from them regardless of parent state. Failed is
    explicitly NOT terminal (recoverable, retryable)."""

    @pytest.mark.parametrize("terminal", ["Created", "Skipped"])
    def test_all_deferred_does_not_touch_terminal(self, terminal: str) -> None:
        # Parents all Deferred → would normally transition → Deferred,
        # but terminal rows are protected.
        assert compute_sync_target(["Deferred", "Deferred"], terminal) is None

    @pytest.mark.parametrize("terminal", ["Created", "Skipped"])
    def test_mixed_parents_does_not_touch_terminal(self, terminal: str) -> None:
        assert compute_sync_target(["Deferred", "Pending"], terminal) is None

    def test_failed_is_not_terminal(self) -> None:
        """Failed IS transitionable — parents all Deferred should move
        Failed → Deferred, matching Probe 7's Phase C expected behavior
        (Failed info preservation via snapshot; transition via sync)."""
        assert compute_sync_target(["Deferred"], "Failed") == DEFERRED_STATUS


class TestConstantsSanity:
    def test_deferred_status_spelling(self) -> None:
        """The DEFERRED_STATUS constant must match the DocType enum
        spelling. If this test fails, someone changed the constant
        without updating the SCR/ACR status enum (or vice versa)."""
        assert DEFERRED_STATUS == "Deferred"

    def test_undefer_default(self) -> None:
        assert UNDEFER_DEFAULT_STATUS == "Pending"
