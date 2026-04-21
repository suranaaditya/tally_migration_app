"""Unit tests for the get_session_decisions query core.

Exercises ``rgi_migration.rgi_migration.page.md_review.query.fetch_session_decisions``,
the Frappe-free pure-logic implementation. The Frappe wrapper
``md_review.get_session_decisions`` can't be unit-tested from a local
pytest environment (no Frappe ORM available), so it's exercised via
the server-side bench-console smoke test instead.

Covers (per morning authorization for Commit 3, 5 tests):

1. Empty-session case — zero decisions, zero counts
2. Default ordering applied when caller passes no order_by
3. Caller's filters merged with the session filter
4. Session-scope guard — caller's override of ``session`` filter is
   silently overridden by the argument (security-by-default)
5. Pagination — start + page_length honored
"""

from __future__ import annotations

from rgi_migration.rgi_migration.page.md_review.query import (
    DEFAULT_ORDER_BY,
    fetch_session_decisions,
)


# ---------------------------------------------------------------------------
# Fake Frappe primitives — duck-type what fetch_session_decisions calls
# ---------------------------------------------------------------------------


class _FakeStore:
    """Minimal in-memory stand-in for the bits of Frappe we touch.

    ``records`` is a list of dicts; each dict has a ``session`` key
    plus whatever domain fields the test is exercising.
    """

    def __init__(self, records):
        self.records = records
        self.last_get_all_kwargs = None  # captured for assertions

    def get_all(self, **kw):
        """Mimics frappe.get_all('Mapping Decision', ...) enough for the test."""
        self.last_get_all_kwargs = kw
        # Apply the filters dict as AND-matches
        filtered = [
            r
            for r in self.records
            if all(r.get(k) == v for k, v in kw["filters"].items())
        ]
        # Apply order_by — recognised cases only; default or explicit
        order_by = kw.get("order_by") or ""
        if "review_action" in order_by and "net_amount" in order_by:
            # review_action ASC, net_amount DESC (the default)
            filtered.sort(
                key=lambda r: (r.get("review_action", ""), -r.get("net_amount", 0.0))
            )
        # Apply pagination
        start = kw.get("start", 0)
        page_length = kw.get("page_length", 50)
        paginated = filtered[start : start + page_length]
        # Mimic Frappe: return dicts projected to the requested field list
        fields = kw.get("fields", [])
        return [{f: r.get(f) for f in fields if f in r} for r in paginated]

    def count(self, filters):
        """Mimics frappe.db.count('Mapping Decision', filters)."""
        return sum(
            1
            for r in self.records
            if all(r.get(k) == v for k, v in filters.items())
        )


def _noop_permission():
    """No-op permission check — tests don't exercise the denial path."""
    pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_empty_session_returns_empty_list_with_zero_counts():
    """Session with zero decisions — the current state of TMS-CACSPU--00495
    on the bench. Should return ``{"decisions": [], ...: 0, ...: 0}``."""

    store = _FakeStore(records=[])

    result = fetch_session_decisions(
        session_name="S-001",
        filters=None,
        start=0,
        page_length=50,
        order_by=None,
        check_permission=_noop_permission,
        get_all=store.get_all,
        count=store.count,
    )

    assert result == {
        "decisions": [],
        "total_count": 0,
        "filtered_count": 0,
    }


def test_default_ordering_review_action_asc_then_net_amount_desc():
    """No ``order_by`` supplied → default sort applies: Pending first
    (alphabetical), then largest balance first within each state."""

    store = _FakeStore(
        records=[
            {"name": "MD-2", "session": "S-001", "review_action": "Pending",
             "net_amount": 100.0, "tally_name": "Small Pending"},
            {"name": "MD-3", "session": "S-001", "review_action": "Pending",
             "net_amount": 500.0, "tally_name": "Big Pending"},
            {"name": "MD-1", "session": "S-001", "review_action": "Approved",
             "net_amount": 1000.0, "tally_name": "Big Approved"},
        ]
    )

    result = fetch_session_decisions(
        session_name="S-001",
        filters=None,
        start=0,
        page_length=50,
        order_by=None,  # triggers default
        check_permission=_noop_permission,
        get_all=store.get_all,
        count=store.count,
    )

    # Expected: Approved (A < P alphabetically), then Pending desc-by-amount
    names = [d["name"] for d in result["decisions"]]
    assert names == ["MD-1", "MD-3", "MD-2"]

    # And confirm the default string was passed to get_all
    assert store.last_get_all_kwargs["order_by"] == DEFAULT_ORDER_BY


