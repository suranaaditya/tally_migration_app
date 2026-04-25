# Copyright (c) 2026, Dux Digitech and contributors
# For license information, please see license.txt

"""SCR / ACR status synchronization for the Deferred review_action.

Item 8.5 Stage 2 — when a Mapping Decision's ``review_action``
transitions to or away from ``Deferred``, the linked
``Supplier Creation Request`` and ``Account Creation Request`` child
rows must reflect the same "skipped this pass" intent. Otherwise the
Process SCR / Process ACR dialogs would keep showing Deferred rows as
actionable, and the reviewer's Deferred decision wouldn't carry
through to the workflow surface.

Sync rule (Phase A Q4):

    * **All parent decisions Deferred** → SCR/ACR row → ``Deferred``
    * **Any non-Deferred parent** → SCR/ACR row keeps its prior status
      (Pending / Created / Skipped / Failed)
    * **Currently-Deferred row + any non-Deferred parent** → revert to
      ``Pending`` (Stage 2 default; the snapshot-restore path in
      ``undo_decision`` handles the within-10s case via the undo cache)

Corner case — all-parents-Rejected: an SCR with all parent MDs in
``Rejected`` does NOT auto-transition to anything. It sits at its
prior status (typically ``Pending``), harmless and dormant. The
generators silent-skip Rejected parents; the SCR has no actionable
work but also doesn't pollute the dialog count visibly. Documented
here so future readers understand why the helper doesn't try to
clean up these rows.

Pure / Frappe split:

    * :func:`compute_sync_target` — pure logic, no Frappe. Computes
      the target status for one SCR/ACR row given its parent MDs'
      review_action values and its current status. Unit-testable
      without a bench.
    * :func:`sync_creation_requests_for_decision` — Frappe wrapper.
      Reads the session, iterates SCR/ACR child rows, calls
      ``compute_sync_target``, applies transitions, returns a
      snapshot for the undo cache.
    * :func:`restore_creation_requests_from_snapshot` — Frappe
      wrapper. Reads a snapshot produced by ``sync_*`` and restores
      SCR/ACR rows to their pre-sync status. Used by
      ``undo_decision``.
"""

from __future__ import annotations

from typing import Any

from rgi_migration.rgi_migration.page.md_review.query import (
    parse_source_decisions_csv,
)


# Status value for a deferred CR row. Defined as a constant so the
# sync helper, the schema migration, and the dialog-filter consumer
# all agree on the spelling.
DEFERRED_STATUS = "Deferred"

# Default status to revert to when an SCR/ACR row is currently
# Deferred but at least one parent decision has transitioned out of
# Deferred. Stage 2 ships with a fixed "Pending" default for the
# save_decision-driven un-defer path (information loss is accepted
# per Phase A Q3 — undo_decision uses the cache snapshot to do
# better when within the 10-second window).
UNDEFER_DEFAULT_STATUS = "Pending"

# Terminal statuses the sync helper MUST NOT transition away from.
# Created = a Supplier / Account was actually created in ERPNext;
# moving this back to Deferred would contradict the audit trail.
# Skipped = reviewer explicitly rejected the creation request; same
# permanence argument as Rejected on the MD side.
# Failed is DELIBERATELY excluded from this set — Failed means "create
# attempt errored, reviewer may retry", which is a recoverable state
# that Stage 2 treats like Pending for transition purposes. The Q3
# snapshot-restore path preserves Failed on undo; the sync transition
# path is free to move Failed → Deferred when parents all defer.
_TERMINAL_STATUSES = frozenset({"Created", "Skipped"})


def compute_sync_target(
    parent_actions: list[str],
    current_status: str,
) -> str | None:
    """Compute the target SCR/ACR status given its parent MDs' review_actions.

    Pure function — no Frappe dependency. Unit-testable.

    Args:
        parent_actions: list of ``review_action`` strings, one per
            source_decision linked to this SCR/ACR row. Order doesn't
            matter; uniqueness doesn't matter.
        current_status: the SCR/ACR row's current status value.

    Returns:
        The target status string if the row should transition; ``None``
        if no transition is needed (already at target, or no rule
        applies).

    Cases:
        * ``parent_actions == []`` (orphan row, no parents) → ``None``.
          The helper doesn't try to interpret orphan state; cleanup is
          a different concern (see ``_cleanup_pending_scr_on_transition``
          for the historical pattern).
        * All Deferred + already Deferred → ``None``
        * All Deferred + not Deferred → ``"Deferred"``
        * Any non-Deferred + currently Deferred → ``"Pending"``
          (un-defer default)
        * Any non-Deferred + not Deferred → ``None`` (no change)
    """
    if not parent_actions:
        return None

    # Terminal statuses (Created / Skipped) never transition — the
    # underlying Supplier / Account creation is either done or
    # explicitly refused. Preserve the audit trail regardless of
    # parent-MD review_action churn.
    if current_status in _TERMINAL_STATUSES:
        return None

    all_deferred = all(action == "Deferred" for action in parent_actions)

    if all_deferred:
        if current_status == DEFERRED_STATUS:
            return None
        return DEFERRED_STATUS

    # At least one parent is not Deferred.
    if current_status == DEFERRED_STATUS:
        return UNDEFER_DEFAULT_STATUS

    return None


