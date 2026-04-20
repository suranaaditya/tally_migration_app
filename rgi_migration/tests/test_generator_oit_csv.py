"""Unit tests for generator #2 — OIT CSV.

Covers the pure core (``build_oit_rows`` + ``format_csv``) without
touching Frappe. The Frappe-aware entry point ``generate_oit_csv`` is
exercised via the end-of-Week-3 integration test, not here.

13 scenarios per the approved prose design + one Order A test for
multiple supplier issues at once.
"""

from __future__ import annotations

import pytest

from rgi_migration.generators.oit_csv import (
    OITGenerationError,
    OITRow,
    SupplierInfo,
    build_oit_rows,
    format_csv,
)
from rgi_migration.mapper.mapper import MappedDecision


# ---------------------------------------------------------------------------
# Builders — minimal, explicit
# ---------------------------------------------------------------------------


def _decision(
    tally_name: str,
    *,
    tier: str,
    tally_id: str | None = "1",
    proposed_supplier: str | None = None,
    opening_dr: float = 0.0,
    opening_cr: float = 0.0,
    tally_root_type: str = "Liability",
) -> MappedDecision:
    return MappedDecision(
        tally_name=tally_name,
        tally_id=tally_id,
        tally_root_type=tally_root_type,
        opening_dr=opening_dr,
        opening_cr=opening_cr,
        tier=tier,
        proposed_account=None,
        review_action="Pending",
        matched_rule=None,
        confidence=1.0,
        proposed_supplier=proposed_supplier,
        supplier_match_score=1.0 if proposed_supplier else 0.0,
    )


def _sup(name: str, display: str | None = None, disabled: bool = False) -> SupplierInfo:
    return SupplierInfo(
        name=name,
        supplier_name=display or name,
        disabled=disabled,
    )


_BUILD_KW = dict(
    abbr="CACSPU",
    posting_date="2026-04-01",
    session_name="TMS-CACSPU-2026-2027-00001",
)


# ---------------------------------------------------------------------------
# Scenario 1 — pure net-Cr vendor
# ---------------------------------------------------------------------------


def test_pure_net_cr_vendor_produces_one_row() -> None:
    decisions = [
        _decision("ACME Vendor", tier="tier1_supplier_exact",
                  proposed_supplier="ACME", opening_cr=150000.0),
    ]
    supplier_index = {"ACME": _sup("ACME", "ACME Pvt Ltd")}
    rows = build_oit_rows(decisions=decisions, supplier_index=supplier_index, **_BUILD_KW)
    assert len(rows) == 1
    r = rows[0]
    assert r.invoice_number == "OB-CACSPU-OIT-0001"
    assert r.party_type == "Supplier"
    assert r.party_id == "ACME"
    assert r.party_name == "ACME Pvt Ltd"
    assert r.temporary_opening_account == "Temporary Opening - CACSPU"
    assert r.posting_date == "2026-04-01"
    assert r.due_date == "2026-04-01"
    assert r.supplier_invoice_date == "2026-04-01"
    assert r.item_name == "Opening Invoice Item"
    assert r.outstanding_amount == 150000.0
    assert r.quantity == "1"
    assert r.cost_center == ""


# ---------------------------------------------------------------------------
# Scenario 2 — two ledgers, same supplier, net positive → summed
# ---------------------------------------------------------------------------


def test_two_ledgers_same_supplier_net_positive_sums_to_one_row() -> None:
    decisions = [
        _decision("ACME - Invoice 1", tier="tier1_supplier_exact",
                  proposed_supplier="ACME", tally_id="A1", opening_cr=100000.0),
        _decision("ACME - Invoice 2", tier="tier1_supplier_fuzzy",
                  proposed_supplier="ACME", tally_id="A2", opening_cr=75000.0),
    ]
    supplier_index = {"ACME": _sup("ACME", "ACME Pvt Ltd")}
    rows = build_oit_rows(decisions=decisions, supplier_index=supplier_index, **_BUILD_KW)
    assert len(rows) == 1
    assert rows[0].outstanding_amount == 175000.0


# ---------------------------------------------------------------------------
# Scenario 3 — two ledgers same supplier, net zero → skipped, no refusal
# ---------------------------------------------------------------------------


def test_two_ledgers_same_supplier_net_zero_skipped() -> None:
    decisions = [
        _decision("ACME Cr", tier="tier1_supplier_exact",
                  proposed_supplier="ACME", tally_id="A1", opening_cr=100.0),
        _decision("ACME Dr", tier="tier1_supplier_exact",
                  proposed_supplier="ACME", tally_id="A2", opening_dr=100.0),
    ]
    supplier_index = {"ACME": _sup("ACME")}
    # Must not raise — skipped silently, not a refusal condition.
    rows = build_oit_rows(decisions=decisions, supplier_index=supplier_index, **_BUILD_KW)
    assert rows == []


