"""Unit tests for generator #1 — Main Opening Journal Entry.

Covers the pure core (``build_je_payload``) without touching Frappe. The
Frappe-aware ``generate_main_opening_je`` entry point is exercised via the
full-file smoke test on the bench, not here.

Each test isolates one decision point so a regression surfaces as a single
failure rather than a buried assertion in a bigger scenario.
"""

from __future__ import annotations

import pytest

from rgi_migration.generators.opening_je import (
    MainJEGenerationError,
    build_je_payload,
)
from rgi_migration.mapper.mapper import MappedDecision
from rgi_migration.parsers.normalized_schema import Ledger, ParsedTallyTB


# ---------------------------------------------------------------------------
# Builders — minimal, explicit, no hidden defaults
# ---------------------------------------------------------------------------


def _ledger(
    name: str,
    *,
    root_type: str = "Asset",
    opening_dr: float = 0.0,
    opening_cr: float = 0.0,
    tally_id: str | None = "1",
    is_leaf: bool = True,
    is_student_ledger: bool = False,
    is_system_account: bool = False,
    is_pnl_closed_zero: bool = False,
    parent_chain: list[str] | None = None,
) -> Ledger:
    net = opening_cr - opening_dr
    return Ledger(
        name=name,
        tally_id=tally_id,
        parent_group=(parent_chain or ["Root"])[-1],
        parent_chain=parent_chain or ["Root"],
        root_type=root_type,
        opening_dr=opening_dr,
        opening_cr=opening_cr,
        net_amount=net,
        net_side=("Cr" if net > 0 else "Dr" if net < 0 else "Zero"),
        is_leaf=is_leaf,
        is_system_account=is_system_account,
        is_student_ledger=is_student_ledger,
        is_pnl_closed_zero=is_pnl_closed_zero,
    )


def _decision(
    tally_name: str,
    *,
    tier: str,
    proposed_account: str | None = None,
    tally_id: str | None = "1",
    matched_rule: str | None = None,
    opening_dr: float = 0.0,
    opening_cr: float = 0.0,
    tally_root_type: str = "Asset",
    review_action: str = "Pending",
) -> MappedDecision:
    return MappedDecision(
        tally_name=tally_name,
        tally_id=tally_id,
        tally_root_type=tally_root_type,
        opening_dr=opening_dr,
        opening_cr=opening_cr,
        tier=tier,
        proposed_account=proposed_account,
        review_action=review_action,
        matched_rule=matched_rule,
        confidence=1.0,
    )


def _tb(ledgers: list[Ledger]) -> ParsedTallyTB:
    total_dr = sum(l.opening_dr for l in ledgers)
    total_cr = sum(l.opening_cr for l in ledgers)
    return ParsedTallyTB(
        company_name="Test Co",
        tb_date="2026-04-01",
        source_format="xml",
        source_file="/tmp/test.xml",
        ledgers=ledgers,
        groups=[],
        total_dr=total_dr,
        total_cr=total_cr,
        is_balanced=abs(total_dr - total_cr) < 0.01,
    )


_BUILD_KW = dict(
    abbr="CACSPU",
    erpnext_company="GHR CACS Pune",
    full_name="GH Raisoni College of Arts Commerce & Science, Pune",
    fiscal_year="2026-2027",
    posting_date="2026-04-01",
    session_name="TMS-CACSPU-2026-2027-001",
    source_sha256="abc123def456789",
    reference_id="OB-CACSPU-2026-01",
    timestamp_iso="2026-04-20T12:00:00Z",
)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_simple_two_ledger_je_balances_via_temp_opening() -> None:
    """Cash Dr 100 + Share Capital Cr 100 → exactly balanced → 2 rows
    (NO balancer; Temp Opening row skipped per the 2026-04-25
    zero-balancer-skip fix). The JE is self-balancing via the two
    contribution rows. Frappe rejects 0/0 rows so the balancer must
    be omitted when the residual is zero. See
    test_exactly_balanced_contributions_skip_temp_opening_row for the
    explicit fix coverage.
    """
    ledgers = [
        _ledger("Cash", root_type="Asset", opening_dr=100.0, tally_id="1"),
        _ledger("Share Capital", root_type="Equity", opening_cr=100.0, tally_id="2"),
    ]
    decisions = [
        _decision("Cash", tier="tier1_exact",
                  proposed_account="Cash - CACSPU",
                  tally_id="1", opening_dr=100.0),
        _decision("Share Capital", tier="tier1_exact",
                  proposed_account="Share Capital - CACSPU",
                  tally_id="2", opening_cr=100.0, tally_root_type="Equity"),
    ]
    payload = build_je_payload(tb=_tb(ledgers), decisions=decisions, **_BUILD_KW)

    accounts = [r["account"] for r in payload.rows]
    assert "Cash - CACSPU" in accounts
    assert "Share Capital - CACSPU" in accounts
    # Exactly balanced → balancer skipped (Frappe rejects 0/0 rows).
    assert "Temporary Opening - CACSPU" not in accounts
    assert payload.temp_opening_amount == 0.0
    assert payload.contributions_count == 2
    assert payload.row_count == 2  # 2 contribution rows, no balancer


