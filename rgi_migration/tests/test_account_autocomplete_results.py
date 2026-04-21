"""Unit tests for the build_account_autocomplete_results pure formatter.

Exercises ``rgi_migration.rgi_migration.page.md_review.query.build_account_autocomplete_results``
— the Frappe-free implementation that turns Account-search rows
into the ``[name, description]`` pairs Frappe's Link autocomplete
expects. The description is the reviewer-facing disambiguation hint
(parent + root type) identified as a UX need during the OQ2 spike.

Covers (5 tests):

1. Standard row with both parent_account and root_type produces the
   full `"<root_type> · under <parent>"` subtitle.
2. Missing root_type falls back to `"under <parent>"`.
3. Root-level account (no parent) with root_type shows just the
   root_type as the subtitle.
4. Completely-empty account (no parent, no root_type) shows the
   `"(root)"` placeholder rather than a blank subtitle.
5. Empty input list returns empty output list — no crash, no
   sentinel row. Also: defensive skip of rows without a name.
"""

from __future__ import annotations

from rgi_migration.rgi_migration.page.md_review.query import (
    build_account_autocomplete_results,
)


def test_standard_row_produces_full_subtitle():
    """The common case — a leaf account with both a parent and a
    root_type. Subtitle should show both, separated by a middle dot."""

    rows = [
        {
            "name": "Bank of Maharashtra - CACSPU",
            "account_name": "Bank of Maharashtra",
            "parent_account": "Bank Accounts - CACSPU",
            "root_type": "Asset",
        },
    ]

    result = build_account_autocomplete_results(rows)

    assert len(result) == 1
    assert result[0][0] == "Bank of Maharashtra - CACSPU"
    # Subtitle uses U+00B7 MIDDLE DOT per the query.py spec
    assert result[0][1] == "Asset \u00b7 under Bank Accounts - CACSPU"


def test_missing_root_type_falls_back_to_parent_only():
    """If the Account row somehow has a parent but no root_type
    (shouldn't happen in practice — ERPNext always populates root_type
    on non-root accounts — but defensive). Subtitle shows just the
    parent."""

    rows = [
        {
            "name": "Stray Acct - CACSPU",
            "account_name": "Stray Acct",
            "parent_account": "Application of Funds - CACSPU",
            "root_type": "",
        },
    ]

    result = build_account_autocomplete_results(rows)

    assert result[0][1] == "under Application of Funds - CACSPU"


def test_root_account_shows_only_root_type():
    """A top-level ERPNext account (parent_account empty, root_type
    set). Subtitle should show just the root_type — the reviewer can
    see at a glance this is a root-level account."""

    rows = [
        {
            "name": "Application of Funds - CACSPU",
            "account_name": "Application of Funds",
            "parent_account": None,
            "root_type": "Asset",
        },
    ]

    result = build_account_autocomplete_results(rows)

    assert result[0][1] == "Asset"


def test_empty_account_shows_root_placeholder():
    """If both parent_account and root_type are missing (extremely
    defensive — a malformed Account row), the subtitle falls back to
    the `"(root)"` placeholder so the autocomplete renderer doesn't
    get a blank string that collapses the row's height."""

    rows = [
        {
            "name": "Orphan Acct",
            "account_name": "Orphan Acct",
            "parent_account": "",
            "root_type": None,
        },
    ]

    result = build_account_autocomplete_results(rows)

    assert result[0][1] == "(root)"


def test_empty_input_and_missing_name_are_safe():
    """Two defenses in one test (both in the same failure family):

    - Empty input list returns empty list (no crash, no placeholder).
    - A row with a missing ``name`` is silently skipped rather than
      returned as ``[None, "..."]`` which would break the autocomplete
      renderer (it calls ``.toLowerCase()`` on element 0).
    """

    assert build_account_autocomplete_results([]) == []

    rows = [
        {
            "name": "Good Acct - CACSPU",
            "parent_account": "Assets - CACSPU",
            "root_type": "Asset",
        },
        {
            "name": "",  # Malformed — should be skipped
            "parent_account": "Assets - CACSPU",
            "root_type": "Asset",
        },
        {
            # No name key at all
            "parent_account": "Assets - CACSPU",
            "root_type": "Asset",
        },
    ]

    result = build_account_autocomplete_results(rows)

    assert len(result) == 1
    assert result[0][0] == "Good Acct - CACSPU"