# ---------------------------------------------------------------------------
# Scenario 4 — two ledgers same supplier, net negative → skipped (gen #3)
# ---------------------------------------------------------------------------


def test_two_ledgers_same_supplier_net_negative_skipped() -> None:
    decisions = [
        _decision("ACME Cr", tier="tier1_supplier_exact",
                  proposed_supplier="ACME", tally_id="A1", opening_cr=50.0),
        _decision("ACME Dr (advance)", tier="tier1_supplier_exact",
                  proposed_supplier="ACME", tally_id="A2", opening_dr=200.0),
    ]
    supplier_index = {"ACME": _sup("ACME")}
    rows = build_oit_rows(decisions=decisions, supplier_index=supplier_index, **_BUILD_KW)
    # Net is -150 Cr; goes to generator #3 (party-advance JE), not OIT.
    assert rows == []


# ---------------------------------------------------------------------------
# Scenario 5 — pending_supplier_creation → refuse with count
# ---------------------------------------------------------------------------


def test_pending_supplier_creation_refuses_with_count() -> None:
    decisions = [
        _decision("New Vendor A", tier="pending_supplier_creation",
                  tally_id="V1", opening_cr=10000.0),
        _decision("New Vendor B", tier="pending_supplier_creation",
                  tally_id="V2", opening_cr=5000.0),
        _decision("ACME Resolved", tier="tier1_supplier_exact",
                  proposed_supplier="ACME", tally_id="A1", opening_cr=1000.0),
    ]
    supplier_index = {"ACME": _sup("ACME")}
    with pytest.raises(OITGenerationError) as exc:
        build_oit_rows(decisions=decisions, supplier_index=supplier_index, **_BUILD_KW)
    msg = str(exc.value)
    assert "2 suppliers pending creation" in msg
    assert "Supplier Creation Request" in msg


# ---------------------------------------------------------------------------
# Scenario 6 — resolved supplier missing from master → refuse with name
# ---------------------------------------------------------------------------


def test_missing_supplier_refuses_with_specific_name() -> None:
    decisions = [
        _decision("Nilesh Traders", tier="tier1_supplier_exact",
                  proposed_supplier="Nilesh Traders",
                  tally_id="V1", opening_cr=5000.0),
    ]
    supplier_index: dict[str, SupplierInfo] = {}  # supplier deleted from master
    with pytest.raises(OITGenerationError) as exc:
        build_oit_rows(decisions=decisions, supplier_index=supplier_index, **_BUILD_KW)
    msg = str(exc.value)
    assert "Nilesh Traders" in msg
    assert "deleted from Supplier master" in msg


# ---------------------------------------------------------------------------
# Scenario 7 — disabled supplier → refuse with name
# ---------------------------------------------------------------------------


def test_disabled_supplier_refuses_with_specific_name() -> None:
    decisions = [
        _decision("Abhi Tria", tier="tier1_supplier_exact",
                  proposed_supplier="Abhi Tria",
                  tally_id="V1", opening_cr=1000.0),
    ]
    supplier_index = {"Abhi Tria": _sup("Abhi Tria", disabled=True)}
    with pytest.raises(OITGenerationError) as exc:
        build_oit_rows(decisions=decisions, supplier_index=supplier_index, **_BUILD_KW)
    msg = str(exc.value)
    assert "Abhi Tria" in msg
    assert "disabled" in msg


# ---------------------------------------------------------------------------
# Scenario 8 — single ledger with both Dr AND Cr set → net correctly applied
# ---------------------------------------------------------------------------


def test_both_sided_single_ledger_uses_net() -> None:
    decisions = [
        _decision("ACME (both-sided)", tier="tier1_supplier_exact",
                  proposed_supplier="ACME", tally_id="A1",
                  opening_dr=30.0, opening_cr=100.0),
    ]
    supplier_index = {"ACME": _sup("ACME")}
    rows = build_oit_rows(decisions=decisions, supplier_index=supplier_index, **_BUILD_KW)
    assert len(rows) == 1
    assert rows[0].outstanding_amount == 70.0  # 100 Cr − 30 Dr


# ---------------------------------------------------------------------------
# Scenario 9 — multiple suppliers → sequential invoice numbers
# ---------------------------------------------------------------------------