def test_dr_heavy_residual_absorbed_as_credit_on_temp_opening() -> None:
    """Dr 500, Cr 300 → balancer posts Cr 200 on Temp Opening; total Dr=Cr=500."""
    ledgers = [
        _ledger("Cash", root_type="Asset", opening_dr=500.0, tally_id="1"),
        _ledger("Creditor", root_type="Liability", opening_cr=300.0, tally_id="2"),
    ]
    decisions = [
        _decision("Cash", tier="tier1_exact", proposed_account="Cash - CACSPU",
                  tally_id="1", opening_dr=500.0),
        _decision("Creditor", tier="tier1_exact", proposed_account="Creditor - CACSPU",
                  tally_id="2", opening_cr=300.0, tally_root_type="Liability"),
    ]
    payload = build_je_payload(tb=_tb(ledgers), decisions=decisions, **_BUILD_KW)
    temp_row = next(r for r in payload.rows if r["account"] == "Temporary Opening - CACSPU")
    assert temp_row["credit_in_account_currency"] == 200.0
    assert temp_row["debit_in_account_currency"] == 0.0
    assert payload.temp_opening_amount == 200.0  # positive = Cr side
    total_dr = sum(r["debit_in_account_currency"] for r in payload.rows)
    total_cr = sum(r["credit_in_account_currency"] for r in payload.rows)
    assert total_dr == total_cr == 500.0


def test_cr_heavy_residual_absorbed_as_debit_on_temp_opening() -> None:
    ledgers = [
        _ledger("Cash", root_type="Asset", opening_dr=100.0, tally_id="1"),
        _ledger("Reserves", root_type="Equity", opening_cr=400.0, tally_id="2"),
    ]
    decisions = [
        _decision("Cash", tier="tier1_exact", proposed_account="Cash - CACSPU",
                  tally_id="1", opening_dr=100.0),
        _decision("Reserves", tier="tier1_exact", proposed_account="Reserves - CACSPU",
                  tally_id="2", opening_cr=400.0, tally_root_type="Equity"),
    ]
    payload = build_je_payload(tb=_tb(ledgers), decisions=decisions, **_BUILD_KW)
    temp_row = next(r for r in payload.rows if r["account"] == "Temporary Opening - CACSPU")
    assert temp_row["debit_in_account_currency"] == 300.0
    assert temp_row["credit_in_account_currency"] == 0.0
    assert payload.temp_opening_amount == -300.0  # negative = Dr side


def test_combined_amounts_merge_into_single_row_with_combined_suffix() -> None:
    """Two Tally ledgers mapped to the same ERPNext account sum on one JE row.

    Per-contributor user_remark lines both carry ``+combined``.
    """
    ledgers = [
        _ledger("Office Equipment", root_type="Asset", opening_dr=100.0, tally_id="1"),
        _ledger("Office Furniture", root_type="Asset", opening_dr=50.0, tally_id="2"),
    ]
    decisions = [
        _decision("Office Equipment", tier="tier1_rule",
                  proposed_account="Office Equipment - CACSPU",
                  tally_id="1", matched_rule="sec 4.1", opening_dr=100.0),
        _decision("Office Furniture", tier="tier1_rule",
                  proposed_account="Office Equipment - CACSPU",
                  tally_id="2", matched_rule="sec 4.1", opening_dr=50.0),
    ]
    payload = build_je_payload(tb=_tb(ledgers), decisions=decisions, **_BUILD_KW)

    office_rows = [r for r in payload.rows if r["account"] == "Office Equipment - CACSPU"]
    assert len(office_rows) == 1
    assert office_rows[0]["debit_in_account_currency"] == 150.0
    remark = office_rows[0]["user_remark"]
    assert remark.count("\n") == 1  # two contributors → two lines
    assert "Office Equipment" in remark
    assert "Office Furniture" in remark
    assert remark.count("+combined") == 2


