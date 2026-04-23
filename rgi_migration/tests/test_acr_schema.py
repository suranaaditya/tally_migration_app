"""Schema-validation tests for the Account Creation Request DocType.

Pure filesystem tests — reads the DocType JSON and asserts structural
invariants without touching Frappe. Parallel to
``test_mapping_decision_schema.py``.

Item 4 Commit 2 added ``account_type`` (Data, optional) to let the
AccountResolutionDialog's reviewer-typed account_type hint land
somewhere on the ACR row. Commit 3's ``approve_acr`` will pass this
value through to ERPNext's Account insert.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_DOCTYPE_JSON = (
    Path(__file__).parent.parent
    / "rgi_migration"
    / "doctype"
    / "account_creation_request"
    / "account_creation_request.json"
)


@pytest.fixture(scope="module")
def doctype() -> dict:
    with _DOCTYPE_JSON.open("r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def fields_by_name(doctype: dict) -> dict[str, dict]:
    return {f["fieldname"]: f for f in doctype["fields"]}


def test_doctype_is_child_table(doctype: dict) -> None:
    """ACR lives as a child table under Tally Migration Session.
    ``istable=1`` is load-bearing — Frappe refuses to append child
    rows to a DocType without it."""
    assert doctype.get("istable") == 1


# ---------------------------------------------------------------------------
# Item 4 Commit 2 addition — account_type field
# ---------------------------------------------------------------------------


def test_account_type_field_present(fields_by_name: dict) -> None:
    f = fields_by_name.get("account_type")
    assert f is not None, "account_type field missing"
    # Data rather than Select for the same reason new_account_root_type
    # is Data: insulates against ERPNext adding account_type enum values
    # we don't know about. Frappe Select validation would otherwise
    # reject legitimate future values.
    assert f["fieldtype"] == "Data"
    # Optional — reviewer leaves blank for accounts with no special
    # treatment (the common case).
    assert f.get("reqd") in (0, None, False)


def test_account_type_in_field_order(doctype: dict) -> None:
    """Every field in the schema must appear in field_order — a field
    defined without an ordering entry is a silent bug (won't render
    on the DocType form)."""
    assert "account_type" in doctype["field_order"]


# ---------------------------------------------------------------------------
# Structural invariants — shared with every other DocType JSON guard
# ---------------------------------------------------------------------------


def test_field_order_has_no_duplicates(doctype: dict) -> None:
    order = doctype["field_order"]
    dups = [fn for fn in set(order) if order.count(fn) > 1]
    assert not dups, f"duplicate entries in field_order: {dups}"


def test_every_field_order_entry_has_a_definition(
    doctype: dict, fields_by_name: dict
) -> None:
    orphans = [fn for fn in doctype["field_order"] if fn not in fields_by_name]
    assert not orphans, f"field_order references undefined fields: {orphans}"


def test_status_enum_values_are_complete(fields_by_name: dict) -> None:
    """ACR lifecycle: Pending → Created (approve success) / Failed
    (approve error) / Skipped (reject). Guards against a future rename
    that would break the state machine."""
    status = fields_by_name["status"]
    assert status["fieldtype"] == "Select"
    options = set(status["options"].split("\n"))
    required = {"Pending", "Created", "Skipped", "Failed"}
    missing = required - options
    assert not missing, f"status Select missing: {sorted(missing)}"
