"""
Mapping Decision Review — pure-logic query core.

Factored out of ``md_review.py`` so the query-building, session-scope
guard, default-ordering, and response-shape logic can be unit-tested
from a local pytest environment that doesn't have Frappe installed.
See ``rgi_migration/tests/test_get_session_decisions.py`` for the
test harness.

This module has **no Frappe dependencies**. The Frappe wrapper in
``md_review.get_session_decisions`` supplies ``check_permission``,
``get_all``, and ``count`` as callables.

Contract (§1.3 + §1.12 of ``docs/week4_review_ui_design.md``):

- Decisions are always filtered to ``session = <session_name>``.
  Even if the caller passes a ``session`` key in ``filters``, the
  argument value wins (security-by-default — prevents a caller from
  reading another session's decisions via filter-override).
- Default sort is ``review_action ASC, net_amount DESC`` per §1.3.
- Response shape is ``{"decisions": [...], "total_count": N,
  "filtered_count": M}`` — ``total_count`` is the session's full
  decision count (unfiltered), ``filtered_count`` is the count
  matching the caller's filters (pre-pagination).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


DEFAULT_DECISION_FIELDS: list[str] = [
    "name",
    "tally_name",
    "tally_parent_chain",
    "tally_root_type",
    "net_amount",
    "net_side",
    "tier",
    "proposed_account",
    "review_action",
    "opening_dr",
    "opening_cr",
]


DEFAULT_ORDER_BY: str = "review_action asc, net_amount desc"


def fetch_session_decisions(
    *,
    session_name: str,
    filters: dict | None,
    start: int,
    page_length: int,
    order_by: str | None,
    check_permission: Callable[[], None],
    get_all: Callable[..., list[dict[str, Any]]],
    count: Callable[[dict], int],
) -> dict[str, Any]:
    """Pure implementation of the get_session_decisions endpoint.

    Args:
        session_name: Target Tally Migration Session name. Required
            (empty string / None raises ``ValueError``).
        filters: Optional dict of additional filters. A ``session``
            key, if present, is silently overwritten by the
            ``session_name`` argument — see the security guard below.
        start: Pagination offset (non-negative int).
        page_length: Number of rows to return (positive int).
        order_by: SQL ORDER BY clause. ``None`` applies
            ``DEFAULT_ORDER_BY`` (§1.3 default sort).
        check_permission: Callable that raises on insufficient access
            (in Frappe production, wraps ``session_doc.check_permission``).
        get_all: Callable that takes the standard Frappe kwargs
            (``filters``, ``fields``, ``order_by``, ``start``,
            ``page_length``) and returns a list of dicts.
        count: Callable that takes a filter dict and returns the
            matching row count.

    Returns:
        ``{"decisions": [...], "total_count": int, "filtered_count": int}``

    Raises:
        ValueError: when ``session_name`` is falsy.
        Whatever ``check_permission`` raises on denial (in Frappe,
        typically ``frappe.PermissionError``).
    """
    if not session_name:
        raise ValueError("session_name is required")

    check_permission()

    # --- Session-scope guard (security-by-default) ---
    # Build the filter dict starting from the caller's filters, then
    # FORCE-SET ``session`` to the argument value. This overrides any
    # attempt to pass ``filters={"session": "<other-session>"}`` as a
    # malicious bypass. The session being queried is always the session
    # the caller named in the ``session_name`` parameter, never
    # something a caller can smuggle in via filters.
    query_filters: dict[str, Any] = dict(filters) if filters else {}
    query_filters["session"] = session_name

    if not order_by:
        order_by = DEFAULT_ORDER_BY

    decisions = get_all(
        filters=query_filters,
        fields=DEFAULT_DECISION_FIELDS,
        order_by=order_by,
        start=int(start),
        page_length=int(page_length),
    )

    filtered_count = count(query_filters)
    total_count = count({"session": session_name})

    return {
        "decisions": decisions,
        "total_count": total_count,
        "filtered_count": filtered_count,
    }


def fetch_decision_detail(
    *,
    decision_name: str,
    get_decision_fn: Callable[[str], dict],
    get_session_company_fn: Callable[[str], str],
    get_docinfo_fn: Callable[[str], dict],
    permission_check_fn: Callable[[], None],
) -> dict:
    """Pure implementation of the get_decision_detail endpoint.

    Fetches a single Mapping Decision with its docinfo and the linked
    session's company abbreviation. Used by the detail pane (§1.4
    sections 1, 2, 5, 6) of the Mapping Decision Review page.

    Permission enforcement is delegated to the injected
    ``permission_check_fn`` (typically closes over a pre-fetched
    ``session_doc`` and calls ``.check_permission("read")``). The pure
    function doesn't know or care whether permission is decision-level
    or session-level — that's a wrapper concern.

    Args:
        decision_name: Mapping Decision name. Required (empty string /
            None raises ``ValueError``).
        get_decision_fn: ``(name) -> dict`` — returns the decision's
            fields. Must include a ``"session"`` key referencing the
            parent Tally Migration Session (per the Commit 1 schema
            invariant: ``session`` is ``reqd=1`` on the DocType).
        get_session_company_fn: ``(session_name) -> str`` — returns
            the linked session's ``company_abbr`` for display purposes.
        get_docinfo_fn: ``(decision_name) -> dict`` — returns Frappe's
            ``get_docinfo`` payload (assignments, comments, versions,
            attachments). Section 5 (Assignment) reads
            ``docinfo["assignments"]``.
        permission_check_fn: ``() -> None`` — called after the decision
            is fetched, before docinfo / company lookups. Closure over
            the session doc in the production wrapper.

    Returns:
        ``{"decision": dict, "docinfo": dict, "session_company_abbr": str}``

    Raises:
        ValueError: ``decision_name`` is empty, or the fetched decision
            is ``None``, or the decision's ``session`` field is empty
            (should be unreachable given the Commit 1 schema).
        Whatever ``permission_check_fn`` raises on denial (in Frappe,
        typically ``frappe.PermissionError``).
        Whatever ``get_decision_fn`` raises on not-found (in Frappe,
        typically ``frappe.DoesNotExistError`` — propagated via the
        wrapper's ``frappe.get_doc`` call, not this pure function).
    """
    if not decision_name:
        raise ValueError("decision_name is required")

    decision = get_decision_fn(decision_name)
    if decision is None:
        raise ValueError(
            "get_decision_fn returned None for "
            f"decision_name={decision_name!r}"
        )

    # Accommodate both dict (production — Document.as_dict()) and plain
    # object (some test fixtures).
    if isinstance(decision, dict):
        session_name = decision.get("session")
    else:
        session_name = getattr(decision, "session", None)

    if not session_name:
        raise ValueError(
            f"Mapping Decision {decision_name!r} has no session link "
            "(expected reqd=1 field per Commit 1 schema invariant)"
        )

    # Permission gate — call BEFORE fetching docinfo / company so that
    # a denied user doesn't leak any data via the subsequent calls.
    permission_check_fn()

    docinfo = get_docinfo_fn(decision_name)
    session_company_abbr = get_session_company_fn(session_name)

    return {
        "decision": decision,
        "docinfo": docinfo,
        "session_company_abbr": session_company_abbr,
    }


# ---------------------------------------------------------------------------
# Commit 4b — save_decision core + account autocomplete formatter
# ---------------------------------------------------------------------------


SAVE_DECISION_FIELDS: tuple[str, ...] = (
    "review_action",
    "final_account",
    "final_dr",
    "final_cr",
    "reviewer_notes",
)


def apply_decision_save(
    *,
    current: dict,
    review_action: str,
    final_account: str | None,
    reviewer_notes_input: str | None,
    session_user: str,
    now_str: str,
) -> dict:
    """Pure implementation of the save-decision mutation logic.

    Given the current Mapping Decision doc-dict and the reviewer's
    inputs, returns a dict of the fields that should be written back
    to the doc before save. The Frappe wrapper applies these and calls
    ``doc.save()``.

    Encapsulates two non-trivial rules from
    ``docs/week4_review_ui_design.md §1.4``:

    * **Refinement 2** — ``final_dr`` / ``final_cr`` are *not*
      reviewer-editable in v1. They mirror ``opening_dr`` /
      ``opening_cr`` exactly. The splitting workflow
      (one Tally balance → multiple ERPNext accounts) is deferred to
      v2; in v1 the mapping is always 1:1 so the derivation is
      trivial.

    * **Refinement 3** — ``reviewer_notes`` accumulates as a
      chronology. When the reviewer submits new note content *and*
      prior stored content is non-empty *and* the new content differs
      from the stored content, a header of the form
      ``[<user>, <YYYY-MM-DD HH:MM>] `` is prepended to the new
      content and the whole thing is concatenated with a blank line
      separator before the stored content. If the stored content is
      empty (first-note case), the new content is written verbatim
      with no header — the doc's ``owner`` / ``creation`` fields
      already anchor the first authorship. If the new content is
      empty or identical to stored, the stored value is left alone.

    Args:
        current: The current Mapping Decision as a dict. Must contain
            ``opening_dr``, ``opening_cr``, ``reviewer_notes`` (may
            be ``None`` / empty).
        review_action: The target ``review_action`` Select value.
        final_account: The target ``final_account`` Link value, or
            ``None`` / empty string to clear.
        reviewer_notes_input: The reviewer's new notes content — just
            the new text, not the accumulated history. ``None`` or
            empty string means "no new note this save; preserve
            stored value".
        session_user: ``frappe.session.user`` — captured by the
            wrapper and passed in for determinism in tests.
        now_str: Current datetime formatted as ``YYYY-MM-DD HH:MM`` —
            captured by the wrapper for determinism in tests.

    Returns:
        A dict with exactly the keys in :data:`SAVE_DECISION_FIELDS`.
        Caller assigns these onto the Frappe doc and calls ``save()``.
    """
    opening_dr = float(current.get("opening_dr") or 0.0)
    opening_cr = float(current.get("opening_cr") or 0.0)

    # final_account: empty string is the "clear" sentinel — normalise
    # to None so Frappe writes NULL rather than an empty-string Link
    # (which would fail Account-existence validation).
    final_account_clean: str | None = final_account or None

    final_notes = _apply_chronology_header(
        stored_notes=current.get("reviewer_notes") or "",
        new_input=reviewer_notes_input or "",
        session_user=session_user,
        now_str=now_str,
    )

    return {
        "review_action": review_action,
        "final_account": final_account_clean,
        "final_dr": opening_dr,
        "final_cr": opening_cr,
        "reviewer_notes": final_notes,
    }


def _apply_chronology_header(
    *,
    stored_notes: str,
    new_input: str,
    session_user: str,
    now_str: str,
) -> str:
    """Shared reviewer_notes chronology-header logic.

    Rule (`docs/week4_review_ui_design.md §1.4` refinement 3):

    * Empty ``new_input`` → preserve ``stored_notes`` verbatim.
    * Empty ``stored_notes`` (first-note case) → write ``new_input``
      verbatim with no header; the doc's ``owner`` / ``creation``
      already anchor first authorship.
    * ``new_input == stored_notes`` after strip → no-op save; leave
      stored unchanged (avoids spurious chronology headers on
      idempotent re-save).
    * Else prepend ``[<user>, <YYYY-MM-DD HH:MM>] `` to ``new_input``
      and concatenate with a blank-line separator before ``stored_notes``.

    Extracted from ``apply_decision_save`` in Item 3 Commit 1b so the
    supplier resolution path (``apply_supplier_resolution``) can reuse
    the same reviewer_notes handling without duplicating it.
    """
    stored_stripped = stored_notes.strip()
    new_stripped = new_input.strip()

    if not new_stripped:
        return stored_notes
    if not stored_stripped:
        return new_input
    if new_stripped == stored_stripped:
        return stored_notes

    header = f"[{session_user}, {now_str}] "
    return f"{header}{new_input}\n\n{stored_notes}"


def build_account_autocomplete_results(
    accounts: list[dict],
) -> list[list[str]]:
    """Pure implementation of the parent-chain-annotated autocomplete.

    The Mapping Decision Review page's ``final_account`` Link widget
    uses a custom ``query`` (wired via ``df.get_query``) so reviewers
    can see each candidate's parent account + root type as the
    autocomplete dropdown's description line. Without it, reviewers
    staring at "Bank of Maharashtra - CACSPU" can't tell whether
    it sits under "Bank Accounts - CACSPU" or "Fixed Deposits -
    CACSPU" without clicking through — a real failure mode observed
    during the spike.

    Frappe's Link autocomplete accepts each result as either
    ``[name]`` (name-only) or ``[name, description, ...extras]``
    (name + grey subtitle on the right). This function returns the
    latter shape.

    Args:
        accounts: Rows from ``frappe.get_all("Account", ...)``.
            Each row must have ``name``; ``parent_account`` and
            ``root_type`` are optional but make the subtitle useful.

    Returns:
        A list of ``[name, description]`` pairs. ``description`` is
        ``"<root_type> · under <parent_account>"`` when both are
        present, or a shorter form when one is missing. Empty list
        if ``accounts`` is empty.
    """
    results: list[list[str]] = []
    for acc in accounts:
        name = acc.get("name")
        if not name:
            # Defensive — should never happen with frappe.get_all,
            # but refuse to return a [null, ...] row that would crash
            # the autocomplete renderer.
            continue
        parent = (acc.get("parent_account") or "").strip()
        root = (acc.get("root_type") or "").strip()
        if parent and root:
            description = f"{root} \u00b7 under {parent}"
        elif parent:
            description = f"under {parent}"
        elif root:
            description = root
        else:
            description = "(root)"
        results.append([name, description])
    return results


# ---------------------------------------------------------------------------
# Commit 4b — auto-flip truth table (§1.4 Section 4, refinement 1)
# ---------------------------------------------------------------------------


# Every Select option whose semantics are "still-awaiting-decision". When
# the reviewer is in one of these states, the auto-flip logic reacts to
# ``final_account`` changes; when they're in a terminal state (Approved,
# Rejected, Deferred, etc.), we respect the explicit choice and leave it
# alone.
PENDING_REVIEW_STATES: frozenset[str] = frozenset({
    "Pending",
    "Pending Account Creation",
    "Pending Group Account Resolution",
    "Pending Supplier Creation",
})


def compute_auto_flip(
    *,
    original_proposed_account: str | None,
    current_review_action: str,
    current_final_account: str | None,
) -> str:
    """Pure implementation of the review_action auto-flip truth table.

    Mirror of ``DetailPane._computeAutoFlip`` in ``md_review.js`` — the
    JS version runs live in the browser on every ``final_account``
    change; this Python version exists so the logic can be unit-tested
    in pytest. Both must stay behaviourally identical. If one is
    changed, update the other and the tests.

    Truth table (§1.4 Section 4, refinement 1):

    =====================================  ==========================  =========================
    Starting state                          Reviewer action              review_action flips to
    =====================================  ==========================  =========================
    unmapped + Pending                      picks final_account         Approved
    mapped + Pending                        picks same as proposal      Approved
    mapped + Pending                        picks different account     Manual Override
    unmapped or mapped + Pending            clears final_account        Pending
    Any state                               manually picks terminal     Select value wins (no-op)
    =====================================  ==========================  =========================

    The function treats all four ``Pending*`` variants as Pending-
    like — they represent "still awaiting decision" in various
    sub-flavours. Terminal states (Approved / Rejected / Manual
    Override / Deferred / Skipped / Excluded (P&L)) mean the reviewer
    has made an explicit call; we don't overwrite.

    Args:
        original_proposed_account: ``proposed_account`` at row-load
            time. ``None`` / empty string means the mapper left the
            ledger unmapped.
        current_review_action: The value currently shown in the
            ``review_action`` Select control.
        current_final_account: The value currently shown in the
            ``final_account`` Link control. ``None`` / empty string
            means the reviewer hasn't picked / has cleared it.

    Returns:
        The ``review_action`` Select value to display. Caller sets
        this via ``control.set_value()`` (never via a save — the
        flip is UI-only until the reviewer saves).
    """
    # Rule 5 — reviewer already picked a terminal state. Respect it.
    if current_review_action not in PENDING_REVIEW_STATES:
        return current_review_action

    # Rule 4 — final_account cleared (or never set). Back to Pending.
    if not current_final_account:
        return "Pending"

    # From here, final_account is non-empty and we're in a Pending*
    # variant. The decision is whether this counts as agreeing with
    # the mapper (Approved) or overriding it (Manual Override).
    if not original_proposed_account:
        # Rule 1 — unmapped + reviewer supplied a mapping. The reviewer
        # agreed "there should be a mapping here" and filled it. Not
        # an override.
        return "Approved"

    if current_final_account == original_proposed_account:
        # Rule 2 — mapped + reviewer picked same as proposal.
        return "Approved"

    # Rule 3 — mapped + reviewer picked something different. This is
    # the Manual Override case (downstream rule-promotion treats it as
    # "existing rule is wrong for this ledger" versus Approved-on-
    # unmapped's "new rule needed").
    return "Manual Override"


# ---------------------------------------------------------------------------
# Commit 5 — undo_decision core
# ---------------------------------------------------------------------------


# Fields Undo was restoring before Item 3 Commit 1b. Preserved as
# documentation of the account-save contract; not used by the
# shape-agnostic ``apply_decision_undo`` (Item 3 Commit 1b made it
# adapt to whatever keys the cached snapshot actually contains, so
# a supplier-save snapshot restores ``final_supplier`` + ``tier``
# while an account-save snapshot restores ``final_account`` + amounts).
UNDO_RESTORE_FIELDS: tuple[str, ...] = SAVE_DECISION_FIELDS


def apply_decision_undo(
    *,
    current: dict,
    snapshot: dict,
) -> dict:
    """Pure implementation of the undo-save mutation logic.

    Given the pre-save snapshot that the Frappe wrapper stashed in
    ``frappe.cache()``, return a dict of the fields to restore onto
    the doc. The caller assigns these and calls ``doc.save()``.

    **Shape-agnostic** (since Item 3 Commit 1b): restores every key
    present in the snapshot dict, whatever shape it has. Account save
    snapshots ``SAVE_DECISION_FIELDS``; supplier save snapshots
    ``SUPPLIER_SAVE_FIELDS``. Each restores the corresponding fields.

    The reviewer_notes field is restored verbatim, so any chronology
    header that the save being undone prepended is discarded along
    with the rest of that save's changes (undo means "never happened").

    Args:
        current: The current Mapping Decision as a dict. Only used
            as a shape reference — all fields in the return dict
            come from ``snapshot``.
        snapshot: The pre-save snapshot from
            ``frappe.cache()["mdr_undo:<user>:<decision>"]``.

    Returns:
        A dict with the same keys as ``snapshot``. Caller assigns
        these onto the Frappe doc and calls ``save()``.

    Raises:
        ValueError: if ``snapshot`` is empty (defensive — a
            zero-field restore would be a no-op save with no audit
            value).
    """
    _ = current  # reserved for future diff-based restoration; unused in v1
    if not snapshot:
        raise ValueError(
            "undo snapshot is empty — refusing a zero-field restore"
        )
    return dict(snapshot)


# ---------------------------------------------------------------------------
# Commit 5 — get_next_pending core
# ---------------------------------------------------------------------------


def compute_next_pending(
    *,
    decisions: list[dict],
    after_name: str,
    after_position: int | None = None,
) -> str | None:
    """Pure implementation of the auto-advance "what's next" lookup.

    Given a *refreshed* list of decisions matching the reviewer's
    current filter (already ordered per the default sort) and the
    name of the just-saved decision, return the name of the next
    decision to select, or ``None`` if the list is exhausted.

    §1.7 describes two branches based on whether ``after_name`` is
    still in the filtered list:

    1. **Still in filter** (e.g. saved as ``Pending Account
       Creation`` while the Pending preset is active — still
       Pending-like). Find its position, return the row one below.
       Last row → ``None``.

    2. **Dropped out of filter** (e.g. saved as ``Approved`` while
       Pending filter is active). The spec says "stay at the same
       index, which now points to what was the next row before."
       Requires the caller to supply the old ``after_position`` so
       we can still locate "what was next." If
       ``after_position`` isn't given, we return ``None`` —
       safer than returning an arbitrary row.

    Args:
        decisions: Refreshed filtered list of decision dicts. Each
            must have a ``name`` key. Order is the default
            (review_action ASC, net_amount DESC) — this function
            trusts the caller; it does not re-sort.
        after_name: The just-saved decision's name. May or may not
            be in ``decisions`` depending on whether the save
            dropped it out of the filter.
        after_position: The 0-based index that ``after_name`` had
            in the *pre-save* filtered list. Used only when
            ``after_name`` is no longer present. ``None`` means
            "I don't know" — function returns ``None`` for the
            dropped-out case rather than guessing.

    Returns:
        The next decision's ``name``, or ``None`` if no next
        decision exists (end-of-list, empty filter, or dropped-out
        without a position hint).
    """
    names = [d.get("name") for d in decisions]

    if after_name in names:
        i = names.index(after_name)
        if i + 1 < len(names):
            return names[i + 1]
        return None  # after_name was the last row

    # after_name not in refreshed list — dropped out of filter.
    if after_position is not None and 0 <= after_position < len(names):
        # The row that used to be at after_position + 1 is now at
        # after_position (since after_name was removed from the list).
        return names[after_position]

    return None


# ---------------------------------------------------------------------------
# Commit 6 — bulk-approve tier-1 matches (Path B per WEEK4_DEFERRED_ITEMS.md)
# ---------------------------------------------------------------------------


# Tiers eligible for bulk auto-approval. Excludes ``tier1_supplier_fuzzy``
# because supplier resolution UX (Final Supplier picker, default-payable
# auto-derivation) is owned by Items 3-4 — bulk-approving suppliers via
# the Final Account path would write the wrong field. Excludes tier-2 /
# tier-3 because those are reviewer-judgement tiers by definition.
BULK_APPROVE_TIER1_TIERS: frozenset[str] = frozenset({
    "tier1_exact",
    "tier1_rule",
    "tier1_pattern",
})


def is_bulk_approve_eligible(decision: dict) -> tuple[bool, str | None]:
    """Eligibility predicate for bulk tier-1 approval.

    Per Phase A confirmation (Item 1, eligibility filter):

    * ``tier`` must be one of :data:`BULK_APPROVE_TIER1_TIERS`.
    * ``review_action`` must be one of :data:`PENDING_REVIEW_STATES`
      — never overwrite a reviewer-resolved row (Approved, Rejected,
      Manual Override, Deferred, Skipped, Excluded).
    * ``proposed_account`` must be non-empty — the bulk action sets
      ``final_account = proposed_account``, so a missing proposal
      can't be auto-approved.
    * ``final_account`` must be empty — never overwrite a reviewer's
      manual pick. A row where the reviewer typed a Final Account
      but didn't change Review Action away from Pending is still
      "user has touched this"; defer to them.

    Returns:
        ``(True, None)`` if eligible, ``(False, reason)`` otherwise.
        ``reason`` is a short human-readable string suitable for the
        bulk-approve summary toast / dialog.
    """
    tier = (decision.get("tier") or "").strip()
    if tier not in BULK_APPROVE_TIER1_TIERS:
        return False, f"tier {tier!r} not in tier-1 set"

    review_action = decision.get("review_action") or ""
    if review_action not in PENDING_REVIEW_STATES:
        return False, f"already resolved ({review_action})"

    proposed_account = (decision.get("proposed_account") or "").strip()
    if not proposed_account:
        return False, "no proposed_account"

    final_account = (decision.get("final_account") or "").strip()
    if final_account:
        return False, "final_account already set by reviewer"

    return True, None


def compute_bulk_approve_mutations(decision: dict) -> dict:
    """Field updates for a single bulk-approved row.

    Mirrors :func:`apply_decision_save` but skips chronology-header
    logic — reviewer_notes is left untouched on a bulk save (Phase A
    confirmation: "no chronology header on bulk"). Caller is
    responsible for calling :func:`is_bulk_approve_eligible` first;
    this function trusts the caller and produces the mutation dict
    unconditionally.

    Returns a dict with the same shape as :data:`SAVE_DECISION_FIELDS`
    minus ``reviewer_notes`` (which the wrapper leaves alone). Caller
    assigns these onto the Frappe doc and calls ``save()``.
    """
    opening_dr = float(decision.get("opening_dr") or 0.0)
    opening_cr = float(decision.get("opening_cr") or 0.0)
    proposed_account = (decision.get("proposed_account") or "").strip()

    return {
        "review_action": "Approved",
        "final_account": proposed_account,
        "final_dr": opening_dr,
        "final_cr": opening_cr,
    }


def apply_bulk_approve(decisions: list[dict]) -> dict:
    """Pure split of a session's decisions into bulk-approve buckets.

    Given the full set of decisions for a session (or any iterable
    slice the caller wants to bulk-action), partition into:

    * **eligible**: rows that pass :func:`is_bulk_approve_eligible`.
      Each entry includes the precomputed ``updates`` dict from
      :func:`compute_bulk_approve_mutations` so the Frappe wrapper
      can assign-and-save in one loop.
    * **skipped**: rows that failed eligibility, with a short
      ``reason`` for the summary dialog.

    The Frappe wrapper layers per-row save try/except on top of this
    output (catching ``frappe.ValidationError`` etc.) — actual save
    failures are tracked separately as ``failed`` in the wrapper's
    final summary. This pure function never raises.

    Returns:
        ``{"eligible": [{"name", "updates"}, ...],
           "skipped":  [{"name", "reason"}, ...]}``
    """
    eligible: list[dict] = []
    skipped: list[dict] = []

    for decision in decisions:
        name = decision.get("name")
        ok, reason = is_bulk_approve_eligible(decision)
        if ok:
            eligible.append({
                "name": name,
                "updates": compute_bulk_approve_mutations(decision),
            })
        else:
            skipped.append({"name": name, "reason": reason})

    return {"eligible": eligible, "skipped": skipped}


# ---------------------------------------------------------------------------
# Item 3 Commit 1b — supplier resolution save + supplier autocomplete
# ---------------------------------------------------------------------------


# Fields written by the supplier-resolution save path (parallel to
# SAVE_DECISION_FIELDS for the account save path). Dedicated shape so
# the account save contract stays untouched and the supplier Undo
# snapshot restores exactly the fields the supplier save wrote.
SUPPLIER_SAVE_FIELDS: tuple[str, ...] = (
    "review_action",
    "final_supplier",
    "tier",
    "reviewer_notes",
)


def apply_supplier_resolution(
    *,
    current: dict,
    final_supplier: str | None,
    reviewer_notes_input: str | None,
    session_user: str,
    now_str: str,
) -> dict:
    """Pure implementation of the map-to-existing supplier save.

    Called by the ``save_supplier_resolution`` Frappe wrapper when the
    reviewer picks an existing Supplier from the rich dialog (Item 3
    Commit 1b). Writes:

    * ``final_supplier`` — the reviewer's picked Supplier doc ID.
    * ``tier = "tier1_supplier_exact"`` — lifts the decision out of
      the mapper's original tier so the refusal gates in
      ``oit_csv`` / ``advance_je`` see it as resolved (Item 3 AMB-2).
    * ``review_action = "Approved"`` — reviewer authoritatively
      confirmed the match.
    * ``reviewer_notes`` — new content concatenated via the shared
      chronology-header helper (same rule as the account save path).

    The Frappe wrapper is responsible for validating that
    ``final_supplier`` exists + is not disabled in the Supplier master
    before calling this. This pure function trusts the Link value.

    Args:
        current: The current Mapping Decision as a dict. Must contain
            ``reviewer_notes`` (may be ``None`` / empty).
        final_supplier: The reviewer's picked Supplier doc ID. Empty
            string / ``None`` are rejected by the Frappe wrapper — this
            pure function only sees non-empty values in normal use.
        reviewer_notes_input: New note content — just the increment,
            not accumulated history.
        session_user: ``frappe.session.user`` — captured by the wrapper.
        now_str: Current ``YYYY-MM-DD HH:MM`` — captured by the wrapper.

    Returns:
        A dict with exactly the keys in :data:`SUPPLIER_SAVE_FIELDS`.
    """
    final_notes = _apply_chronology_header(
        stored_notes=current.get("reviewer_notes") or "",
        new_input=reviewer_notes_input or "",
        session_user=session_user,
        now_str=now_str,
    )
    return {
        "review_action": "Approved",
        "final_supplier": final_supplier or None,
        "tier": "tier1_supplier_exact",
        "reviewer_notes": final_notes,
    }


def build_supplier_autocomplete_results(
    suppliers: list[dict],
) -> list[list[str]]:
    """Pure implementation of the supplier picker autocomplete formatting.

    Parallel to :func:`build_account_autocomplete_results`. The rich
    supplier resolution dialog's Supplier Link widget uses a custom
    ``query`` that returns the supplier's ``supplier_group`` as the
    grey subtitle on the right, so reviewers can disambiguate between
    e.g. "Nilesh Traders" (Services) vs. "Nilesh Traders"
    (Raw Material) without clicking through.

    Frappe Link autocomplete accepts each row as ``[name, description,
    ...extras]``; this function returns that shape.

    The Frappe wrapper is responsible for the ``disabled=0`` filter at
    query time — disabled suppliers must never reach this formatter
    (reviewer-picking a disabled supplier would fail downstream at
    generator time, per ``oit_csv`` / ``advance_je`` re-check Order A).

    Args:
        suppliers: Rows from ``frappe.get_all("Supplier", ...)``.
            Each row must have ``name``; ``supplier_name`` falls back
            to ``name`` when empty; ``supplier_group`` is optional.

    Returns:
        A list of ``[name, description]`` pairs. ``description`` is
        ``"Group: <supplier_group>"`` when the group is populated,
        ``"(no group)"`` otherwise. Empty list on empty input.
    """
    results: list[list[str]] = []
    for sup in suppliers:
        name = sup.get("name")
        if not name:
            # Defensive — same refusal as build_account_autocomplete_results
            # for name-less rows that would crash the autocomplete renderer.
            continue
        group = (sup.get("supplier_group") or "").strip()
        description = f"Group: {group}" if group else "(no group)"
        results.append([name, description])
    return results


# ---------------------------------------------------------------------------
# Item 3 Commit 2 — Supplier Creation Request payload builder
# ---------------------------------------------------------------------------


def build_scr_payload(
    *,
    decision: dict,
    proposed_supplier_name: str,
    supplier_group: str | None,
    reviewer_notes: str | None,
) -> dict:
    """Pure implementation of the SCR child-row payload.

    Called by the ``create_supplier_creation_request`` Frappe wrapper
    when the reviewer submits the Create-new path in the
    SupplierResolutionDialog (Item 3 Commit 2). Returns a dict suitable
    for ``session.append("supplier_creation_requests", <dict>)``.

    The ``detected_balance`` signed-net-Cr convention matches the SCR
    DocType's field label ("Detected Balance (net Cr)"): positive for
    normal vendor payables, negative for vendor advances (Dr balances).
    Derived from ``decision.net_amount`` which the parser + Item 2
    persistence layer compute as ``opening_cr - opening_dr``.

    ``source_decisions`` is a CSV string field on the SCR DocType;
    Commit 2 writes a single decision name. Future merge workflows
    would CSV-concat multiple decision names here.

    Args:
        decision: Current Mapping Decision as a dict. Must contain
            ``name``, ``tally_name``, ``tally_id``, ``net_amount``.
        proposed_supplier_name: Reviewer-typed name for the Supplier
            to be created. Must be non-empty (caller validates).
        supplier_group: Reviewer-typed group, or ``None`` / empty
            (allowed — SCR field is not reqd; downstream approval
            will require it before creating the Supplier).
        reviewer_notes: Verbatim reviewer note (no chronology header;
            SCR is a single-use record). ``None`` / empty allowed.

    Returns:
        A dict with keys matching the SCR DocType fields:
        status, tally_vendor_name, tally_vendor_id,
        proposed_supplier_name, proposed_supplier_group,
        detected_balance, reviewer_notes, source_decisions.
    """
    return {
        "status": "Pending",
        "tally_vendor_name": decision.get("tally_name") or "",
        "tally_vendor_id": decision.get("tally_id"),
        "proposed_supplier_name": proposed_supplier_name,
        "proposed_supplier_group": (supplier_group or "").strip() or None,
        "detected_balance": float(decision.get("net_amount") or 0.0),
        "reviewer_notes": reviewer_notes or None,
        "source_decisions": decision.get("name") or "",
    }


def build_acr_payload(
    *,
    decision: dict,
    proposed_account_name: str,
    proposed_parent: str,
    account_type: str | None,
    reason: str | None,
    reviewer_notes: str | None,
) -> dict:
    """Pure implementation of the Account Creation Request child-row payload.

    Called by the ``create_account_creation_request`` Frappe wrapper
    when the reviewer submits the AccountResolutionDialog (Item 4
    Commit 2). Returns a dict suitable for
    ``session.append("account_creation_requests", <dict>)``.

    Server-owned invariants (never accept from client):

    * ``status = "Pending"`` — initial state; approve_acr in Commit 3
      transitions to Created / Failed / Skipped.
    * ``proposed_is_group = 0`` — v1 locks ACR to leaf-account creation
      (AMB C2-1). Reviewers can't create group accounts through the
      migration workflow; groups belong to COA scaffolding set up
      before migration.
    * ``proposed_root_type`` — derived from the source decision
      (AMB C2-B). Prefers ``new_account_root_type`` (mapper's
      suggestion for pending_account_creation rows), falls back to
      ``tally_root_type`` (always populated by the parser). Client's
      readonly Data field value is ignored.
    * ``source_decisions`` — the source decision's name. CSV column on
      the ACR child DocType; future merge workflows (multiple ACRs
      for decisions that need the same new Account) would concat
      decision names with a comma separator.

    Args:
        decision: Source Mapping Decision as dict. Must contain
            ``name``, ``tally_name``, ``new_account_root_type``,
            ``tally_root_type``.
        proposed_account_name: Reviewer-typed bare name (no ABBR
            suffix). Must be non-empty (caller validates).
        proposed_parent: Reviewer-picked parent Account (ABBR-suffixed,
            e.g. "Current Assets - CACSPU"). Must be non-empty
            (caller validates). Guaranteed by the dialog tree picker
            to be a group account in the right root_type branch.
        account_type: Optional ERPNext Account.account_type value
            (Bank / Receivable / Tax / etc.). Empty string / None =
            not set. Full validation deferred to Commit 3's
            approve_acr (AMB C2-4).
        reason: Reviewer's justification for creating the account.
            Visible to the approver on the ACR child row.
        reviewer_notes: Verbatim reviewer note. The SCR pattern stores
            it as-is on the ACR; the decision's reviewer_notes
            separately gets a chronology-header-prepended version
            (AMB C2-2) handled by the Frappe wrapper.

    Returns:
        Dict with keys matching the ACR DocType fields.
    """
    # AMB C2-B: backend is the single source of truth for root_type.
    # Decision-driven chain: mapper's creation suggestion first,
    # parser's tally_root_type as fallback. Both are pre-populated by
    # run_mapper; at least one is always set for real Tally ledgers.
    root_type = (
        (decision.get("new_account_root_type") or "").strip()
        or (decision.get("tally_root_type") or "").strip()
        or ""
    )

    return {
        "status": "Pending",
        "proposed_account_name": proposed_account_name,
        "proposed_parent": proposed_parent,
        "proposed_root_type": root_type,
        # AMB C2-1: server forces leaf-creation regardless of client.
        "proposed_is_group": 0,
        "account_type": (account_type or "").strip() or None,
        "reason": (reason or "").strip() or None,
        "reviewer_notes": reviewer_notes or None,
        "source_decisions": decision.get("name") or "",
    }


def parse_source_decisions_csv(raw: str | None) -> list[str]:
    """Split a ``source_decisions`` CSV string into clean tokens.

    Used by the duplicate-detection guard in
    ``create_supplier_creation_request`` — exact-token match on the
    decision name, not substring (prevents false positives like
    ``MD-2026-00001`` matching ``MD-2026-00010``).

    Empty / ``None`` input → empty list. Whitespace around tokens is
    stripped; empty tokens (from trailing commas) are dropped.
    """
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


# ---------------------------------------------------------------------------
# Item 3 Commit 3 — SCR approval / rejection pure-core helpers
# ---------------------------------------------------------------------------


def build_supplier_doc_payload(
    *,
    scr_row: dict,
    default_supplier_type: str = "Company",
) -> dict:
    """Build the Supplier DocType insert payload from an SCR row.

    Called by ``approve_scr`` when transitioning a Pending / Failed SCR
    to Created — the payload here goes straight into
    ``frappe.get_doc({...}).insert()``.

    ``supplier_type`` is a required Supplier field (Select: Company /
    Individual / Partnership). The SCR form doesn't collect it today —
    most CACSPU-style Indian vendors are Company entities, so we
    default silently and let the reviewer edit on the Supplier form
    post-creation for edge cases (per 2026-04-22 design decision).

    Args:
        scr_row: The SCR child row as a dict. Must contain
            ``proposed_supplier_name``; ``proposed_supplier_group`` is
            optional.
        default_supplier_type: Override for tests. Production callers
            should let the ``"Company"`` default stand.

    Returns:
        Dict with ``doctype``, ``supplier_name``, ``supplier_group``,
        ``supplier_type``. Additional Supplier fields (country,
        tax_id, payment_terms) are not set — the Supplier defaults
        cover them.
    """
    return {
        "doctype": "Supplier",
        "supplier_name": scr_row.get("proposed_supplier_name") or "",
        "supplier_group": scr_row.get("proposed_supplier_group") or None,
        "supplier_type": default_supplier_type,
    }


def apply_scr_approval_to_decision(
    *,
    resolved_supplier_name: str,
) -> dict:
    """Decision field updates when an SCR is approved.

    Lifts the decision out of pending_supplier_creation: generators
    will now see it as ``tier1_supplier_exact`` (pointing at the
    freshly-created Supplier) and ``review_action=Approved``.
    Reviewer's master-pane indicator flips green.

    ``resolved_supplier_name`` is the Supplier's DocType name AS
    CREATED by Frappe — may differ from the SCR's
    ``proposed_supplier_name`` if a naming collision triggered a
    suffix (e.g. proposed "Acme Pvt Ltd" → resolved "Acme Pvt Ltd 1"
    when one already existed). Always read post-insert; never trust
    the proposed name.
    """
    return {
        "final_supplier": resolved_supplier_name,
        "tier": "tier1_supplier_exact",
        "review_action": "Approved",
    }


def build_account_parent_autocomplete_results(
    accounts: list[dict],
) -> list[list[str]]:
    """Pure formatter for the AccountResolutionDialog parent picker.

    Parallel to :func:`build_account_autocomplete_results` but tailored
    for parent-account picking (Item 4 Commit 1, SUB-1):

    * Backend filter ensures ``is_group = 1`` and ``root_type = X`` (the
      decision's ``tally_root_type``), so only group-account candidates
      in the right branch reach this formatter.
    * Sort is **depth ASC, account_name ASC** — top-level categories
      (e.g. "Current Assets - CACSPU") surface before deeply-nested
      siblings (e.g. "Advance for Expenses - CACSPU" under Current
      Assets). Reviewers wanted a "tree-ordered" feel without the cost
      of an actual tree widget (see AMB-10 resolution).
    * "Depth" is computed **relative to the result set**, not the full
      COA. A node whose ``parent_account`` is outside the set is treated
      as depth 0 (effectively the root within this filter). This keeps
      the first-row pick meaningful when the reviewer has typed a
      prefix that doesn't cover the literal tree root.

    Description format mirrors ``build_account_autocomplete_results``:
    ``"<root_type> · under <parent>"``, with fallbacks for rows missing
    one or both. The frontend renders description as the grey subtitle
    on the right of the autocomplete row.

    Args:
        accounts: Rows from ``frappe.get_all("Account", ...)``. Each
            row needs ``name``, ``parent_account``, ``root_type``.
            ``account_name`` is optional — falls back to ``name`` for
            the alpha sort key.

    Returns:
        List of ``[name, description]`` pairs in (depth, name) order.
        Empty input → empty list. Rows without a ``name`` are silently
        dropped (same refusal as the peer formatter).
    """
    by_name: dict[str, dict] = {}
    for a in accounts:
        n = a.get("name")
        if n:
            by_name[n] = a

    def _depth(start: str) -> int:
        """Walk parent_account chain, counting only steps that stay
        inside the result set. Cycle-safe via visited set."""
        d = 0
        cursor = start
        visited: set[str] = set()
        while cursor and cursor not in visited:
            visited.add(cursor)
            row = by_name.get(cursor)
            if row is None:
                break
            parent = row.get("parent_account")
            if not parent or parent not in by_name:
                break
            cursor = parent
            d += 1
        return d

    annotated: list[tuple[int, str, dict]] = []
    for a in accounts:
        name = a.get("name")
        if not name:
            continue
        sort_key = (a.get("account_name") or name or "").lower()
        annotated.append((_depth(name), sort_key, a))

    annotated.sort(key=lambda t: (t[0], t[1]))

    results: list[list[str]] = []
    for _depth_val, _sort_key, acc in annotated:
        name = acc.get("name")
        parent = (acc.get("parent_account") or "").strip()
        root = (acc.get("root_type") or "").strip()
        if parent and root:
            description = f"{root} \u00b7 under {parent}"
        elif parent:
            description = f"under {parent}"
        elif root:
            description = root
        else:
            description = "(root)"
        results.append([name, description])
    return results


def apply_scr_rejection_to_decision() -> dict:
    """Decision field updates when an SCR is rejected.

    ``review_action`` flips to ``Rejected``; ``tier`` stays at
    ``pending_supplier_creation`` (mapper-authoritative — the mapper
    can't "unmap" a decision just because the reviewer said no).
    Generator refusal gates (oit_csv / advance_je) were extended
    in Item 3 Commit 3 to SKIP rejected rows rather than refuse on
    them, so Rejected-with-pending-tier is generator-friendly.

    ``final_supplier`` is explicitly cleared (any prior Map-to-existing
    pick on this decision is stale — the reviewer's latest intent
    is Rejected).
    """
    return {
        "review_action": "Rejected",
        "final_supplier": None,
    }