def test_single_ledger_row_has_no_combined_suffix() -> None:
    ledgers = [_ledger("Cash", root_type="Asset", opening_dr=100.0, tally_id="1")]
    decisions = [
        _decision("Cash", tier="tier1_exact", proposed_account="Cash - CACSPU",
                  tally_id="1", opening_dr=100.0),
    ]
    payload = build_je_payload(tb=_tb(ledgers), decisions=decisions, **_BUILD_KW)
    cash_row = next(r for r in payload.rows if r["account"] == "Cash - CACSPU")
    assert "+combined" not in cash_row["user_remark"]
    # Format check: "Tally: Cash [tally_id=1] via tier1_exact/exact"
    assert cash_row["user_remark"].startswith("Tally: Cash [tally_id=1] ")
    assert "via tier1_exact/exact" in cash_row["user_remark"]


def test_both_sided_ledger_nets_to_single_side() -> None:
    """A ledger with both opening_dr and opening_cr nets to a single side.

    Production hotfix 2026-04-25 (GHRCEMPU): Frappe Journal Entry
    rejects rows with both debit AND credit populated ("You cannot
    credit and debit same account at the same time"). Tally's
    gross-Dr/gross-Cr lots on a single ledger collapse to ERPNext's
    net opening position before insertion.
    """
    ledgers = [
        _ledger("Sundry Party", root_type="Asset",
                opening_dr=150.0, opening_cr=40.0, tally_id="1"),
    ]
    decisions = [
        _decision("Sundry Party", tier="tier1_exact",
                  proposed_account="Sundry Party - CACSPU",
                  tally_id="1", opening_dr=150.0, opening_cr=40.0),
    ]
    payload = build_je_payload(tb=_tb(ledgers), decisions=decisions, **_BUILD_KW)
    party_row = next(r for r in payload.rows if r["account"] == "Sundry Party - CACSPU")
    # Net Dr 110 (150 - 40); Cr side cleared to 0 so Frappe accepts.
    assert party_row["debit_in_account_currency"] == 110.0
    assert party_row["credit_in_account_currency"] == 0.0


def test_multiple_ledgers_same_account_mixed_sides_net() -> None:
    """Multiple Tally ledgers mapped to the same ERPNext account with
    OPPOSITE sides (Dr-only and Cr-only) net to a single side. Triggered
    on GHRCEMPU when reviewer mapped two Inventory ledgers to the same
    Material Inventory account.
    """
    ledgers = [
        _ledger("Inv Lot A", root_type="Asset",
                opening_dr=500.0, tally_id="1"),
        _ledger("Inv Lot B", root_type="Asset",
                opening_cr=200.0, tally_id="2"),
    ]
    decisions = [
        _decision("Inv Lot A", tier="tier1_exact",
                  proposed_account="Material Inventory - CACSPU",
                  tally_id="1", opening_dr=500.0),
        _decision("Inv Lot B", tier="tier1_exact",
                  proposed_account="Material Inventory - CACSPU",
                  tally_id="2", opening_cr=200.0),
    ]
    payload = build_je_payload(tb=_tb(ledgers), decisions=decisions, **_BUILD_KW)
    inv_row = next(r for r in payload.rows
                   if r["account"] == "Material Inventory - CACSPU")
    # Net Dr 300 (500 - 200); Frappe-acceptable single side.
    assert inv_row["debit_in_account_currency"] == 300.0
    assert inv_row["credit_in_account_currency"] == 0.0


