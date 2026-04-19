"""Scaffold-only tests for SupplierSource — Work Item 6 pre-work.

Validates that CsvFileSupplierSource and InMemorySupplierSource produce
the right shapes and lookup behavior on synthetic hand-built data.

Does NOT test against the real erpnext_suppliers_real.csv — that fixture
is pending Aditya's export. Work-Item-6 tests will extend this file with
real-CSV cases once available.
"""

from __future__ import annotations

import csv

import pytest

from rgi_migration.mapper.supplier_source import (
    CsvFileSupplierSource,
    InMemorySupplierSource,
    Supplier,
)


SYNTHETIC_ROWS = [
    {"name": "SUP-0001", "supplier_name": "Acme Stationers Pvt Ltd",
     "supplier_group": "Stationary"},
    {"name": "SUP-0002", "supplier_name": "Bengal Electric Supplies",
     "supplier_group": "Electrical"},
    {"name": "SUP-0003", "supplier_name": "Chennai Print House",
     "supplier_group": "Stationary"},
    {"name": "SUP-0004", "supplier_name": "Delhi Dairy Distributors",
     "supplier_group": "Food"},
    {"name": "SUP-0005", "supplier_name": "Eastern IT Services",
     "supplier_group": "Services"},
]


@pytest.fixture
def synthetic_csv(tmp_path):
    path = tmp_path / "suppliers.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["name", "supplier_name", "supplier_group"]
        )
        writer.writeheader()
        for row in SYNTHETIC_ROWS:
            writer.writerow(row)
    return path


# ---------------------------------------------------------------------------
# CsvFileSupplierSource
# ---------------------------------------------------------------------------


def test_csv_loads_all_rows(synthetic_csv):
    src = CsvFileSupplierSource(synthetic_csv)
    assert len(src.get_all_suppliers()) == len(SYNTHETIC_ROWS)


def test_csv_supplier_objects_round_trip(synthetic_csv):
    src = CsvFileSupplierSource(synthetic_csv)
    suppliers = src.get_all_suppliers()
    # Every returned object is a Supplier dataclass, not a raw dict
    assert all(isinstance(s, Supplier) for s in suppliers)
    # Fields land correctly
    ids = [s.name for s in suppliers]
    assert ids == [r["name"] for r in SYNTHETIC_ROWS]
    names = [s.supplier_name for s in suppliers]
    assert names == [r["supplier_name"] for r in SYNTHETIC_ROWS]


def test_csv_get_by_id_exact(synthetic_csv):
    src = CsvFileSupplierSource(synthetic_csv)
    s = src.get_by_id("SUP-0003")
    assert s is not None
    assert s.supplier_name == "Chennai Print House"
    assert s.supplier_group == "Stationary"


def test_csv_get_by_id_missing_returns_none(synthetic_csv):
    src = CsvFileSupplierSource(synthetic_csv)
    assert src.get_by_id("SUP-9999") is None


def test_csv_get_by_name_case_insensitive(synthetic_csv):
    src = CsvFileSupplierSource(synthetic_csv)
    # All three casings should resolve to the same supplier
    lower = src.get_by_name("acme stationers pvt ltd")
    upper = src.get_by_name("ACME STATIONERS PVT LTD")
    exact = src.get_by_name("Acme Stationers Pvt Ltd")
    assert lower is not None
    assert lower.name == upper.name == exact.name == "SUP-0001"


def test_csv_get_by_name_missing_returns_none(synthetic_csv):
    src = CsvFileSupplierSource(synthetic_csv)
    assert src.get_by_name("No Such Vendor") is None


def test_csv_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        CsvFileSupplierSource(tmp_path / "not_there.csv")


def test_csv_blank_name_rows_skipped(tmp_path):
    path = tmp_path / "has_blanks.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["name", "supplier_name", "supplier_group"]
        )
        writer.writeheader()
        writer.writerow({"name": "SUP-0001", "supplier_name": "Real",
                         "supplier_group": "X"})
        writer.writerow({"name": "", "supplier_name": "Blank ID",
                         "supplier_group": "X"})  # must be skipped
        writer.writerow({"name": "SUP-0002", "supplier_name": "Also Real",
                         "supplier_group": "Y"})
    src = CsvFileSupplierSource(path)
    assert len(src.get_all_suppliers()) == 2


# ---------------------------------------------------------------------------
# InMemorySupplierSource
# ---------------------------------------------------------------------------


def test_inmemory_count_and_lookups():
    src = InMemorySupplierSource([
        Supplier(name="S1", supplier_name="Test Supplier One",
                 supplier_group="A"),
        Supplier(name="S2", supplier_name="Test Supplier Two",
                 supplier_group="B"),
    ])
    assert len(src.get_all_suppliers()) == 2
    assert src.get_by_id("S1").supplier_name == "Test Supplier One"
    assert src.get_by_name("test supplier two").name == "S2"
    assert src.get_by_id("S3") is None
    assert src.get_by_name("nope") is None


def test_inmemory_duplicate_display_names_first_wins():
    # Two different IDs, same display name. First-seen wins the by-name
    # lookup — matches the stated behavior in supplier_source.py docstring.
    src = InMemorySupplierSource([
        Supplier(name="S1", supplier_name="Dup Name Ltd", supplier_group="A"),
        Supplier(name="S2", supplier_name="Dup Name Ltd", supplier_group="B"),
    ])
    assert src.get_by_name("Dup Name Ltd").name == "S1"
    # Both still resolvable by ID
    assert src.get_by_id("S1").supplier_group == "A"
    assert src.get_by_id("S2").supplier_group == "B"
