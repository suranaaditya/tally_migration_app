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
    SUPPLIER_SAVE_FIELDS,
    _apply_chronology_header,
    apply_bulk_approve,
    apply_decision_save,
    apply_decision_undo,
    apply_scr_approval_to_decision,
    apply_scr_rejection_to_decision,
    apply_supplier_resolution,
    build_account_autocomplete_results,
    build_account_parent_autocomplete_results,
    build_scr_payload,
    build_supplier_autocomplete_results,
    build_supplier_doc_payload,
    compute_next_pending,
    fetch_decision_detail,
    fetch_session_decisions,
    parse_source_decisions_csv,
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
    previous_review_action = current.get("review_action")

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

    # Item 3 Commit 2 followup: when a decision transitions out of
    # 'Supplier Creation Requested' (e.g., via Reject / Defer), clean
    # up any orphan Pending SCR row on the session.
    _cleanup_pending_scr_on_transition(decision_doc, previous_review_action)

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
def account_parent_query(
    doctype,
    txt,
    searchfield,
    start,
    page_len,
    filters,
):
    """Custom Link-query for the AccountResolutionDialog parent picker.

    Parallel to :func:`account_query_with_parent` but for group
    accounts (Item 4 Commit 1, SUB-1). Two server-side filters are
    enforced regardless of caller input to prevent a stale client
    filter from bypassing the invariant:

    * ``is_group = 1`` — parent accounts must be groups. A leaf parent
      would break ERPNext's tree model.
    * ``root_type = <caller-supplied>`` — restricts candidates to the
      decision's ``tally_root_type`` branch. Caller passes it via
      ``filters``; missing / blank caller input raises (the whole
      point of this endpoint is the branch filter).

    ``disabled = 0`` is also enforced (same reason as the peer query:
    reviewer-picking a disabled parent would later fail Account insert
    validation at ACR-approve time).

    The pure-core formatter handles the depth-then-alpha sort — see
    :func:`query.build_account_parent_autocomplete_results` for the
    "tree-ordered" intent.

    Args mirror the Frappe Link-query contract. ``filters`` MUST
    contain ``company`` and ``root_type``.

    Returns:
        ``list[list[str]]`` — each inner list is
        ``[account_name, description]``.
    """
    filters = dict(filters) if filters else {}
    company = filters.get("company")
    if not company:
        frappe.throw("account_parent_query requires a 'company' filter")

    root_type = filters.get("root_type")
    if not root_type:
        frappe.throw(
            "account_parent_query requires a 'root_type' filter "
            "(derived from the decision's tally_root_type)",
        )

    # Re-pin the server-owned invariants after the caller's dict is
    # consumed — a client attempting to pass is_group=0 (to get leaf
    # candidates) or disabled=1 (to see disabled rows) is ignored.
    filters["is_group"] = 1
    filters["disabled"] = 0
    filters["root_type"] = root_type

    accounts = frappe.get_all(
        "Account",
        filters=filters,
        or_filters=[
            ["name", "like", f"%{txt}%"],
            ["account_name", "like", f"%{txt}%"],
        ] if txt else None,
        fields=["name", "account_name", "parent_account", "root_type"],
        # lft ordering gives a tree pre-order, which the depth-sort pure
        # core then re-orders to breadth-first. Keep name desc tie-break
        # so the pure-core sort is deterministic on name ties within a
        # depth bucket (shouldn't happen on a clean COA but defensive).
        order_by="lft asc, name asc",
        start=int(start or 0),
        # Raise from the peer query's 20 to 50 because the parent-
        # picker branch has a narrower candidate pool per row-load
        # (only groups in the right root_type) and reviewers benefit
        # from seeing the full first-level before scrolling.
        page_length=int(page_len or 50),
    )

    return build_account_parent_autocomplete_results(accounts)