def test_caller_filters_merged_with_session_filter():
    """Caller passes a non-session filter (e.g. ``{"tier": "unmapped"}``).
    Both the session filter and the caller filter must be applied."""

    store = _FakeStore(
        records=[
            {"name": "MD-1", "session": "S-001", "tier": "unmapped",
             "review_action": "Pending", "net_amount": 100.0},
            {"name": "MD-2", "session": "S-001", "tier": "tier1_exact",
             "review_action": "Approved", "net_amount": 200.0},
            {"name": "MD-3", "session": "S-001", "tier": "unmapped",
             "review_action": "Pending", "net_amount": 300.0},
            {"name": "MD-4", "session": "S-OTHER", "tier": "unmapped",
             "review_action": "Pending", "net_amount": 400.0},
        ]
    )

    result = fetch_session_decisions(
        session_name="S-001",
        filters={"tier": "unmapped"},
        start=0,
        page_length=50,
        order_by=None,
        check_permission=_noop_permission,
        get_all=store.get_all,
        count=store.count,
    )

    # Only MD-1 and MD-3 match (session=S-001 AND tier=unmapped);
    # MD-2 filtered out by tier; MD-4 filtered out by session.
    names = {d["name"] for d in result["decisions"]}
    assert names == {"MD-1", "MD-3"}
    assert result["filtered_count"] == 2
    # total_count is session-scoped only (ignores tier filter)
    assert result["total_count"] == 3  # MD-1, MD-2, MD-3


def test_session_scope_guard_prevents_caller_override():
    """Security guard — if the caller passes ``filters={"session": "<other>"}``
    in an attempt to read a different session's decisions, the
    ``session_name`` argument wins. MD-4 (session S-OTHER) must NOT
    appear in the result."""

    store = _FakeStore(
        records=[
            {"name": "MD-1", "session": "S-001", "review_action": "Pending",
             "net_amount": 100.0},
            {"name": "MD-4", "session": "S-OTHER", "review_action": "Pending",
             "net_amount": 999.0},
        ]
    )

    result = fetch_session_decisions(
        session_name="S-001",
        filters={"session": "S-OTHER"},  # attempted bypass
        start=0,
        page_length=50,
        order_by=None,
        check_permission=_noop_permission,
        get_all=store.get_all,
        count=store.count,
    )

    # Attempted bypass is silently ignored — only MD-1 returned.
    names = [d["name"] for d in result["decisions"]]
    assert names == ["MD-1"]
    # Confirm the filter dict that went down to get_all was rewritten
    assert store.last_get_all_kwargs["filters"]["session"] == "S-001"


def test_pagination_respects_start_and_page_length():
    """With 10 decisions and ``start=2, page_length=3``, expect rows
    3-5 of the ordered set (indexing from 1). The default sort
    applies, then pagination slices the ordered result."""

    # 10 decisions on the same session, distinct net_amounts
    # so the sort order is unambiguous.
    store = _FakeStore(
        records=[
            {
                "name": f"MD-{i:02d}",
                "session": "S-001",
                "review_action": "Pending",
                "net_amount": float(1000 - i),  # DESC sort → MD-00 first
                "tally_name": f"Ledger {i}",
            }
            for i in range(10)
        ]
    )

    result = fetch_session_decisions(
        session_name="S-001",
        filters=None,
        start=2,
        page_length=3,
        order_by=None,
        check_permission=_noop_permission,
        get_all=store.get_all,
        count=store.count,
    )

    # After DESC sort by net_amount: MD-00, MD-01, MD-02, MD-03, ...
    # start=2, page_length=3 → MD-02, MD-03, MD-04
    names = [d["name"] for d in result["decisions"]]
    assert names == ["MD-02", "MD-03", "MD-04"]
    # Counts always reflect the full set (not the page)
    assert result["filtered_count"] == 10
    assert result["total_count"] == 10


