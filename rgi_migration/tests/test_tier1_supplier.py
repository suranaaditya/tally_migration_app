"""Tier-1 supplier resolution tests.

Synthetic suppliers + stub ledgers. Exercises:
    - Party-ledger detection (parent_chain-based)
    - Layer 1 exact_ci match (case / whitespace insensitive)
    - Layer 3 fuzzy match (rapidfuzz backed)
    - No-match route to pending_supplier_creation
    - Mapper integration: party ledgers route through supplier path,
      non-party ledgers continue to use the existing rule/exact path.

Layer 2 (Supplier Alias Rule hits) is a stub today — its tests land
once the reviewer-promotion workflow populates real alias rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from rgi_migration.mapper import (
    CoaAccount,
    InMemoryRuleSource,
    InMemorySupplierSource,
    Mapper,
    Supplier,
)
from rgi_migration.mapper.rule_source import Rule
from rgi_migration.mapper.tier1_supplier import (
    _clean,
    find_exact_supplier,
    find_fuzzy_supplier,
    is_control_account,
    is_vendor_party_ledger,
    resolve_supplier,
)


@dataclass
class StubLedger:
    name: str
    root_type: str
    parent_chain: list[str] = field(default_factory=list)
    opening_dr: float = 0.0
    opening_cr: float = 0.0
    tally_id: str | None = None


SUPPLIERS = [
    Supplier(name="SUP-0001", supplier_name=" Nilesh Traders",
             supplier_group="Purchase Register"),  # leading space (real data quirk)
    Supplier(name="SUP-0002", supplier_name="Abhi Tria",
             supplier_group="Purchase Register"),
    Supplier(name="SUP-0003", supplier_name="Gulab Hardware",
             supplier_group="Purchase Register"),
    Supplier(name="SUP-0004", supplier_name="Eastern IT Services Private Ltd",
             supplier_group="Services"),
]


# ---------------------------------------------------------------------------
# Helper: _clean
# ---------------------------------------------------------------------------


def test_clean_strips_whitespace():
    assert _clean("  foo  ") == "foo"


def test_clean_collapses_internal_spaces():
    assert _clean("foo   bar") == "foo bar"


def test_clean_strips_tally_party_suffix():
    assert _clean("AARNA SOLUTION-VA0290") == "AARNA SOLUTION"
    assert _clean("18 CONSULTANT-VC0094") == "18 CONSULTANT"
    assert _clean("Adapt Computer-SA0205") == "Adapt Computer"


def test_clean_preserves_names_without_suffix():
    assert _clean("Nilesh Traders") == "Nilesh Traders"
    assert _clean("M/s Something Pvt Ltd") == "M/s Something Pvt Ltd"


# ---------------------------------------------------------------------------
# Party-ledger detection
# ---------------------------------------------------------------------------


def test_is_vendor_party_by_parent_chain():
    ledger = StubLedger(
        name="AARNA SOLUTION",
        root_type="Liability",
        parent_chain=["Current Liabilities", "Sundry Creditors"],
    )
    assert is_vendor_party_ledger(ledger) is True


def test_is_vendor_party_case_insensitive():
    ledger = StubLedger(
        name="X",
        root_type="Liability",
        parent_chain=["sundry creditors"],
    )
    assert is_vendor_party_ledger(ledger) is True


def test_non_vendor_ledger_not_flagged():
    ledger = StubLedger(
        name="Cash",
        root_type="Asset",
        parent_chain=["Current Assets", "Bank Accounts"],
    )
    assert is_vendor_party_ledger(ledger) is False


def test_student_under_sundry_debtors_not_flagged():
    # Sundry Debtors is NOT in the vendor-marker list — students go to
    # the Phase-2 CSV, not supplier resolution
    ledger = StubLedger(
        name="SOME STUDENT",
        root_type="Asset",
        parent_chain=["Current Assets", "Sundry Debtors", "Students"],
    )
    assert is_vendor_party_ledger(ledger) is False


# ---------------------------------------------------------------------------
# Layer 1: exact_ci
# ---------------------------------------------------------------------------


def test_exact_match_case_insensitive():
    src = InMemorySupplierSource(SUPPLIERS)
    assert find_exact_supplier("abhi tria", src).name == "SUP-0002"
    assert find_exact_supplier("ABHI TRIA", src).name == "SUP-0002"
    assert find_exact_supplier("Abhi Tria", src).name == "SUP-0002"


def test_exact_match_whitespace_tolerant_against_data_artefact():
    # Supplier stored with leading space; lookup by clean name still finds it
    src = InMemorySupplierSource(SUPPLIERS)
    hit = find_exact_supplier("Nilesh Traders", src)
    assert hit is not None
    assert hit.name == "SUP-0001"


def test_exact_match_no_hit_returns_none():
    src = InMemorySupplierSource(SUPPLIERS)
    assert find_exact_supplier("Nonexistent Vendor", src) is None


# ---------------------------------------------------------------------------
# Layer 3: fuzzy
# ---------------------------------------------------------------------------


def test_fuzzy_match_hits_close_name():
    # "Eastern IT Services Pvt Ltd" vs stored "Eastern IT Services Private Ltd"
    src = InMemorySupplierSource(SUPPLIERS)
    hit = find_fuzzy_supplier("Eastern IT Services Pvt Ltd", src, threshold=80.0)
    assert hit is not None
    supplier, score = hit
    assert supplier.name == "SUP-0004"
    assert 0.0 < score <= 1.0


def test_fuzzy_match_below_threshold_returns_none():
    src = InMemorySupplierSource(SUPPLIERS)
    # Nothing in the 4-supplier master looks like this
    assert find_fuzzy_supplier("totally unrelated vendor", src,
                               threshold=85.0) is None


# ---------------------------------------------------------------------------
# Top-level resolve_supplier
# ---------------------------------------------------------------------------


def test_resolve_exact():
    src = InMemorySupplierSource(SUPPLIERS)
    ledger = StubLedger(name="Gulab Hardware", root_type="Liability",
                        parent_chain=["Sundry Creditors"])
    tier, sup, conf, alias = resolve_supplier(ledger, src)
    assert tier == "tier1_supplier_exact"
    assert sup.name == "SUP-0003"
    assert conf == 1.0
    assert alias is None


def test_resolve_pending_creation_when_no_match():
    src = InMemorySupplierSource(SUPPLIERS)
    ledger = StubLedger(name="AARNA SOLUTION-VA0290", root_type="Liability",
                        parent_chain=["Sundry Creditors"])
    tier, sup, conf, alias = resolve_supplier(ledger, src)
    assert tier == "pending_supplier_creation"
    assert sup is None
    assert conf == 0.0


# ---------------------------------------------------------------------------
# Mapper integration: party ledger routes through supplier path
# ---------------------------------------------------------------------------


def _trivial_rules():
    return InMemoryRuleSource([])


def _empty_coa():
    return {}


def test_mapper_routes_party_ledger_to_supplier_resolution():
    supplier_source = InMemorySupplierSource(SUPPLIERS)
    m = Mapper(
        _trivial_rules(), _empty_coa(), abbr="TEST",
        supplier_source=supplier_source,
    )
    ledger = StubLedger(
        name="Abhi Tria", root_type="Liability",
        parent_chain=["Current Liabilities", "Sundry Creditors"],
        opening_cr=50000.0,
    )
    d = m.resolve(ledger)
    assert d.tier == "tier1_supplier_exact"
    assert d.proposed_supplier == "SUP-0002"
    assert d.supplier_match_score == 1.0
    assert d.review_action == "Pending"
    # Did NOT get routed through the account-mapping pathway
    assert d.proposed_account is None


def test_mapper_emits_supplier_creation_for_unmatched_party():
    supplier_source = InMemorySupplierSource(SUPPLIERS)
    m = Mapper(
        _trivial_rules(), _empty_coa(), abbr="TEST",
        supplier_source=supplier_source,
    )
    ledger = StubLedger(
        name="Nonexistent Vendor Pvt Ltd-VA0001", root_type="Liability",
        parent_chain=["Current Liabilities", "Sundry Creditors"],
        opening_cr=1000.0,
    )
    d = m.resolve(ledger)
    assert d.tier == "pending_supplier_creation"
    assert d.review_action == "Pending Supplier Creation"
    assert d.requires_supplier_creation is True
    # Cleaned name landed in new_supplier_name (reviewer's starting point)
    assert d.new_supplier_name == "Nonexistent Vendor Pvt Ltd"


def test_mapper_non_party_ledger_unaffected_by_supplier_source():
    # A non-party (Asset) ledger with supplier_source provided should still
    # flow through the account-mapping path, NOT supplier resolution.
    supplier_source = InMemorySupplierSource(SUPPLIERS)
    m = Mapper(
        _trivial_rules(), _empty_coa(), abbr="TEST",
        supplier_source=supplier_source,
    )
    ledger = StubLedger(
        name="Some Fixed Asset", root_type="Asset",
        parent_chain=["Fixed Assets"],
    )
    d = m.resolve(ledger)
    # No rule, no COA match → unmapped (existing behavior)
    assert d.tier == "unmapped"
    assert d.proposed_supplier is None


def test_mapper_without_supplier_source_ignores_party_path():
    # Backward compat: a Mapper built without supplier_source behaves
    # exactly as before Work Item 6 — party ledgers flow to unmapped
    m = Mapper(_trivial_rules(), _empty_coa(), abbr="TEST")  # no supplier_source
    ledger = StubLedger(
        name="Abhi Tria", root_type="Liability",
        parent_chain=["Current Liabilities", "Sundry Creditors"],
    )
    d = m.resolve(ledger)
    assert d.tier == "unmapped"
    assert d.proposed_supplier is None


# ---------------------------------------------------------------------------
# Control-account pattern list (Layer 2 of the control-account fix)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,expect_control", [
    # Should match — these are control accounts, not vendors
    ("Advances Received For Expenses", True),
    ("Advance Received", True),
    ("TDS Payable 194C", True),
    ("TDS Payable On Rent", True),
    ("Tax Collected at Source", True),
    ("GST Tax Collected", True),
    ("Provision for Expenses", True),
    ("Provision for Audit Fees", True),
    ("Suspense A/c", True),
    ("Unadjusted Receipts", True),
    ("Unadjusted Advance from Customer", True),
    ("GST Payable", True),
    ("GST Input", True),
    ("GST Output", True),
    ("Round off", True),
    ("Rounding off", True),
    # Should NOT match — these are genuine vendor-name patterns
    ("Nilesh Traders", False),
    ("AARNA SOLUTION", False),
    ("Best Advances Trading Co", False),     # doesn't start with "Advances Received"
    ("Suspense Ac", False),                   # slash required — prevents ambiguity with Ac*
    ("Suspense Account", False),              # separate full-word form; not in our 8-pattern list
    ("Gulab Hardware", False),
    ("Amit", False),
])
def test_control_account_pattern_detection(name, expect_control):
    ledger = StubLedger(name=name, root_type="Liability",
                        parent_chain=["Sundry Creditors"])
    assert is_control_account(ledger) is expect_control


def test_control_account_under_sundry_creditors_not_flagged_as_party():
    # Even though parent chain says Sundry Creditors, the control-account
    # name pattern short-circuits party classification.
    ledger = StubLedger(
        name="Advances Received For Expenses",
        root_type="Liability",
        parent_chain=["Current Liabilities", "Sundry Creditors"],
    )
    assert is_vendor_party_ledger(ledger) is False


def test_control_account_stays_in_main_flow_via_mapper():
    # Integration: control account under Sundry Creditors should flow
    # through account-mapping (rule/exact/unmapped), not supplier path.
    supplier_source = InMemorySupplierSource(SUPPLIERS)
    m = Mapper(_trivial_rules(), _empty_coa(), abbr="TEST",
               supplier_source=supplier_source)
    ledger = StubLedger(
        name="TDS Payable 194C",
        root_type="Liability",
        parent_chain=["Current Liabilities", "Sundry Creditors"],
        opening_cr=10500.0,
    )
    d = m.resolve(ledger)
    # No rule, no COA match -> unmapped. NOT pending_supplier_creation.
    assert d.tier == "unmapped"
    assert d.proposed_supplier is None
    assert d.requires_supplier_creation is False


# ---------------------------------------------------------------------------
# Rule-first ordering (Layer 1 of the control-account fix)
# ---------------------------------------------------------------------------


def _positive_rule(**overrides) -> Rule:
    """Minimal positive-rule factory for tests."""
    defaults: dict = dict(
        source_section="§TEST",
        source_hash="hash-test",
        rule_name="test",
        is_anti_pattern=False,
        status="confirmed",
        applies_to_entity_types="*",
        tally_pattern="",
        tally_match_mode="exact_ci",
        tally_pattern_alternates=(),
        applicable_root_type="Any",
        tally_parent_contains=None,
        erpnext_account_template=None,
        combine_amounts=False,
        forbidden_erpnext_template=None,
        anti_pattern_reason=None,
        suggested_alternative_template=None,
        creates_erpnext_account=False,
        new_account_name_template=None,
        new_account_parent=None,
        new_account_root_type=None,
        new_account_is_group=False,
    )
    defaults.update(overrides)
    return Rule(**defaults)


def test_rule_first_ordering_beats_party_branch():
    """A ledger under Sundry Creditors whose name matches a positive rule
    resolves via the rule, NOT via supplier resolution. This is the
    §4.6 / §4.7 regression fix — rules live-under-party-parent must
    still fire before party routing."""
    # Rule mimicking §4.6 Unpaid Expenditure Account
    rules = InMemoryRuleSource([
        _positive_rule(
            source_section="§4.6",
            source_hash="hash-sec46",
            rule_name="Unpaid Expenditure Account (test)",
            tally_pattern="Unpaid Expenditure Account",
            tally_match_mode="exact_ci",
            applicable_root_type="Liability",
            erpnext_account_template="Unpaid Expenditure Provision - {ABBR}",
        ),
    ])
    coa = {
        "Unpaid Expenditure Provision - TEST": CoaAccount(
            name="Unpaid Expenditure Provision - TEST",
            parent_account="Other Liabilities - TEST",
            root_type="Liability",
            is_group=False,
            company_abbr="TEST",
        ),
    }
    supplier_source = InMemorySupplierSource(SUPPLIERS)
    m = Mapper(rules, coa, abbr="TEST", supplier_source=supplier_source)

    ledger = StubLedger(
        name="Unpaid Expenditure Account",
        root_type="Liability",
        parent_chain=["Current Liabilities", "Sundry Creditors"],
        opening_cr=500000.0,
    )
    d = m.resolve(ledger)
    # Should match the rule, NOT route to supplier
    assert d.tier == "tier1_rule"
    assert d.matched_rule == "§4.6"
    assert d.proposed_account == "Unpaid Expenditure Provision - TEST"
    assert d.proposed_supplier is None


def test_vendor_ledger_without_matching_rule_still_routes_to_supplier():
    """Regression guard: reordering must not break the normal supplier
    path. A vendor ledger whose name does NOT match any rule still
    flows through supplier resolution as before."""
    supplier_source = InMemorySupplierSource(SUPPLIERS)
    m = Mapper(_trivial_rules(), _empty_coa(), abbr="TEST",
               supplier_source=supplier_source)
    ledger = StubLedger(
        name="Abhi Tria",
        root_type="Liability",
        parent_chain=["Current Liabilities", "Sundry Creditors"],
    )
    d = m.resolve(ledger)
    assert d.tier == "tier1_supplier_exact"
    assert d.proposed_supplier == "SUP-0002"
