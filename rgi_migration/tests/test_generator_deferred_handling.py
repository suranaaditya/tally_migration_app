"""Tests for Item 8.5 Stage 2 generator predicate updates.

Covers the two distinct semantics per Q6 resolution:

1. **Deferred rows excluded from contributions** — a Deferred row
   produces no JE line / CSV row.
2. **Deferred rows excluded from refusal counts** — a Deferred row
   does not trigger the generator's refusal gate.

Three generators affected: opening_je, oit_csv, advance_je. Students
CSV is unchanged (operates on parser-level Ledgers, not Mapping
Decisions).
"""

from __future__ import annotations

import pytest

from rgi_migration.generators.opening_je import (
    MainJEGenerationError,
    _select_contributions,
)
from rgi_migration.generators.oit_csv import (
    OITGenerationError,
    _aggregate_per_supplier as _oit_aggregate,
    _enforce_preflight as _oit_preflight,
)
from rgi_migration.generators.advance_je import (
    AdvanceJEGenerationError,
    _aggregate_per_supplier as _advance_aggregate,
    _enforce_preflight as _advance_preflight,
)
from rgi_migration.mapper.mapper import MappedDecision
from rgi_migration.parsers.normalized_schema import Ledger


def _ledger(name: str, tally_id: str = "1", root_type: str = "Asset") -> Ledger:
    """Minimal eligible Ledger for Main JE contribution tests."""
    return Ledger(
        name=name, tally_id=tally_id,
        parent_group="", parent_chain=[],
        root_type=root_type, opening_dr=100.0, opening_cr=0.0,
        net_amount=-100.0, net_side="Dr",
        is_leaf=True, is_student_ledger=False, is_system_account=False,
        is_pnl_closed_zero=False,
    )


def _ledger_index(decisions: list[MappedDecision]) -> dict:
    """Build a ledger_index matching the decisions — every MD gets a
    matching eligible Ledger so `_is_main_je_eligible` passes."""
    return {
        (d.tally_name, d.tally_id): _ledger(
            d.tally_name, d.tally_id or "1", d.tally_root_type,
        )
        for d in decisions
    }


def _md(**kwargs) -> MappedDecision:
    """Build a MappedDecision with sensible defaults for test rows."""
    defaults = {
        "tally_name": "T",
        "tally_id": "1",
        "tally_root_type": "Asset",
        "opening_dr": 100.0,
        "opening_cr": 0.0,
        "tier": "tier1_exact",
        "proposed_account": "Some Acct - ABBR",
        "review_action": "Pending",
        "matched_rule": None,
        "confidence": 1.0,
    }
    defaults.update(kwargs)
    return MappedDecision(**defaults)


# ---------------------------------------------------------------------------
# Main JE — opening_je._select_contributions
# ---------------------------------------------------------------------------


class TestMainJEDeferredHandling:
    """opening_je refuses on tier in {unmapped, pending_account_creation,
    group_refused, anti_pattern_blocked}. Deferred review_action on
    unmapped/pending_account_creation rows must short-circuit the
    refusal path."""

    def test_deferred_unmapped_excluded_from_refusals(self) -> None:
        """Refusal-count semantic: Deferred unmapped row should NOT
        appear in the refusal list."""
        decisions = [
            _md(tier="unmapped", review_action="Deferred",
                proposed_account=None),
        ]
        contributions, refusals = _select_contributions(decisions, _ledger_index(decisions))
        assert len(refusals) == 0, (
            "Deferred unmapped row leaked into refusal list — "
            "Stage 2 predicate didn't catch it"
        )
        assert len(contributions) == 0  # also shouldn't contribute

    def test_deferred_pending_account_creation_excluded_from_refusals(self) -> None:
        decisions = [
            _md(tier="pending_account_creation", review_action="Deferred",
                proposed_account=None),
        ]
        _, refusals = _select_contributions(decisions, _ledger_index(decisions))
        assert len(refusals) == 0

    def test_rejected_unmapped_still_excluded(self) -> None:
        """Regression: Rejected behavior preserved (was pre-Stage-2
        working; just confirming Stage 2 predicate extension didn't
        break it)."""
        decisions = [
            _md(tier="unmapped", review_action="Rejected",
                proposed_account=None),
        ]
        _, refusals = _select_contributions(decisions, _ledger_index(decisions))
        assert len(refusals) == 0

    def test_pending_unmapped_still_refuses(self) -> None:
        """Regression: a genuinely-Pending unmapped row MUST appear in
        the refusal list. If this breaks, Deferred has over-reached."""
        decisions = [
            _md(tier="unmapped", review_action="Pending",
                proposed_account=None),
        ]
        _, refusals = _select_contributions(decisions, _ledger_index(decisions))
        assert len(refusals) == 1

    def test_deferred_does_not_escape_group_refused(self) -> None:
        """group_refused is mapper-structural; reviewer's Deferred
        shouldn't make it silent-skip. Matches the existing comment in
        opening_je.py:158-161 about group_refused staying a refusal
        regardless of review_action."""
        decisions = [
            _md(tier="group_refused", review_action="Deferred",
                proposed_account=None),
        ]
        _, refusals = _select_contributions(decisions, _ledger_index(decisions))
        assert len(refusals) == 1, (
            "Deferred on group_refused unexpectedly silent-skipped — "
            "the narrow exclusion is tier-gated, group_refused must "
            "still refuse per mapper-structural contract"
        )


