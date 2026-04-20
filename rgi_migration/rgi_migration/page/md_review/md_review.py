"""
Mapping Decision Review — backend whitelist methods.

Scope per Week 4 Item 1 Commit 2: stubs only. Actual implementations
land in Commits 3 and 5 per docs/week4_review_ui_design.md §1.12.
"""

import frappe


@frappe.whitelist()
def get_session_decisions(session_name, filters=None, start=0, page_length=50, order_by=None):
    """Return Mapping Decisions for a session with filtering + pagination.

    Used by the master pane to populate the decision list.
    Implementation lands in Commit 3.
    """
    raise NotImplementedError("get_session_decisions lands in Commit 3")


@frappe.whitelist()
def save_decision(decision_name, review_action, final_account, reviewer_notes):
    """Save a Mapping Decision with validation (see §1.6 validation semantics).

    Includes chronology-header prepend on reviewer_notes (see §1.4 Section 4).
    Snapshots pre-save state for Undo (see §1.7).
    Implementation lands in Commit 5.
    """
    raise NotImplementedError("save_decision lands in Commit 5")


@frappe.whitelist()
def undo_decision(decision_name):
    """Revert a recently-saved decision using the cached pre-save snapshot.

    5-second TTL per §1.7. Implementation lands in Commit 5.
    """
    raise NotImplementedError("undo_decision lands in Commit 5")


@frappe.whitelist()
def get_next_pending(session_name, after_decision_name, filters=None):
    """Find the next Pending decision after the current one.

    Server-computed to avoid client-state desync between reviewers.
    Implementation lands in Commit 5.
    """
    raise NotImplementedError("get_next_pending lands in Commit 5")
