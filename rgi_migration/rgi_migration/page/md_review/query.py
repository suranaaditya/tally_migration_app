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
