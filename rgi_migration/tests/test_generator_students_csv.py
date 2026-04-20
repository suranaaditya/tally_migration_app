"""Unit tests for generator #4 — Students CSV for dux_voucher handoff.

14 scenarios per authorized scope (11 core + 3 additional).
"""

from __future__ import annotations

import csv
import io

import pytest

from rgi_migration.generators.students_csv import (
    StudentRow,
    StudentsCSVGenerationError,
    build_student_rows,
    format_csv,
    refuse_if_empty,
)
from rgi_migration.parsers.normalized_schema import Ledger


def _ledger(
    name: str,
    *,
    tally_id: str | None = None,
    opening_dr: float = 0.0,
    opening_cr: float = 0.0,
    parent_chain: list[str] | None = None,
    is_student_ledger: bool = True,
    is_leaf: bool = True,
) -> Ledger:
    net = opening_cr - opening_dr
    side = "Cr" if net > 0 else "Dr" if net < 0 else "Zero"
    return Ledger(
        name=name,
        tally_id=tally_id,
        parent_group=(parent_chain or ["STUDENTS"])[-1],
        parent_chain=parent_chain or [
            "Current Assets", "Sundry Debtors", "STUDENTS",
        ],
        root_type="Asset",
        opening_dr=opening_dr,
        opening_cr=opening_cr,
        net_amount=net,
        net_side=side,
        is_leaf=is_leaf,
        is_student_ledger=is_student_ledger,
    )


# ---------------------------------------------------------------------------
# 1. Single-ledger net-Dr student → one row
# ---------------------------------------------------------------------------


def test_single_net_dr_student_produces_one_row() -> None:
    rows = build_student_rows([
        _ledger("RAHUL SHARMA", tally_id="1001", opening_dr=5000.0),
    ])
    assert len(rows) == 1
    r = rows[0]
    assert r.student_name == "RAHUL SHARMA"
    assert r.debit_amount == 5000.0
    assert r.credit_amount == 0.0
    assert "1001" in r.remarks
    assert "1 Tally ledger" in r.remarks


# ---------------------------------------------------------------------------
# 2. Single-ledger net-Cr student → one row
# ---------------------------------------------------------------------------


def test_single_net_cr_student_produces_one_row() -> None:
    rows = build_student_rows([
        _ledger("PRIYA DESHMUKH", tally_id="2002", opening_cr=3000.0),
    ])
    assert len(rows) == 1
    assert rows[0].debit_amount == 0.0
    assert rows[0].credit_amount == 3000.0


# ---------------------------------------------------------------------------
# 3. Single zero-balance ledger → skipped (NOT refused)
# ---------------------------------------------------------------------------


def test_single_zero_balance_ledger_skipped() -> None:
    """Tally definitional noise: student master created but never transacted.
    Zero-net skip handles this without a separate validator."""
    rows = build_student_rows([
        _ledger("Sachin Gawande-30", tally_id="3003",
                opening_dr=0.0, opening_cr=0.0),
    ])
    assert rows == []


# ---------------------------------------------------------------------------
# 4. Student with net ₹0.01 → emitted (NOT tolerance-skipped)
# ---------------------------------------------------------------------------


def test_net_one_paisa_student_is_emitted_not_skipped() -> None:
    """Confirms the FP-noise tolerance (₹0.005) doesn't swallow a legitimate
    ₹0.01 balance. Small residuals flow to dux_voucher for accounting-team
    decision, not silent loss."""
    rows = build_student_rows([
        _ledger("TINY BALANCE STUDENT", tally_id="4004",
                opening_dr=0.01),
    ])
    assert len(rows) == 1
    assert rows[0].debit_amount == 0.01


# ---------------------------------------------------------------------------
# 5. Two ledgers same student, net-Dr → aggregated, one row with summed amount
# ---------------------------------------------------------------------------


def test_two_ledgers_same_student_net_dr_aggregated() -> None:
    rows = build_student_rows([
        _ledger("AMIT KUMAR", tally_id="5005", opening_dr=10000.0),
        _ledger("AMIT KUMAR", tally_id="5006", opening_dr=2500.0),
    ])
    assert len(rows) == 1
    assert rows[0].student_name == "AMIT KUMAR"
    assert rows[0].debit_amount == 12500.0
    # Distinct tally_ids → AUDIT_MERGED trigger
    assert rows[0].remarks.startswith("AUDIT_MERGED:")
    assert "5005" in rows[0].remarks
    assert "5006" in rows[0].remarks