def test_multiple_suppliers_produce_sequential_invoice_numbers() -> None:
    decisions = [
        _decision("Vendor A", tier="tier1_supplier_exact",
                  proposed_supplier="Alpha", tally_id="A",
                  opening_cr=100.0),
        _decision("Vendor B", tier="tier1_supplier_exact",
                  proposed_supplier="Bravo", tally_id="B",
                  opening_cr=200.0),
        _decision("Vendor C", tier="tier1_supplier_exact",
                  proposed_supplier="Charlie", tally_id="C",
                  opening_cr=300.0),
    ]
    supplier_index = {
        "Alpha": _sup("Alpha"),
        "Bravo": _sup("Bravo"),
        "Charlie": _sup("Charlie"),
    }
    rows = build_oit_rows(decisions=decisions, supplier_index=supplier_index, **_BUILD_KW)
    assert [r.invoice_number for r in rows] == [
        "OB-CACSPU-OIT-0001",
        "OB-CACSPU-OIT-0002",
        "OB-CACSPU-OIT-0003",
    ]
    # Deterministic ordering: alphabetical by supplier_id
    assert [r.party_id for r in rows] == ["Alpha", "Bravo", "Charlie"]


# ---------------------------------------------------------------------------
# Scenario 10 — UTF-8 supplier name preserved through CSV
# ---------------------------------------------------------------------------


def test_utf8_supplier_name_preserved_in_csv() -> None:
    decisions = [
        _decision("Café Vendor", tier="tier1_supplier_exact",
                  proposed_supplier="CAFE", tally_id="C1",
                  opening_cr=100.0),
    ]
    supplier_index = {"CAFE": _sup("CAFE", "Café Vendor — ₹ accounts")}
    rows = build_oit_rows(decisions=decisions, supplier_index=supplier_index, **_BUILD_KW)
    csv_text = format_csv(rows)
    assert "Café Vendor — ₹ accounts" in csv_text
    # Round-trip UTF-8 — encoding to bytes and back must not corrupt.
    assert csv_text.encode("utf-8").decode("utf-8") == csv_text


# ---------------------------------------------------------------------------
# Scenario 11 — supplier name with comma → RFC 4180 quoted
# ---------------------------------------------------------------------------


def test_supplier_name_with_comma_is_rfc4180_quoted() -> None:
    decisions = [
        _decision("Smith,Jones & Co", tier="tier1_supplier_exact",
                  proposed_supplier="SMITHJONES",
                  tally_id="S1", opening_cr=500.0),
    ]
    supplier_index = {"SMITHJONES": _sup("SMITHJONES", "Smith, Jones & Co.")}
    csv_text = format_csv(build_oit_rows(
        decisions=decisions, supplier_index=supplier_index, **_BUILD_KW,
    ))
    # Embedded comma must be wrapped in double quotes.
    assert '"Smith, Jones & Co."' in csv_text
    # Header must still be the 12 fixed columns, unquoted.
    header_line = csv_text.splitlines()[0]
    assert header_line == (
        "Invoice Number,Party Type,Party ID,Party Name,"
        "Temporary Opening Account,Posting Date,Due Date,"
        "Supplier Invoice Date,Item Name,Outstanding Amount,"
        "Quantity,Cost Center"
    )


def test_supplier_name_with_embedded_quote_is_escaped() -> None:
    decisions = [
        _decision("Weird Vendor", tier="tier1_supplier_exact",
                  proposed_supplier="WEIRD", tally_id="W1",
                  opening_cr=50.0),
    ]
    supplier_index = {"WEIRD": _sup("WEIRD", 'Ram "Happy" Traders')}
    csv_text = format_csv(build_oit_rows(
        decisions=decisions, supplier_index=supplier_index, **_BUILD_KW,
    ))
    # Embedded " becomes "" inside a quoted field per RFC 4180.
    assert '"Ram ""Happy"" Traders"' in csv_text


# ---------------------------------------------------------------------------
# Scenario 12 — format_csv determinism (substitute for full-Frappe idempotency)
# ---------------------------------------------------------------------------


