"""One-time migration: Mapping Decision child rows → standalone records.

Runs via Frappe's patches.txt mechanism on ``bench migrate`` after the
``Mapping Decision.istable`` flip from 1 → 0. For each
``Tally Migration Session``, iterates its ``mapping_decisions`` child
table (if present on the pre-migration snapshot) and inserts each row
as a standalone ``Mapping Decision`` with the ``session`` Link field
populated.

Idempotent: skips any session that already has standalone
``Mapping Decision`` records linked. Safe to re-run after rollback or
partial-failure scenarios.

On a bench with zero existing sessions (e.g. the erp.jewonline.in
dev bench as of 2026-04-20), this patch is a logical no-op. Its value
is forward-compatibility for any bench where child rows already
exist, and as registration infrastructure so ``bench migrate`` records
that the migration has been applied.

Architecture note — the core ``migrate_decisions`` function takes
its Frappe dependencies as callables, so the logic is unit-testable
without a live bench. ``execute`` (the Frappe patch entry point) is
the thin wrapper that supplies the real Frappe implementations.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

# NOTE: `import frappe` is deliberately deferred into ``execute()`` so the
# unit tests in ``rgi_migration/tests/test_decisions_migration_patch.py``
# can import the pure ``migrate_decisions`` function and its helpers from
# a local pytest environment that doesn't have Frappe installed.


_FRAMEWORK_FIELDS: frozenset[str] = frozenset({
    "idx",
    "parent",
    "parenttype",
    "parentfield",
    "doctype",
    "docstatus",
    "creation",
    "modified",
    "modified_by",
    "owner",
    "name",
})


def execute() -> None:
    """Frappe patch entry point — wraps :func:`migrate_decisions`."""
    import frappe  # noqa: PLC0415 — deferred import; see module docstring

    migrate_decisions(
        get_session_names=lambda: [
            s["name"] for s in frappe.get_all("Tally Migration Session", fields=["name"])
        ],
        get_session_doc=lambda name: frappe.get_doc("Tally Migration Session", name),
        count_standalone=lambda session_name: frappe.db.count(
            "Mapping Decision", {"session": session_name}
        ),
        insert_standalone=lambda data: frappe.get_doc(data).insert(ignore_permissions=True),
        commit=frappe.db.commit,
        log=print,
    )


def migrate_decisions(
    *,
    get_session_names: Callable[[], list[str]],
    get_session_doc: Callable[[str], Any],
    count_standalone: Callable[[str], int],
    insert_standalone: Callable[[dict], Any],
    commit: Callable[[], None],
    log: Callable[[str], None],
) -> tuple[int, int]:
    """Testable core.

    Args:
        get_session_names: return every Tally Migration Session name.
        get_session_doc: fetch the Session document for a given name.
            Its ``.mapping_decisions`` attribute (if present) is the
            pre-migration child-table list.
        count_standalone: return the count of standalone Mapping Decision
            records already linked to a given session (idempotency probe).
        insert_standalone: persist one new Mapping Decision. Receives a
            dict of the field values, including ``doctype`` and ``session``.
        commit: flush the transaction.
        log: receive one line of status output.

    Returns:
        (total_migrated, total_skipped) — number of rows newly inserted
        vs. number of child rows skipped because the session already had
        standalone records.
    """

    session_names = get_session_names()
    total_migrated = 0
    total_skipped = 0

    for session_name in session_names:
        session_doc = get_session_doc(session_name)
        child_decisions = list(getattr(session_doc, "mapping_decisions", None) or [])

        if not child_decisions:
            continue

        existing = count_standalone(session_name)
        if existing > 0:
            log(
                f"Session {session_name}: {existing} standalone records already exist, "
                f"skipping {len(child_decisions)} child rows"
            )
            total_skipped += len(child_decisions)
            continue

        for child in child_decisions:
            data: dict[str, Any] = {
                "doctype": "Mapping Decision",
                "session": session_name,
            }
            for fieldname, value in _iter_child_fields(child):
                data[fieldname] = value
            insert_standalone(data)
            total_migrated += 1

        log(f"Session {session_name}: migrated {len(child_decisions)} decisions")

    commit()
    log(f"Migration complete: {total_migrated} migrated, {total_skipped} skipped (already standalone)")
    return total_migrated, total_skipped


def _iter_child_fields(child: Any) -> Iterable[tuple[str, Any]]:
    """Yield (fieldname, value) pairs for non-framework fields on a child row.

    Handles three input shapes:

    * Frappe ``Document`` (production path): uses ``as_dict()``.
    * ``dict`` (test fixture shape A): iterates items.
    * Plain object with attributes (test fixture shape B): iterates
      ``vars(obj).items()``.

    Framework fields (``parent``, ``idx``, ``creation``, etc.) are
    filtered so they don't leak into the new standalone record.
    """
    if hasattr(child, "as_dict") and callable(child.as_dict):
        source = child.as_dict()
    elif isinstance(child, dict):
        source = child
    else:
        source = vars(child)

    for fieldname, value in source.items():
        if fieldname in _FRAMEWORK_FIELDS:
            continue
        if value is None:
            continue
        yield fieldname, value
