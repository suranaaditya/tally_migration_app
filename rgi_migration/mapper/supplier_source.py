"""Supplier-source abstraction for Tier-1 supplier matching (Work Item 6).

Scaffold only — no matching logic yet. This module mirrors
`rgi_migration/mapper/rule_source.py` in structural shape so consumers can
follow the same mental model.

`SupplierSource` is a Protocol; the mapper consumes `Supplier` dataclasses
without caring where they came from. Three concrete implementations:

* `CsvFileSupplierSource` — reads from an ERPNext Supplier-export CSV
  (columns `name`, `supplier_name`, `supplier_group` at minimum; extras
  ignored). Used in dev / tests until Work Item 6 wires in the live
  Frappe source.
* `InMemorySupplierSource` — list-backed, for unit tests.
* `FrappeSupplierSource` — placeholder that will read from the `Supplier`
  DocType via `frappe.get_all`. Stub today; implemented in Work Item 6.

Matching intelligence (exact_ci / fuzzy_85 / fuzzy_90) does NOT live
here — this module only answers "what suppliers exist". The resolver
that picks a supplier given a Tally vendor name lands in Work Item 6,
parallel to `tier1_rules.find_matching_positive_rule`.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


# ---------------------------------------------------------------------------
# Value object
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Supplier:
    """Canonical shape consumed by the mapper.

    `name` is the ERPNext document ID (the primary key / `name` column on
    `tabSupplier`). `supplier_name` is the human-readable display name
    that gets compared against Tally vendor ledger names.
    """

    name: str            # ERPNext document ID (primary key)
    supplier_name: str   # Display name — matched against Tally vendor names
    supplier_group: str  # Group classification (e.g. "Services", "Raw Material")
    disabled: bool = False
    country: str | None = None


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class SupplierSource(Protocol):
    """Anything that produces `Supplier` instances. The mapper calls each
    method as needed; implementations cache internally as they see fit.

    Method vocabulary intentionally separates the two lookup directions:
      * `get_by_id` — exact match on the ERPNext doc ID (`name` field).
        Unique. Use when you already know the ID.
      * `get_by_name` — case-insensitive exact match on `supplier_name`.
        Not unique in principle (duplicate display names exist); the
        implementation picks the first match. Use when resolving a Tally
        vendor name.
    """

    def get_all_suppliers(self) -> list[Supplier]: ...
    def get_by_id(self, supplier_id: str) -> Supplier | None: ...
    def get_by_name(self, supplier_name: str) -> Supplier | None: ...


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip() in ("1", "True", "true", "yes", "Yes")


def _pick(row: dict[str, Any], *keys: str) -> str:
    """First non-empty string value among the given keys. Returns "" if
    none match — lets us tolerate minor column-name variations across
    ERPNext-export schemas."""
    for k in keys:
        if k in row:
            v = row[k]
            if v is None:
                continue
            s = str(v).strip()
            if s:
                return s
    return ""


def _supplier_from_dict(row: dict[str, Any]) -> Supplier | None:
    """Build a Supplier from a CSV-row dict. Returns None for rows
    without a `name` (blank / malformed)."""
    name = _pick(row, "name", "Name", "ID")
    if not name:
        return None
    return Supplier(
        name=name,
        supplier_name=_pick(row, "supplier_name", "Supplier Name") or name,
        supplier_group=_pick(row, "supplier_group", "Supplier Group"),
        disabled=_truthy(row.get("disabled") or row.get("Disabled")),
        country=(_pick(row, "country", "Country") or None),
    )


def _build_indices(
    suppliers: list[Supplier],
) -> tuple[dict[str, Supplier], dict[str, Supplier]]:
    """Return (by_id, by_name_lowercase) lookup dicts. For duplicate
    display names, the FIRST supplier in insertion order wins — matches
    Work-Item-6 reviewer expectations that the earliest seeded record
    is canonical."""
    by_id: dict[str, Supplier] = {}
    by_name: dict[str, Supplier] = {}
    for s in suppliers:
        if s.name and s.name not in by_id:
            by_id[s.name] = s
        key = s.supplier_name.lower()
        if key and key not in by_name:
            by_name[key] = s
    return by_id, by_name


# ---------------------------------------------------------------------------
# Concrete implementations
# ---------------------------------------------------------------------------


class CsvFileSupplierSource:
    """Loads suppliers from an ERPNext Supplier-export CSV.

    Expected columns (case-sensitive match, with common aliases):
      * `name` / `Name` / `ID`            — ERPNext doc ID (primary key)
      * `supplier_name` / `Supplier Name` — display name
      * `supplier_group` / `Supplier Group`
      * `disabled` / `Disabled`           — optional, truthy parsed
      * `country` / `Country`             — optional

    Extra columns are ignored. Rows with blank `name` are skipped
    silently.
    """

    def __init__(self, csv_path: str | Path):
        path = Path(csv_path)
        if not path.exists():
            raise FileNotFoundError(f"Supplier CSV not found: {path}")
        suppliers: list[Supplier] = []
        with path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                s = _supplier_from_dict(row)
                if s is not None:
                    suppliers.append(s)
        self._suppliers: list[Supplier] = suppliers
        self._by_id, self._by_name = _build_indices(suppliers)

    def get_all_suppliers(self) -> list[Supplier]:
        return list(self._suppliers)

    def get_by_id(self, supplier_id: str) -> Supplier | None:
        return self._by_id.get(supplier_id)

    def get_by_name(self, supplier_name: str) -> Supplier | None:
        return self._by_name.get(supplier_name.lower())


class InMemorySupplierSource:
    """List-backed supplier source. For unit tests."""

    def __init__(self, suppliers: list[Supplier]):
        self._suppliers: list[Supplier] = list(suppliers)
        self._by_id, self._by_name = _build_indices(self._suppliers)

    def get_all_suppliers(self) -> list[Supplier]:
        return list(self._suppliers)

    def get_by_id(self, supplier_id: str) -> Supplier | None:
        return self._by_id.get(supplier_id)

    def get_by_name(self, supplier_name: str) -> Supplier | None:
        return self._by_name.get(supplier_name.lower())


class FrappeSupplierSource:
    """Placeholder. Work Item 6 will implement this by reading the
    `Supplier` DocType via `frappe.get_all` / `frappe.get_doc` on
    erp.jewonline.in. Until then, use `CsvFileSupplierSource` against
    an exported CSV."""

    def get_all_suppliers(self) -> list[Supplier]:
        raise NotImplementedError(
            "FrappeSupplierSource is a Work Item 6 stub. Use "
            "CsvFileSupplierSource against "
            "rgi_migration/tests/fixtures/erpnext_suppliers_real.csv "
            "until Work Item 6 lands the live Frappe read path."
        )

    def get_by_id(self, supplier_id: str) -> Supplier | None:
        raise NotImplementedError(
            "FrappeSupplierSource.get_by_id is a Work Item 6 stub."
        )

    def get_by_name(self, supplier_name: str) -> Supplier | None:
        raise NotImplementedError(
            "FrappeSupplierSource.get_by_name is a Work Item 6 stub."
        )
