"""One-time backfill: create Pass 1 Migration Pass rows for pre-Stage-3 Submitted sessions.

Item 8.5 Stage 3 (Q-C Option a). Pre-Stage-3, Tally Migration Sessions in
status=Submitted have artefact links on the session itself
(``generated_je_draft``, ``generated_advance_je``, ``generated_oit_file``,
``student_ledger_file``) but no Migration Pass child rows. Stage 3's
pass-aware code expects at least one Migration Pass row on every Submitted
or Partial Submitted session.

This patch is a forward-compatibility shim:

- For each session with ``status == "Submitted"`` and empty
  ``migration_passes`` table, insert a synthetic Pass 1 row that mirrors
  the session-level artefact fields as of the Submitted timestamp.
- For each Mapping Decision on such a session that would have been emitted
  by a generator (review_action NOT IN ("Rejected", "Deferred",
  "Excluded (P&L)", "Excluded (Zero Balance)", "Pending*"), stamp
  ``generated_in_pass = 1``. Deferred rows remain with generated_in_pass
  NULL (correctly — they were never emitted).

Idempotent: skips any session that already has Migration Pass rows.

On a bench with zero existing Submitted sessions (erp.jewonline.in as of
Stage 3 deploy date 2026-04-25), this patch is a logical no-op. Value is:

  1. Future-proofs backup-restore scenarios where an older bench snapshot
     containing Submitted sessions is restored onto a Stage-3 codebase.
  2. Registration infrastructure: ``bench migrate`` records that the
     backfill has been applied, so re-installs won't double-stamp.

Architecture note: core logic takes Frappe dependencies as callables so
the pure function is unit-testable without a live bench. ``execute`` is
the thin wrapper.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# Review actions that generators SILENTLY SKIP (do not emit, do not refuse).
# Mapping Decisions with these actions remain generated_in_pass=NULL.
# Source: opening_je.py:162-170, oit_csv.py:150-158, advance_je.py:123-127.
_NON_EMITTED_REVIEW_ACTIONS: frozenset[str] = frozenset({
    "Pending",
    "Rejected",
    "Deferred",
    "Skipped",
    "Excluded (P&L)",
    "Excluded (Zero Balance)",
    "Pending Account Creation",
    "Pending Group Account Resolution",
    "Pending Supplier Creation",
    "Supplier Creation Requested",
    "Account Creation Requested",
})


def backfill_pass_1(
    get_submitted_session_names: Callable[[], list[str]],
    get_session_doc: Callable[[str], Any],
    get_decisions_for_session: Callable[[str], list[dict[str, Any]]],
    set_decision_pass: Callable[[str, int], None],
    save_session: Callable[[Any], None],
    now_datetime: Callable[[], Any],
) -> dict[str, int]:
    """Create Pass 1 rows + stamp generated_in_pass=1 for Submitted sessions.

    Returns a counter dict: ``{"sessions_backfilled": int,
    "decisions_stamped": int, "sessions_skipped_has_passes": int}``.
    """
    counters = {
        "sessions_backfilled": 0,
        "decisions_stamped": 0,
        "sessions_skipped_has_passes": 0,
    }

    for session_name in get_submitted_session_names():
        session = get_session_doc(session_name)

        # Idempotency: skip sessions that already have Migration Pass rows.
        existing_passes = getattr(session, "migration_passes", None) or []
        if existing_passes:
            counters["sessions_skipped_has_passes"] += 1
            continue

        # Build Pass 1 row from session-level artefact fields.
        generated_at = (
            getattr(session, "completed_at", None)
            or getattr(session, "modified", None)
            or now_datetime()
        )
        submitted_at = getattr(session, "modified", None) or now_datetime()

        decisions = get_decisions_for_session(session_name)
        emitted_count = sum(
            1 for d in decisions
            if d.get("review_action") not in _NON_EMITTED_REVIEW_ACTIONS
        )
        deferred_count = sum(
            1 for d in decisions
            if d.get("review_action") == "Deferred"
        )

        pass_row = {
            "pass_number": 1,
            "pass_status": "Submitted",
            "generated_at": generated_at,
            "submitted_at": submitted_at,
            "main_je_name": getattr(session, "generated_je_draft", None),
            "advance_je_name": getattr(session, "generated_advance_je", None),
            "oit_file_url": getattr(session, "generated_oit_file", None),
            "students_file_url": getattr(session, "student_ledger_file", None),
            "reference_id_main": getattr(session, "generated_je_reference", None),
            "reference_id_advance": getattr(
                session, "generated_advance_je_reference", None,
            ),
            "decisions_included_count": emitted_count,
            "decisions_deferred_count": deferred_count,
            "student_decisions_count": getattr(
                session, "student_ledger_count", 0,
            ) or 0,
            "temp_opening_contribution": getattr(
                session, "temp_opening_amount", 0,
            ) or 0,
            "generator_error_log": (
                "[backfill] Pass 1 row synthesized from pre-Stage-3 "
                "session-level artefact fields."
            ),
        }
        session.append("migration_passes", pass_row)
        save_session(session)
        counters["sessions_backfilled"] += 1

        # Stamp generated_in_pass=1 on decisions that were actually emitted.
        for d in decisions:
            if d.get("review_action") in _NON_EMITTED_REVIEW_ACTIONS:
                continue
            set_decision_pass(d["name"], 1)
            counters["decisions_stamped"] += 1

    return counters


def execute() -> None:
    """Frappe patch entry point — wraps :func:`backfill_pass_1`."""
    import frappe  # noqa: PLC0415 — deferred import for unit-testability

    def _get_submitted_session_names() -> list[str]:
        return [
            s["name"]
            for s in frappe.get_all(
                "Tally Migration Session",
                filters={"status": "Submitted"},
                fields=["name"],
            )
        ]

    def _get_session_doc(name: str) -> Any:
        return frappe.get_doc("Tally Migration Session", name)

    def _get_decisions_for_session(session_name: str) -> list[dict[str, Any]]:
        return frappe.get_all(
            "Mapping Decision",
            filters={"session": session_name},
            fields=["name", "review_action"],
        )

    def _set_decision_pass(decision_name: str, pass_number: int) -> None:
        frappe.db.set_value(
            "Mapping Decision",
            decision_name,
            "generated_in_pass",
            pass_number,
        )

    def _save_session(session: Any) -> None:
        # flags.ignore_permissions because patches run as Administrator;
        # avoid write-access warnings on nested saves.
        session.flags.ignore_permissions = True
        session.save()

    counters = backfill_pass_1(
        get_submitted_session_names=_get_submitted_session_names,
        get_session_doc=_get_session_doc,
        get_decisions_for_session=_get_decisions_for_session,
        set_decision_pass=_set_decision_pass,
        save_session=_save_session,
        now_datetime=frappe.utils.now_datetime,
    )

    frappe.db.commit()

    print(
        f"[backfill_pass_1] backfilled={counters['sessions_backfilled']} "
        f"sessions, stamped={counters['decisions_stamped']} decisions, "
        f"skipped={counters['sessions_skipped_has_passes']} sessions "
        f"(already had passes)."
    )