def test_exactly_balanced_contributions_skip_temp_opening_row() -> None:
    """Production hotfix 2026-04-25 (GHRCEMPUMCA session): when the
    contributing rows sum to a perfect Dr=Cr balance, the Temporary
    Opening balancer row would be 0/0. Frappe rejects 0/0 JE rows
    ("Both Debit and Credit values cannot be zero" — typically
    surfaces as the LAST row of the JE since the balancer is
    appended last). Skip the balancer entirely; the JE is
    self-balancing without it.
    """
    ledgers = [
        _ledger("Cash", root_type="Asset",
                opening_dr=1000.0, tally_id="1"),
        _ledger("Share Capital", root_type="Equity",
                opening_cr=1000.0, tally_id="2"),
    ]
    decisions = [
        _decision("Cash", tier="tier1_exact",
                  proposed_account="Cash - CACSPU",
                  tally_id="1", opening_dr=1000.0),
        _decision("Share Capital", tier="tier1_exact",
                  proposed_account="Share Capital - CACSPU",
                  tally_id="2", opening_cr=1000.0,
                  tally_root_type="Equity"),
    ]
    payload = build_je_payload(tb=_tb(ledgers), decisions=decisions, **_BUILD_KW)
    accounts = [r["account"] for r in payload.rows]
    # No Temporary Opening row — exact balance, no residual to absorb.
    assert "Temporary Opening - CACSPU" not in accounts, (
        "Exactly-balanced contributions must skip the 0/0 balancer row "
        "to satisfy Frappe's no-zero-rows JE validator."
    )
    # The contribution rows themselves are present and balanced
    assert "Cash - CACSPU" in accounts
    assert "Share Capital - CACSPU" in accounts
    total_dr = sum(r["debit_in_account_currency"] for r in payload.rows)
    total_cr = sum(r["credit_in_account_currency"] for r in payload.rows)
    assert total_dr == total_cr == 1000.0
    assert payload.temp_opening_amount == 0.0


def test_imbalanced_contributions_still_emit_temp_opening_row() -> None:
    """Regression: when contributions don't perfectly balance, the
    Temporary Opening row MUST appear with the residual. Confirms the
    skip-on-zero guard doesn't accidentally skip the balancer when it
    carries a non-zero residual."""
    ledgers = [
        _ledger("Cash", root_type="Asset",
                opening_dr=1500.0, tally_id="1"),
        _ledger("Share Capital", root_type="Equity",
                opening_cr=1000.0, tally_id="2"),
    ]
    decisions = [
        _decision("Cash", tier="tier1_exact",
                  proposed_account="Cash - CACSPU",
                  tally_id="1", opening_dr=1500.0),
        _decision("Share Capital", tier="tier1_exact",
                  proposed_account="Share Capital - CACSPU",
                  tally_id="2", opening_cr=1000.0,
                  tally_root_type="Equity"),
    ]
    payload = build_je_payload(tb=_tb(ledgers), decisions=decisions, **_BUILD_KW)
    accounts = [r["account"] for r in payload.rows]
    assert "Temporary Opening - CACSPU" in accounts
    temp_row = next(r for r in payload.rows
                    if r["account"] == "Temporary Opening - CACSPU")
    # Dr 1500 - Cr 1000 = 500 residual on Dr side; balancer absorbs
    # by posting 500 on Cr side of Temp Opening.
    assert temp_row["credit_in_account_currency"] == 500.0
    assert temp_row["debit_in_account_currency"] == 0.0


def test_aggregation_to_net_zero_skips_row() -> None:
    """Two contributions with offsetting balances net to zero — the
    row is skipped (Frappe rejects 0/0 rows). Pure-data audit lives
    in mapper output; ERPNext JE captures only non-trivial positions.
    """
    ledgers = [
        _ledger("Offset A", root_type="Asset",
                opening_dr=100.0, tally_id="1"),
        _ledger("Offset B", root_type="Asset",
                opening_cr=100.0, tally_id="2"),
    ]
    decisions = [
        _decision("Offset A", tier="tier1_exact",
                  proposed_account="Suspense - CACSPU",
                  tally_id="1", opening_dr=100.0),
        _decision("Offset B", tier="tier1_exact",
                  proposed_account="Suspense - CACSPU",
                  tally_id="2", opening_cr=100.0),
    ]
    payload = build_je_payload(tb=_tb(ledgers), decisions=decisions, **_BUILD_KW)
    # The 0/0 Suspense row was skipped; only Temp Opening balancer remains
    accounts = [r["account"] for r in payload.rows]
    assert "Suspense - CACSPU" not in accounts, (
        "Net-zero aggregation must skip the row to satisfy Frappe's "
        "no-zero-rows JE validator."
    )