@frappe.whitelist()
def get_account_tree(company, root_type):
    """Return all group accounts in a company's root_type branch as a
    flat list, ordered by ``lft`` (tree pre-order).

    Backs the AccountResolutionDialog's in-dialog tree picker (Item 4
    Commit 1, AMB-10 option C re-decision). Parent candidates must be
    groups, so the tree only needs group nodes — leaf accounts never
    appear as parents. At CACSPU scale the biggest branch has ~18
    groups; fetching the whole branch once is faster than lazy-fetching
    children per-expand and keeps the frontend code simpler.

    ``lft`` order gives us tree pre-order, which the client uses to
    rebuild the hierarchy via ``parent_account`` links without needing
    a recursive SQL query.

    Args:
        company: ERPNext Company name.
        root_type: One of Asset / Liability / Equity / Income /
            Expense. Required — the tree is always branch-scoped.

    Returns:
        ``[{"name", "account_name", "parent_account", "lft", "rgt"}, ...]``

    Raises:
        frappe.ValidationError: missing company or root_type.
    """
    if not company:
        frappe.throw("get_account_tree requires a 'company' argument")
    if not root_type:
        frappe.throw("get_account_tree requires a 'root_type' argument")

    accounts = frappe.get_all(
        "Account",
        filters={
            "company": company,
            "root_type": root_type,
            "is_group": 1,
            "disabled": 0,
        },
        fields=["name", "account_name", "parent_account", "lft", "rgt"],
        order_by="lft asc",
    )
    return accounts


@frappe.whitelist()
def supplier_query(
    doctype, txt, searchfield, start, page_len, filters
):
    """Custom autocomplete query for the supplier resolution dialog's
    picker (Item 3 Commit 1b).

    Parallel to :func:`account_query_with_parent` — Frappe's default
    Link autocomplete shows just the Supplier name; this custom query
    appends the ``supplier_group`` as the grey subtitle so reviewers
    can disambiguate same-named suppliers or orient by industry /
    category at a glance.

    Always filters ``disabled=0`` at query time — a reviewer picking
    a disabled Supplier would fail the generator's re-check pass
    (``docs/mapper_design_notes.md §8.3`` Order A). Keeping disabled
    Suppliers out of the picker prevents that downstream failure.

    Args:
        doctype: Frappe-supplied; always ``"Supplier"``. Ignored.
        txt: Reviewer's typed substring, or empty.
        searchfield: Frappe-supplied field name to OR-match; ignored
            in favour of our matched-on-name-OR-supplier_name OR_FILTERS.
        start: Pagination offset.
        page_len: Pagination page length.
        filters: Frappe-supplied caller filters. The ``disabled=0``
            constraint is applied here regardless.

    Returns:
        List of ``[name, description]`` pairs for the Link autocomplete.
    """
    _ = doctype, searchfield, filters  # unused

    suppliers = frappe.get_all(
        "Supplier",
        filters={"disabled": 0},
        or_filters=[
            ["name", "like", f"%{txt}%"],
            ["supplier_name", "like", f"%{txt}%"],
        ] if txt else None,
        fields=["name", "supplier_name", "supplier_group"],
        order_by="name asc",
        start=int(start or 0),
        page_length=int(page_len or 20),
    )

    return build_supplier_autocomplete_results(suppliers)


