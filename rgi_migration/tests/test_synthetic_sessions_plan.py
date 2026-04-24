"""Pure-Python tests for `rgi_migration.session.synthetic_sessions`
helpers that don't require Frappe.

The create/teardown functions themselves need a bench and are
exercised via Phase C bench smoke. Here we cover the pure
`_build_tier_plan` helper + `_decision_row` structural shape
(which is a pure dict builder with no Frappe dependency).
"""

from __future__ import annotations

import pytest

from rgi_migration.session.synthetic_sessions import (
    _DEFAULT_TIER_DISTRIBUTION,
    _build_tier_plan,
    _decision_row,
    _fixture_xml_path,
)


class TestTierPlanDefault:
    def test_default_distribution_5_decisions(self) -> None:
        plan = _build_tier_plan(5, None)
        assert len(plan) == 5
        assert plan.count("tier1_exact") == 3
        assert plan.count("tier1_rule") == 2


class TestTierPlanExpansion:
    def test_custom_dist_exact_sum(self) -> None:
        dist = {"tier1_exact": 2, "tier1_rule": 2, "tier2_fuzzy": 1}
        plan = _build_tier_plan(5, dist)
        assert plan.count("tier1_exact") == 2
        assert plan.count("tier1_rule") == 2
        assert plan.count("tier2_fuzzy") == 1

    def test_under_specified_pads_with_first_key(self) -> None:
        plan = _build_tier_plan(10, {"tier1_exact": 3, "tier1_rule": 2})
        assert len(plan) == 10
        # 3 + 2 = 5 explicit; 5 pad with first key ("tier1_exact")
        assert plan.count("tier1_exact") == 3 + 5
        assert plan.count("tier1_rule") == 2

    def test_over_specified_truncates(self) -> None:
        plan = _build_tier_plan(3, {"tier1_exact": 5, "tier1_rule": 5})
        assert len(plan) == 3
        # Truncation preserves dict-iteration order: first 3 of the
        # expanded list.
        assert plan.count("tier1_exact") == 3

    def test_empty_dist_falls_back(self) -> None:
        plan = _build_tier_plan(2, {})
        assert len(plan) == 2
        # Empty dist → fallback padding uses "tier1_exact".
        assert plan == ["tier1_exact", "tier1_exact"]


class TestDecisionRowShape:
    def test_required_fields_present(self) -> None:
        row = _decision_row(
            index=0, tier="tier1_exact", session_name="TMS-TEST-00001",
            company_abbr="CACSPU", all_approved=True,
        )
        for field in (
            "doctype", "session", "tally_name", "tally_id", "tier",
            "proposed_account", "review_action",
        ):
            assert field in row, f"missing field {field!r}"
        assert row["doctype"] == "Mapping Decision"
        assert row["session"] == "TMS-TEST-00001"

    def test_all_approved_populates_final_account(self) -> None:
        row = _decision_row(
            index=0, tier="tier1_exact", session_name="TMS-TEST-00001",
            company_abbr="CACSPU", all_approved=True,
        )
        assert row["review_action"] == "Approved"
        # Even-index Dr rows post against Professional Tax (Asset side);
        # odd-index Cr rows against ICICI BANK (Liability side). Split
        # avoids Main JE wash-trade refusal. See _decision_row docstring.
        assert row["final_account"] == "Professional Tax - CACSPU"
        assert row["proposed_account"] == "Professional Tax - CACSPU"

    def test_cr_index_uses_liability_account(self) -> None:
        # index 2 is the first Cr row under the 2-out-of-3 predicate.
        row = _decision_row(
            index=2, tier="tier1_exact", session_name="TMS-TEST-00001",
            company_abbr="CACSPU", all_approved=True,
        )
        assert row["final_account"] == "ICICI BANK - 624205021153 - CACSPU"

    def test_pending_leaves_final_account_null(self) -> None:
        row = _decision_row(
            index=0, tier="tier1_exact", session_name="TMS-TEST-00001",
            company_abbr="CACSPU", all_approved=False,
        )
        assert row["review_action"] == "Pending"
        assert row["final_account"] is None

    def test_balance_pattern_guarantees_nonzero_net(self) -> None:
        """The 2-out-of-3 Dr predicate (index % 3 != 2) ensures Dr count
        ≠ Cr count for any total N, so the Main JE's Temporary Opening
        balancer is always non-zero. ERPNext rejects zero-amount rows."""
        # Indices 0, 1 → Dr. Index 2 → Cr.
        assert _decision_row(
            index=0, tier="tier1_exact", session_name="S",
            company_abbr="CACSPU", all_approved=True,
        )["opening_dr"] == 100.0
        assert _decision_row(
            index=1, tier="tier1_exact", session_name="S",
            company_abbr="CACSPU", all_approved=True,
        )["opening_dr"] == 100.0
        assert _decision_row(
            index=2, tier="tier1_exact", session_name="S",
            company_abbr="CACSPU", all_approved=True,
        )["opening_cr"] == 100.0

    def test_any_num_decisions_yields_unequal_dr_cr(self) -> None:
        """Invariant: for any N, #Dr rows != #Cr rows."""
        for n in (1, 2, 3, 4, 5, 6, 7, 9, 12, 15):
            dr = sum(
                1 for i in range(n)
                if _decision_row(
                    index=i, tier="tier1_exact", session_name="S",
                    company_abbr="CACSPU", all_approved=True,
                )["opening_dr"] > 0
            )
            cr = n - dr
            assert dr != cr, (
                f"N={n}: Dr={dr}, Cr={cr} — balanced Dr/Cr will break "
                f"Main JE Temporary Opening balancer"
            )

    def test_synthetic_marker_in_tally_name(self) -> None:
        row = _decision_row(
            index=42, tier="tier1_exact", session_name="S",
            company_abbr="CACSPU", all_approved=True,
        )
        assert "SYNTHETIC" in row["tally_name"]
        assert row["tally_id"] == "SYN0042"


class TestFixturePath:
    def test_fixture_path_resolves(self) -> None:
        from pathlib import Path

        p = _fixture_xml_path()
        # Sanity: ends with the known fixture filename.
        assert p.endswith("sample_cacspu_masters_sample.xml")
        # File exists on the local worktree (also mirrored on bench).
        assert Path(p).is_file(), f"fixture not found at {p}"


class TestDefaultDistributionConstant:
    def test_default_is_tier1_heavy(self) -> None:
        # Defensive: if someone changes _DEFAULT_TIER_DISTRIBUTION
        # without re-running generator smoke, this test flags it.
        assert _DEFAULT_TIER_DISTRIBUTION == {
            "tier1_exact": 3,
            "tier1_rule": 2,
        }
