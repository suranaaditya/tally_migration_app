"""Unit tests for the apply_bulk_approve pure-logic core.

Exercises ``rgi_migration.rgi_migration.page.md_review.query.apply_bulk_approve``
and its eligibility helper — the Frappe-free implementation that
splits a session's decisions into eligible / skipped buckets for the
Commit 6 bulk-approve action (Path B per
``docs/WEEK4_DEFERRED_ITEMS.md``).

Covers (4 tests):

1. All-eligible session: every row is tier-1 + Pending + has a
   proposed_account + no final_account → all bucketed as eligible
   with the correct mutation dict shape.
2. Mixed session: rows ineligible for each distinct reason
   (resolved, no proposal, manual final_account, wrong tier) are
   all skipped with the right reason string.
3. Empty session: zero decisions in / zero out, both buckets empty.
4. Excluded supplier-fuzzy: tier1_supplier_fuzzy is intentionally
   NOT in the bulk set — verifies it's skipped even though the
   ``tier1_*`` prefix would suggest otherwise.
"""

from __future__ import annotations

from rgi_migration.rgi_migration.page.md_review.query import (
    BULK_APPROVE_TIER1_TIERS,
    apply_bulk_approve,
    is_bulk_approve_eligible,
)


def _decision(**overrides) -> dict:
    """Minimal Mapping Decision dict fixture for bulk-approve tests."""
    base = {
        "name": "MD-2026-00001",
        "tier": "tier1_exact",
        "review_action": "Pending",
        "proposed_account": "Bank of Maharashtra - CACSPU",
        "final_account": None,
        "opening_dr": 1000.0,
        "opening_cr": 0.0,
    }
    base.update(overrides)
    return base


def test_all_eligible_session_buckets_every_row():
    """Every row passes eligibility; eligible list mirrors input,
    each entry carries the expected mutation dict; skipped is empty."""

    decisions = [
        _decision(name="MD-001", tier="tier1_exact",
                  proposed_account="Cash - CACSPU",
                  opening_dr=500.0, opening_cr=0.0),
        _decision(name="MD-002", tier="tier1_rule",
                  proposed_account="HDFC Current - CACSPU",
                  opening_dr=0.0, opening_cr=12500.0),
        _decision(name="MD-003", tier="tier1_pattern",
                  review_action="Pending Account Creation",
                  proposed_account="Salary Payable - CACSPU",
                  opening_dr=0.0, opening_cr=8000.0),
    ]

    result = apply_bulk_approve(decisions)

    assert len(result["eligible"]) == 3
    assert result["skipped"] == []

    by_name = {e["name"]: e for e in result["eligible"]}

    # MD-001 — Dr balance, exact match
    assert by_name["MD-001"]["updates"] == {
        "review_action": "Approved",
        "final_account": "Cash - CACSPU",
        "final_dr": 500.0,
        "final_cr": 0.0,
    }

    # MD-002 — Cr balance, rule match
    assert by_name["MD-002"]["updates"] == {
        "review_action": "Approved",
        "final_account": "HDFC Current - CACSPU",
        "final_dr": 0.0,
        "final_cr": 12500.0,
    }

    # MD-003 — pattern match, also from Pending Account Creation (still
    # a Pending* variant, so eligible).
    assert by_name["MD-003"]["updates"]["review_action"] == "Approved"
    assert by_name["MD-003"]["updates"]["final_account"] == "Salary Payable - CACSPU"


def test_mixed_eligibility_skips_with_reason_per_case():
    """Each distinct ineligibility cause produces its own reason
    string; eligible rows still flow through correctly."""

    decisions = [
        # eligible
        _decision(name="MD-OK", tier="tier1_exact"),
        # already resolved (Approved is a terminal state)
        _decision(name="MD-RESOLVED", review_action="Approved"),
        # no proposed_account
        _decision(name="MD-NO-PROP", proposed_account=""),
        # reviewer already typed a final_account
        _decision(name="MD-MANUAL", final_account="Other Account - CACSPU"),
        # tier-2 — out of bulk-approve scope
        _decision(name="MD-TIER2", tier="tier2_fuzzy"),
        # unmapped tier
        _decision(name="MD-UNMAPPED", tier="unmapped",
                  proposed_account=""),
    ]

    result = apply_bulk_approve(decisions)

    eligible_names = [e["name"] for e in result["eligible"]]
    assert eligible_names == ["MD-OK"]

    skipped_by_name = {s["name"]: s["reason"] for s in result["skipped"]}
    assert "already resolved" in skipped_by_name["MD-RESOLVED"]
    assert "no proposed_account" in skipped_by_name["MD-NO-PROP"]
    assert "final_account already set" in skipped_by_name["MD-MANUAL"]
    assert "not in tier-1 set" in skipped_by_name["MD-TIER2"]
    # MD-UNMAPPED hits the tier check first (frozenset miss) — order
    # of eligibility checks is documented in is_bulk_approve_eligible.
    assert "not in tier-1 set" in skipped_by_name["MD-UNMAPPED"]


def test_empty_session_returns_empty_buckets():
    """Zero decisions in → zero eligible, zero skipped. Defensive guard:
    the bulk-approve action is still safe to invoke on a freshly-created
    session that has no parsed decisions yet."""

    result = apply_bulk_approve([])

    assert result == {"eligible": [], "skipped": []}


def test_supplier_fuzzy_explicitly_excluded():
    """tier1_supplier_fuzzy is a tier-1 classification but resolution
    requires the Items 3-4 supplier-picker UX. Bulk-approving it via
    final_account would write to the wrong field. Verify it's skipped
    with a clear reason — the constant defines the contract."""

    assert "tier1_supplier_fuzzy" not in BULK_APPROVE_TIER1_TIERS

    decision = _decision(
        name="MD-SUPPLIER",
        tier="tier1_supplier_fuzzy",
        proposed_account="Some Supplier Account - CACSPU",
    )

    eligible, reason = is_bulk_approve_eligible(decision)
    assert eligible is False
    assert reason is not None
    assert "tier1_supplier_fuzzy" in reason
    assert "not in tier-1 set" in reason