@frappe.whitelist()
def save_supplier_resolution(decision_name, final_supplier, reviewer_notes):
    """Persist a supplier resolution from the rich dialog (Item 3 Commit 1b).

    Called when the reviewer picks a Supplier via the Map-to-existing
    path. Writes four fields (see :data:`SUPPLIER_SAVE_FIELDS`):
    ``final_supplier``, ``tier = "tier1_supplier_exact"``,
    ``review_action = "Approved"``, and ``reviewer_notes`` (chronology-
    header-prepended via the shared pure-core helper).

    Guards:

    * ``final_supplier`` must be non-empty and reference an existing,
      non-disabled Supplier. Empty or unknown values fail here so the
      reviewer sees a precise error instead of a downstream Link
      validation error on ``doc.save()``.

    Participates in the Undo pattern: snapshots the pre-save state of
    ``SUPPLIER_SAVE_FIELDS`` into ``frappe.cache()`` with a 10-second
    TTL. The :func:`undo_decision` method is shape-agnostic (Item 3
    Commit 1b) so the same endpoint restores either shape.

    Args:
        decision_name: The Mapping Decision name to mutate.
        final_supplier: The reviewer's picked Supplier doc ID.
        reviewer_notes: New note content (increment only; chronology
            logic concatenates with any stored history).

    Returns:
        Dict form of the saved Mapping Decision (post-save).

    Raises:
        frappe.ValidationError: final_supplier empty, unknown, or
            disabled.
        frappe.DoesNotExistError: decision or session missing.
        frappe.PermissionError: caller lacks session read permission.
    """
    if not final_supplier:
        frappe.throw(
            "final_supplier is required. Pick a Supplier from the "
            "dropdown or switch to Create new.",
        )

    supplier_row = frappe.db.get_value(
        "Supplier", final_supplier, ["name", "disabled"], as_dict=True,
    )
    if not supplier_row:
        frappe.throw(f"Supplier {final_supplier!r} does not exist.")
    if supplier_row.get("disabled"):
        frappe.throw(
            f"Supplier {final_supplier!r} is disabled. "
            "Re-enable it in the Supplier master or pick a different "
            "Supplier.",
        )

    decision_doc = frappe.get_doc("Mapping Decision", decision_name)
    session_doc = frappe.get_doc(
        "Tally Migration Session", decision_doc.session
    )
    session_doc.check_permission("read")

    current = decision_doc.as_dict()
    previous_review_action = current.get("review_action")

    # Snapshot the pre-save state for Undo — shape follows
    # SUPPLIER_SAVE_FIELDS (not SAVE_DECISION_FIELDS) so undo_decision
    # restores exactly what save_supplier_resolution wrote. The cache
    # key space is shared with save_decision; one reviewer can only
    # have one pending undo at a time per decision, which matches the
    # 10-second Gmail-style semantics.
    snapshot = {k: current.get(k) for k in SUPPLIER_SAVE_FIELDS}
    cache_key = f"mdr_undo:{frappe.session.user}:{decision_name}"
    frappe.cache().set_value(
        cache_key,
        json.dumps(snapshot, default=str),
        expires_in_sec=10,
    )

    updates = apply_supplier_resolution(
        current=current,
        final_supplier=final_supplier,
        reviewer_notes_input=reviewer_notes,
        session_user=frappe.session.user,
        now_str=frappe.utils.now_datetime().strftime("%Y-%m-%d %H:%M"),
    )

    for field, value in updates.items():
        decision_doc.set(field, value)
    decision_doc.save()

    # Item 3 Commit 2 followup: reviewer switched from Create-new to
    # Map-to-existing via the dialog → pending SCR for this decision
    # is stale. Clean it up so approver doesn't process a supersede
    # request.
    _cleanup_pending_scr_on_transition(decision_doc, previous_review_action)

    return decision_doc.as_dict()


_SCR_CREATE_REFUSED_STATUSES = frozenset({"Submitted", "Cancelled"})


def _cleanup_pending_scr_on_transition(decision_doc, previous_review_action):
    """Delete orphan Pending SCR row when a decision transitions out of
    'Supplier Creation Requested' (Item 3 Commit 2 followup).

    When a reviewer Rejects / Defers / remaps-via-dialog a decision that
    previously had an SCR request, the SCR row on the session is stale:
    the approver should not process a request for a decision the reviewer
    has since changed their mind about. Deleting the Pending SCR keeps
    the Session form honest.

    Scope:
      * Only fires when the decision WAS 'Supplier Creation Requested'
        before this save AND is now something else.
      * Only deletes Pending SCRs. Created / Skipped / Failed SCRs are
        preserved — those are terminal states from the approver's side
        (a Created SCR means a Supplier record was actually created in
        ERPNext; deleting would lose audit trail). Reviewer rejecting
        an already-Created SCR should ideally warn them; for now the
        SCR just stays as an auditable record of what happened.
      * Only touches SCRs whose source_decisions CSV references this
        decision.

    Args:
        decision_doc: The Mapping Decision doc after its review_action
            mutation + save.
        previous_review_action: The value BEFORE the save. Caller is
            responsible for capturing this before the mutation.

    Returns:
        List of deleted SCR row names (empty when no transition / no
        matching Pending SCRs).
    """
    if previous_review_action != "Supplier Creation Requested":
        return []
    if decision_doc.review_action == "Supplier Creation Requested":
        return []

    session_doc = frappe.get_doc(
        "Tally Migration Session", decision_doc.session
    )
    keep = []
    deleted = []
    for row in (session_doc.supplier_creation_requests or []):
        tokens = parse_source_decisions_csv(row.source_decisions)
        if decision_doc.name in tokens and row.status == "Pending":
            deleted.append(row.name)
        else:
            keep.append(row)

    if deleted:
        session_doc.supplier_creation_requests = keep
        session_doc.save(ignore_permissions=True)
        frappe.db.commit()

    return deleted


