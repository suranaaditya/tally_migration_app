"""Unit tests for generator #3 — Party-wise Dr JE for vendor advances.

Exactly 11 scenarios per the authorized prose-design scope:

 1. Single ledger net-Dr → one line, correct amount, is_advance="Yes"
 2. Two ledgers same supplier net-Dr → summed, single line
 3. Mixed Cr+Dr same supplier netting to Dr → correct net
 4. Net-Cr supplier → zero lines (goes to gen #2)
 5. Net-zero supplier → zero lines, no refusal
 6. Balancer math: Dr total == Cr total (Temp Opening absorbs whole Dr sum)
 7. Refusal — pending_supplier_creation > 0 with count in message
 8. Refusal — supplier deleted from master, specific name in message
 9. Refusal — supplier disabled, specific name in message
10. Refusal — Order A multi-problem (2 missing + 1 disabled → single refusal listing all 3)
11. is_advance="Yes" set on every supplier line; never on Temp Opening balancer

No supplementary tests added without explicit authorization.
"""

from __future__ import annotations

import pytest

from rgi_migration.generators.advance_je import (
    AdvanceJEGenerationError,
    SupplierInfo,
    build_advance_je_payload,
)
from rgi_migration.mapper.mapper import MappedDecision


def _decision(
    tally_name: str,
    *,
    tier: str,
    tally_id: str | None = "1",
    proposed_supplier: str | None = None,
    opening_dr: float = 0.0,
    opening_cr: float = 0.0,
    matched_rule: str | None = None,
) -> MappedDecision:
    return MappedDecision(
        tally_name=tally_name,
        tally_id=tally_id,
        tally_root_type="Liability",
        opening_dr=opening_dr,
        opening_cr=opening_cr,
        tier=tier,
        proposed_account=None,
        review_action="Pending",
        matched_rule=matched_rule,
        confidence=1.0,
        proposed_supplier=proposed_supplier,
        supplier_match_score=1.0 if proposed_supplier else 0.0,
    )


def _sup(name: str, display: str | None = None, disabled: bool = False) -> SupplierInfo:
    return SupplierInfo(
        name=name, supplier_name=display or name, disabled=disabled,
    )


_BUILD_KW = dict(
    abbr="CACSPU",
    erpnext_company="GHR CACS Pune",
    full_name="GH Raisoni College of Arts Commerce & Science, Pune",
    fiscal_year="2026-2027",
    posting_date="2026-04-01",
    session_name="TMS-CACSPU-2026-2027-00001",
    source_sha256="abc123def456789",
    reference_id="OB-CACSPU-2026-02",
    timestamp_iso="2026-04-20T12:00:00Z",
)


# --- 1. Single net-Dr ledger → one line ------------------------------------


def test_single_net_dr_ledger_produces_one_line() -> None:
    decisions = [
        _decision("Atharva Tyres Advance", tier="tier1_supplier_exact",
                  proposed_supplier="Atharva Tyres",
                  tally_id="A1", opening_dr=25700.0),
    ]
    supplier_index = {
        "Atharva Tyres": _sup("Atharva Tyres", "Atharva Tyres Pvt Ltd"),
    }
    payload = build_advance_je_payload(
        decisions=decisions, supplier_index=supplier_index, **_BUILD_KW,
    )
    # One supplier line + one balancer
    assert payload.supplier_count == 1
    assert payload.row_count == 2
    supplier_row = payload.rows[0]
    assert supplier_row["account"] == "Sundry Creditors - CACSPU"
    assert supplier_row["party_type"] == "Supplier"
    assert supplier_row["party"] == "Atharva Tyres"
    assert supplier_row["is_advance"] == "Yes"
    assert supplier_row["debit_in_account_currency"] == 25700.0
    assert supplier_row["credit_in_account_currency"] == 0.0


# --- 2. Two ledgers same supplier net-Dr → summed --------------------------


def test_two_ledgers_same_supplier_net_dr_summed_on_one_line() -> None:
    decisions = [
        _decision("Go Digital Advance 1", tier="tier1_supplier_exact",
                  proposed_supplier="Go Digital",
                  tally_id="G1", opening_dr=100000.0),
        _decision("Go Digital Advance 2", tier="tier1_supplier_fuzzy",
                  proposed_supplier="Go Digital",
                  tally_id="G2", opening_dr=94911.73),
    ]
    supplier_index = {"Go Digital": _sup("Go Digital")}
    payload = build_advance_je_payload(
        decisions=decisions, supplier_index=supplier_index, **_BUILD_KW,
    )
    assert payload.supplier_count == 1
    assert payload.rows[0]["debit_in_account_currency"] == 194911.73


# --- 3. Mixed Cr + Dr netting to Dr → net applied --------------------------


