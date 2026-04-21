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