@frappe.whitelist()
def create_supplier_creation_request(
    decision_name,
    proposed_supplier_name,
    supplier_group,
    reviewer_notes,
):
    """Create-new path submit (Item 3 Commit 2).

    Inserts a Supplier Creation Request child row on the decision's
    parent session, flags the decision as ``Pending Supplier Creation``,
    and appends the reviewer's note to the decision's reviewer_notes
    via the shared chronology-header helper.

    Design decision (per 2026-04-22 authorisation): this path does NOT
    participate in the Undo toast pattern. SCR creation has side
    effects (child row insert, potential cascading Supplier creation
    in Commit 3) that make a clean Undo expensive. Reviewer recovery
    is manual — re-open the dialog + pick a different action, or
    clean up via the Session form / Supplier list later. The frontend
    shows a plain "Saved" toast without an Undo button to distinguish
    from undoable saves.

    Guards:

    * Session status must not be ``Submitted`` or ``Cancelled`` —
      those are terminal states where SCR creation is meaningless.
    * No existing SCR on this session can already reference the
      decision (exact-token CSV match on ``source_decisions``).
      Protects against fast-double-click duplicates + explicit re-
      submits.
    * ``proposed_supplier_name`` required.

    Args:
        decision_name: The Mapping Decision being resolved.
        proposed_supplier_name: Reviewer's typed supplier name.
        supplier_group: Reviewer's typed group (may be empty; SCR
            approval in Commit 3 will require it before Supplier
            creation).
        reviewer_notes: New note content. Written verbatim to the SCR
            row; chronology-header-prepended to the Mapping Decision.

    Returns:
        ``{"status": "ok", "scr_row_name": str, "decision": dict}``.

    Raises:
        frappe.ValidationError: guard failure (status, duplicate,
            empty name).
        frappe.DoesNotExistError: decision or session missing.
        frappe.PermissionError: caller lacks session read permission.
    """
    if not proposed_supplier_name or not str(proposed_supplier_name).strip():
        frappe.throw("Proposed supplier name is required.")
    proposed_supplier_name = str(proposed_supplier_name).strip()

    decision_doc = frappe.get_doc("Mapping Decision", decision_name)
    session_doc = frappe.get_doc(
        "Tally Migration Session", decision_doc.session
    )
    session_doc.check_permission("read")

    if session_doc.status in _SCR_CREATE_REFUSED_STATUSES:
        frappe.throw(
            f"Session {session_doc.name!r} has status {session_doc.status!r}; "
            f"Supplier Creation Request creation is no longer allowed "
            f"(refused statuses: {sorted(_SCR_CREATE_REFUSED_STATUSES)}).",
        )

    # Duplicate-refusal guard: exact-token CSV match on existing SCRs.
    # source_decisions is a Small Text CSV field; substring match
    # would false-positive on MD-2026-00001 ∈ MD-2026-00010 and friends,
    # so parse + compare tokens explicitly. Empty source_decisions
    # (possible on legacy rows) is treated as "no match."
    for existing in (session_doc.supplier_creation_requests or []):
        tokens = parse_source_decisions_csv(existing.source_decisions)
        if decision_name in tokens:
            frappe.throw(
                f"An SCR row already exists for decision "
                f"{decision_name!r} on this session (SCR status: "
                f"{existing.status!r}). Edit it on the Session form, "
                f"or mark the existing row Rejected before creating a "
                f"new one.",
            )

    # Build + append the child row via pure-core helper.
    payload = build_scr_payload(
        decision=decision_doc.as_dict(),
        proposed_supplier_name=proposed_supplier_name,
        supplier_group=supplier_group,
        reviewer_notes=reviewer_notes,
    )
    session_doc.append("supplier_creation_requests", payload)

    # Append chronology-header note to the decision in parallel. The
    # SCR's reviewer_notes carries the verbatim reviewer comment;
    # the Mapping Decision's reviewer_notes accumulates the full audit
    # trail like every other save path on the page.
    current = decision_doc.as_dict()
    decision_doc.reviewer_notes = _apply_chronology_header(
        stored_notes=current.get("reviewer_notes") or "",
        new_input=reviewer_notes or "",
        session_user=frappe.session.user,
        now_str=frappe.utils.now_datetime().strftime("%Y-%m-%d %H:%M"),
    )
    # "Supplier Creation Requested" is the reviewer-has-acted state —
    # distinguishes from "Pending Supplier Creation" (untouched) so the
    # reviewer's Pending filter stops surfacing rows they've already
    # initiated SCRs on. Indicator renders green (done); generator
    # refusal is unaffected because gates are tier-based, not
    # review_action-based. Commit 3's SCR approval flow will transition
    # this to "Approved" + lift the tier to tier1_supplier_exact.
    decision_doc.review_action = "Supplier Creation Requested"
    # tier intentionally unchanged — still pending_supplier_creation.
    # Commit 3's SCR approval flow will lift tier to tier1_supplier_exact.

    session_doc.save(ignore_permissions=True)
    decision_doc.save(ignore_permissions=True)
    frappe.db.commit()

    # Resolve the freshly-inserted SCR row's name for the response.
    # Frappe generates a hash-like name on save (SCR DocType has
    # autoname=None). Last row in the child list is the new one.
    new_row = session_doc.supplier_creation_requests[-1] if session_doc.supplier_creation_requests else None

    return {
        "status": "ok",
        "scr_row_name": new_row.name if new_row else None,
        "decision": decision_doc.as_dict(),
    }


