"""Unit tests for the build_account_parent_autocomplete_results formatter.

Exercises
``rgi_migration.rgi_migration.page.md_review.query.build_account_parent_autocomplete_results``
— the pure formatter feeding the AccountResolutionDialog's parent-
picker autocomplete (Item 4 Commit 1, AMB-10 → SUB-1).

Contract:

* Sort is (depth ASC, account_name ASC) where depth counts only steps
  that stay inside the result set. A node whose parent_account is
  outside the set is treated as depth 0 — the reviewer's "tree-ordered"
  feel kicks in without needing to include every ancestor.
* Description format mirrors the peer account autocomplete's subtitle
  ("<root> · under <parent>" with fallbacks). Drops name-less rows.

Covers (6 tests):

1. Flat set (all siblings): preserves alpha order.
2. Mixed-depth set: top-level groups surface before nested leaves.
3. Parent outside result set is treated as depth 0 (e.g. typed-prefix
   search that matches a deep-nested group whose ancestors aren't
   in the result).
4. Cycle in parent_account is cycle-safe (defensive — broken data
   shouldn't crash the autocomplete).
5. Description subtitle formatting — shared with
   build_account_autocomplete_results's fallback matrix.
6. Empty + name-less rows: graceful empty return, silent skip.
"""

from __future__ import annotations

from rgi_migration.rgi_migration.page.md_review.query import (
    build_account_parent_autocomplete_results,
)


def test_flat_siblings_preserve_alpha_order():
    """Three top-level groups with no parent in the set. All depth 0;
    sort falls through to account_name ASC."""
    rows = [
        {
            "name": "Current Assets - CACSPU",
            "account_name": "Current Assets",
            "parent_account": "Application Of Funds - CACSPU",
            "root_type": "Asset",
        },
        {
            "name": "Fixed Assets - CACSPU",
            "account_name": "Fixed Assets",
            "parent_account": "Application Of Funds - CACSPU",
            "root_type": "Asset",
        },
        {
            "name": "Bank Accounts - CACSPU",
            "account_name": "Bank Accounts",
            "parent_account": "Application Of Funds - CACSPU",
            "root_type": "Asset",
        },
    ]

    result = build_account_parent_autocomplete_results(rows)

    # All three depth 0 (parent outside set). Sort by account_name ASC.
    assert [r[0] for r in result] == [
        "Bank Accounts - CACSPU",
        "Current Assets - CACSPU",
        "Fixed Assets - CACSPU",
    ]


def test_mixed_depth_surfaces_top_level_first():
    """Depth 0 rows come before depth 1, even when alpha order would
    otherwise interleave. This is the core AMB-10 behaviour — reviewer
    sees categories before nested siblings."""
    rows = [
        # depth 1 — nested under Current Assets
        {
            "name": "Advance for Expenses - CACSPU",
            "account_name": "Advance for Expenses",
            "parent_account": "Current Assets - CACSPU",
            "root_type": "Asset",
        },
        # depth 0 — parent outside set
        {
            "name": "Current Assets - CACSPU",
            "account_name": "Current Assets",
            "parent_account": "Application Of Funds - CACSPU",
            "root_type": "Asset",
        },
        # depth 0 — parent outside set
        {
            "name": "Fixed Assets - CACSPU",
            "account_name": "Fixed Assets",
            "parent_account": "Application Of Funds - CACSPU",
            "root_type": "Asset",
        },
    ]

    result = build_account_parent_autocomplete_results(rows)

    # Current Assets (d=0) < Fixed Assets (d=0) < Advance for Expenses (d=1).
    # Even though "Advance" alphabetically precedes "Current",
    # the depth-first sort wins.
    assert [r[0] for r in result] == [
        "Current Assets - CACSPU",
        "Fixed Assets - CACSPU",
        "Advance for Expenses - CACSPU",
    ]