def sync_creation_requests_for_decision(decision_doc: Any) -> dict[str, list[dict[str, str]]]:
    """Sync SCR/ACR child rows on the linked session for one MD's transition.

    Called from ``save_decision`` immediately after the MD's
    ``review_action`` has been written. Iterates SCR + ACR child rows,
    applies :func:`compute_sync_target`, mutates rows in-place, and
    saves the session.

    Returns a snapshot dict suitable for stashing into the undo cache:

    .. code-block:: python

        {
            "scr_snapshot": [{"row_name": "scr-xxx", "prior_status": "Pending"}, ...],
            "acr_snapshot": [{"row_name": "acr-yyy", "prior_status": "Failed"}, ...],
        }

    The undo cache reader (``restore_creation_requests_from_snapshot``)
    uses this snapshot to restore exact prior statuses, including
    ``Failed`` (so an undo restores the "this still needs attention"
    signal, not a generic Pending — Phase A Q3 resolution).

    No-op on no transitions: returns empty lists in both keys without
    saving the session.

    Args:
        decision_doc: the Mapping Decision doc post-mutation.

    Returns:
        Snapshot dict (see above). Always returns the dict; lists are
        empty when no rows transitioned.
    """
    import frappe

    session_doc = frappe.get_doc("Tally Migration Session", decision_doc.session)

    scr_snapshot: list[dict[str, str]] = []
    acr_snapshot: list[dict[str, str]] = []

    # SCR sync
    for row in (session_doc.supplier_creation_requests or []):
        tokens = parse_source_decisions_csv(row.source_decisions)
        if decision_doc.name not in tokens:
            continue
        parent_actions = _fetch_parent_actions(tokens)
        target = compute_sync_target(parent_actions, row.status or "")
        if target is None:
            continue
        scr_snapshot.append({"row_name": row.name, "prior_status": row.status or ""})
        row.status = target

    # ACR sync
    for row in (session_doc.account_creation_requests or []):
        tokens = parse_source_decisions_csv(row.source_decisions)
        if decision_doc.name not in tokens:
            continue
        parent_actions = _fetch_parent_actions(tokens)
        target = compute_sync_target(parent_actions, row.status or "")
        if target is None:
            continue
        acr_snapshot.append({"row_name": row.name, "prior_status": row.status or ""})
        row.status = target

    if scr_snapshot or acr_snapshot:
        session_doc.save(ignore_permissions=True)

    return {"scr_snapshot": scr_snapshot, "acr_snapshot": acr_snapshot}


def restore_creation_requests_from_snapshot(
    session_name: str,
    scr_snapshot: list[dict[str, str]],
    acr_snapshot: list[dict[str, str]],
) -> dict[str, list[str]]:
    """Restore SCR/ACR row statuses from an undo cache snapshot.

    Called from ``undo_decision`` after the MD itself has been
    restored. Mirror of :func:`sync_creation_requests_for_decision` —
    where sync writes the new status and snapshots the prior, restore
    writes the prior status back.

    Empty snapshots are no-ops (the original save didn't transition
    any rows). Returns a summary dict for the caller's logging /
    audit needs.

    Args:
        session_name: the Tally Migration Session name.
        scr_snapshot: list of ``{"row_name": str, "prior_status": str}``
            dicts produced by ``sync_*``.
        acr_snapshot: same shape, for ACR.

    Returns:
        ``{"scr_restored": [row_name, ...], "acr_restored": [row_name, ...]}``.
    """
    import frappe

    if not scr_snapshot and not acr_snapshot:
        return {"scr_restored": [], "acr_restored": []}

    session_doc = frappe.get_doc("Tally Migration Session", session_name)

    scr_restored: list[str] = []
    acr_restored: list[str] = []

    scr_by_name = {row.name: row for row in (session_doc.supplier_creation_requests or [])}
    for entry in scr_snapshot:
        row = scr_by_name.get(entry["row_name"])
        if row is None:
            # Row deleted between save + undo — nothing to restore. Skip.
            continue
        row.status = entry["prior_status"]
        scr_restored.append(row.name)

    acr_by_name = {row.name: row for row in (session_doc.account_creation_requests or [])}
    for entry in acr_snapshot:
        row = acr_by_name.get(entry["row_name"])
        if row is None:
            continue
        row.status = entry["prior_status"]
        acr_restored.append(row.name)

    if scr_restored or acr_restored:
        session_doc.save(ignore_permissions=True)

    return {"scr_restored": scr_restored, "acr_restored": acr_restored}


def _fetch_parent_actions(decision_names: list[str]) -> list[str]:
    """Fetch ``review_action`` for each MD name. Missing MDs map to
    empty string (treated as non-Deferred by :func:`compute_sync_target`).

    Frappe-coupled helper; isolated for unit-test substitution if needed.
    """
    import frappe

    return [
        frappe.db.get_value("Mapping Decision", name, "review_action") or ""
        for name in decision_names
    ]