# ---------------------------------------------------------------------------
# Refusals — each bucket blocks generation independently
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier,label", [
    ("unmapped", "ledgers unmapped"),
    ("pending_account_creation", "pending account creation"),
    ("group_refused", "refused by group validator"),
    ("anti_pattern_blocked", "anti-pattern blocked"),
])
def test_refuses_on_any_unresolved_tier(tier: str, label: str) -> None:
    ledgers = [_ledger("Mystery", root_type="Asset", opening_dr=50.0, tally_id="1")]
    decisions = [
        _decision("Mystery", tier=tier, proposed_account=None,
                  tally_id="1", opening_dr=50.0),
    ]
    with pytest.raises(MainJEGenerationError) as exc:
        build_je_payload(tb=_tb(ledgers), decisions=decisions, **_BUILD_KW)
    assert label in str(exc.value)
    assert "review UI" in str(exc.value)


def test_refusal_message_aggregates_multiple_buckets() -> None:
    ledgers = [
        _ledger("A", opening_dr=1.0, tally_id="1"),
        _ledger("B", opening_dr=1.0, tally_id="2"),
        _ledger("C", opening_dr=1.0, tally_id="3"),
    ]
    decisions = [
        _decision("A", tier="unmapped", tally_id="1", opening_dr=1.0),
        _decision("B", tier="pending_account_creation", tally_id="2", opening_dr=1.0),
        _decision("C", tier="group_refused", tally_id="3", opening_dr=1.0),
    ]
    with pytest.raises(MainJEGenerationError) as exc:
        build_je_payload(tb=_tb(ledgers), decisions=decisions, **_BUILD_KW)
    msg = str(exc.value)
    assert "1 ledgers unmapped" in msg
    assert "1 pending account creation" in msg
    assert "1 refused by group validator" in msg


def test_eligible_tier_without_proposed_account_counts_as_refusal() -> None:
    """Defensive: tier is tier1_exact/rule/pattern but proposed_account missing."""
    ledgers = [_ledger("Cash", opening_dr=100.0, tally_id="1")]
    decisions = [
        _decision("Cash", tier="tier1_exact", proposed_account=None,
                  tally_id="1", opening_dr=100.0),
    ]
    with pytest.raises(MainJEGenerationError):
        build_je_payload(tb=_tb(ledgers), decisions=decisions, **_BUILD_KW)


# ---------------------------------------------------------------------------
# Silently skipped — in TB/decisions but not JE, not refusal
# ---------------------------------------------------------------------------


def test_supplier_tiers_are_silently_skipped_not_refused() -> None:
    """Vendor party ledgers (handled by gen #2/#3) don't trigger refusal."""
    ledgers = [
        _ledger("Cash", opening_dr=100.0, tally_id="1"),
        _ledger("ACME Vendor", root_type="Liability",
                opening_cr=80.0, tally_id="V1",
                parent_chain=["Liabilities", "Sundry Creditors"]),
        _ledger("XYZ Vendor", root_type="Liability",
                opening_cr=20.0, tally_id="V2",
                parent_chain=["Liabilities", "Sundry Creditors"]),
    ]
    decisions = [
        _decision("Cash", tier="tier1_exact", proposed_account="Cash - CACSPU",
                  tally_id="1", opening_dr=100.0),
        _decision("ACME Vendor", tier="tier1_supplier_exact",
                  tally_id="V1", opening_cr=80.0, tally_root_type="Liability"),
        _decision("XYZ Vendor", tier="pending_supplier_creation",
                  tally_id="V2", opening_cr=20.0, tally_root_type="Liability"),
    ]
    # Must not raise — vendor tiers are silently skipped.
    payload = build_je_payload(tb=_tb(ledgers), decisions=decisions, **_BUILD_KW)
    accounts = [r["account"] for r in payload.rows]
    # Only Cash + Temp Opening balancer.
    assert accounts == ["Cash - CACSPU", "Temporary Opening - CACSPU"]
    assert payload.contributions_count == 1


