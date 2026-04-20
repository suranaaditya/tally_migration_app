"""Unit tests for ``resolve_exact_name`` — the Layer-3 account-name resolver.

Surfaced during Work Item 7 diagnostic (commit c1497fe) when the 1160-unmapped
bucket on CACSPU included ledgers like ``STUDENT PAYABLE CYBERVIDYA`` whose
target account existed in the COA with title-case casing. The pre-fix
implementation did a raw ``dict.__contains__`` lookup on the constructed
``f'{ledger.name} - {abbr}'`` string, which is case-sensitive and
whitespace-intolerant.

These tests lock down the fixed behaviour: case-insensitive,
whitespace-tolerant, returns original ERPNext casing, and does not introduce
false positives (substring vs exact match).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rgi_migration.mapper.mapper import CoaAccount
from rgi_migration.mapper.tier1_rules import resolve_exact_name


@dataclass
class StubLedger:
    name: str
    tally_id: str | None = None
    parent_chain: list[str] = field(default_factory=list)
    root_type: str = "Asset"
    opening_dr: float = 0.0
    opening_cr: float = 0.0


def _coa(*names: str) -> dict[str, CoaAccount]:
    return {
        n: CoaAccount(
            name=n, parent_account=None, root_type="Asset",
            is_group=False, company_abbr="CACSPU",
        )
        for n in names
    }


# ---------------------------------------------------------------------------
# The scenario that motivated the fix
# ---------------------------------------------------------------------------


def test_cybervidya_all_caps_tally_matches_title_case_coa() -> None:
    coa = _coa("Student Payable Cybervidya - CACSPU", "Cash - CACSPU")
    ledger = StubLedger(name="STUDENT PAYABLE CYBERVIDYA")
    result = resolve_exact_name(ledger, coa, "CACSPU")
    # Match succeeds AND returns the original ERPNext casing for the Link.
    assert result == "Student Payable Cybervidya - CACSPU"


def test_bank_account_mixed_case_matches() -> None:
    """Simulates one of the missed bank variants from the CACSPU sample."""
    coa = _coa("ICICI Bank - 624205021153 - CACSPU")
    ledger = StubLedger(name="ICICI BANK - 624205021153")
    result = resolve_exact_name(ledger, coa, "CACSPU")
    assert result == "ICICI Bank - 624205021153 - CACSPU"


# ---------------------------------------------------------------------------
# Whitespace tolerance
# ---------------------------------------------------------------------------


def test_trailing_whitespace_tolerated_on_tally_side() -> None:
    coa = _coa("Cash - CACSPU")
    ledger = StubLedger(name="Cash ")  # trailing space from Tally
    assert resolve_exact_name(ledger, coa, "CACSPU") == "Cash - CACSPU"


def test_internal_double_whitespace_collapsed() -> None:
    coa = _coa("Petty Cash - CACSPU")
    ledger = StubLedger(name="Petty  Cash")  # double space in middle
    assert resolve_exact_name(ledger, coa, "CACSPU") == "Petty Cash - CACSPU"


def test_whitespace_artefact_on_coa_side_tolerated() -> None:
    coa = _coa("  Staff Advance  - CACSPU")  # CSV-import leading/trailing
    ledger = StubLedger(name="Staff Advance")
    assert resolve_exact_name(ledger, coa, "CACSPU") == "  Staff Advance  - CACSPU"


# ---------------------------------------------------------------------------
# False-positive guards — normalisation must not over-match
# ---------------------------------------------------------------------------


def test_cash_does_not_match_petty_cash() -> None:
    """Critical guard — substring-like false positives would break the Layer-3
    contract. ``Cash`` and ``Petty Cash`` are both in the COA; resolving a
    Tally ``Cash`` ledger must return ``Cash - CACSPU`` exactly."""
    coa = _coa("Cash - CACSPU", "Petty Cash - CACSPU")
    ledger = StubLedger(name="Cash")
    assert resolve_exact_name(ledger, coa, "CACSPU") == "Cash - CACSPU"


def test_petty_cash_ledger_still_resolves_correctly() -> None:
    coa = _coa("Cash - CACSPU", "Petty Cash - CACSPU")
    ledger = StubLedger(name="petty cash")  # lowercase from Tally
    assert resolve_exact_name(ledger, coa, "CACSPU") == "Petty Cash - CACSPU"


def test_different_abbr_does_not_match() -> None:
    """Abbr token is part of the candidate — mismatched abbr must miss."""
    coa = _coa("Cash - CACSPU")
    ledger = StubLedger(name="Cash")
    assert resolve_exact_name(ledger, coa, "GHRCE") is None


# ---------------------------------------------------------------------------
# Defensive guards
# ---------------------------------------------------------------------------


def test_empty_ledger_name_returns_none() -> None:
    coa = _coa("Cash - CACSPU")
    ledger = StubLedger(name="")
    assert resolve_exact_name(ledger, coa, "CACSPU") is None


def test_no_match_returns_none() -> None:
    coa = _coa("Cash - CACSPU")
    ledger = StubLedger(name="Some Ledger That Does Not Exist")
    assert resolve_exact_name(ledger, coa, "CACSPU") is None


def test_empty_coa_returns_none() -> None:
    ledger = StubLedger(name="Cash")
    assert resolve_exact_name(ledger, {}, "CACSPU") is None


# ---------------------------------------------------------------------------
# Returns original casing — matters for Frappe Link-field resolution
# ---------------------------------------------------------------------------


def test_result_is_original_erpnext_casing_not_normalised() -> None:
    """Downstream code uses the returned string as the Frappe Account doc
    ID (case-sensitive). Must return the COA's original string verbatim,
    not the lowercase-normalised form used for matching."""
    coa = _coa("Bank of Maharashtra (Cap) - 60451303968 - CACSPU")
    ledger = StubLedger(name="bank of maharashtra (cap) - 60451303968")
    result = resolve_exact_name(ledger, coa, "CACSPU")
    assert result == "Bank of Maharashtra (Cap) - 60451303968 - CACSPU"
    # Sanity: result is NOT lowercased.
    assert result != result.lower()  # type: ignore[union-attr]
