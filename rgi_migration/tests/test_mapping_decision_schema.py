"""Schema-validation tests for the Mapping Decision DocType JSON.

Pure filesystem tests — reads the DocType JSON and asserts structural
invariants without touching Frappe. Guardrail for Week 4 Item 2
Commit 1 schema additions (4 supplier fields + tier enum extension).

If these fail, the DocType JSON drifted from what Item 2 / 3 expect.
Check ``rgi_migration/rgi_migration/doctype/mapping_decision/mapping_decision.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_DOCTYPE_JSON = (
    Path(__file__).parent.parent
    / "rgi_migration"
    / "doctype"
    / "mapping_decision"
    / "mapping_decision.json"
)


@pytest.fixture(scope="module")
def doctype() -> dict:
    with _DOCTYPE_JSON.open("r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def fields_by_name(doctype: dict) -> dict[str, dict]:
    return {f["fieldname"]: f for f in doctype["fields"]}


# ---------------------------------------------------------------------------
# New Item 2 Commit 1 fields
# ---------------------------------------------------------------------------


def test_proposed_supplier_field_present(fields_by_name: dict) -> None:
    f = fields_by_name.get("proposed_supplier")
    assert f is not None, "proposed_supplier field missing"
    assert f["fieldtype"] == "Link"
    assert f["options"] == "Supplier"


def test_final_supplier_field_present(fields_by_name: dict) -> None:
    f = fields_by_name.get("final_supplier")
    assert f is not None, "final_supplier field missing"
    assert f["fieldtype"] == "Link"
    assert f["options"] == "Supplier"


def test_supplier_match_score_field_present(fields_by_name: dict) -> None:
    f = fields_by_name.get("supplier_match_score")
    assert f is not None, "supplier_match_score field missing"
    assert f["fieldtype"] == "Float"
    # precision=3 captures fuzzy score resolution (WRatio/100 to 3 dp)
    assert f.get("precision") == "3"


def test_new_supplier_name_field_present(fields_by_name: dict) -> None:
    f = fields_by_name.get("new_supplier_name")
    assert f is not None, "new_supplier_name field missing"
    # Data, not Small Text — suggestion is a short label, not prose
    assert f["fieldtype"] == "Data"


# ---------------------------------------------------------------------------
# Item 4 Commit 1 additions — new_account_* quartet
# ---------------------------------------------------------------------------


def test_new_account_name_field_present(fields_by_name: dict) -> None:
    f = fields_by_name.get("new_account_name")
    assert f is not None, "new_account_name field missing"
    # Data (short label), parallel to new_supplier_name's choice.
    assert f["fieldtype"] == "Data"


def test_new_account_parent_field_present(fields_by_name: dict) -> None:
    f = fields_by_name.get("new_account_parent")
    assert f is not None, "new_account_parent field missing"
    # Data, not Link to Account — the suggestion may reference an Account
    # that doesn't exist yet at the time the mapper runs (the whole point
    # of pending_account_creation). Link validation would reject it.
    assert f["fieldtype"] == "Data"


def test_new_account_root_type_field_present(fields_by_name: dict) -> None:
    f = fields_by_name.get("new_account_root_type")
    assert f is not None, "new_account_root_type field missing"
    # Data, not Select — mapper-emitted values are always in the
    # canonical root_type set but using Data avoids a Select-enum mismatch
    # at insert time if ERPNext ever adds a root_type.
    assert f["fieldtype"] == "Data"


def test_new_account_is_group_field_present(fields_by_name: dict) -> None:
    f = fields_by_name.get("new_account_is_group")
    assert f is not None, "new_account_is_group field missing"
    assert f["fieldtype"] == "Check"
    # Explicit default="0" — Frappe Check fields without a default
    # insert as NULL which breaks the 0/1 contract elsewhere.
    assert f.get("default") == "0"


def test_field_order_contains_new_account_fields(doctype: dict) -> None:
    """Item 4 Commit 1: all four new_account_* keys must appear in
    field_order. A field defined without an ordering entry is a silent
    bug — it won't render on the DocType form."""
    order = doctype["field_order"]
    for fn in (
        "new_account_name",
        "new_account_parent",
        "new_account_root_type",
        "new_account_is_group",
    ):
        assert fn in order, f"{fn} defined but not in field_order"


