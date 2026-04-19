"""Tier-1 mapper self-test — 5 ledgers + 5 COA entries, all hand-built inline.

Proves the mapper pipeline works end-to-end without external data. Each of
the five ledgers exercises a different code path; if any path breaks, one
test fails in isolation rather than hiding behind a broader fixture.

Paths covered:
    1. `tier1_exact`                    — Tally name exact-matches an ERP leaf.
    2. `tier1_rule` (exact_ci)          — positive rule renames Tally to an
                                          existing ERP leaf.
    3. `tier1_pattern` (regex)          — regex positive rule collapses many
                                          Tally variants to one ERP leaf.
    4. `pending_account_creation` + anti-pattern block — anti-pattern refuses
                                          a same-name exact match, steers to
                                          a non-existent alternative;
                                          creates_erpnext_account=1 triggers
                                          a creation request.
    5. `excluded_pnl`                   — Income/Expense root short-circuits
                                          before any rule evaluation.

These are synthetic rules (prefix §TEST.*), not the real §4/§11 seed — the
self-test must not depend on docs/seed_plan.json being present.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from rgi_migration.mapper import (
    CoaAccount,
    InMemoryRuleSource,
    Mapper,
    Rule,
    summarize,
)


ABBR = "TEST"


# ---------------------------------------------------------------------------
# Stub ledger — shape-compatible with parsers.tally_xml_parser.Ledger
# ---------------------------------------------------------------------------


@dataclass
class StubLedger:
    name: str
    root_type: str
    parent_chain: list[str] = field(default_factory=list)
    opening_dr: float = 0.0
    opening_cr: float = 0.0
    tally_id: str | None = None


# ---------------------------------------------------------------------------
# 5 COA entries — one per realistic scenario we might need
# ---------------------------------------------------------------------------


def _coa() -> dict[str, CoaAccount]:
    rows = [
        # Leaf the exact-match test hits directly.
        CoaAccount("Gadget - TEST", "Movable Properties - TEST", "Asset", False, "TEST"),
        # Leaf the regex rule resolves to.
        CoaAccount("MSEB Deposit - TEST", "Loans & Advances - TEST", "Asset", False, "TEST"),
        # Same-name ERP account wrongly under Income — the anti-pattern refuses this.
        CoaAccount("Hostel Fee A/c - TEST", "Direct Incomes - TEST", "Income", False, "TEST"),
        # Group account, parent for the anti-pattern's created child account.
        CoaAccount("Liability For Students - TEST", "Current Liabilities - TEST",
                   "Liability", True, "TEST"),
        # Group account, structural refusal target. Kept for future test expansion;
        # also used by the batch summary to prove group_refused doesn't fire here.
        CoaAccount("Sundry Creditors - TEST", "Current Liabilities - TEST",
                   "Liability", True, "TEST"),
    ]
    return {a.name: a for a in rows}


# ---------------------------------------------------------------------------
# 3 synthetic rules (2 positive + 1 anti-pattern). Rule count < ledger count
# because tier1_exact and excluded_pnl fire WITHOUT any rule.
# ---------------------------------------------------------------------------


def _make_rule(**kwargs) -> Rule:
    defaults: dict = dict(
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
    defaults.update(kwargs)
    return Rule(**defaults)


def _rules() -> InMemoryRuleSource:
    return InMemoryRuleSource([
        # A — positive, exact_ci. "widget" (Tally) → "Gadget - TEST" (ERP leaf).
        _make_rule(
            source_section="§TEST.A",
            source_hash="hash-a",
            rule_name="Test exact_ci rule (Widget → Gadget)",
            is_anti_pattern=False,
            status="confirmed",
            applies_to_entity_types="*",
            tally_pattern="widget",
            tally_match_mode="exact_ci",
            applicable_root_type="Asset",
            erpnext_account_template="Gadget - {ABBR}",
        ),
        # B — positive, regex. "MSEB Connection \d+" → "MSEB Deposit - TEST".
        _make_rule(
            source_section="§TEST.B",
            source_hash="hash-b",
            rule_name="Test regex rule (MSEB connection variants)",
            is_anti_pattern=False,
            status="confirmed",
            applies_to_entity_types="*",
            tally_pattern=r"^mseb connection \d+$",
            tally_match_mode="regex",
            applicable_root_type="Asset",
            erpnext_account_template="MSEB Deposit - {ABBR}",
        ),
        # C — anti-pattern + creates_account. "Hostel Fee A/c" (Liability)
        #     refuses the same-name ERP account (Income) and creates
        #     "Hostel Fee Advance Payable - TEST" under Liability For Students.
        _make_rule(
            source_section="§TEST.C",
            source_hash="hash-c",
            rule_name="Test anti-pattern (Hostel Fee A/c)",
            is_anti_pattern=True,
            status="confirmed",
            applies_to_entity_types="*",
            tally_pattern="hostel fee a/c",
            tally_match_mode="exact_ci",
            applicable_root_type="Liability",
            forbidden_erpnext_template="Hostel Fee A/c - {ABBR}",
            anti_pattern_reason=(
                "Test-only: ERP 'Hostel Fee A/c' lives under Direct Incomes "
                "(P&L); Tally source is a BS liability."
            ),
            suggested_alternative_template="Hostel Fee Advance Payable - {ABBR}",
            creates_erpnext_account=True,
            new_account_name_template="Hostel Fee Advance Payable - {ABBR}",
            new_account_parent="Liability For Students",
            new_account_root_type="Liability",
            new_account_is_group=False,
        ),
    ])


@pytest.fixture
def mapper() -> Mapper:
    return Mapper(rule_source=_rules(), coa=_coa(), abbr=ABBR, entity_type="*")


# ---------------------------------------------------------------------------
# Path 1 — tier1_exact (Layer 3; no rule fires, exact-name match wins)
# ---------------------------------------------------------------------------


def test_path_1_tier1_exact_match(mapper: Mapper) -> None:
    ledger = StubLedger(name="Gadget", root_type="Asset", parent_chain=["Fixed Assets"])
    d = mapper.resolve(ledger)
    assert d.tier == "tier1_exact"
    assert d.proposed_account == "Gadget - TEST"
    assert d.matched_rule is None
    assert d.review_action == "Pending"
    assert not d.anti_pattern_blocked
    assert not d.requires_account_creation


# ---------------------------------------------------------------------------
# Path 2 — tier1_rule (Layer 1; exact_ci positive rule)
# ---------------------------------------------------------------------------


def test_path_2_tier1_rule_exact_ci(mapper: Mapper) -> None:
    ledger = StubLedger(name="Widget", root_type="Asset", parent_chain=["Fixed Assets"])
    d = mapper.resolve(ledger)
    assert d.tier == "tier1_rule"
    assert d.matched_rule == "§TEST.A"
    assert d.proposed_account == "Gadget - TEST"
    assert d.review_action == "Pending"


# ---------------------------------------------------------------------------
# Path 3 — tier1_pattern (Layer 2; regex positive rule)
# ---------------------------------------------------------------------------


def test_path_3_tier1_pattern_regex(mapper: Mapper) -> None:
    ledger = StubLedger(
        name="MSEB Connection 12345",
        root_type="Asset",
        parent_chain=["Current Assets", "Loans & Advances"],
    )
    d = mapper.resolve(ledger)
    assert d.tier == "tier1_pattern"
    assert d.matched_rule == "§TEST.B"
    assert d.proposed_account == "MSEB Deposit - TEST"


# ---------------------------------------------------------------------------
# Path 4 — Pending Account Creation via anti-pattern
# ---------------------------------------------------------------------------


def test_path_4_pending_account_creation_via_anti_pattern(mapper: Mapper) -> None:
    """Tally 'Hostel Fee A/c' (Liability root). Flow:

        - No positive rule fires.
        - Layer-3 exact match finds 'Hostel Fee A/c - TEST'. But the anti-pattern
          §TEST.C forbids that template → refused.
        - Anti-pattern's suggested alternative is 'Hostel Fee Advance Payable -
          TEST' which is NOT in the COA, and creates_erpnext_account=1 →
          emit Pending Account Creation with full new-account fields.
    """
    ledger = StubLedger(
        name="Hostel Fee A/c",
        root_type="Liability",
        parent_chain=["Current Liabilities"],
        opening_cr=19000.0,
    )
    d = mapper.resolve(ledger)
    assert d.tier == "pending_account_creation"
    assert d.review_action == "Pending Account Creation"
    assert d.anti_pattern_blocked is True
    assert d.anti_pattern_rule == "§TEST.C"
    assert "Direct Incomes" in (d.anti_pattern_message or "")
    assert d.requires_account_creation is True
    assert d.new_account_name == "Hostel Fee Advance Payable - TEST"
    assert d.new_account_parent == "Liability For Students - TEST"
    assert d.new_account_root_type == "Liability"
    assert d.new_account_is_group is False
    # The blocked target should be surfaced for reviewer context
    assert "Hostel Fee A/c - TEST" in (d.excluded_reason or "")


# ---------------------------------------------------------------------------
# Path 5 — Structural P&L exclusion
# ---------------------------------------------------------------------------


def test_path_5_pnl_excluded(mapper: Mapper) -> None:
    ledger = StubLedger(
        name="Tuition Fees",
        root_type="Income",
        parent_chain=["Direct Incomes"],
        opening_cr=500000.0,
    )
    d = mapper.resolve(ledger)
    assert d.tier == "excluded_pnl"
    assert d.review_action == "Excluded (P&L)"
    assert d.proposed_account is None
    assert "Income/Expense" in (d.excluded_reason or "")


# ---------------------------------------------------------------------------
# Batch summary — prove map_all + summarize hit every expected tier
# ---------------------------------------------------------------------------


def test_batch_summary_covers_all_5_paths(mapper: Mapper) -> None:
    ledgers = [
        StubLedger(name="Gadget", root_type="Asset", parent_chain=["Fixed Assets"]),
        StubLedger(name="Widget", root_type="Asset", parent_chain=["Fixed Assets"]),
        StubLedger(name="MSEB Connection 12345", root_type="Asset",
                   parent_chain=["Current Assets"]),
        StubLedger(name="Hostel Fee A/c", root_type="Liability",
                   parent_chain=["Current Liabilities"]),
        StubLedger(name="Tuition Fees", root_type="Income",
                   parent_chain=["Direct Incomes"]),
    ]
    s = summarize(mapper.map_all(ledgers))

    assert s["total"] == 5
    assert s["by_tier"]["tier1_exact"] == 1
    assert s["by_tier"]["tier1_rule"] == 1
    assert s["by_tier"]["tier1_pattern"] == 1
    assert s["by_tier"]["pending_account_creation"] == 1
    assert s["by_tier"]["excluded_pnl"] == 1
    assert s["by_tier"].get("unmapped", 0) == 0
    assert s["anti_pattern_blocked_count"] == 1
    assert s["account_creation_requests"] == 1
    assert s["hit_rate"] == 1.0  # zero unmapped