# ---------------------------------------------------------------------------
# Item 3 Commit 3 — SCR approval / rejection workflow
# ---------------------------------------------------------------------------


_SCR_ACTION_ALLOWED_STATUSES = frozenset({"Pending", "Failed"})
_SCR_ACTION_TERMINAL_STATUSES = frozenset({"Created", "Skipped"})


def _append_scr_error_log(scr_row, block: str) -> None:
    """Append a timestamped block to an SCR's error_log. Matches the
    ``session.error_log`` convention from the Week-3 generators."""
    existing = scr_row.error_log or ""
    separator = "\n\n" if existing else ""
    scr_row.error_log = f"{existing}{separator}{block}"


def _iso_timestamp() -> str:
    return frappe.utils.now_datetime().isoformat(timespec="seconds")


@frappe.whitelist()
def approve_scr(session_name, scr_row_name):
    """Approve a Supplier Creation Request: create the Supplier record
    and write-back to the source Mapping Decision(s).

    Flow:
      1. Load session + locate the SCR child row by name.
      2. Validate SCR.status in {Pending, Failed}; refuse on Created /
         Skipped with explicit message.
      3. Build Supplier payload (supplier_type defaults to Company per
         Item 3 Commit 3 AMB — reviewer edits on Supplier form
         post-creation for Individual / Partnership edge cases).
      4. Insert Supplier via Frappe ORM. Read the RESOLVED
         Supplier.name post-insert (Frappe may suffix on naming
         collision). On failure, set SCR.status=Failed, append
         error_log block, source decisions untouched, return error.
      5. Update SCR: status=Created, created_supplier=<resolved>,
         clear error_log on first-time success (preserve on retry
         success as history).
      6. Loop each decision name in SCR.source_decisions CSV:
         set final_supplier + tier=tier1_supplier_exact + review_action=Approved.
         Mid-loop failures: continue, aggregate errors into
         error_log (reviewer can retry via Approve-on-Failed to
         complete partial state).

    Empty / malformed ``source_decisions`` → SCR goes Created with a
    warning appended to error_log; no decision write-back. Defensive
    benign (matches AMB Q6 resolution).

    Returns ``{"status": "ok", "created_supplier": str, "updated_decisions": [str, ...], "scr_status": "Created"}``
    on success. On Supplier.insert failure returns
    ``{"status": "failed", "scr_status": "Failed", "error": str}``
    (200 OK — caller inspects ``status`` field; not an exception
    because the UI needs to refresh the SCR row's error_log display).
    """
    session_doc = frappe.get_doc("Tally Migration Session", session_name)
    session_doc.check_permission("read")

    scr_row = _find_scr_row(session_doc, scr_row_name)
    if scr_row is None:
        frappe.throw(f"SCR row {scr_row_name!r} not found on session {session_name!r}.")

    if scr_row.status in _SCR_ACTION_TERMINAL_STATUSES:
        if scr_row.status == "Created":
            frappe.throw(
                f"SCR already created Supplier {scr_row.created_supplier!r}. "
                f"Edit that Supplier directly, or create a new SCR if you need "
                f"a different record."
            )
        frappe.throw(
            f"SCR was Rejected ({scr_row.status!r}). Create a new SCR on the "
            f"decision if you've changed your mind."
        )

    if not scr_row.proposed_supplier_name or not (scr_row.proposed_supplier_name or "").strip():
        frappe.throw("SCR proposed_supplier_name is empty — cannot approve.")

    # --- Build + insert Supplier ---
    payload = build_supplier_doc_payload(scr_row=scr_row.as_dict())
    try:
        new_supplier = frappe.get_doc(payload).insert(ignore_permissions=True)
        resolved_name = new_supplier.name
    except Exception as exc:
        # Mark SCR Failed with context; source decisions untouched.
        block = (
            f"[{_iso_timestamp()}] approve_scr failed creating Supplier: "
            f"{type(exc).__name__}: {exc}"
        )
        _append_scr_error_log(scr_row, block)
        scr_row.status = "Failed"
        session_doc.save(ignore_permissions=True)
        frappe.db.commit()
        return {
            "status": "failed",
            "scr_status": "Failed",
            "error": str(exc),
            "error_type": type(exc).__name__,
        }

    # --- Update SCR on Supplier.insert success ---
    scr_row.status = "Created"
    scr_row.created_supplier = resolved_name
    # Leave error_log intact on retry success — prior-attempt history
    # is useful audit trail. Fresh-approve with no prior failures
    # starts with empty error_log already.

    # --- Loop source decisions ---
    decision_names = parse_source_decisions_csv(scr_row.source_decisions)
    updated = []
    per_decision_errors: list[str] = []

    if not decision_names:
        _append_scr_error_log(
            scr_row,
            f"[{_iso_timestamp()}] approve succeeded — Supplier "
            f"{resolved_name!r} created — but no source_decisions CSV "
            f"tokens were found. SCR has no Mapping Decisions to write "
            f"back to. Check the SCR's source_decisions field manually.",
        )

    for md_name in decision_names:
        try:
            decision_doc = frappe.get_doc("Mapping Decision", md_name)
            updates = apply_scr_approval_to_decision(
                resolved_supplier_name=resolved_name
            )
            for field, value in updates.items():
                decision_doc.set(field, value)
            decision_doc.save(ignore_permissions=True)
            updated.append(md_name)
        except Exception as exc:  # noqa: BLE001 — aggregate, don't mask
            per_decision_errors.append(
                f"  - {md_name}: {type(exc).__name__}: {exc}"
            )

    if per_decision_errors:
        block = (
            f"[{_iso_timestamp()}] approve succeeded for Supplier "
            f"{resolved_name!r} but {len(per_decision_errors)}/"
            f"{len(decision_names)} source decision update(s) failed:\n"
            + "\n".join(per_decision_errors)
            + "\nRetry via Approve-on-Failed to complete the remaining writes."
        )
        _append_scr_error_log(scr_row, block)

    session_doc.save(ignore_permissions=True)
    frappe.db.commit()

    return {
        "status": "ok",
        "scr_status": "Created",
        "created_supplier": resolved_name,
        "updated_decisions": updated,
        "failed_decisions": len(per_decision_errors),
    }