def test_mixed_cr_and_dr_netting_to_dr_uses_net_amount() -> None:
    decisions = [
        _decision("Vendor Cr row", tier="tier1_supplier_exact",
                  proposed_supplier="Mixed Vendor",
                  tally_id="M1", opening_cr=5000.0),
        _decision("Vendor Dr row (bigger advance)", tier="tier1_supplier_exact",
                  proposed_supplier="Mixed Vendor",
                  tally_id="M2", opening_dr=30000.0),
    ]
    supplier_index = {"Mixed Vendor": _sup("Mixed Vendor")}
    payload = build_advance_je_payload(
        decisions=decisions, supplier_index=supplier_index, **_BUILD_KW,
    )
    assert payload.supplier_count == 1
    # 30000 Dr − 5000 Cr = 25000 net-Dr
    assert payload.rows[0]["debit_in_account_currency"] == 25000.0


# --- 4. Net-Cr supplier → zero lines (belongs to gen #2) -------------------


def test_net_cr_supplier_produces_zero_lines() -> None:
    decisions = [
        _decision("Payable vendor", tier="tier1_supplier_exact",
                  proposed_supplier="Payable Vendor",
                  tally_id="P1", opening_cr=50000.0),
    ]
    supplier_index = {"Payable Vendor": _sup("Payable Vendor")}
    payload = build_advance_je_payload(
        decisions=decisions, supplier_index=supplier_index, **_BUILD_KW,
    )
    # Item 8.5 Stage 3 Phase C bug fix: empty-payload guard.
    # Previously this returned a payload with supplier_count=0 and a
    # 0/0 balancer row — but Frappe rejects 0/0 rows on insert. The
    # build function now returns None and the caller skips JE creation.
    # See docs/mapper_design_notes.md §5 (Generator empty-payload guard).
    assert payload is None


# --- 5. Net-zero supplier → zero lines, no refusal -------------------------


def test_net_zero_supplier_silently_skipped_no_refusal() -> None:
    decisions = [
        _decision("Balanced Vendor Cr", tier="tier1_supplier_exact",
                  proposed_supplier="Balanced Vendor",
                  tally_id="B1", opening_cr=1000.0),
        _decision("Balanced Vendor Dr", tier="tier1_supplier_exact",
                  proposed_supplier="Balanced Vendor",
                  tally_id="B2", opening_dr=1000.0),
    ]
    supplier_index = {"Balanced Vendor": _sup("Balanced Vendor")}
    # Must not raise — net-zero is silently skipped, not a refusal.
    payload = build_advance_je_payload(
        decisions=decisions, supplier_index=supplier_index, **_BUILD_KW,
    )
    # Same Stage 3 fix applies: zero net-Dr lines → returns None
    # (skip JE creation rather than emit a 0/0 balancer Frappe rejects).
    assert payload is None


# --- 6. Balancer math: Dr total == Cr total --------------------------------


def test_balancer_makes_total_dr_equal_total_cr() -> None:
    decisions = [
        _decision("A", tier="tier1_supplier_exact",
                  proposed_supplier="Alpha", tally_id="A1",
                  opening_dr=10000.0),
        _decision("B", tier="tier1_supplier_exact",
                  proposed_supplier="Bravo", tally_id="B1",
                  opening_dr=25000.0),
        _decision("C", tier="tier1_supplier_exact",
                  proposed_supplier="Charlie", tally_id="C1",
                  opening_dr=5000.0),
    ]
    supplier_index = {
        "Alpha": _sup("Alpha"),
        "Bravo": _sup("Bravo"),
        "Charlie": _sup("Charlie"),
    }
    payload = build_advance_je_payload(
        decisions=decisions, supplier_index=supplier_index, **_BUILD_KW,
    )
    total_dr = sum(r["debit_in_account_currency"] for r in payload.rows)
    total_cr = sum(r["credit_in_account_currency"] for r in payload.rows)
    assert total_dr == total_cr == 40000.0
    # Balancer row is the last row and posts entire sum on Cr side of Temp Opening.
    balancer = payload.rows[-1]
    assert balancer["account"] == "Temporary Opening - CACSPU"
    assert balancer["credit_in_account_currency"] == 40000.0
    assert balancer["debit_in_account_currency"] == 0.0
    assert payload.temp_opening_amount == 40000.0


# --- 7. Refuse — pending_supplier_creation ---------------------------------


def test_pending_supplier_creation_refuses_with_count() -> None:
    decisions = [
        _decision("New Vendor A", tier="pending_supplier_creation",
                  tally_id="V1", opening_dr=1000.0),
        _decision("New Vendor B", tier="pending_supplier_creation",
                  tally_id="V2", opening_dr=2000.0),
        _decision("ACME Advance", tier="tier1_supplier_exact",
                  proposed_supplier="ACME", tally_id="A1",
                  opening_dr=500.0),
    ]
    supplier_index = {"ACME": _sup("ACME")}
    with pytest.raises(AdvanceJEGenerationError) as exc:
        build_advance_je_payload(
            decisions=decisions, supplier_index=supplier_index, **_BUILD_KW,
        )
    msg = str(exc.value)
    assert "2 suppliers pending creation" in msg
    assert "Supplier Creation Request" in msg


