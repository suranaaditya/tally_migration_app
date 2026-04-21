"""Unit tests for the review_action auto-flip truth table.

Exercises ``rgi_migration.rgi_migration.page.md_review.query.compute_auto_flip``
— the Python mirror of ``DetailPane._computeAutoFlip`` in md_review.js.
Both implementations must stay behaviourally identical; the JS version
runs live in the browser, the Python version is what's actually
verified here.

Covers all 6 rows of the §1.4 Section 4 truth table (refinement 1):

1. unmapped + Pending + picks account → Approved
2. mapped + Pending + picks same as proposal → Approved
3. mapped + Pending + picks different account → Manual Override
4. unmapped + Pending + clears → Pending
5. mapped + Pending + clears → Pending
6. terminal state (Rejected) + any final_account change → stays Rejected

Plus one cross-check that each ``Pending*`` sub-variant is treated as
Pending-like (the four-element PENDING_REVIEW_STATES membership is
load-bearing — miss one and that sub-state stops auto-flipping).
"""

from __future__ import annotations

import pytest

from rgi_migration.rgi_migration.page.md_review.query import (
    PENDING_REVIEW_STATES,
    compute_auto_flip,
)


def test_unmapped_plus_picks_account_flips_to_approved():
    """Row 1 — the canonical "filling in an unmapped ledger" case. The
    reviewer agreed there should be a mapping, supplied one; outcome
    is Approved, not Manual Override (that would imply overriding a
    mapper proposal, which doesn't exist here)."""

    result = compute_auto_flip(
        original_proposed_account=None,
        current_review_action="Pending",
        current_final_account="Bank of Maharashtra - CACSPU",
    )
    assert result == "Approved"


def test_mapped_plus_picks_same_account_flips_to_approved():
    """Row 2 — reviewer endorsed the mapper's proposal by leaving the
    final_account at the proposed value. Outcome is Approved (reviewer
    agreed with the Tier-1 output)."""

    result = compute_auto_flip(
        original_proposed_account="Cash - CACSPU",
        current_review_action="Pending",
        current_final_account="Cash - CACSPU",
    )
    assert result == "Approved"


def test_mapped_plus_picks_different_account_flips_to_manual_override():
    """Row 3 — reviewer DISAGREED with the mapper proposal and picked
    something else. Outcome is Manual Override — downstream rule-
    promotion reads this as "the existing rule should not apply to
    this case" (vs Approved-on-unmapped's "we need a new rule")."""

    result = compute_auto_flip(
        original_proposed_account="Cash - CACSPU",
        current_review_action="Pending",
        current_final_account="Petty Cash - CACSPU",
    )
    assert result == "Manual Override"


def test_unmapped_plus_clears_account_returns_to_pending():
    """Row 4a — reviewer opened an unmapped row, may have typed
    something into final_account but then cleared it. Back to
    Pending — no decision made."""

    result = compute_auto_flip(
        original_proposed_account=None,
        current_review_action="Pending",
        current_final_account="",
    )
    assert result == "Pending"

    # Also test None (the pure function should accept either empty-
    # sentinel — the JS side may pass "" while the Python side may
    # pass None depending on how Frappe round-trips the Link field).
    result_none = compute_auto_flip(
        original_proposed_account=None,
        current_review_action="Pending",
        current_final_account=None,
    )
    assert result_none == "Pending"


def test_mapped_plus_clears_account_returns_to_pending():
    """Row 4b — reviewer had a mapped row pre-populated, cleared the
    final_account (e.g. typed to search for another, then changed
    mind, hit Backspace). Back to Pending regardless of whether a
    proposal existed."""

    result = compute_auto_flip(
        original_proposed_account="Cash - CACSPU",
        current_review_action="Pending",
        current_final_account="",
    )
    assert result == "Pending"


def test_terminal_state_blocks_auto_flip():
    """Row 5 — reviewer manually set review_action to a terminal
    state (Rejected / Deferred / Approved / Manual Override / etc.).
    Any subsequent final_account change must NOT overwrite. The
    explicit choice wins."""

    # Reviewer on unmapped ledger, manually Rejected, THEN fiddles
    # with final_account. review_action must stay Rejected.
    assert compute_auto_flip(
        original_proposed_account=None,
        current_review_action="Rejected",
        current_final_account="Anything - CACSPU",
    ) == "Rejected"

    # Same guard for Deferred, Approved, Manual Override, Skipped,
    # Excluded (P&L) — the full set of non-Pending terminals. A
    # single assert covers the principle; the six-way enum-drift risk
    # is guarded by the PENDING_REVIEW_STATES membership test below.
    assert compute_auto_flip(
        original_proposed_account="Cash - CACSPU",
        current_review_action="Deferred",
        current_final_account="Petty Cash - CACSPU",
    ) == "Deferred"


def test_pending_subvariants_all_flip():
    """Cross-check — the three non-``"Pending"`` Pending-like states
    (Pending Account Creation, Pending Group Account Resolution,
    Pending Supplier Creation) must also auto-flip to Approved when
    the reviewer supplies a final_account. If one of them drifts out
    of PENDING_REVIEW_STATES, auto-flip would silently stop working
    for that sub-flow — this test catches that.

    Not a distinct truth-table row, but a safety net on the enum
    membership that all the flip rules depend on."""

    assert PENDING_REVIEW_STATES == frozenset({
        "Pending",
        "Pending Account Creation",
        "Pending Group Account Resolution",
        "Pending Supplier Creation",
    }), "PENDING_REVIEW_STATES drifted — auto-flip rows may silently break"

    for pending_variant in PENDING_REVIEW_STATES:
        result = compute_auto_flip(
            original_proposed_account=None,
            current_review_action=pending_variant,
            current_final_account="Some Account - CACSPU",
        )
        assert result == "Approved", (
            f"Pending variant {pending_variant!r} did not auto-flip — "
            f"got {result!r}"
        )