@frappe.whitelist()
def reject_scr(session_name, scr_row_name):
    """Reject a Supplier Creation Request: close the SCR (status=Skipped)
    and flag source decisions as review_action=Rejected.

    Per Item 3 Commit 3 AMB Q4: decision.tier stays
    ``pending_supplier_creation`` (mapper-authoritative). Generator
    refusal gates in oit_csv + advance_je now SKIP rows where
    review_action=Rejected, so rejected rows silently drop out of
    the generator pipeline without contributing or refusing.

    Validates SCR.status in {Pending, Failed} (reject accepted on both —
    Failed SCR that reviewer gives up retrying goes Skipped).

    Returns ``{"status": "ok", "scr_status": "Skipped", "updated_decisions": [...]}``.
    """
    session_doc = frappe.get_doc("Tally Migration Session", session_name)
    session_doc.check_permission("read")

    scr_row = _find_scr_row(session_doc, scr_row_name)
    if scr_row is None:
        frappe.throw(f"SCR row {scr_row_name!r} not found on session {session_name!r}.")

    if scr_row.status in _SCR_ACTION_TERMINAL_STATUSES:
        if scr_row.status == "Created":
            frappe.throw(
                f"Cannot reject — SCR already created Supplier "
                f"{scr_row.created_supplier!r}. Delete that Supplier from the "
                f"master if you need to undo."
            )
        frappe.throw("SCR was already rejected (status=Skipped).")

    scr_row.status = "Skipped"

    decision_names = parse_source_decisions_csv(scr_row.source_decisions)
    updated = []
    for md_name in decision_names:
        try:
            decision_doc = frappe.get_doc("Mapping Decision", md_name)
            updates = apply_scr_rejection_to_decision()
            for field, value in updates.items():
                decision_doc.set(field, value)
            decision_doc.save(ignore_permissions=True)
            updated.append(md_name)
        except Exception as exc:  # noqa: BLE001
            _append_scr_error_log(
                scr_row,
                f"[{_iso_timestamp()}] reject: decision {md_name} update "
                f"failed: {type(exc).__name__}: {exc}",
            )

    session_doc.save(ignore_permissions=True)
    frappe.db.commit()

    return {
        "status": "ok",
        "scr_status": "Skipped",
        "updated_decisions": updated,
    }