def test_student_ledgers_never_appear_in_main_je() -> None:
    """Student ledgers are routed by the parser to tb.student_ledgers; any
    slipping into tb.ledgers with is_student_ledger=True must still be skipped."""
    ledgers = [
        _ledger("Cash", opening_dr=100.0, tally_id="1"),
        _ledger("Student Fee Outstanding", root_type="Asset",
                opening_dr=50000.0, tally_id="S1", is_student_ledger=True),
    ]
    decisions = [
        _decision("Cash", tier="tier1_exact", proposed_account="Cash - CACSPU",
                  tally_id="1", opening_dr=100.0),
        # Hypothetical stray mapping — shouldn't happen, but guard anyway.
        _decision("Student Fee Outstanding", tier="tier1_exact",
                  proposed_account="Student Fee Outstanding - CACSPU",
                  tally_id="S1", opening_dr=50000.0),
    ]
    payload = build_je_payload(tb=_tb(ledgers), decisions=decisions, **_BUILD_KW)
    accounts = [r["account"] for r in payload.rows]
    assert "Student Fee Outstanding - CACSPU" not in accounts
    assert payload.contributions_count == 1


def test_system_accounts_skipped() -> None:
    ledgers = [
        _ledger("Cash", opening_dr=100.0, tally_id="1"),
        _ledger("Profit & Loss A/c", root_type="Equity",
                opening_cr=500.0, tally_id="SYS", is_system_account=True),
    ]
    decisions = [
        _decision("Cash", tier="tier1_exact", proposed_account="Cash - CACSPU",
                  tally_id="1", opening_dr=100.0),
        _decision("Profit & Loss A/c", tier="tier1_exact",
                  proposed_account="Profit & Loss - CACSPU",
                  tally_id="SYS", opening_cr=500.0, tally_root_type="Equity"),
    ]
    payload = build_je_payload(tb=_tb(ledgers), decisions=decisions, **_BUILD_KW)
    accounts = [r["account"] for r in payload.rows]
    assert "Profit & Loss - CACSPU" not in accounts


def test_non_leaf_ledgers_skipped() -> None:
    ledgers = [
        _ledger("Cash", opening_dr=100.0, tally_id="1"),
        _ledger("Current Assets Group", root_type="Asset",
                opening_dr=200.0, tally_id="G1", is_leaf=False),
    ]
    decisions = [
        _decision("Cash", tier="tier1_exact", proposed_account="Cash - CACSPU",
                  tally_id="1", opening_dr=100.0),
        _decision("Current Assets Group", tier="tier1_exact",
                  proposed_account="Current Assets - CACSPU",
                  tally_id="G1", opening_dr=200.0),
    ]
    payload = build_je_payload(tb=_tb(ledgers), decisions=decisions, **_BUILD_KW)
    accounts = [r["account"] for r in payload.rows]
    assert "Current Assets - CACSPU" not in accounts


# ---------------------------------------------------------------------------
# P&L corner-case warnings
# ---------------------------------------------------------------------------


def test_zero_balance_pnl_silently_excluded_no_warning() -> None:
    ledgers = [
        _ledger("Cash", opening_dr=100.0, tally_id="1"),
        _ledger("Sales", root_type="Income", tally_id="P1", is_pnl_closed_zero=True),
    ]
    decisions = [
        _decision("Cash", tier="tier1_exact", proposed_account="Cash - CACSPU",
                  tally_id="1", opening_dr=100.0),
        _decision("Sales", tier="excluded_pnl",
                  tally_id="P1", tally_root_type="Income"),
    ]
    payload = build_je_payload(tb=_tb(ledgers), decisions=decisions, **_BUILD_KW)
    assert payload.warnings == []