# --- 8. Refuse — supplier deleted from master ------------------------------


def test_missing_supplier_refuses_with_specific_name() -> None:
    decisions = [
        _decision("Nilesh Advance", tier="tier1_supplier_exact",
                  proposed_supplier="Nilesh Traders",
                  tally_id="N1", opening_dr=5000.0),
    ]
    supplier_index: dict[str, SupplierInfo] = {}  # supplier was deleted
    with pytest.raises(AdvanceJEGenerationError) as exc:
        build_advance_je_payload(
            decisions=decisions, supplier_index=supplier_index, **_BUILD_KW,
        )
    msg = str(exc.value)
    assert "Nilesh Traders" in msg
    assert "deleted from Supplier master" in msg


# --- 9. Refuse — supplier disabled -----------------------------------------


def test_disabled_supplier_refuses_with_specific_name() -> None:
    decisions = [
        _decision("Abhi Tria Advance", tier="tier1_supplier_exact",
                  proposed_supplier="Abhi Tria",
                  tally_id="A1", opening_dr=3000.0),
    ]
    supplier_index = {"Abhi Tria": _sup("Abhi Tria", disabled=True)}
    with pytest.raises(AdvanceJEGenerationError) as exc:
        build_advance_je_payload(
            decisions=decisions, supplier_index=supplier_index, **_BUILD_KW,
        )
    msg = str(exc.value)
    assert "Abhi Tria" in msg
    assert "disabled" in msg


# --- 10. Order A — multi-problem single refusal ----------------------------


def test_order_a_multi_problem_single_refusal_with_all_three_named() -> None:
    """2 missing + 1 disabled → one AdvanceJEGenerationError listing all 3."""
    decisions = [
        _decision("Nilesh Advance", tier="tier1_supplier_exact",
                  proposed_supplier="Nilesh Traders",
                  tally_id="N1", opening_dr=10.0),
        _decision("Abhi Advance", tier="tier1_supplier_exact",
                  proposed_supplier="Abhi Tria",
                  tally_id="A1", opening_dr=20.0),
        _decision("Gulab Advance", tier="tier1_supplier_exact",
                  proposed_supplier="Gulab Hardware",
                  tally_id="G1", opening_dr=30.0),
        _decision("ACME Healthy Advance", tier="tier1_supplier_exact",
                  proposed_supplier="ACME", tally_id="OK1",
                  opening_dr=100.0),
    ]
    supplier_index = {
        # Nilesh Traders: missing
        # Gulab Hardware: missing
        "Abhi Tria": _sup("Abhi Tria", disabled=True),
        "ACME": _sup("ACME"),
    }
    with pytest.raises(AdvanceJEGenerationError) as exc:
        build_advance_je_payload(
            decisions=decisions, supplier_index=supplier_index, **_BUILD_KW,
        )
    msg = str(exc.value)
    assert "3 supplier resolution issues" in msg
    assert "Nilesh Traders: deleted from Supplier master" in msg
    assert "Abhi Tria: disabled" in msg
    assert "Gulab Hardware: deleted from Supplier master" in msg
    assert "Re-run mapping or restore/enable suppliers" in msg


# --- 11. is_advance="Yes" on supplier lines; never on balancer -------------


def test_is_advance_yes_on_supplier_lines_never_on_balancer() -> None:
    decisions = [
        _decision("V1 Advance", tier="tier1_supplier_exact",
                  proposed_supplier="V1", tally_id="V1",
                  opening_dr=1000.0),
        _decision("V2 Advance", tier="tier1_supplier_exact",
                  proposed_supplier="V2", tally_id="V2",
                  opening_dr=2000.0),
    ]
    supplier_index = {"V1": _sup("V1"), "V2": _sup("V2")}
    payload = build_advance_je_payload(
        decisions=decisions, supplier_index=supplier_index, **_BUILD_KW,
    )
    # Supplier lines carry is_advance="Yes"
    supplier_rows = payload.rows[:-1]
    for r in supplier_rows:
        assert r["is_advance"] == "Yes"
        assert r["party_type"] == "Supplier"
        assert r["party"] in ("V1", "V2")
    # Balancer row (last row) has NO is_advance key and no party_*.
    balancer = payload.rows[-1]
    assert balancer["account"] == "Temporary Opening - CACSPU"
    assert "is_advance" not in balancer
    assert "party_type" not in balancer
    assert "party" not in balancer