@frappe.whitelist()
def bulk_approve_scrs(session_name, scr_row_names):
    """Approve a batch of SCR rows in one RPC.

    Wraps ``approve_scr`` per row, isolating failures. Single-row
    failures don't block the remaining SCRs — each row's state
    (Created / Failed) reflects its own outcome.

    Use case: Reviewer has 18 CACSPU pending_supplier_creation rows
    all with clean proposed names; clicks Select All → Approve
    Selected → one RPC kicks off the batch. Response carries
    per-row outcomes so the UI can display which succeeded and
    which need follow-up.

    Args:
        session_name: The session.
        scr_row_names: JSON-serialised list of SCR row names (Frappe
            whitelist serialises array args as JSON over the wire —
            accept both the JSON string and a raw list for testability).

    Returns:
        ``{"ok": [<row_name>, ...], "failed": [{"scr": <row_name>, "error": <str>}, ...]}``
    """
    row_names = _parse_whitelist_list_arg(scr_row_names)
    ok: list[str] = []
    failed: list[dict] = []
    for row_name in row_names:
        try:
            result = approve_scr(session_name=session_name, scr_row_name=row_name)
            if result.get("status") == "ok":
                ok.append(row_name)
            else:
                # Supplier.insert failure inside approve_scr returns
                # {"status": "failed", ...} rather than raising. Surface
                # as a bulk-row failure without breaking the loop.
                failed.append({
                    "scr": row_name,
                    "error": f"{result.get('error_type', 'Error')}: {result.get('error', 'Supplier insert failed')}",
                })
        except Exception as exc:  # noqa: BLE001 — aggregate, don't mask
            failed.append({
                "scr": row_name,
                "error": f"{type(exc).__name__}: {exc}",
            })
    return {"ok": ok, "failed": failed}


@frappe.whitelist()
def bulk_reject_scrs(session_name, scr_row_names):
    """Reject a batch of SCR rows in one RPC. Parallel to
    :func:`bulk_approve_scrs`.

    Returns ``{"ok": [...], "failed": [...]}``.
    """
    row_names = _parse_whitelist_list_arg(scr_row_names)
    ok: list[str] = []
    failed: list[dict] = []
    for row_name in row_names:
        try:
            reject_scr(session_name=session_name, scr_row_name=row_name)
            ok.append(row_name)
        except Exception as exc:  # noqa: BLE001
            failed.append({
                "scr": row_name,
                "error": f"{type(exc).__name__}: {exc}",
            })
    return {"ok": ok, "failed": failed}