# ---------------------------------------------------------------------------
# OIT CSV — oit_csv._enforce_preflight + _aggregate_per_supplier
# ---------------------------------------------------------------------------


class TestOITDeferredHandling:
    def test_deferred_pending_supplier_creation_not_in_refusal_count(self) -> None:
        """Refusal-count semantic: Deferred pending_supplier_creation
        row should NOT trigger preflight refusal."""
        decisions = [
            _md(tier="pending_supplier_creation", review_action="Deferred",
                proposed_account=None),
        ]
        # Empty aggregated + no supplier issues + no non-Deferred pending.
        # Should NOT raise.
        _oit_preflight(
            decisions=decisions, aggregated={},
            supplier_index={}, session_name="TEST",
        )

    def test_pending_pending_supplier_creation_still_refuses(self) -> None:
        decisions = [
            _md(tier="pending_supplier_creation", review_action="Pending",
                proposed_account=None),
        ]
        with pytest.raises(OITGenerationError, match="pending creation"):
            _oit_preflight(
                decisions=decisions, aggregated={},
                supplier_index={}, session_name="TEST",
            )

    def test_deferred_resolved_supplier_excluded_from_aggregation(self) -> None:
        """Contribution semantic: Deferred row with a resolved supplier
        must NOT contribute to the OIT aggregation."""
        decisions = [
            _md(
                tier="tier1_supplier_exact",
                review_action="Deferred",
                proposed_supplier="ACME Corp",
                opening_dr=0.0, opening_cr=500.0,
            ),
        ]
        agg = _oit_aggregate(decisions)
        assert "ACME Corp" not in agg, (
            "Deferred resolved-supplier row leaked into OIT aggregation"
        )

    def test_rejected_resolved_supplier_excluded_from_aggregation(self) -> None:
        """Stage 2 Latent-bug-fix: Rejected rows also excluded from
        aggregation now. Pre-Stage-2 they leaked."""
        decisions = [
            _md(
                tier="tier1_supplier_exact",
                review_action="Rejected",
                proposed_supplier="ACME Corp",
                opening_dr=0.0, opening_cr=500.0,
            ),
        ]
        agg = _oit_aggregate(decisions)
        assert "ACME Corp" not in agg

    def test_pending_resolved_supplier_still_aggregated(self) -> None:
        """Regression: non-Deferred-non-Rejected rows still aggregate."""
        decisions = [
            _md(
                tier="tier1_supplier_exact",
                review_action="Pending",
                proposed_supplier="ACME Corp",
                opening_dr=0.0, opening_cr=500.0,
            ),
        ]
        agg = _oit_aggregate(decisions)
        assert "ACME Corp" in agg
        assert agg["ACME Corp"].cr == 500.0


# ---------------------------------------------------------------------------
# Advance JE — advance_je._enforce_preflight + _aggregate_per_supplier
# ---------------------------------------------------------------------------


class TestAdvanceJEDeferredHandling:
    def test_deferred_pending_supplier_creation_not_in_refusal_count(self) -> None:
        decisions = [
            _md(tier="pending_supplier_creation", review_action="Deferred",
                proposed_account=None),
        ]
        _advance_preflight(
            decisions=decisions, aggregated={},
            supplier_index={}, session_name="TEST",
        )  # should not raise

    def test_pending_pending_supplier_creation_still_refuses(self) -> None:
        decisions = [
            _md(tier="pending_supplier_creation", review_action="Pending",
                proposed_account=None),
        ]
        with pytest.raises(AdvanceJEGenerationError, match="pending"):
            _advance_preflight(
                decisions=decisions, aggregated={},
                supplier_index={}, session_name="TEST",
            )

    def test_deferred_resolved_supplier_excluded_from_aggregation(self) -> None:
        decisions = [
            _md(
                tier="tier1_supplier_exact",
                review_action="Deferred",
                proposed_supplier="ACME Corp",
                opening_dr=500.0, opening_cr=0.0,
            ),
        ]
        agg = _advance_aggregate(decisions)
        assert "ACME Corp" not in agg

    def test_rejected_resolved_supplier_excluded_from_aggregation(self) -> None:
        decisions = [
            _md(
                tier="tier1_supplier_exact",
                review_action="Rejected",
                proposed_supplier="ACME Corp",
                opening_dr=500.0, opening_cr=0.0,
            ),
        ]
        agg = _advance_aggregate(decisions)
        assert "ACME Corp" not in agg

    def test_pending_resolved_supplier_still_aggregated(self) -> None:
        decisions = [
            _md(
                tier="tier1_supplier_exact",
                review_action="Pending",
                proposed_supplier="ACME Corp",
                opening_dr=500.0, opening_cr=0.0,
            ),
        ]
        agg = _advance_aggregate(decisions)
        assert "ACME Corp" in agg
        assert agg["ACME Corp"].dr == 500.0
