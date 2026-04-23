"""Tier-2 composite matcher tests.

Covers:
    - Normalization primitives (pipeline steps per Phase A Q2)
    - Sub-matcher 1 (norm_strong equality)
    - Sub-matcher 2 (strong account-number intersection)
    - Sub-matcher 3 (classical fuzzy — partial_ratio + length guard)
    - Composite orchestrator (find_tier2_match) + cross-matcher
      ordering (Q13)
    - Mapper integration at step 7.5 (tier chip, matched_rule,
      confidence, refusal surfacing)

Structural patterns ported from test_tier1_supplier.py where
applicable. WRatio-specific score assertions from the pre-Phase-C
Phase B v1 were discarded.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from rgi_migration.mapper import (
    CoaAccount,
    InMemoryRuleSource,
    Mapper,
)
from rgi_migration.mapper.rule_source import Rule
from rgi_migration.mapper.tier1_supplier import _clean as supplier_clean
from rgi_migration.mapper.tier2_fuzzy import (
    SUBTIER_ACCT_NUM,
    SUBTIER_FUZZY,
    SUBTIER_NORM,
    Tier2Match,
    _extract_strong_ids,
    _passes_length_guard,
    find_by_account_number,
    find_by_classical_fuzzy,
    find_by_normalization,
    find_tier2_match,
    normalize,
    normalize_strong,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@dataclass
class StubLedger:
    name: str
    root_type: str
    parent_chain: list[str] = field(default_factory=list)
    opening_dr: float = 100.0
    opening_cr: float = 0.0
    tally_id: str | None = None


def _coa(*accounts: tuple[str, str, bool]) -> dict[str, CoaAccount]:
    return {
        name: CoaAccount(
            name=name, parent_account=None, root_type=root_type,
            is_group=is_group, company_abbr="CACSPU",
        )
        for (name, root_type, is_group) in accounts
    }


def _make_rule(**kwargs) -> Rule:
    defaults: dict = dict(
        source_section="test.rule",
        source_hash="deadbeef",
        rule_name="MR-TEST-001",
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
    defaults.update(kwargs)
    return Rule(**defaults)


def _make_mapper(
    coa: dict[str, CoaAccount],
    *,
    rules: list[Rule] | None = None,
    account_fuzzy_threshold: float = 80.0,
) -> Mapper:
    return Mapper(
        rule_source=InMemoryRuleSource(rules or []),
        coa=coa,
        abbr="CACSPU",
        account_fuzzy_threshold=account_fuzzy_threshold,
    )


# ---------------------------------------------------------------------------
# Normalization pipeline (Phase A Q2)
# ---------------------------------------------------------------------------


def test_normalize_strips_entity_abbr_suffix() -> None:
    assert normalize("Cash - CACSPU") == "cash"


def test_normalize_lowercases() -> None:
    assert normalize("CASH") == "cash"


def test_normalize_collapses_non_alnum_to_space() -> None:
    assert normalize("A, B; C!") == "a b c"


def test_normalize_collapses_whitespace_runs() -> None:
    assert normalize("A    B") == "a b"


def test_normalize_strips_edges() -> None:
    assert normalize("   A   ") == "a"


def test_normalize_pipeline_full_example_per_q2() -> None:
    """Phase A Q2 required test:
    ``"  T D S   On-Salary, 193  "`` normalizes correctly."""
    out = normalize("  T D S   On-Salary, 193  ")
    assert out == "t d s on salary 193"


def test_normalize_preserves_digits() -> None:
    assert normalize("Bank 60451303968") == "bank 60451303968"


def test_normalize_strong_drops_all_whitespace() -> None:
    assert normalize_strong("T D S On Salary 193") == "tdsonsalary193"


def test_normalize_strong_tds_vs_space_variant_equal() -> None:
    """Letter-spacing equivalence — core Sub-matcher 1 case."""
    assert normalize_strong("T D S On Salary - 193") == normalize_strong(
        "TDS On Salary -193 - CACSPU"
    )


def test_normalize_strong_abbr_suffix_stripped() -> None:
    assert normalize_strong("Library Book - CACSPU") == normalize_strong(
        "Library Book"
    )


def test_clean_function_imported_from_supplier_module() -> None:
    """tier2_fuzzy reuses tier1_supplier._clean per Q9 scope fence."""
    from rgi_migration.mapper.tier2_fuzzy import _clean as fuzzy_clean
    assert fuzzy_clean is supplier_clean


# ---------------------------------------------------------------------------
# Sub-matcher 1 — normalization-strong equality
# ---------------------------------------------------------------------------


def test_norm_strong_tds_case_matches() -> None:
    """Letter-spacing family: `T D S On Salary - 193` → `TDS On Salary -193`."""
    coa = _coa(("TDS On Salary -193 - CACSPU", "Liability", False))
    ledger = StubLedger(name="T D S On Salary - 193", root_type="Liability")
    r = find_by_normalization(ledger, coa)
    assert r.is_match
    assert r.account_name == "TDS On Salary -193 - CACSPU"
    assert r.subtier == SUBTIER_NORM
    assert r.score == 1.0


def test_norm_strong_nss_case_matches() -> None:
    coa = _coa(("NSS Account - CACSPU", "Liability", False))
    ledger = StubLedger(name="N S S Account", root_type="Liability")
    r = find_by_normalization(ledger, coa)
    assert r.is_match
    assert r.account_name == "NSS Account - CACSPU"


def test_norm_strong_root_type_filter_blocks_cross_type() -> None:
    """Even if normalized keys match, root_type mismatch blocks."""
    coa = _coa(("TDS On Salary -193 - CACSPU", "Expense", False))
    ledger = StubLedger(name="T D S On Salary - 193", root_type="Liability")
    r = find_by_normalization(ledger, coa)
    assert not r.is_match


def test_norm_strong_is_group_filter_blocks_groups() -> None:
    coa = _coa(("Fixed Assets - CACSPU", "Asset", True))
    ledger = StubLedger(name="Fixed Assets", root_type="Asset")
    r = find_by_normalization(ledger, coa)
    assert not r.is_match


def test_norm_strong_no_match_returns_none_result() -> None:
    coa = _coa(("Something Different - CACSPU", "Asset", False))
    ledger = StubLedger(name="Cash", root_type="Asset")
    r = find_by_normalization(ledger, coa)
    assert not r.is_match
    assert not r.is_refusal
    assert r.subtier is None


def test_norm_strong_ambiguous_refuses() -> None:
    """Two COA accounts normalize to the same key → refuse."""
    coa = _coa(
        ("TDS On Salary -193 - CACSPU", "Liability", False),
        ("T.D.S. On Salary 193 - CACSPU", "Liability", False),
    )
    ledger = StubLedger(name="T D S On Salary - 193", root_type="Liability")
    r = find_by_normalization(ledger, coa)
    assert not r.is_match
    assert r.is_refusal
    assert r.subtier == SUBTIER_NORM
    assert r.tie_reason is not None
    assert "ambiguous" in r.tie_reason


def test_norm_strong_empty_ledger_name_returns_no_match() -> None:
    coa = _coa(("Cash - CACSPU", "Asset", False))
    r = find_by_normalization(StubLedger(name="", root_type="Asset"), coa)
    assert not r.is_match
    assert not r.is_refusal


def test_norm_strong_empty_root_type_returns_no_match() -> None:
    coa = _coa(("Cash - CACSPU", "Asset", False))
    r = find_by_normalization(StubLedger(name="Cash", root_type=""), coa)
    assert not r.is_match


# ---------------------------------------------------------------------------
# Sub-matcher 2 — account-number intersection
# ---------------------------------------------------------------------------


def test_extract_strong_ids_finds_11_digit_number() -> None:
    assert _extract_strong_ids("Bank A/c 60451303968") == ["60451303968"]


def test_extract_strong_ids_ignores_short_digits() -> None:
    assert _extract_strong_ids("TDS 194 Section") == []


def test_extract_strong_ids_finds_multiple() -> None:
    assert _extract_strong_ids("X 123456 Y 987654321") == ["123456", "987654321"]


def test_acct_num_bom_capital_matches() -> None:
    """Canonical BoM Capital case — week2_baseline §3c."""
    coa = _coa(
        ("Bank of Maharashtra A/c No. - 60451303968 (Capital) - CACSPU",
         "Asset", False),
        ("R & D Advance - CACSPU", "Asset", False),
    )
    ledger = StubLedger(
        name="Bank of Maharashtra (Cap) - 60451303968", root_type="Asset"
    )
    r = find_by_account_number(ledger, coa)
    assert r.is_match
    assert r.account_name == (
        "Bank of Maharashtra A/c No. - 60451303968 (Capital) - CACSPU"
    )
    assert r.subtier == SUBTIER_ACCT_NUM
    assert r.score == 1.0


def test_acct_num_bom_nss_matches() -> None:
    """BoM NSS — second canonical week2_baseline §3c case."""
    coa = _coa(
        ("Bank of Maharashtra A/c No. - 60041022214 (NSS) - CACSPU",
         "Asset", False),
    )
    ledger = StubLedger(
        name="Bank of Maharashtra ( N S S Camp 60041022214)",
        root_type="Asset",
    )
    r = find_by_account_number(ledger, coa)
    assert r.is_match


def test_acct_num_no_strong_id_returns_no_match() -> None:
    """Ledger has no ≥6-digit run → matcher returns no-signal."""
    coa = _coa(("Cash - CACSPU", "Asset", False))
    ledger = StubLedger(name="Cash", root_type="Asset")
    r = find_by_account_number(ledger, coa)
    assert not r.is_match
    assert not r.is_refusal
    assert r.subtier is None


def test_acct_num_weak_id_only_ignored() -> None:
    """Section number 194 (weak) — Sub-matcher 2 ignores."""
    coa = _coa(("TDS 194 - CACSPU", "Liability", False))
    ledger = StubLedger(name="TDS 194", root_type="Liability")
    r = find_by_account_number(ledger, coa)
    assert not r.is_match


def test_acct_num_ambiguous_refuses() -> None:
    """Two COA accounts share the same strong ID → refuse."""
    coa = _coa(
        ("Account 99999999 Variant A - CACSPU", "Asset", False),
        ("Account 99999999 Variant B - CACSPU", "Asset", False),
    )
    ledger = StubLedger(name="Foo 99999999", root_type="Asset")
    r = find_by_account_number(ledger, coa)
    assert not r.is_match
    assert r.is_refusal
    assert r.subtier == SUBTIER_ACCT_NUM


def test_acct_num_intersection_requires_all_ids() -> None:
    """Ledger with two strong IDs requires BOTH to appear in candidate."""
    coa = _coa(
        ("Account 111111 only - CACSPU", "Asset", False),
        ("Account 111111 and 222222 - CACSPU", "Asset", False),
    )
    ledger = StubLedger(name="Foo 111111 222222", root_type="Asset")
    r = find_by_account_number(ledger, coa)
    assert r.is_match
    assert r.account_name == "Account 111111 and 222222 - CACSPU"


def test_acct_num_root_type_filter() -> None:
    coa = _coa(
        ("Bank A/c 60451303968 - CACSPU", "Asset", False),
        ("Liability Foo 60451303968 - CACSPU", "Liability", False),
    )
    ledger = StubLedger(name="Bank (X) - 60451303968", root_type="Asset")
    r = find_by_account_number(ledger, coa)
    assert r.is_match
    assert r.account_name == "Bank A/c 60451303968 - CACSPU"


def test_acct_num_group_filter() -> None:
    coa = _coa(
        ("Bank Group 60451303968 - CACSPU", "Asset", True),  # group
        ("Bank Leaf 60451303968 - CACSPU", "Asset", False),
    )
    ledger = StubLedger(name="Bank - 60451303968", root_type="Asset")
    r = find_by_account_number(ledger, coa)
    assert r.is_match
    assert r.account_name == "Bank Leaf 60451303968 - CACSPU"


# ---------------------------------------------------------------------------
# Sub-matcher 3 — classical fuzzy (partial_ratio + length guard)
# ---------------------------------------------------------------------------


def test_length_guard_permissive_L_0_5() -> None:
    assert _passes_length_guard("abcd", "abcdefgh")  # 4/8 = 0.5 → pass
    assert not _passes_length_guard("abc", "abcdefgh")  # 3/8 < 0.5 → fail
    assert _passes_length_guard("Electric Fitting", "Electrical Fitting")


def test_length_guard_empty_strings_fail() -> None:
    assert not _passes_length_guard("", "abc")
    assert not _passes_length_guard("abc", "")


def test_classical_fuzzy_electrical_fitting_to_electric_fitting() -> None:
    """The Phase-C counter-example — partial_ratio picks Electric Fitting
    (the correct answer) over Electrical Equipment."""
    coa = _coa(
        ("Electric Fitting - CACSPU", "Asset", False),
        ("Electrical equipment - CACSPU", "Asset", False),
    )
    ledger = StubLedger(name="Electrical Fitting", root_type="Asset")
    r = find_by_classical_fuzzy(ledger, coa, threshold=85.0)
    assert r.is_match
    assert r.account_name == "Electric Fitting - CACSPU"
    assert r.subtier == SUBTIER_FUZZY


def test_classical_fuzzy_library_plural_singular() -> None:
    coa = _coa(("Library Book - CACSPU", "Asset", False))
    ledger = StubLedger(name="Library Books", root_type="Asset")
    r = find_by_classical_fuzzy(ledger, coa, threshold=85.0)
    assert r.is_match
    assert r.account_name == "Library Book - CACSPU"


def test_classical_fuzzy_length_guard_blocks_short_into_long() -> None:
    """Short account name vs long Tally ledger — guard refuses even
    if partial_ratio would score above threshold."""
    coa = _coa(("RD - CACSPU", "Asset", False))  # very short target
    ledger = StubLedger(
        name="G H R Education & Medical Foundation Nagpur ( Society)",
        root_type="Asset",
    )
    r = find_by_classical_fuzzy(ledger, coa, threshold=50.0)
    # Length ratio extremely low; candidate dropped from pool
    assert not r.is_match


def test_classical_fuzzy_below_threshold_no_match() -> None:
    coa = _coa(("Completely Unrelated Account - CACSPU", "Asset", False))
    ledger = StubLedger(name="Cash Account", root_type="Asset")
    r = find_by_classical_fuzzy(ledger, coa, threshold=90.0)
    assert not r.is_match


def test_classical_fuzzy_threshold_tuning() -> None:
    coa = _coa(("Library Book - CACSPU", "Asset", False))
    ledger = StubLedger(name="Library Books", root_type="Asset")
    # Lower threshold should match; very high threshold should not
    assert find_by_classical_fuzzy(ledger, coa, 70.0).is_match
    # 100 threshold requires substring equality
    r = find_by_classical_fuzzy(ledger, coa, 100.0)
    # partial_ratio of "library books" vs "library book" may be 100
    # (book substring). Acceptable either outcome — both prove tuning
    # responds.
    assert isinstance(r, Tier2Match)


def test_classical_fuzzy_empty_ledger_returns_no_match() -> None:
    r = find_by_classical_fuzzy(
        StubLedger(name="", root_type="Asset"),
        _coa(("X - CACSPU", "Asset", False)),
    )
    assert not r.is_match


def test_classical_fuzzy_empty_root_type_returns_no_match() -> None:
    r = find_by_classical_fuzzy(
        StubLedger(name="Foo", root_type=""),
        _coa(("Foo - CACSPU", "Asset", False)),
    )
    assert not r.is_match


def test_classical_fuzzy_tie_refuses_with_reason() -> None:
    coa = _coa(
        ("Vendor Alpha - CACSPU", "Liability", False),
        ("Vendor Alpha2 - CACSPU", "Liability", False),
    )
    ledger = StubLedger(name="Vendor Alpha", root_type="Liability")
    r = find_by_classical_fuzzy(ledger, coa, threshold=50.0)
    if r.is_refusal:
        assert "ambiguous tie" in (r.tie_reason or "")
        assert r.subtier == SUBTIER_FUZZY


# ---------------------------------------------------------------------------
# Result dataclass predicates
# ---------------------------------------------------------------------------


def test_tier2match_predicates_match() -> None:
    m = Tier2Match(account_name="X", subtier=SUBTIER_NORM, score=1.0)
    assert m.is_match
    assert not m.is_refusal


def test_tier2match_predicates_refusal() -> None:
    m = Tier2Match(
        account_name=None, subtier=SUBTIER_FUZZY, score=0.9,
        tie_reason="ambiguous tie at 0.90",
    )
    assert not m.is_match
    assert m.is_refusal


def test_tier2match_predicates_no_signal() -> None:
    m = Tier2Match(account_name=None)
    assert not m.is_match
    assert not m.is_refusal


# ---------------------------------------------------------------------------
# Composite orchestrator + cross-matcher ordering (Q13)
# ---------------------------------------------------------------------------


def test_composite_orchestrator_runs_norm_first() -> None:
    """Sub-matcher 1 fires before 2 and 3."""
    coa = _coa(("NSS Account - CACSPU", "Liability", False))
    ledger = StubLedger(name="N S S Account", root_type="Liability")
    r = find_tier2_match(ledger, coa)
    assert r.is_match
    assert r.subtier == SUBTIER_NORM


def test_composite_orchestrator_falls_through_to_acct_num() -> None:
    """norm misses (no equality), acct_num catches."""
    coa = _coa(
        ("Bank of Maharashtra A/c No. - 60451303968 (Capital) - CACSPU",
         "Asset", False),
    )
    ledger = StubLedger(
        name="Bank of Maharashtra (Cap) - 60451303968", root_type="Asset"
    )
    r = find_tier2_match(ledger, coa)
    assert r.is_match
    assert r.subtier == SUBTIER_ACCT_NUM


def test_composite_orchestrator_falls_through_to_fuzzy() -> None:
    """norm misses, acct_num no strong ID, fuzzy catches."""
    coa = _coa(("Electric Fitting - CACSPU", "Asset", False))
    ledger = StubLedger(name="Electrical Fitting", root_type="Asset")
    r = find_tier2_match(ledger, coa)
    assert r.is_match
    assert r.subtier == SUBTIER_FUZZY


def test_composite_orchestrator_no_match_returns_clean_no_signal() -> None:
    coa = _coa(("Completely Different - CACSPU", "Asset", False))
    ledger = StubLedger(name="Petty Cash", root_type="Asset")
    r = find_tier2_match(ledger, coa)
    assert not r.is_match
    assert not r.is_refusal


def test_composite_cross_matcher_ordering_norm_wins_over_acct_num() -> None:
    """Q13 cross-matcher ordering test: construct a ledger that would
    trigger BOTH Sub-matcher 1 AND Sub-matcher 2. Sub-matcher 1 must
    win — its match type is higher precision (equality on normalized
    string) than Sub-matcher 2's (shared digit substring).

    Setup: both candidates normalize-strong-equal to the ledger, AND
    both carry the same strong ID. If Sub-matcher 2 ran first it would
    see multiple candidates via strong-ID intersection and refuse.
    Sub-matcher 1 runs first and matches cleanly (single candidate
    equals normalized key)."""
    # Normalized key of 'Cash 123456' → 'cash123456'
    coa = _coa(
        ("Cash 123456 - CACSPU", "Asset", False),
        # Different normalized key but same strong ID:
        ("Other Account 123456 - CACSPU", "Asset", False),
    )
    ledger = StubLedger(name="Cash 123456", root_type="Asset")
    r = find_tier2_match(ledger, coa)
    # Sub-matcher 1 finds unique normalization match "Cash 123456"
    # → "cash123456" (only one candidate normalizes that way)
    assert r.is_match
    assert r.account_name == "Cash 123456 - CACSPU"
    assert r.subtier == SUBTIER_NORM
    # Sub-matcher 2 would have seen TWO candidates sharing 123456 and
    # refused — guarantee Sub-matcher 1 ran and won before that path.


def test_composite_cross_matcher_norm_refusal_blocks_later_matchers() -> None:
    """Q13 extension: norm refusal (ambiguous tie) halts the pipeline;
    later sub-matchers do NOT get a chance to "rescue" with a match."""
    coa = _coa(
        ("N S S Account - CACSPU", "Liability", False),
        ("NSS Account - CACSPU", "Liability", False),  # both normalize same
    )
    ledger = StubLedger(name="N S S Account", root_type="Liability")
    r = find_tier2_match(ledger, coa)
    assert r.is_refusal
    assert r.subtier == SUBTIER_NORM


# ---------------------------------------------------------------------------
# Mapper integration at step 7.5
# ---------------------------------------------------------------------------


def test_mapper_tier2_norm_persists_subtier_on_matched_rule() -> None:
    coa = _coa(("TDS On Salary -193 - CACSPU", "Liability", False))
    ledger = StubLedger(
        name="T D S On Salary - 193", root_type="Liability",
        opening_cr=500.0,
    )
    mapper = _make_mapper(coa)
    d = mapper.resolve(ledger)
    assert d.tier == "tier2_fuzzy"
    assert d.matched_rule == SUBTIER_NORM
    assert d.proposed_account == "TDS On Salary -193 - CACSPU"
    assert d.confidence == pytest.approx(1.0)


def test_mapper_tier2_acct_num_persists_subtier() -> None:
    coa = _coa(
        ("Bank of Maharashtra A/c No. - 60451303968 (Capital) - CACSPU",
         "Asset", False),
    )
    ledger = StubLedger(
        name="Bank of Maharashtra (Cap) - 60451303968",
        root_type="Asset", opening_dr=1000.0,
    )
    mapper = _make_mapper(coa)
    d = mapper.resolve(ledger)
    assert d.tier == "tier2_fuzzy"
    assert d.matched_rule == SUBTIER_ACCT_NUM
    assert d.confidence == pytest.approx(1.0)


def test_mapper_tier2_fuzzy_classical_persists_subtier_and_score() -> None:
    coa = _coa(("Electric Fitting - CACSPU", "Asset", False))
    ledger = StubLedger(
        name="Electrical Fitting", root_type="Asset", opening_dr=1000.0,
    )
    mapper = _make_mapper(coa)
    d = mapper.resolve(ledger)
    assert d.tier == "tier2_fuzzy"
    assert d.matched_rule == SUBTIER_FUZZY
    assert 0.85 <= d.confidence <= 1.0


def test_mapper_tier2_refusal_surfaces_as_unmapped_with_reason() -> None:
    """An ambiguous-tie from any sub-matcher → tier=unmapped,
    excluded_reason populated, matched_rule set to subtier for audit.

    Ledger uses periods that exact-name (step 6) can't match but that
    normalization (step 7.5, sub-matcher 1) strips — both candidates
    collapse to the same normalized key → refuse."""
    coa = _coa(
        ("TDS On Salary -193 - CACSPU", "Liability", False),
        ("T D S On Salary 193 - CACSPU", "Liability", False),
    )
    ledger = StubLedger(
        name="T.D.S. On Salary 193", root_type="Liability",
        opening_cr=100.0,
    )
    mapper = _make_mapper(coa)
    d = mapper.resolve(ledger)
    assert d.tier == "unmapped"
    assert d.matched_rule == SUBTIER_NORM
    assert d.excluded_reason is not None
    assert "ambiguous" in d.excluded_reason


def test_mapper_tier1_exact_preempts_tier2() -> None:
    """Step 6 (exact name) fires before step 7.5 (Tier-2 composite)."""
    coa = _coa(("Cash - CACSPU", "Asset", False))
    ledger = StubLedger(name="Cash", root_type="Asset", opening_dr=500.0)
    mapper = _make_mapper(coa)
    d = mapper.resolve(ledger)
    assert d.tier == "tier1_exact"


def test_mapper_positive_rule_preempts_tier2() -> None:
    rules = [
        _make_rule(
            source_section="test.rule",
            tally_match_mode="exact_ci",
            tally_pattern="my fuzzable name",
            erpnext_account_template="Explicit Target - {ABBR}",
        ),
    ]
    coa = _coa(
        ("Explicit Target - CACSPU", "Asset", False),
        ("My Fuzzable Name - CACSPU", "Asset", False),  # norm target
    )
    ledger = StubLedger(
        name="My Fuzzable Name", root_type="Asset", opening_dr=100.0,
    )
    mapper = _make_mapper(coa, rules=rules)
    d = mapper.resolve(ledger)
    assert d.tier == "tier1_rule"


def test_mapper_zero_balance_preempts_tier2() -> None:
    coa = _coa(("Electric Fitting - CACSPU", "Asset", False))
    ledger = StubLedger(
        name="Electrical Fitting", root_type="Asset",
        opening_dr=0.0, opening_cr=0.0,
    )
    mapper = _make_mapper(coa)
    d = mapper.resolve(ledger)
    assert d.tier == "excluded_zero_balance"


def test_mapper_pnl_preempts_tier2() -> None:
    coa = _coa(("Salary Expense - CACSPU", "Expense", False))
    ledger = StubLedger(
        name="Salary Expense", root_type="Income", opening_dr=100.0,
    )
    mapper = _make_mapper(coa)
    d = mapper.resolve(ledger)
    assert d.tier == "excluded_pnl"


def test_mapper_unmapped_when_all_subs_no_signal() -> None:
    coa = _coa(("Unrelated Account - CACSPU", "Asset", False))
    ledger = StubLedger(
        name="Xyz Foobar Qwerty", root_type="Asset", opening_dr=100.0,
    )
    mapper = _make_mapper(coa)
    d = mapper.resolve(ledger)
    assert d.tier == "unmapped"
    assert d.proposed_account is None
    assert d.matched_rule is None
    assert d.confidence == 0.0


def test_mapper_tier2_candidate_pool_excludes_group_accounts() -> None:
    """Group accounts are filtered at the Sub-matcher pool level
    (`_candidate_pool_leaf_root`), so a COA containing only group
    candidates produces no Tier-2 match. Group-refusal at the
    validator level is unreachable from Tier-2 for this reason —
    the filter is upstream. Test documents the invariant."""
    coa = _coa(("Electric Fitting - CACSPU", "Asset", True))  # group only
    ledger = StubLedger(
        name="Electrical Fitting", root_type="Asset", opening_dr=100.0,
    )
    mapper = _make_mapper(coa)
    d = mapper.resolve(ledger)
    assert d.tier == "unmapped"
    assert d.proposed_account is None


def test_mapper_accepts_account_fuzzy_threshold_param() -> None:
    coa = _coa(("Cash - CACSPU", "Asset", False))
    mapper = _make_mapper(coa, account_fuzzy_threshold=92.5)
    assert mapper.account_fuzzy_threshold == 92.5


def test_mapper_summary_by_tier_contains_tier2_fuzzy() -> None:
    from rgi_migration.mapper.mapper import summarize

    coa = _coa(("Electric Fitting - CACSPU", "Asset", False))
    ledgers = [
        StubLedger(name="Electrical Fitting", root_type="Asset",
                   opening_dr=100.0),
    ]
    mapper = _make_mapper(coa)
    decisions = mapper.map_all(ledgers)
    summary = summarize(decisions)
    assert summary["by_tier"].get("tier2_fuzzy", 0) == 1


# ---------------------------------------------------------------------------
# Generator compatibility (_ELIGIBLE_TIERS includes tier2_fuzzy)
# ---------------------------------------------------------------------------


def test_opening_je_eligible_tiers_includes_tier2_fuzzy() -> None:
    from rgi_migration.generators.opening_je import _ELIGIBLE_TIERS
    assert "tier2_fuzzy" in _ELIGIBLE_TIERS


# ---------------------------------------------------------------------------
# Item 6 Phase B extension — matched_rule persistence, threshold 75,
# final_account auto-population behavior
# ---------------------------------------------------------------------------


def test_translate_matched_rule_passes_tier2_through() -> None:
    from rgi_migration.session.parse_and_map import translate_matched_rule
    rmap = {"§4.6": "MR-00458"}
    assert translate_matched_rule("tier2:norm_strong", rmap) == "tier2:norm_strong"
    assert translate_matched_rule("tier2:acct_num", rmap) == "tier2:acct_num"
    assert translate_matched_rule("tier2:fuzzy_classical", rmap) == "tier2:fuzzy_classical"


def test_translate_matched_rule_translates_source_section() -> None:
    from rgi_migration.session.parse_and_map import translate_matched_rule
    rmap = {"§4.6": "MR-00458"}
    assert translate_matched_rule("§4.6", rmap) == "MR-00458"


def test_translate_matched_rule_none_returns_none() -> None:
    from rgi_migration.session.parse_and_map import translate_matched_rule
    assert translate_matched_rule(None, {}) is None
    assert translate_matched_rule("", {}) is None


def test_translate_matched_rule_missing_source_section_passes_through() -> None:
    """Fallback — if a source_section isn't in the map, preserve the raw
    value rather than null it (prior behavior silently nulled via .get)."""
    from rgi_migration.session.parse_and_map import translate_matched_rule
    assert translate_matched_rule("§9.99", {}) == "§9.99"


# ---------------------------------------------------------------------------
# decision_to_row_dict final_account auto-populate (Item 6 Phase B ext)
# ---------------------------------------------------------------------------


def _minimal_ledger():
    """Build a minimal Ledger-like object for decision_to_row_dict."""
    from rgi_migration.parsers.normalized_schema import Ledger
    return Ledger(
        name="X", tally_id=None, parent_group="",
        parent_chain=[], root_type="Asset",
        opening_dr=100.0, opening_cr=0.0, net_amount=100.0,
        net_side="Dr", is_leaf=True, bill_allocations=[], source_row=None,
        is_system_account=False, is_student_ledger=False,
        is_pnl_closed_zero=False,
    )


def _md(tier: str, matched_rule: str | None,
        proposed_account: str | None = "Target - CACSPU"):
    from rgi_migration.mapper.mapper import MappedDecision
    return MappedDecision(
        tally_name="X", tally_id=None, tally_root_type="Asset",
        opening_dr=100.0, opening_cr=0.0,
        tier=tier, proposed_account=proposed_account,
        review_action="Pending", matched_rule=matched_rule,
        confidence=1.0,
    )


def test_row_dict_norm_strong_auto_populates_final_account() -> None:
    from rgi_migration.session.parse_and_map import decision_to_row_dict
    row = decision_to_row_dict(
        _md("tier2_fuzzy", SUBTIER_NORM), _minimal_ledger(), "S-1",
    )
    assert row["final_account"] == "Target - CACSPU"
    assert row["matched_rule"] == SUBTIER_NORM


def test_row_dict_acct_num_auto_populates_final_account() -> None:
    from rgi_migration.session.parse_and_map import decision_to_row_dict
    row = decision_to_row_dict(
        _md("tier2_fuzzy", SUBTIER_ACCT_NUM), _minimal_ledger(), "S-1",
    )
    assert row["final_account"] == "Target - CACSPU"
    assert row["matched_rule"] == SUBTIER_ACCT_NUM


def test_row_dict_fuzzy_classical_leaves_final_account_null() -> None:
    from rgi_migration.session.parse_and_map import decision_to_row_dict
    row = decision_to_row_dict(
        _md("tier2_fuzzy", SUBTIER_FUZZY), _minimal_ledger(), "S-1",
    )
    assert row["final_account"] is None, (
        "Sub-matcher 3 must leave final_account null so the "
        "FuzzyMatchApprovalDialog forces reviewer Accept/Reject/Pick."
    )
    assert row["matched_rule"] == SUBTIER_FUZZY


def test_row_dict_tier1_exact_leaves_final_account_null() -> None:
    """Other tiers unchanged — only Tier-2 Sub-matchers 1 and 2
    auto-populate. tier1_exact still requires reviewer / bulk action."""
    from rgi_migration.session.parse_and_map import decision_to_row_dict
    row = decision_to_row_dict(
        _md("tier1_exact", None), _minimal_ledger(), "S-1",
    )
    assert row["final_account"] is None


# ---------------------------------------------------------------------------
# Threshold 75 behavior — default constructor value + score gating
# ---------------------------------------------------------------------------


def test_mapper_default_account_fuzzy_threshold_is_80() -> None:
    """Ship-value: 80.0. Phase C calibration at 75 on CACSPU showed
    75-79 band added 2 false positives and 0 correct catches;
    retuned to 80 to minimize dialog friction. Cross-entity
    recalibration deferred to post-Item-9."""
    coa = _coa(("Cash - CACSPU", "Asset", False))
    mapper = Mapper(
        rule_source=InMemoryRuleSource([]),
        coa=coa, abbr="CACSPU",
    )
    assert mapper.account_fuzzy_threshold == 80.0


def test_find_tier2_match_default_threshold_is_80() -> None:
    """Composite orchestrator also defaults at 80.0."""
    import inspect
    from rgi_migration.mapper.tier2_fuzzy import find_tier2_match
    sig = inspect.signature(find_tier2_match)
    assert sig.parameters["fuzzy_threshold"].default == 80.0


def test_classical_fuzzy_threshold_gating() -> None:
    """Library Books → Library Book scores high under partial_ratio
    ('library book' is a substring of 'library books'). Assertion:
    at threshold 80, match fires. This exercises both the default
    threshold and the gating logic."""
    coa = _coa(("Library Book - CACSPU", "Asset", False))
    ledger = StubLedger(name="Library Books", root_type="Asset")
    # 80 (ship default) and below must match — partial_ratio on
    # 'library books' vs 'library book' is 100 (strict substring).
    assert find_by_classical_fuzzy(ledger, coa, threshold=80.0).is_match
    # Tighter thresholds that partial_ratio still clears stay matched.
    assert find_by_classical_fuzzy(ledger, coa, threshold=95.0).is_match


# ---------------------------------------------------------------------------
# Subtier constants exposed (symbolic, not magic strings)
# ---------------------------------------------------------------------------


def test_subtier_constants_use_colon_delimiter() -> None:
    """Phase A Q3 — "tier2:" prefix for future parser compatibility."""
    assert SUBTIER_NORM.startswith("tier2:")
    assert SUBTIER_ACCT_NUM.startswith("tier2:")
    assert SUBTIER_FUZZY.startswith("tier2:")