def test_parent_outside_set_is_depth_zero():
    """When the reviewer's typed-prefix filter matches a deeply-nested
    group but not its ancestors, that group is depth 0 *within the
    result set*. Important because the absolute depth would otherwise
    push it to the bottom."""
    rows = [
        {
            "name": "General Store Inventory - CACSPU",
            "account_name": "General Store Inventory",
            # parent exists in the full COA but NOT in this result set
            "parent_account": "Inventory - Consumables - CACSPU",
            "root_type": "Asset",
        },
    ]

    result = build_account_parent_autocomplete_results(rows)

    # Single row — trivially sorted. But the depth logic must treat the
    # parent-outside-set case as depth 0, not crash with a lookup miss.
    assert len(result) == 1
    assert result[0][0] == "General Store Inventory - CACSPU"


def test_cycle_in_parent_chain_is_safe():
    """A -> B -> A cycle in parent_account (broken data) must not
    infinite-loop the depth calculation. Visited-set guard exits the
    walk and the row still renders (sorted by its partial depth)."""
    rows = [
        {
            "name": "A - CACSPU",
            "account_name": "A",
            "parent_account": "B - CACSPU",
            "root_type": "Asset",
        },
        {
            "name": "B - CACSPU",
            "account_name": "B",
            "parent_account": "A - CACSPU",
            "root_type": "Asset",
        },
    ]

    # Should return both rows without hanging.
    result = build_account_parent_autocomplete_results(rows)
    assert len(result) == 2
    # Both in cycle → same depth (1 each before the visited guard trips).
    # Alpha sort as tiebreaker: "A" then "B".
    assert [r[0] for r in result] == ["A - CACSPU", "B - CACSPU"]


def test_description_subtitle_fallbacks():
    """Mirror the peer formatter's fallback matrix for the grey
    subtitle. Matters because the autocomplete renderer shows the
    subtitle as disambiguation text; an empty subtitle collapses the
    row height."""
    rows = [
        {
            "name": "Has both - CACSPU",
            "account_name": "Has both",
            "parent_account": "Parent - CACSPU",
            "root_type": "Asset",
        },
        {
            "name": "Root type only - CACSPU",
            "account_name": "Root type only",
            "parent_account": "",
            "root_type": "Liability",
        },
        {
            "name": "Parent only - CACSPU",
            "account_name": "Parent only",
            "parent_account": "Some Parent - CACSPU",
            "root_type": "",
        },
        {
            "name": "Neither - CACSPU",
            "account_name": "Neither",
            "parent_account": None,
            "root_type": None,
        },
    ]

    # Depth 0 for all (no parents in set). Alpha order drives output.
    result = build_account_parent_autocomplete_results(rows)

    # Map name → description for readable asserts regardless of order.
    by_name = {r[0]: r[1] for r in result}
    assert by_name["Has both - CACSPU"] == "Asset \u00b7 under Parent - CACSPU"
    assert by_name["Root type only - CACSPU"] == "Liability"
    assert by_name["Parent only - CACSPU"] == "under Some Parent - CACSPU"
    assert by_name["Neither - CACSPU"] == "(root)"


def test_empty_input_and_missing_name_are_safe():
    """Empty input list + rows missing the name key. Same safety
    contract as the peer formatter — the autocomplete renderer will
    crash on [null, ...] so we silently drop name-less rows."""
    assert build_account_parent_autocomplete_results([]) == []

    rows = [
        {
            "name": "Good - CACSPU",
            "account_name": "Good",
            "parent_account": "",
            "root_type": "Asset",
        },
        {
            "name": "",  # Malformed — must be skipped
            "parent_account": "",
            "root_type": "Asset",
        },
        {
            # No name key at all — must be skipped
            "parent_account": "",
            "root_type": "Asset",
        },
    ]

    result = build_account_parent_autocomplete_results(rows)
    assert len(result) == 1
    assert result[0][0] == "Good - CACSPU"
