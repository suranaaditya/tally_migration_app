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
import frappe.utils

from rgi_migration.rgi_migration.page.md_review.query import (
    DEFAULT_ORDER_BY,
    SAVE_DECISION_FIELDS,
    apply_decision_save,
    apply_decision_undo,
    build_account_autocomplete_results,
    compute_next_pending,
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

    result = fetch_decision_detail(
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

    # Section 4's ``final_account`` Link field filters the Account
    # autocomplete to the session's Company. The pure function returns
    # only the company abbr (for display labels); the full Company Link
    # value is a Frappe-wrapper concern, derived from the already-
    # fetched session_doc here to save the frontend a round-trip.
    result["session_erpnext_company"] = session_doc.erpnext_company
    return result


@frappe.whitelist()
def save_decision(decision_name, review_action, final_account, reviewer_notes):
    """Persist a Mapping Decision update from the review UI.

    Thin Frappe wrapper around :func:`query.apply_decision_save`. The
    pure function encodes the two non-trivial rules (derive
    ``final_dr`` / ``final_cr`` from ``opening_dr`` / ``opening_cr``;
    chronology-header prepend on ``reviewer_notes``); this wrapper
    handles the Frappe-specific concerns:

    1. Load the doc and permission-check against the linked session.
    2. Snapshot the pre-save state into ``frappe.cache()`` so
       ``undo_decision`` (Commit 5) can revert within the 10-second
       window. Caching happens BEFORE save so a save failure doesn't
       leave the user without an escape hatch.
    3. Apply the pure-function output onto the doc and call
       ``doc.save()`` — Frappe runs its standard validation here
       (Select-option validity, Link-target existence, required
       fields). Client-side validation (§1.6) prevents most
       failures before the round-trip, but the server-side guard
       is the source of truth.
    4. Return the refreshed doc as a dict so the frontend can re-
       render the detail pane immediately.

    Args:
        decision_name: The Mapping Decision name.
        review_action: The target ``review_action`` Select value.
        final_account: The target ``final_account`` Link value. May
            be empty string — the pure function normalises empty to
            ``None``.
        reviewer_notes: The reviewer's new note content (just the
            increment, not the accumulated history — the
            chronology-header logic concatenates).

    Returns:
        Dict form of the saved Mapping Decision.

    Raises:
        frappe.DoesNotExistError: if the decision or its session is
            missing.
        frappe.PermissionError: if the caller lacks read permission
            on the session.
    """
    decision_doc = frappe.get_doc("Mapping Decision", decision_name)
    session_doc = frappe.get_doc("Tally Migration Session", decision_doc.session)
    session_doc.check_permission("read")

    current = decision_doc.as_dict()

    # Snapshot for Undo — captured BEFORE mutation so a save failure
    # doesn't swallow the prior state. Only the fields that
    # save_decision itself writes are snapshotted; other fields can't
    # be mutated via this endpoint so there's nothing else to revert.
    snapshot = {k: current.get(k) for k in SAVE_DECISION_FIELDS}
    cache_key = f"mdr_undo:{frappe.session.user}:{decision_name}"
    frappe.cache().set_value(cache_key, json.dumps(snapshot, default=str), expires_in_sec=10)

    updates = apply_decision_save(
        current=current,
        review_action=review_action,
        final_account=final_account,
        reviewer_notes_input=reviewer_notes,
        session_user=frappe.session.user,
        now_str=frappe.utils.now_datetime().strftime("%Y-%m-%d %H:%M"),
    )

    for field, value in updates.items():
        decision_doc.set(field, value)
    decision_doc.save()

    return decision_doc.as_dict()


@frappe.whitelist()
def account_query_with_parent(
    doctype,
    txt,
    searchfield,
    start,
    page_len,
    filters,
):
    """Custom Link-query for ``final_account`` that shows parent_account.

    Wired into the Mapping Decision Review page's Section 4
    ``final_account`` Link control via ``df.get_query`` (see
    ``md_review.js``). Returns ``[name, description]`` rows so the
    autocomplete dropdown shows each candidate account with its
    parent + root type on the right — answers the spike feedback
    that reviewers need this disambiguation to pick correctly
    between similarly-named accounts across the COA.

    Company scoping is enforced by the caller (via ``filters``); this
    method adds the ``is_group=0`` guard server-side as a defense
    against a stale or malformed client-side filter.

    Args mirror the Frappe Link-query contract. ``txt`` is the
    user's typed prefix; ``filters`` MUST contain ``company``.

    Returns:
        ``list[list[str]]`` — each inner list is
        ``[account_name, description]``. Frappe renders element 0 as
        the primary autocomplete match text and element 1 as the
        muted subtitle.
    """
    filters = dict(filters) if filters else {}
    company = filters.get("company")
    if not company:
        # Without a company, results would span all companies — the
        # exact failure mode this endpoint exists to prevent. Refuse.
        frappe.throw("account_query_with_parent requires a 'company' filter")

    # Leaf-only: reviewers map Tally leaves to ERPNext leaves per
    # mapper_design_notes.md §7. Enforce here too — a client bug
    # that dropped is_group=0 from get_query would otherwise silently
    # let group accounts into the results.
    filters["is_group"] = 0
    # Also scope disabled accounts out of the picker.
    filters["disabled"] = 0

    accounts = frappe.get_all(
        "Account",
        filters=filters,
        or_filters=[
            ["name", "like", f"%{txt}%"],
            ["account_name", "like", f"%{txt}%"],
        ] if txt else None,
        fields=["name", "account_name", "parent_account", "root_type"],
        order_by="name asc",
        start=int(start or 0),
        page_length=int(page_len or 20),
    )

    return build_account_autocomplete_results(accounts)


@frappe.whitelist()
def undo_decision(decision_name):
    """Revert a Mapping Decision to its pre-save snapshot.

    Companion to :func:`save_decision`. The save side stashes a
    snapshot into ``frappe.cache()`` under
    ``mdr_undo:<user>:<decision_name>`` with a 10-second TTL; this
    method reads that snapshot, applies the pre-save values onto
    the doc, saves, and invalidates the cache entry so a second
    undo call is a no-op (single-use — matches Gmail-style Undo).

    The chronology header that ``save_decision`` prepended to
    ``reviewer_notes`` is discarded along with the rest of the
    undone save — undo means "never happened," not "also add a
    new header line."

    Args:
        decision_name: The Mapping Decision name to revert.

    Returns:
        Dict form of the reverted Mapping Decision (post-save).

    Raises:
        frappe.ValidationError: if no snapshot exists in the cache
            (either never saved, or the 10-second TTL expired). The
            frontend surfaces this as "Too late to undo" per §1.7.
        frappe.DoesNotExistError: if the decision or its session is
            missing.
        frappe.PermissionError: if the caller lacks read permission
            on the session.
    """
    cache_key = f"mdr_undo:{frappe.session.user}:{decision_name}"
    raw_snapshot = frappe.cache().get_value(cache_key)
    if not raw_snapshot:
        frappe.throw(
            "Undo window expired — the 10-second cache entry is gone.",
            title="Too late to undo",
        )

    # Snapshot was stored as JSON string (default=str handled any
    # datetime/Currency edge cases on write). Decode back to dict.
    snapshot = json.loads(raw_snapshot) if isinstance(raw_snapshot, str) else raw_snapshot

    decision_doc = frappe.get_doc("Mapping Decision", decision_name)
    session_doc = frappe.get_doc("Tally Migration Session", decision_doc.session)
    session_doc.check_permission("read")

    current = decision_doc.as_dict()
    restored = apply_decision_undo(current=current, snapshot=snapshot)

    for field, value in restored.items():
        decision_doc.set(field, value)
    decision_doc.save()

    # Single-use — invalidate immediately so a second undo raises.
    # Also blocks a stale snapshot from a parallel reviewer's save
    # (snapshot is keyed by user, so cross-reviewer contention isn't
    # possible, but defensive).
    frappe.cache().delete_value(cache_key)

    return decision_doc.as_dict()


@frappe.whitelist()
def get_next_pending(
    session_name,
    after_decision_name,
    after_position=None,
    filters=None,
):
    """Return the name of the decision to auto-advance to after a save.

    Implements §1.7 auto-advance step 4: given the session, the
    just-saved decision, and the reviewer's current filter state,
    find "what's next." The response also carries the refreshed
    filtered count so the frontend can distinguish
    "end-of-list but still rows to resolve" from "all done — show
    empty state."

    Note on ``after_position``: this is a spec extension over the
    §1.12 three-arg signature. §1.7 requires "stay at the same
    index" when the just-saved decision dropped out of the filter
    (e.g. saved as Approved while Pending filter is active). The
    server can only honour that request if the client tells it
    where the row USED to be — the refreshed list doesn't contain
    that information. Optional; falls through to ``None`` if
    absent.

    Args:
        session_name: The session being reviewed.
        after_decision_name: The just-saved decision's name.
        after_position: 0-based index the decision had in the
            pre-save filtered list. Used only when
            ``after_decision_name`` dropped out of the refreshed
            list. May be ``None`` — then the dropped-out case
            returns ``None``.
        filters: Additional filter dict matching the client's
            current filter preset (e.g.
            ``{"review_action": ["in", [...pending variants...]]}``).

    Returns:
        ``{"next_decision_name": <name> or None, "filtered_count": int}``

    Raises:
        frappe.DoesNotExistError: if the session is missing.
        frappe.PermissionError: if the caller lacks read permission
            on the session.
    """
    if isinstance(filters, str):
        filters = json.loads(filters) if filters else None

    if isinstance(after_position, str):
        # Frappe serialises ints as strings over the wire; coerce
        # back. Empty string → None (no position hint).
        after_position = int(after_position) if after_position.strip() else None

    session_doc = frappe.get_doc("Tally Migration Session", session_name)
    session_doc.check_permission("read")

    # Session-scope guard — identical pattern to
    # fetch_session_decisions. Caller can't override session via
    # filters; the argument wins.
    query_filters: dict = dict(filters) if filters else {}
    query_filters["session"] = session_name

    decisions = frappe.get_all(
        "Mapping Decision",
        filters=query_filters,
        fields=["name"],
        order_by=DEFAULT_ORDER_BY,
    )

    next_name = compute_next_pending(
        decisions=decisions,
        after_name=after_decision_name,
        after_position=after_position,
    )

    return {
        "next_decision_name": next_name,
        "filtered_count": len(decisions),
    }