# ---------------------------------------------------------------------------
# Tier enum coverage — every mapper-emitted tier must be in the Select
# ---------------------------------------------------------------------------


# Authoritative list of tier values the mapper emits. Sourced from
# rgi_migration/mapper/mapper.py and rgi_migration/mapper/tier1_supplier.py.
# If a new tier is added in mapper code, this assert forces the enum to
# grow with it — the whole point of this guardrail.
_MAPPER_EMITTED_TIERS = frozenset({
    "tier1_exact",
    "tier1_rule",
    "tier1_pattern",
    "tier2_fuzzy",
    "tier3_claude",
    "unmapped",
    "excluded_pnl",
    "excluded_zero_balance",
    "group_refused",
    "anti_pattern_blocked",
    "pending_account_creation",
    "tier1_supplier_exact",
    "tier1_supplier_alias",
    "tier1_supplier_fuzzy",
    "pending_supplier_creation",
})


def test_tier_enum_covers_all_mapper_tiers(fields_by_name: dict) -> None:
    tier = fields_by_name["tier"]
    assert tier["fieldtype"] == "Select"
    enum_options = set(tier["options"].split("\n"))
    missing = _MAPPER_EMITTED_TIERS - enum_options
    assert not missing, (
        f"tier Select enum missing mapper-emitted values: {sorted(missing)}. "
        f"Inserts with these values would silently drop on Frappe write."
    )


def test_tier_enum_includes_item_2_additions(fields_by_name: dict) -> None:
    """Explicit coverage for Item 2 Commit 1 additions. Narrower than
    the all-tiers test — regresses specifically if either Item 2
    addition is removed."""
    opts = set(fields_by_name["tier"]["options"].split("\n"))
    assert "tier1_supplier_exact" in opts
    assert "tier1_supplier_alias" in opts


# ---------------------------------------------------------------------------
# review_action enum coverage — every mapper-emitted value must be in Select
# ---------------------------------------------------------------------------


# Authoritative list of review_action strings emitted by the mapper +
# validators. Sourced by grep of ``rgi_migration/mapper/`` for
# ``review_action=`` literals. Reviewer-facing values (Approved,
# Rejected, Manual Override, Deferred, Skipped) are included because
# save_decision (Item 1 Commit 4b) writes them.
_MAPPER_AND_REVIEWER_REVIEW_ACTIONS = frozenset({
    # Mapper emissions
    "Pending",
    "Excluded (P&L)",
    "Excluded (Zero Balance)",
    "Pending Account Creation",
    "Pending Group Account Resolution",
    "Pending Supplier Creation",
    # Reviewer-facing
    "Approved",
    "Rejected",
    "Manual Override",
    "Deferred",
    "Skipped",
    # Reviewer-initiated workflow states (Item 3 Commit 2)
    "Supplier Creation Requested",
})


def test_review_action_enum_covers_all_emitted_values(fields_by_name: dict) -> None:
    ra = fields_by_name["review_action"]
    assert ra["fieldtype"] == "Select"
    enum_options = set(ra["options"].split("\n"))
    missing = _MAPPER_AND_REVIEWER_REVIEW_ACTIONS - enum_options
    assert not missing, (
        f"review_action Select enum missing values emitted by "
        f"mapper/validators/reviewer: {sorted(missing)}. Inserts with "
        f"these values would fail Frappe Select validation."
    )


# ---------------------------------------------------------------------------
# Structural invariants — new fields in field_order, no duplicates
# ---------------------------------------------------------------------------


def test_field_order_contains_new_fields(doctype: dict) -> None:
    order = doctype["field_order"]
    for fn in (
        "proposed_supplier",
        "new_supplier_name",
        "supplier_match_score",
        "final_supplier",
    ):
        assert fn in order, f"{fn} defined but not in field_order"


def test_field_order_has_no_duplicates(doctype: dict) -> None:
    order = doctype["field_order"]
    dups = [fn for fn in set(order) if order.count(fn) > 1]
    assert not dups, f"duplicate entries in field_order: {dups}"


def test_every_field_order_entry_has_a_definition(
    doctype: dict, fields_by_name: dict
) -> None:
    orphans = [fn for fn in doctype["field_order"] if fn not in fields_by_name]
    assert not orphans, (
        f"field_order references undefined fields: {orphans}"
    )