def test_format_csv_is_deterministic() -> None:
    """Same rows in = same CSV out. Frappe-side idempotency (delete-and-
    regenerate) layers on top of this; pure-core determinism is verified
    here. Full idempotency path is exercised by the integration smoke."""
    rows = [
        OITRow(
            invoice_number="OB-CACSPU-OIT-0001",
            party_type="Supplier",
            party_id="ACME",
            party_name="ACME Pvt Ltd",
            temporary_opening_account="Temporary Opening - CACSPU",
            posting_date="2026-04-01",
            due_date="2026-04-01",
            supplier_invoice_date="2026-04-01",
            item_name="Opening Invoice Item",
            outstanding_amount=12345.67,
            quantity="1",
            cost_center="",
        ),
    ]
    out1 = format_csv(rows)
    out2 = format_csv(rows)
    assert out1 == out2
    # Row body has 12 comma-separated cells.
    body_line = out1.splitlines()[1]
    # Count top-level commas (outside quotes) by splitting via csv.
    import csv as _csv
    import io as _io
    fields = next(_csv.reader(_io.StringIO(body_line)))
    assert len(fields) == 12
    # Amount formatted to 2 decimals, no currency symbol, no thousands separator.
    assert fields[9] == "12345.67"


# ---------------------------------------------------------------------------
# Scenario 13 — Order A — multiple supplier issues surfaced together
# ---------------------------------------------------------------------------


def test_multiple_supplier_issues_surface_in_one_refusal() -> None:
    """2 missing + 1 disabled → single OITGenerationError naming all 3."""
    decisions = [
        _decision("Nilesh Traders Ledger", tier="tier1_supplier_exact",
                  proposed_supplier="Nilesh Traders",
                  tally_id="N1", opening_cr=10.0),
        _decision("Abhi Tria Ledger", tier="tier1_supplier_exact",
                  proposed_supplier="Abhi Tria",
                  tally_id="A1", opening_cr=20.0),
        _decision("Gulab Hardware Ledger", tier="tier1_supplier_exact",
                  proposed_supplier="Gulab Hardware",
                  tally_id="G1", opening_cr=30.0),
        _decision("ACME Healthy", tier="tier1_supplier_exact",
                  proposed_supplier="ACME",
                  tally_id="OK1", opening_cr=100.0),
    ]
    supplier_index = {
        # Nilesh Traders — missing (deleted)
        # Gulab Hardware — missing (deleted)
        "Abhi Tria": _sup("Abhi Tria", disabled=True),
        "ACME": _sup("ACME"),
    }
    with pytest.raises(OITGenerationError) as exc:
        build_oit_rows(
            decisions=decisions, supplier_index=supplier_index, **_BUILD_KW,
        )
    msg = str(exc.value)
    assert "3 supplier resolution issues" in msg
    assert "Nilesh Traders: deleted from Supplier master" in msg
    assert "Abhi Tria: disabled" in msg
    assert "Gulab Hardware: deleted from Supplier master" in msg
    assert "Re-run mapping or restore/enable suppliers" in msg


def test_combined_pending_and_supplier_issues_surface_together() -> None:
    """Pending-creation AND supplier-master issues both surface in one
    refusal message (Order A spirit extended across both preflight
    buckets)."""
    decisions = [
        _decision("Needs Creation", tier="pending_supplier_creation",
                  tally_id="P1", opening_cr=100.0),
        _decision("Missing Vendor", tier="tier1_supplier_exact",
                  proposed_supplier="GoneSupplier",
                  tally_id="M1", opening_cr=50.0),
    ]
    supplier_index: dict[str, SupplierInfo] = {}
    with pytest.raises(OITGenerationError) as exc:
        build_oit_rows(decisions=decisions, supplier_index=supplier_index, **_BUILD_KW)
    msg = str(exc.value)
    assert "1 suppliers pending creation" in msg
    assert "GoneSupplier: deleted from Supplier master" in msg


# ---------------------------------------------------------------------------
# Supplementary — non-supplier decisions are silently ignored
# ---------------------------------------------------------------------------


def test_non_supplier_decisions_ignored() -> None:
    """tier1_exact, excluded_pnl, excluded_zero_balance etc. must not
    influence OIT row production — they're handled by other generators
    or excluded entirely."""
    decisions = [
        _decision("Cash", tier="tier1_exact", tally_id="1",
                  opening_dr=5000.0, tally_root_type="Asset"),
        _decision("Sales", tier="excluded_pnl", tally_id="P1",
                  opening_cr=1000.0, tally_root_type="Income"),
        _decision("Zero Vendor", tier="excluded_zero_balance",
                  tally_id="Z1", tally_root_type="Liability"),
        _decision("ACME Real", tier="tier1_supplier_exact",
                  proposed_supplier="ACME", tally_id="V1",
                  opening_cr=100.0),
    ]
    supplier_index = {"ACME": _sup("ACME")}
    rows = build_oit_rows(decisions=decisions, supplier_index=supplier_index, **_BUILD_KW)
    assert len(rows) == 1
    assert rows[0].party_id == "ACME"
    assert rows[0].outstanding_amount == 100.0
