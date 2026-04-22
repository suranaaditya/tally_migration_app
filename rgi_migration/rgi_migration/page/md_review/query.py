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

    # reviewer_notes — see Refinement 3 logic above.
    stored_notes = (current.get("reviewer_notes") or "").strip()
    new_input = (reviewer_notes_input or "").strip()

    if not new_input:
        # No new content. Preserve stored exactly (may itself be "").
        final_notes = current.get("reviewer_notes") or ""
    elif not stored_notes:
        # First-note case — write verbatim, no header.
        final_notes = new_input
    elif new_input == stored_notes:
        # Reviewer didn't actually change anything. Leave stored
        # untouched — avoids adding a header for a no-op save.
        final_notes = current.get("reviewer_notes") or ""
    else:
        header = f"[{session_user}, {now_str}] "
        final_notes = f"{header}{new_input}\n\n{stored_notes}"

    return {
        "review_action": review_action,
        "final_account": final_account_clean,
        "final_dr": opening_dr,
        "final_cr": opening_cr,
        "reviewer_notes": final_notes,
    }


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


# Fields that Undo restores. Must match :data:`SAVE_DECISION_FIELDS` —
# the snapshot written by ``save_decision`` uses that same tuple, so
# what gets saved is what gets restored. Duplicated here (rather than
# aliased) so the Undo contract is legible on its own.
UNDO_RESTORE_FIELDS: tuple[str, ...] = SAVE_DECISION_FIELDS


def apply_decision_undo(
    *,
    current: dict,
    snapshot: dict,
) -> dict:
    """Pure implementation of the undo-save mutation logic.

    Given the current Mapping Decision doc-dict and the pre-save
    snapshot that ``save_decision`` stashed in ``frappe.cache()``,
    return a dict of the fields to restore onto the doc before save.
    The Frappe wrapper applies these and calls ``doc.save()`` —
    **without** running the chronology-header logic
    (``apply_decision_save`` is NOT re-invoked on undo; undo means
    "never happened," so no new header line is added).

    The reviewer_notes field is restored verbatim from the snapshot,
    so whatever was stored *before* the last save reappears — any
    chronology header that ``apply_decision_save`` prepended during
    the save being undone is discarded along with the rest of that
    save's changes.

    Args:
        current: The current Mapping Decision as a dict. Only used
            as a shape reference — all fields in the return dict
            come from ``snapshot``.
        snapshot: The pre-save snapshot from
            ``frappe.cache()["mdr_undo:<user>:<decision>"]``. Must
            contain every key in :data:`UNDO_RESTORE_FIELDS`.

    Returns:
        A dict with exactly the keys in :data:`UNDO_RESTORE_FIELDS`.
        Caller assigns these onto the Frappe doc and calls ``save()``.

    Raises:
        KeyError: if ``snapshot`` is missing any of the required keys
            (defensive guard — a malformed cache entry would otherwise
            silently restore partial state and leave the doc in a
            corrupt shape).
    """
    _ = current  # reserved for future diff-based restoration; unused in v1
    restored: dict = {}
    for field in UNDO_RESTORE_FIELDS:
        if field not in snapshot:
            raise KeyError(
                f"undo snapshot missing required field {field!r} — "
                "snapshot is malformed; refusing partial restore"
            )
        restored[field] = snapshot[field]
    return restored


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