# ---------------------------------------------------------------------------
# 6. Two ledgers same student, net-Cr → aggregated
# ---------------------------------------------------------------------------


def test_two_ledgers_same_student_net_cr_aggregated() -> None:
    rows = build_student_rows([
        _ledger("NEHA PATIL", tally_id="6001", opening_cr=2000.0),
        _ledger("NEHA PATIL", tally_id="6002", opening_cr=500.0),
    ])
    assert len(rows) == 1
    assert rows[0].student_name == "NEHA PATIL"
    assert rows[0].debit_amount == 0.0
    assert rows[0].credit_amount == 2500.0


# ---------------------------------------------------------------------------
# 7. Two ledgers same student netting to zero → skipped (NOT refused)
# ---------------------------------------------------------------------------


def test_two_ledgers_netting_to_zero_skipped() -> None:
    """Fully-paid-up student: fee ₹5000 Dr, receipt ₹5000 Cr → zero-net →
    no CSV row. One rule (zero-net skip) covers this AND the all-zero
    case from test #3."""
    rows = build_student_rows([
        _ledger("Rahul Sharma-47", tally_id="7007",
                opening_dr=5000.0),
        _ledger("Rahul Sharma-47", tally_id="7008",
                opening_cr=5000.0),
    ])
    assert rows == []


# ---------------------------------------------------------------------------
# 8. Both-sided single ledger → aggregated net applied to correct side
# ---------------------------------------------------------------------------


def test_both_sided_single_ledger_nets_correctly() -> None:
    """A single Tally ledger can have both opening_dr and opening_cr set
    (bill-allocation split). Aggregator treats it as one contributor and
    applies the net."""
    rows = build_student_rows([
        _ledger("BOTH SIDED STUDENT", tally_id="8008",
                opening_dr=3000.0, opening_cr=1200.0),
    ])
    assert len(rows) == 1
    assert rows[0].debit_amount == 1800.0  # 3000 Dr − 1200 Cr
    assert rows[0].credit_amount == 0.0


# ---------------------------------------------------------------------------
# 9. Empty tb.student_ledgers → refusal at Frappe entry (per Q1)
# ---------------------------------------------------------------------------


def test_empty_input_refuses_with_q1_informational_message() -> None:
    """Q1: empty tb.student_ledgers → build_student_rows returns [] at
    pure core, refuse_if_empty raises informational refusal at entry
    level naming GHRILS per RGI sec 5.4. Single test covers both
    aspects of the authorized 'Empty input' scenario."""
    rows = build_student_rows([])
    assert rows == []
    with pytest.raises(StudentsCSVGenerationError) as exc:
        refuse_if_empty(rows, "TMS-GHRILS-2026-2027-00001")
    msg = str(exc.value)
    assert "TMS-GHRILS-2026-2027-00001" in msg
    assert "No non-zero student balances" in msg
    assert "GHRILS" in msg
    assert "No Phase 2 needed" in msg


# ---------------------------------------------------------------------------
# 10. tally_id=None on contributor → remarks writes "(none)" in ids slot
# ---------------------------------------------------------------------------


def test_tally_id_none_emits_none_in_remarks() -> None:
    """74% of CACSPU student ledgers have tally_id=None — need graceful
    rendering, not empty string."""
    rows = build_student_rows([
        _ledger("AARANYA HARESHWAR MESHRAM 20A0007Bsc_csG1019",
                tally_id=None, opening_dr=58000.0),
    ])
    assert len(rows) == 1
    assert "tally_ids: (none)" in rows[0].remarks


# ---------------------------------------------------------------------------
# 11. Student name containing a comma → RFC 4180 auto-quoted
# ---------------------------------------------------------------------------


def test_student_name_with_comma_rfc4180_quoted() -> None:
    rows = build_student_rows([
        _ledger("LAST, FIRST MIDDLE", tally_id="11011", opening_dr=100.0),
    ])
    text = format_csv(rows)
    assert '"LAST, FIRST MIDDLE"' in text


# ---------------------------------------------------------------------------
# 12. UTF-8 preservation (authorized addition)
# ---------------------------------------------------------------------------


