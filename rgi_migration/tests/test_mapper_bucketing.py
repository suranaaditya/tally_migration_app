"""Unit tests for ``Mapper.resolve`` bucketing order.

Specifically locks down the architectural decision 2026-04-20:
zero-balance ledgers short-circuit at the mapper boundary to a dedicated
``excluded_zero_balance`` tier. These never reach any downstream resolver
(no anti-pattern lookup, no rule match, no exact-name fallback, no party
routing), they never appear in a Journal Entry, and they never appear in
a review queue.

Runs through the real ``Mapper.resolve`` so the order-of-operations
between P&L exclusion, zero-balance exclusion, and downstream resolution
is tested end-to-end.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rgi_migration.mapper.mapper import CoaAccount, Mapper
from rgi_migration.mapper.rule_source import InMemoryRuleSource, Rule


def _make_rule(**kwargs) -> Rule:
    """Mirrors the helper in test_tier1_mapper_self_test.py — defaults the
    long tail of Rule fields so tests only need to specify what they care
    about."""
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


@dataclass
class StubLedger:
    name: str
    root_type: str = "Asset"
    parent_chain: list[str] = field(default_factory=list)
    opening_dr: float = 0.0
    opening_cr: float = 0.0
    tally_id: str | None = "1"


def _mapper(coa: dict[str, CoaAccount] | None = None,
            rules: list[Rule] | None = None) -> Mapper:
    return Mapper(
        InMemoryRuleSource(rules or []),
        coa or {},
        abbr="CACSPU",
        entity_type="college",
    )


def test_zero_balance_short_circuits_before_rule_evaluation() -> None:
    """A ledger with Dr=0 AND Cr=0 must bucket as excluded_zero_balance
    regardless of whether its name would otherwise match a rule."""
    # Rule that would match "Cash" → Cash - CACSPU — but balance is zero,
    # so we expect the zero-balance short-circuit to win before rule eval.
    rule = _make_rule(
        source_section="test-1",
        source_hash="test-hash-1",
        rule_name="Cash match",
        is_anti_pattern=False,
        status="confirmed",
        tally_pattern="Cash",
        tally_match_mode="exact_ci",
        erpnext_account_template="Cash - {ABBR}",
        applies_to_entity_types="*",
    )
    coa = {
        "Cash - CACSPU": CoaAccount(
            name="Cash - CACSPU", parent_account=None, root_type="Asset",
            is_group=False,
        )
    }
    mapper = _mapper(coa=coa, rules=[rule])
    ledger = StubLedger(name="Cash", opening_dr=0, opening_cr=0)

    d = mapper.resolve(ledger)
    assert d.tier == "excluded_zero_balance"
    assert d.proposed_account is None
    assert d.review_action == "Excluded (Zero Balance)"
    assert d.matched_rule is None
    assert d.confidence == 1.0


def test_zero_balance_short_circuits_before_exact_name_fallback() -> None:
    """Exact-name match against COA is skipped too — zero-balance wins first."""
    coa = {
        "Cash - CACSPU": CoaAccount(
            name="Cash - CACSPU", parent_account=None, root_type="Asset",
            is_group=False,
        )
    }
    mapper = _mapper(coa=coa)
    ledger = StubLedger(name="Cash", opening_dr=0, opening_cr=0)
    d = mapper.resolve(ledger)
    assert d.tier == "excluded_zero_balance"
    assert d.proposed_account is None


def test_cr_only_ledger_proceeds_to_normal_flow() -> None:
    """Dr=0 but Cr=100 → NOT excluded. Falls through to exact-name match."""
    coa = {
        "Cash - CACSPU": CoaAccount(
            name="Cash - CACSPU", parent_account=None, root_type="Asset",
            is_group=False,
        )
    }
    mapper = _mapper(coa=coa)
    ledger = StubLedger(name="Cash", opening_dr=0, opening_cr=100.0)
    d = mapper.resolve(ledger)
    assert d.tier == "tier1_exact"
    assert d.proposed_account == "Cash - CACSPU"


def test_dr_only_ledger_proceeds_to_normal_flow() -> None:
    """Dr=100 but Cr=0 → NOT excluded."""
    coa = {
        "Cash - CACSPU": CoaAccount(
            name="Cash - CACSPU", parent_account=None, root_type="Asset",
            is_group=False,
        )
    }
    mapper = _mapper(coa=coa)
    ledger = StubLedger(name="Cash", opening_dr=100.0, opening_cr=0)
    d = mapper.resolve(ledger)
    assert d.tier == "tier1_exact"
    assert d.proposed_account == "Cash - CACSPU"


def test_pnl_takes_precedence_over_zero_balance() -> None:
    """Order-of-operations: a zero-balance Income/Expense ledger buckets as
    excluded_pnl, NOT excluded_zero_balance. The root-type exclusion runs
    first so reviewers investigating P&L export-flag issues see all P&L in
    one bucket regardless of balance."""
    mapper = _mapper()
    ledger = StubLedger(
        name="Salaries", root_type="Expense",
        opening_dr=0, opening_cr=0,
    )
    d = mapper.resolve(ledger)
    assert d.tier == "excluded_pnl"
    assert d.review_action == "Excluded (P&L)"


def test_nonzero_pnl_still_buckets_as_pnl() -> None:
    """Sanity: non-zero P&L → excluded_pnl (existing behaviour, unchanged)."""
    mapper = _mapper()
    ledger = StubLedger(
        name="Stale Sales", root_type="Income",
        opening_dr=0, opening_cr=777.77,
    )
    d = mapper.resolve(ledger)
    assert d.tier == "excluded_pnl"


def test_excluded_zero_balance_has_no_proposed_account() -> None:
    """No JE line should ever be built against a zero-balance decision."""
    mapper = _mapper()
    ledger = StubLedger(name="Some Ledger", opening_dr=0, opening_cr=0)
    d = mapper.resolve(ledger)
    assert d.tier == "excluded_zero_balance"
    assert d.proposed_account is None
    assert d.requires_account_creation is False
    assert d.requires_supplier_creation is False
    assert d.anti_pattern_blocked is False


def test_excluded_zero_balance_preserves_tally_context() -> None:
    """Tally-side fields carry through for downstream display/audit."""
    mapper = _mapper()
    ledger = StubLedger(
        name="Advance To C S I Student Chapter",
        root_type="Asset",
        parent_chain=["Current Assets"],
        opening_dr=0, opening_cr=0,
        tally_id="1173",
    )
    d = mapper.resolve(ledger)
    assert d.tier == "excluded_zero_balance"
    assert d.tally_name == "Advance To C S I Student Chapter"
    assert d.tally_id == "1173"
    assert d.tally_root_type == "Asset"
    assert d.opening_dr == 0
    assert d.opening_cr == 0


def test_summarize_surfaces_excluded_zero_balance_in_by_tier() -> None:
    """Mapper.summarize must report the new tier alongside the existing ones."""
    from rgi_migration.mapper.mapper import summarize

    mapper = _mapper()
    ledgers = [
        StubLedger(name="Zero A", opening_dr=0, opening_cr=0),
        StubLedger(name="Zero B", opening_dr=0, opening_cr=0),
        StubLedger(name="Nonzero C", opening_dr=10.0, opening_cr=0),
    ]
    decisions = mapper.map_all(ledgers)
    summary = summarize(decisions)
    assert summary["by_tier"]["excluded_zero_balance"] == 2
    # The non-zero one falls through to unmapped (no rule / no COA entry).
    assert summary["by_tier"]["unmapped"] == 1