def test_nonzero_pnl_emits_warning_but_still_builds_je() -> None:
    """A P&L ledger with stale balance: excluded from JE, warning surfaces."""
    ledgers = [
        _ledger("Cash", opening_dr=100.0, tally_id="1"),
        _ledger("Stale Sales", root_type="Income",
                opening_cr=777.77, tally_id="P1", is_pnl_closed_zero=False),
    ]
    decisions = [
        _decision("Cash", tier="tier1_exact", proposed_account="Cash - CACSPU",
                  tally_id="1", opening_dr=100.0),
        _decision("Stale Sales", tier="excluded_pnl",
                  tally_id="P1", opening_cr=777.77, tally_root_type="Income"),
    ]
    payload = build_je_payload(tb=_tb(ledgers), decisions=decisions, **_BUILD_KW)
    assert len(payload.warnings) == 1
    w = payload.warnings[0]
    assert "Stale Sales" in w
    assert "777.77" in w
    assert "Export closing as opening" in w
    # JE still built — stale P&L doesn't block generation.
    accounts = [r["account"] for r in payload.rows]
    assert "Cash - CACSPU" in accounts
    assert "Stale Sales - CACSPU" not in accounts


# ---------------------------------------------------------------------------
# Header user_remark format
# ---------------------------------------------------------------------------


def test_header_user_remark_has_reference_id_on_first_line() -> None:
    ledgers = [_ledger("Cash", opening_dr=100.0, tally_id="1")]
    decisions = [
        _decision("Cash", tier="tier1_exact", proposed_account="Cash - CACSPU",
                  tally_id="1", opening_dr=100.0),
    ]
    payload = build_je_payload(tb=_tb(ledgers), decisions=decisions, **_BUILD_KW)
    remark = payload.header["user_remark"]
    assert remark.split("\n", 1)[0] == "OB-CACSPU-2026-01"
    assert "GH Raisoni College of Arts Commerce & Science, Pune" in remark
    assert "TMS-CACSPU-2026-2027-001" in remark
    assert "abc123def456" in remark  # first 12 chars of sha
    assert "abc123def456789" not in remark  # full sha NOT in remark


def test_regenerated_header_includes_old_je_audit_tail() -> None:
    ledgers = [_ledger("Cash", opening_dr=100.0, tally_id="1")]
    decisions = [
        _decision("Cash", tier="tier1_exact", proposed_account="Cash - CACSPU",
                  tally_id="1", opening_dr=100.0),
    ]
    audit = "Regenerated from ACC-JV-2026-00001 (deleted at 2026-04-20T11:50:00Z)"
    payload = build_je_payload(
        tb=_tb(ledgers), decisions=decisions,
        old_je_audit=audit, **_BUILD_KW,
    )
    assert audit in payload.header["user_remark"]


def test_header_fields_are_opening_entry_draft() -> None:
    ledgers = [_ledger("Cash", opening_dr=100.0, tally_id="1")]
    decisions = [
        _decision("Cash", tier="tier1_exact", proposed_account="Cash - CACSPU",
                  tally_id="1", opening_dr=100.0),
    ]
    payload = build_je_payload(tb=_tb(ledgers), decisions=decisions, **_BUILD_KW)
    h = payload.header
    assert h["voucher_type"] == "Opening Entry"
    assert h["is_opening"] == "Yes"
    assert h["company"] == "GHR CACS Pune"
    assert h["posting_date"] == "2026-04-01"
    assert h["title"] == "Opening Entry - CACSPU - FY2026-2027"
    # No docstatus in header → defaults to Draft (0). Never auto-submit.
    assert "docstatus" not in h


# ---------------------------------------------------------------------------
# Item 4 Commit 3 — Rejected ACR rows silent-skip the refusal gate
# (AMB C3-8). Parallel to oit_csv / advance_je's Item 3 Commit 3 behavior
# for Rejected supplier rows.
# ---------------------------------------------------------------------------