def _parse_whitelist_list_arg(raw):
    """Frappe whitelist methods receive list args as JSON strings over
    HTTP transport + as native Python lists during pytest. Normalise
    both shapes to a list. Empty / malformed input returns an empty
    list (the bulk loop then becomes a cheap no-op)."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


@frappe.whitelist()
def list_pending_scrs(session_name):
    """Return Pending + Failed SCR rows for the Session form panel
    dialog. Created / Skipped rows are excluded — they're done.

    Returned dicts are the SCR row fields plus ``_row_name`` (Frappe's
    child-row docname used for approve_scr / reject_scr dispatch).
    Ordered by creation (oldest first) so reviewer processes FIFO.
    """
    session_doc = frappe.get_doc("Tally Migration Session", session_name)
    session_doc.check_permission("read")

    open_statuses = _SCR_ACTION_ALLOWED_STATUSES
    rows = []
    for r in (session_doc.supplier_creation_requests or []):
        if r.status not in open_statuses:
            continue
        rows.append({
            "row_name": r.name,
            "status": r.status,
            "tally_vendor_name": r.tally_vendor_name,
            "tally_vendor_id": r.tally_vendor_id,
            "proposed_supplier_name": r.proposed_supplier_name,
            "proposed_supplier_group": r.proposed_supplier_group,
            "detected_balance": r.detected_balance,
            "reviewer_notes": r.reviewer_notes,
            "source_decisions": r.source_decisions,
            "error_log": r.error_log,
            "creation": str(r.creation) if r.creation else None,
        })
    rows.sort(key=lambda x: x.get("creation") or "")
    return rows


def _find_scr_row(session_doc, row_name: str):
    """Look up an SCR child row by its Frappe-generated name."""
    for r in (session_doc.supplier_creation_requests or []):
        if r.name == row_name:
            return r
    return None


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


@frappe.whitelist()
def bulk_approve_tier1(session_name, dry_run=False):
    """Bulk-approve all eligible tier-1 matches in a session.

    Implements Path B from ``docs/WEEK4_DEFERRED_ITEMS.md``: a
    one-click action on the Mapping Decision Review page that approves
    every tier-1 match (``tier1_exact`` / ``tier1_rule`` /
    ``tier1_pattern``) with a non-empty ``proposed_account`` and a
    Pending* ``review_action``. Saves ~115 manual clicks per CACSPU-
    sized session × 59 entities = ~6,800 clicks across the project.

    Eligibility (per :func:`query.is_bulk_approve_eligible`): never
    overwrites a reviewer's manual ``final_account`` pick or a
    resolved ``review_action``; never approves supplier-fuzzy or
    tier-2/tier-3 rows. Frontend should still preview the eligible
    count via a confirm dialog before invoking.

    Per-row save uses try/except so a single failure (e.g. stale
    Account Link) doesn't abort the rest. The summary returns:

    * ``approved``: list of decision names successfully saved
    * ``skipped``: list of ``{"name", "reason"}`` for ineligible rows
    * ``failed``: list of ``{"name", "reason"}`` for save failures

    No chronology header is prepended to ``reviewer_notes`` — bulk
    saves leave notes alone (Phase A confirmation, Item 1 audit
    point).

    Args:
        session_name: The session whose decisions to scan.
        dry_run: If truthy, return the eligible-row count without
            saving anything. Used by the frontend confirm-dialog to
            preview the action with the *exact* count the same code
            path would approve — avoids preview/execute drift if
            another reviewer edits a row mid-flow. Frappe serialises
            booleans as the strings ``"true"`` / ``"false"`` over the
            wire, so accept either truthy form.

    Returns:
        Live mode: ``{"approved": [...], "skipped": [...], "failed": [...]}``.
        Dry-run mode: ``{"eligible_count": N, "skipped": [...],
        "approved": [], "failed": []}`` — same keys so the frontend
        can branch on ``eligible_count`` presence.

    Raises:
        frappe.DoesNotExistError: if the session is missing.
        frappe.PermissionError: if the caller lacks read permission
            on the session.
    """
    # Frappe serialises booleans as strings in HTTP transport. Handle
    # both shapes so the test harness (passes Python bool) and the
    # browser (passes "true"/"false") both work.
    is_dry_run = dry_run is True or (
        isinstance(dry_run, str) and dry_run.lower() == "true"
    )

    session_doc = frappe.get_doc("Tally Migration Session", session_name)
    session_doc.check_permission("read")

    decisions = frappe.get_all(
        "Mapping Decision",
        filters={"session": session_name},
        fields=[
            "name",
            "tier",
            "review_action",
            "proposed_account",
            "final_account",
            "opening_dr",
            "opening_cr",
        ],
    )

    buckets = apply_bulk_approve(decisions)

    if is_dry_run:
        return {
            "eligible_count": len(buckets["eligible"]),
            "skipped": buckets["skipped"],
            "approved": [],
            "failed": [],
        }

    approved: list[str] = []
    failed: list[dict] = []

    for entry in buckets["eligible"]:
        name = entry["name"]
        updates = entry["updates"]
        try:
            decision_doc = frappe.get_doc("Mapping Decision", name)
            for field, value in updates.items():
                decision_doc.set(field, value)
            decision_doc.save()
            approved.append(name)
        except Exception as exc:
            # Per Phase A: per-row try/except, continue on failure.
            # A single stale Account Link or validation error must not
            # abort the rest of the bulk action.
            failed.append({"name": name, "reason": str(exc)})

    return {
        "approved": approved,
        "skipped": buckets["skipped"],
        "failed": failed,
    }
