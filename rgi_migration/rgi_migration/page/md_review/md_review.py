"""
Mapping Decision Review — backend whitelist methods.

Scope progression:

* Commit 2 (infrastructure): four stubs raising NotImplementedError.
* Commit 3 (this commit): ``get_session_decisions`` implemented as a
  thin Frappe wrapper around :func:`query.fetch_session_decisions`.
  The other three stubs remain NotImplementedError — they land in
  Commit 5 per ``docs/week4_review_ui_design.md §1.12``.
"""

import json

import frappe

from rgi_migration.rgi_migration.page.md_review.query import (
    fetch_decision_detail,
    fetch_session_decisions,
)


@frappe.whitelist()
def get_session_decisions(
    session_name,
    filters=None,
    start=0,
    page_length=50,
    order_by=None,
):
    """Return Mapping Decisions for a session with filtering + pagination.

    Used by the Mapping Decision Review page's master pane. Thin
    Frappe wrapper around :func:`query.fetch_session_decisions`; all
    query-building and session-scope-guard logic lives there, so the
    test suite can exercise it without Frappe.

    Session existence is enforced by the ``frappe.get_doc`` call below
    (raises ``DoesNotExistError`` on a missing session). Read
    permission on the session is enforced by
    ``session_doc.check_permission("read")``.

    Args, return shape, and raises: see :func:`query.fetch_session_decisions`.
    Additional behavior unique to this wrapper:

    * ``filters`` may arrive as a JSON string (standard Frappe client
      serialisation). Parsed to dict before delegating.
    * ``frappe.DoesNotExistError`` propagates from ``get_doc``.
    """
    # Frappe's ``frappe.call`` serialises dict args over the wire as
    # JSON strings for HTTP transport. Parse back to dict before the
    # core function sees it.
    if isinstance(filters, str):
        filters = json.loads(filters) if filters else None

    # Existence + permission check (both enforced by Frappe).
    session_doc = frappe.get_doc("Tally Migration Session", session_name)

    return fetch_session_decisions(
        session_name=session_name,
        filters=filters,
        start=start,
        page_length=page_length,
        order_by=order_by,
        check_permission=lambda: session_doc.check_permission("read"),
        get_all=lambda **kw: frappe.get_all("Mapping Decision", **kw),
        count=lambda f: frappe.db.count("Mapping Decision", f),
    )


@frappe.whitelist()
def get_decision_detail(decision_name):
    """Return a single Mapping Decision with docinfo + session company abbr.

    Used by the Mapping Decision Review page's detail pane (§1.4 sections
    1, 2, 5, 6 — read-only content; Section 4's writable Reviewer Action
    widgets land in Commit 4b).

    Thin Frappe wrapper around :func:`query.fetch_decision_detail`. The
    wrapper fetches both the decision doc and its linked session doc up
    front — this naturally raises ``frappe.DoesNotExistError`` if either
    is missing. The session doc is then closed over by the permission-
    check and company-abbr callables so the pure function doesn't need
    to know anything about the Frappe object model.

    Permission model: session read-permission (same as
    :func:`get_session_decisions`). Users who can read the session can
    read every decision linked to it.
    """
    import frappe.desk.form.load as form_load

    # Existence check for both docs happens here (via frappe.get_doc's
    # natural DoesNotExistError). The fetch_decision_detail pure function
    # takes the already-fetched data via closures below.
    decision_doc = frappe.get_doc("Mapping Decision", decision_name)
    session_doc = frappe.get_doc("Tally Migration Session", decision_doc.session)

    return fetch_decision_detail(
        decision_name=decision_name,
        get_decision_fn=lambda _name: decision_doc.as_dict(),
        get_session_company_fn=lambda _session_name: session_doc.company_abbr,
        # Assignment section (§1.4 Section 5) only needs the assignments
        # list, not the full docinfo payload. Calling get_docinfo directly
        # doesn't work outside HTTP context — it writes to
        # frappe.response["docinfo"] instead of returning (see
        # frappe/desk/form/load.py:134). get_assignments returns cleanly.
        # Shape of response preserves {"assignments": [...]} so the frontend
        # detail-pane code can read docinfo.assignments uniformly.
        get_docinfo_fn=lambda _name: {
            "assignments": form_load.get_assignments("Mapping Decision", decision_name),
        },
        permission_check_fn=lambda: session_doc.check_permission("read"),
    )


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