def test_utf8_student_name_preserved_end_to_end() -> None:
    """Devanagari / accented names survive CSV round-trip in UTF-8."""
    rows = build_student_rows([
        _ledger("राहुल शर्मा — ₹ balance", tally_id="12012",
                opening_dr=7500.0),
        _ledger("Café François", tally_id="12013",
                opening_cr=1000.0),
    ])
    text = format_csv(rows)
    assert "राहुल शर्मा — ₹ balance" in text
    assert "Café François" in text
    # UTF-8 round-trip doesn't mangle.
    assert text.encode("utf-8").decode("utf-8") == text


# ---------------------------------------------------------------------------
# 13. Mixed tally_id present-and-absent in multi-contributor group
# ---------------------------------------------------------------------------


def test_mixed_id_present_and_absent_multi_contributor() -> None:
    """One contributor has tally_id, the other doesn't. Should NOT trigger
    AUDIT_MERGED (requires ≥2 *distinct non-null* tally_ids); normal
    remarks with whatever IDs are present."""
    rows = build_student_rows([
        _ledger("MIXED IDS STUDENT", tally_id="13013", opening_dr=2000.0),
        _ledger("MIXED IDS STUDENT", tally_id=None, opening_dr=500.0),
    ])
    assert len(rows) == 1
    assert rows[0].debit_amount == 2500.0
    assert not rows[0].remarks.startswith("AUDIT_MERGED:")
    assert "2 Tally ledgers" in rows[0].remarks
    # Non-null id appears; the absent one is elided (only 1 non-null id present).
    assert "13013" in rows[0].remarks


# ---------------------------------------------------------------------------
# 15. All-None tally_ids across multiple contributors — preserve count info
# ---------------------------------------------------------------------------


def test_all_none_tally_ids_multi_contributor_preserves_count() -> None:
    """When every contributor in a multi-contributor group has
    tally_id=None, remarks renders ``(all none, N contributors)`` instead
    of a bare ``(none)`` — so the count signal survives the absence of
    IDs. On CACSPU, 74% of student ledgers have tally_id=None, making
    this the common multi-contributor shape rather than an edge case."""
    rows = build_student_rows([
        _ledger("ALL NONE STUDENT", tally_id=None, opening_dr=1000.0),
        _ledger("ALL NONE STUDENT", tally_id=None, opening_dr=500.0),
    ])
    assert len(rows) == 1
    assert rows[0].debit_amount == 1500.0
    # Count-preserving format, not bare (none)
    assert "(all none, 2 contributors)" in rows[0].remarks
    # And NOT AUDIT_MERGED — no distinct non-null tally_ids by definition
    assert not rows[0].remarks.startswith("AUDIT_MERGED:")


# ---------------------------------------------------------------------------
# 14. Aggregation sanity: totals-preservation invariant (authorized addition)
# ---------------------------------------------------------------------------


def test_aggregation_preserves_raw_net_totals() -> None:
    """Σ emitted debit_amount − Σ emitted credit_amount must equal
    Σ raw opening_dr − Σ raw opening_cr across all non-zero-net contributors.
    Catches aggregation drift or sign-flip regressions."""
    ledgers = [
        _ledger("A", tally_id="a", opening_dr=10000.0),
        _ledger("A", tally_id="b", opening_cr=3000.0),
        _ledger("B", tally_id="c", opening_dr=5000.0),
        _ledger("C", tally_id="d", opening_cr=2500.0),
        _ledger("D", tally_id="e", opening_dr=1000.0),
        _ledger("D", tally_id="f", opening_cr=1000.0),  # nets to 0 → skipped
        _ledger("E", tally_id="g", opening_dr=0.0, opening_cr=0.0),  # skipped
    ]
    rows = build_student_rows(ledgers)

    # Emitted net
    emitted_net = sum(r.debit_amount for r in rows) - sum(r.credit_amount for r in rows)

    # Raw net across NON-SKIPPED contributors. Skipped rows are those
    # belonging to students whose aggregate net is zero (D and E here);
    # their raw balances also sum to zero, so the invariant is raw Σdr −
    # raw Σcr across ALL contributors = emitted net.
    raw_net = (
        sum(l.opening_dr for l in ledgers)
        - sum(l.opening_cr for l in ledgers)
    )
    assert emitted_net == raw_net  # both should be 10000 − 3000 + 5000 − 2500 + 0 = 9500
    assert emitted_net == 9500.0


# ---------------------------------------------------------------------------
# Guardrail — is_student_ledger=False slipping in is a parser contract violation
# (not one of the authorized 14; adding as guardrail-only would be out of scope)
# ---------------------------------------------------------------------------
