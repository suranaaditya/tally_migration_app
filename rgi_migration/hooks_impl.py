"""Document-event hook implementations registered in ``hooks.py``.

Item 4 Commit 3: Account / Supplier on_trash hooks that **cascade-revert**
our app's state before Frappe's link-check gate blocks the delete.

Behavior (Option D, finalised Phase D iteration):

When an Account or Supplier is deleted, we:

1. Delete any ACR / SCR child rows whose ``created_account`` /
   ``created_supplier`` pointed at the doc being deleted.
2. Revert every Mapping Decision whose ``final_account`` /
   ``proposed_account`` (or ``final_supplier`` / ``proposed_supplier``)
   referenced the deleted doc:

   * Nullify the Link fields.
   * If the decision was in an Approved-like state,
     flip ``review_action`` back to ``Pending`` so the row re-enters
     the reviewer's queue.
   * If ``tier`` was lifted to ``tier1_exact`` (by the ACR/SCR
     approval), restore the mapper-authoritative tier — inferred
     from whether ``new_account_name`` / ``new_supplier_name``
     is populated (set by the mapper at parse time, never mutated
     post-parse, so a reliable retroactive marker).
   * Append a chronology note to ``reviewer_notes`` so the audit
     trail shows *"was Approved → Account X created → X deleted →
     reverted"* without any lost data.

Why ``on_trash`` not ``before_delete``: Frappe's delete_doc order is
``on_trash`` → ``check_if_doc_is_linked`` → physical delete. Our
nullifications + row deletes in ``on_trash`` clear the link graph
before the link-check runs, so the delete proceeds. If any of our
saves throw, Frappe rolls back the delete transaction — including
our changes — so no half-applied state.

Import boundary: ``frappe`` is imported inside each function body.
``hooks.py`` is loaded by Frappe before app bootstrap; module-level
``import frappe`` here can occasionally bite during app-install when
the Frappe namespace is still warming up.
"""

from __future__ import annotations


# Review-action values that represent an "approved / acted" state
# pointing at a target doc. When that target doc is deleted, these
# should revert to Pending so the reviewer's queue re-surfaces the
# row for re-mapping. Terminal-rejection states (Rejected, Deferred,
# Skipped, Excluded) are NOT reverted — those are reviewer-owned
# intents that don't depend on a live Link target.
_APPROVED_LIKE_ACTIONS = frozenset({
    "Approved",
    "Manual Override",
    "Account Creation Requested",
    "Supplier Creation Requested",
})


def clear_account_links(doc, method=None):
    """Cascade-revert on Account deletion.

    Registered via ``doc_events.Account.on_trash`` in hooks.py.

    See module docstring for the full contract. Summary: delete any ACR
    rows pointing at this Account, revert any Mapping Decisions that
    referenced it (final_account / proposed_account nullified,
    review_action flipped to Pending, tier restored to pre-approval
    mapper-authoritative value, chronology note appended).
    """
    import frappe

    account_name = doc.name
    now_str = frappe.utils.now_datetime().strftime("%Y-%m-%d %H:%M")
    session_user = frappe.session.user

    # --- Step 1: gather all Mapping Decisions referencing this Account ---
    md_names: set[str] = set()
    for field in ("final_account", "proposed_account"):
        md_names.update(
            frappe.get_all(
                "Mapping Decision",
                filters={field: account_name},
                pluck="name",
            )
        )

    # --- Step 2: find and delete ACR child rows whose created_account ---
    #     is the deleted Account. Group by parent session so each
    #     session is loaded + saved once regardless of how many ACR
    #     rows it owns.
    acr_rows = frappe.get_all(
        "Account Creation Request",
        filters={"created_account": account_name},
        fields=["name", "parent"],
    )
    acr_by_session: dict[str, list[str]] = {}
    for r in acr_rows:
        acr_by_session.setdefault(r["parent"], []).append(r["name"])

    for sess_name, acr_to_delete in acr_by_session.items():
        try:
            session_doc = frappe.get_doc(
                "Tally Migration Session", sess_name
            )
        except Exception:
            # Session may have been deleted independently — skip.
            continue
        drop = set(acr_to_delete)
        session_doc.account_creation_requests = [
            r for r in (session_doc.account_creation_requests or [])
            if r.name not in drop
        ]
        session_doc.save(ignore_permissions=True)

    # --- Step 3: revert each affected Mapping Decision ---
    #     Works for both ACR-origin (approve_acr set final_account) and
    #     manual-pick origin (reviewer picked via Section 4).
    for md_name in md_names:
        try:
            md = frappe.get_doc("Mapping Decision", md_name)
        except Exception:
            continue  # decision may have been deleted independently

        final_was_account = (md.final_account == account_name)
        proposed_was_account = (md.proposed_account == account_name)
        if not (final_was_account or proposed_was_account):
            # A concurrent edit may have moved the reference; nothing
            # for us to do on this decision.
            continue

        if final_was_account:
            md.final_account = None
        if proposed_was_account:
            md.proposed_account = None

        # Flip review_action back to Pending if the decision was in
        # an approved-like state pointing at the deleted Account. Leave
        # Rejected/Deferred/Skipped/etc. alone — those are reviewer-
        # owned intents that don't depend on a live Link target.
        if md.review_action in _APPROVED_LIKE_ACTIONS:
            md.review_action = "Pending"

        # Restore mapper-authoritative tier if we lifted it to tier1_exact
        # (approve_acr's lift). new_account_name is set by the mapper at
        # parse time for pending_account_creation rows and stays NULL
        # for unmapped rows — so it's a reliable retroactive marker of
        # the original tier.
        if md.tier == "tier1_exact":
            md.tier = (
                "pending_account_creation"
                if md.new_account_name
                else "unmapped"
            )

        # Append chronology note — reviewer sees the full history on
        # the row's reviewer_notes field. First-note case (stored
        # empty) omits the header per the account-save convention
        # from Item 1 Commit 4b.
        new_note = (
            f"Linked Account {account_name!r} was deleted from COA; "
            f"decision reverted to Pending. Re-map via Section 4 "
            f"or Request Creation."
        )
        md.reviewer_notes = _append_chronology_note(
            stored=md.reviewer_notes,
            new=new_note,
            user=session_user,
            now_str=now_str,
        )
        md.save(ignore_permissions=True)