def test_rejected_unmapped_row_silent_skips_not_refuses() -> None:
    """A reviewer who Rejects an unmapped ACR means "give up on this
    ledger; don't block the generator." The main JE builds successfully
    even with an otherwise-refusing unmapped row, because the Rejected
    review_action triggers silent-skip."""
    ledgers = [
        _ledger("Cash", opening_dr=100.0, tally_id="1"),
        _ledger("RejectedGhost", opening_dr=999.0, tally_id="2"),
    ]
    decisions = [
        _decision("Cash", tier="tier1_exact",
                  proposed_account="Cash - CACSPU",
                  tally_id="1", opening_dr=100.0),
        # Rejected unmapped row — would normally refuse, but silent-skip
        # kicks in. Balances: main JE has Cash (Dr 100) + Temp Opening
        # absorbs the residual, no refusal raised.
        _decision("RejectedGhost", tier="unmapped",
                  proposed_account=None,
                  tally_id="2", opening_dr=999.0,
                  review_action="Rejected"),
    ]
    # Should NOT raise.
    payload = build_je_payload(tb=_tb(ledgers), decisions=decisions, **_BUILD_KW)
    # Only Cash contributes; RejectedGhost silent-skipped.
    assert payload.contributions_count == 1
    accounts_in_je = {r["account"] for r in payload.rows}
    assert "Cash - CACSPU" in accounts_in_je


def test_rejected_pending_account_creation_silent_skips() -> None:
    """Parallel case for pending_account_creation rows (mapper-tagged
    as needing a new Account). Reviewer Rejects via the ACR workflow →
    silent-skip in main JE."""
    ledgers = [
        _ledger("Cash", opening_dr=100.0, tally_id="1"),
        _ledger("RejectedPAC", opening_dr=500.0, tally_id="2"),
    ]
    decisions = [
        _decision("Cash", tier="tier1_exact",
                  proposed_account="Cash - CACSPU",
                  tally_id="1", opening_dr=100.0),
        _decision("RejectedPAC", tier="pending_account_creation",
                  proposed_account=None,
                  tally_id="2", opening_dr=500.0,
                  review_action="Rejected"),
    ]
    payload = build_je_payload(tb=_tb(ledgers), decisions=decisions, **_BUILD_KW)
    assert payload.contributions_count == 1


def test_pending_unmapped_row_still_refuses_when_not_rejected() -> None:
    """Guardrail: only review_action=Rejected triggers silent-skip.
    A plain unmapped+Pending row (reviewer hasn't acted) still refuses
    — we must NOT accidentally silent-skip every unmapped row."""
    ledgers = [_ledger("Mystery", opening_dr=50.0, tally_id="1")]
    decisions = [
        _decision("Mystery", tier="unmapped",
                  proposed_account=None,
                  tally_id="1", opening_dr=50.0,
                  review_action="Pending"),
    ]
    with pytest.raises(MainJEGenerationError) as exc:
        build_je_payload(tb=_tb(ledgers), decisions=decisions, **_BUILD_KW)
    assert "unmapped" in str(exc.value)


def test_pending_account_creation_still_refuses_when_not_rejected() -> None:
    """Parallel guardrail for the pending_account_creation tier."""
    ledgers = [_ledger("Mystery", opening_dr=50.0, tally_id="1")]
    decisions = [
        _decision("Mystery", tier="pending_account_creation",
                  proposed_account=None,
                  tally_id="1", opening_dr=50.0,
                  review_action="Pending"),
    ]
    with pytest.raises(MainJEGenerationError) as exc:
        build_je_payload(tb=_tb(ledgers), decisions=decisions, **_BUILD_KW)
    assert "pending account creation" in str(exc.value)


def test_rejected_group_refused_still_refuses_not_silent_skipped() -> None:
    """The silent-skip is scoped to the reviewer-actionable refusal
    tiers: unmapped + pending_account_creation. A group_refused or
    anti_pattern_blocked row is a mapper-structural decision; the
    reviewer shouldn't be able to bypass it via Reject. Guard against
    future drift where the scope accidentally widens."""
    ledgers = [_ledger("GroupRefused", opening_dr=50.0, tally_id="1")]
    decisions = [
        _decision("GroupRefused", tier="group_refused",
                  proposed_account=None,
                  tally_id="1", opening_dr=50.0,
                  review_action="Rejected"),
    ]
    with pytest.raises(MainJEGenerationError) as exc:
        build_je_payload(tb=_tb(ledgers), decisions=decisions, **_BUILD_KW)
    # Rejected status didn't bypass the refusal — still a refusal.
    assert "refused by group validator" in str(exc.value)