def clear_supplier_links(doc, method=None):
    """Cascade-revert on Supplier deletion. Parallel to
    :func:`clear_account_links`."""
    import frappe

    supplier_name = doc.name
    now_str = frappe.utils.now_datetime().strftime("%Y-%m-%d %H:%M")
    session_user = frappe.session.user

    md_names: set[str] = set()
    for field in ("final_supplier", "proposed_supplier"):
        md_names.update(
            frappe.get_all(
                "Mapping Decision",
                filters={field: supplier_name},
                pluck="name",
            )
        )

    scr_rows = frappe.get_all(
        "Supplier Creation Request",
        filters={"created_supplier": supplier_name},
        fields=["name", "parent"],
    )
    scr_by_session: dict[str, list[str]] = {}
    for r in scr_rows:
        scr_by_session.setdefault(r["parent"], []).append(r["name"])

    for sess_name, scr_to_delete in scr_by_session.items():
        try:
            session_doc = frappe.get_doc(
                "Tally Migration Session", sess_name
            )
        except Exception:
            continue
        drop = set(scr_to_delete)
        session_doc.supplier_creation_requests = [
            r for r in (session_doc.supplier_creation_requests or [])
            if r.name not in drop
        ]
        session_doc.save(ignore_permissions=True)

    for md_name in md_names:
        try:
            md = frappe.get_doc("Mapping Decision", md_name)
        except Exception:
            continue

        final_was_supplier = (md.final_supplier == supplier_name)
        proposed_was_supplier = (md.proposed_supplier == supplier_name)
        if not (final_was_supplier or proposed_was_supplier):
            continue

        if final_was_supplier:
            md.final_supplier = None
        if proposed_was_supplier:
            md.proposed_supplier = None

        if md.review_action in _APPROVED_LIKE_ACTIONS:
            md.review_action = "Pending"

        # Restore mapper-authoritative supplier tier. Three supplier
        # tiers the mapper emits for vendor rows:
        #   * tier1_supplier_exact / tier1_supplier_alias / tier1_supplier_fuzzy
        #     → pre-approval was one of these. We can't distinguish
        #       without an original_tier field, but any of them route
        #       back to the supplier dialog correctly.
        #   * pending_supplier_creation → pre-approval was this if
        #       new_supplier_name was the mapper's suggestion.
        # approve_scr lifts to tier1_supplier_exact. Revert:
        if md.tier == "tier1_supplier_exact":
            md.tier = (
                "pending_supplier_creation"
                if md.new_supplier_name
                else "tier1_supplier_fuzzy"
            )
            # Reviewer sees a slightly different tier on revert than
            # original (tier1_supplier_fuzzy vs tier1_supplier_alias),
            # but the routing (supplier dialog opens on `c`) is the
            # same for all three supplier tiers — functionally
            # equivalent. If precision matters later, add an
            # original_tier field to Mapping Decision.

        new_note = (
            f"Linked Supplier {supplier_name!r} was deleted from the "
            f"Supplier master; decision reverted to Pending. Re-map or "
            f"Resolve Supplier again."
        )
        md.reviewer_notes = _append_chronology_note(
            stored=md.reviewer_notes,
            new=new_note,
            user=session_user,
            now_str=now_str,
        )
        md.save(ignore_permissions=True)


def _append_chronology_note(
    *, stored: str | None, new: str, user: str, now_str: str
) -> str:
    """Append a new note with a `[user, time]` header, preserving
    stored notes below.

    Matches the chronology convention used by
    ``query._apply_chronology_header`` — duplicating here (rather than
    importing the private helper) because ``hooks_impl`` shouldn't
    reach across the page-controller boundary. Keeps the hook module
    self-contained and safely importable during app bootstrap.
    """
    if not new:
        return stored or ""
    stored = stored or ""
    if not stored.strip():
        # First-note case — no header, matches the account-save convention.
        return new
    header = f"[{user}, {now_str}] "
    return f"{header}{new}\n\n{stored}"
