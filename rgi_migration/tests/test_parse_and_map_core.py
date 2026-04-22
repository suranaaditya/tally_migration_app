"""Tests for the Frappe-free parse-and-map core.

Covers ``rgi_migration/session/parse_and_map.py``:

* ``run_mapper_pipeline`` — runs the mapper over a hand-crafted TB.
* ``parse_source`` dispatch — xml / excel / fallback.
* ``decision_to_row_dict`` — builds Mapping Decision insert dicts
  joining MappedDecision with source Ledger.
* ``index_ledgers_by_identity`` — (name, tally_id) keying, collision case.
* Full-pipeline smoke on the committed CACSPU 10 MB sample fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rgi_migration.mapper.mapper import CoaAccount, MappedDecision
from rgi_migration.mapper.rule_source import InMemoryRuleSource, Rule
from rgi_migration.mapper.supplier_source import Supplier
from rgi_migration.parsers.normalized_schema import Ledger, ParsedTallyTB
from rgi_migration.session.parse_and_map import (
    ParseAndMapResult,
    decision_from_doc_row,
    decision_to_row_dict,
    index_ledgers_by_identity,
    ledger_from_doc_row,
    parse_source,
    run_mapper_pipeline,
    run_parse_and_map,
    synthesize_tb_from_ledger_index,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ledger(
    name: str,
    *,
    tally_id: str | None = None,
    parent_chain: list[str] | None = None,
    root_type: str = "Asset",
    opening_dr: float = 0.0,
    opening_cr: float = 0.0,
    is_student_ledger: bool = False,
    is_system_account: bool = False,
    is_pnl_closed_zero: bool = False,
) -> Ledger:
    parent_chain = parent_chain or []
    net = opening_cr - opening_dr
    side = "Dr" if opening_dr > opening_cr else ("Cr" if opening_cr > opening_dr else "Zero")
    return Ledger(
        name=name,
        tally_id=tally_id,
        parent_group=parent_chain[-1] if parent_chain else "",
        parent_chain=parent_chain,
        root_type=root_type,
        opening_dr=opening_dr,
        opening_cr=opening_cr,
        net_amount=net,
        net_side=side,
        is_student_ledger=is_student_ledger,
        is_system_account=is_system_account,
        is_pnl_closed_zero=is_pnl_closed_zero,
    )


def _empty_tb(ledgers: list[Ledger] | None = None) -> ParsedTallyTB:
    ledgers = ledgers or []
    return ParsedTallyTB(
        company_name="CACSPU Test",
        tb_date="2026-03-31",
        source_format="xml",
        source_file="/tmp/fake.xml",
        ledgers=ledgers,
        groups=[],
        total_dr=sum(l.opening_dr for l in ledgers),
        total_cr=sum(l.opening_cr for l in ledgers),
        is_balanced=True,
    )


def _coa_for(name: str, *, root_type: str = "Asset") -> dict[str, CoaAccount]:
    return {
        name: CoaAccount(
            name=name,
            parent_account=None,
            root_type=root_type,
            is_group=False,
            company_abbr="CACSPU",
        ),
    }


# ---------------------------------------------------------------------------
# run_mapper_pipeline — happy path + edge cases
# ---------------------------------------------------------------------------


def test_run_mapper_pipeline_exact_name_match() -> None:
    """Ledger whose name (plus ABBR suffix) exists in the COA hits tier1_exact.

    The mapper's Layer-3 exact-name fallback constructs ``<ledger.name> - {abbr}``
    and scans COA; so Tally ledger "Cash" maps to ERP account "Cash - CACSPU".
    """
    tb = _empty_tb([
        _ledger(
            "Cash",
            tally_id="100",
            root_type="Asset",
            opening_dr=50000.0,
        ),
    ])
    coa = _coa_for("Cash - CACSPU", root_type="Asset")

    result = run_mapper_pipeline(
        tb=tb,
        abbr="CACSPU",
        entity_type="*",
        coa=coa,
        suppliers=[],
        rule_source=InMemoryRuleSource([]),
    )

    assert isinstance(result, ParseAndMapResult)
    assert len(result.decisions) == 1
    d = result.decisions[0]
    assert d.tier == "tier1_exact"
    assert d.proposed_account == "Cash - CACSPU"
    assert result.summary["total"] == 1
    assert result.summary["by_tier"]["tier1_exact"] == 1


def test_run_mapper_pipeline_empty_tb() -> None:
    """Zero ledgers in → zero decisions out, summary still well-formed."""
    result = run_mapper_pipeline(
        tb=_empty_tb([]),
        abbr="CACSPU",
        entity_type="*",
        coa={},
        suppliers=[],
        rule_source=InMemoryRuleSource([]),
    )
    assert result.decisions == []
    assert result.summary["total"] == 0
    assert result.summary["by_tier"] == {}


def test_run_mapper_pipeline_supplier_fuzzy_populates_supplier_fields() -> None:
    """Vendor ledger under Sundry Creditors fuzzy-matches a Supplier; the
    returned MappedDecision carries proposed_supplier + match_score so
    decision_to_row_dict can persist them."""
    tb = _empty_tb([
        _ledger(
            "Aarna Solutions",
            tally_id="VA0290",
            parent_chain=["Liability", "Sundry Creditors"],
            root_type="Liability",
            opening_cr=48500.0,
        ),
    ])
    suppliers = [
        Supplier(
            name="SUP-AARNA",
            supplier_name="Aarna Solutions Pvt Ltd",
            supplier_group="Services",
        ),
    ]
    result = run_mapper_pipeline(
        tb=tb,
        abbr="CACSPU",
        entity_type="*",
        coa={},
        suppliers=suppliers,
        rule_source=InMemoryRuleSource([]),
    )

    assert len(result.decisions) == 1
    d = result.decisions[0]
    assert d.tier in {
        "tier1_supplier_exact", "tier1_supplier_fuzzy", "tier1_supplier_alias",
    }
    assert d.proposed_supplier == "SUP-AARNA"
    assert 0.0 < d.supplier_match_score <= 1.0


def test_run_mapper_pipeline_pending_supplier_creation_carries_suggestion() -> None:
    """Vendor with no Supplier master match gets pending_supplier_creation
    with the cleaned Tally name as new_supplier_name suggestion."""
    tb = _empty_tb([
        _ledger(
            "Nonexistent Vendor Pvt Ltd",
            tally_id="VA9999",
            parent_chain=["Liability", "Sundry Creditors"],
            root_type="Liability",
            opening_cr=1000.0,
        ),
    ])
    result = run_mapper_pipeline(
        tb=tb,
        abbr="CACSPU",
        entity_type="*",
        coa={},
        suppliers=[],  # empty supplier master
        rule_source=InMemoryRuleSource([]),
    )
    d = result.decisions[0]
    assert d.tier == "pending_supplier_creation"
    assert d.proposed_supplier is None
    assert d.new_supplier_name == "Nonexistent Vendor Pvt Ltd"


# ---------------------------------------------------------------------------
# parse_source dispatch
# ---------------------------------------------------------------------------


def test_parse_source_fallback_to_xml_on_unknown_format(tmp_path: Path) -> None:
    """Unknown format strings fall through to XML parser (matches generator
    convention of defaulting source_format to xml)."""
    # Use the committed sample_cacspu_masters_sample.xml fixture — parses
    # successfully under the XML path.
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "sample_cacspu_masters_sample.xml"
    )
    if not fixture.exists():  # guard against fixture relocation
        pytest.skip(f"fixture missing: {fixture}")

    tb = parse_source(str(fixture), "garbage_format")
    assert tb.source_format == "xml"
    assert len(tb.ledgers) > 0


def test_parse_source_normalises_case() -> None:
    """source_format is case-insensitive."""
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "sample_cacspu_masters_sample.xml"
    )
    if not fixture.exists():
        pytest.skip(f"fixture missing: {fixture}")
    tb = parse_source(str(fixture), "XML")
    assert tb.source_format == "xml"


# ---------------------------------------------------------------------------
# decision_to_row_dict — joins MappedDecision with source Ledger
# ---------------------------------------------------------------------------


def _decision(
    **overrides,
) -> MappedDecision:
    defaults = dict(
        tally_name="Cash - CACSPU",
        tally_id="100",
        tally_root_type="Asset",
        opening_dr=50000.0,
        opening_cr=0.0,
        tier="tier1_exact",
        proposed_account="Cash - CACSPU",
        review_action="Pending",
        matched_rule=None,
        confidence=1.0,
    )
    defaults.update(overrides)
    return MappedDecision(**defaults)


def test_decision_to_row_dict_account_tier_happy_path() -> None:
    ledger = _ledger(
        "Cash - CACSPU",
        tally_id="100",
        parent_chain=["Assets", "Current Assets", "Bank Accounts"],
        root_type="Asset",
        opening_dr=50000.0,
    )
    d = _decision()
    row = decision_to_row_dict(d, ledger, "TMS-TEST-00001")

    assert row["doctype"] == "Mapping Decision"
    assert row["session"] == "TMS-TEST-00001"
    assert row["tally_name"] == "Cash - CACSPU"
    assert row["tally_id"] == "100"
    assert row["tally_parent_chain"] == "Assets > Current Assets > Bank Accounts"
    assert row["tier"] == "tier1_exact"
    assert row["proposed_account"] == "Cash - CACSPU"
    assert row["proposed_supplier"] is None  # account-tier — supplier unset
    assert row["review_action"] == "Pending"
    # Carried from ledger, not dataclass
    assert row["net_amount"] == -50000.0
    assert row["net_side"] == "Dr"


def test_decision_to_row_dict_supplier_tier_populates_supplier_fields() -> None:
    ledger = _ledger(
        "Aarna Solutions",
        tally_id="VA0290",
        parent_chain=["Liability", "Sundry Creditors"],
        root_type="Liability",
        opening_cr=48500.0,
    )
    d = _decision(
        tally_name="Aarna Solutions",
        tally_id="VA0290",
        tally_root_type="Liability",
        opening_dr=0.0,
        opening_cr=48500.0,
        tier="tier1_supplier_fuzzy",
        proposed_account=None,
        review_action="Pending",
        confidence=0.87,
        proposed_supplier="SUP-AARNA",
        supplier_match_score=0.87,
    )
    row = decision_to_row_dict(d, ledger, "TMS-TEST-00001")

    assert row["tier"] == "tier1_supplier_fuzzy"
    assert row["proposed_supplier"] == "SUP-AARNA"
    assert row["supplier_match_score"] == 0.87
    assert row["proposed_account"] is None
    # Reviewer fields all initial-null — mapper doesn't decide for the reviewer
    assert row["final_supplier"] is None
    assert row["final_account"] is None


def test_decision_to_row_dict_pending_supplier_creation_suggestion() -> None:
    ledger = _ledger(
        "Nonexistent Vendor",
        tally_id="V001",
        parent_chain=["Liability", "Sundry Creditors"],
        root_type="Liability",
        opening_cr=1000.0,
    )
    d = _decision(
        tally_name="Nonexistent Vendor",
        tally_id="V001",
        tally_root_type="Liability",
        opening_dr=0.0,
        opening_cr=1000.0,
        tier="pending_supplier_creation",
        proposed_account=None,
        review_action="Pending Supplier Creation",
        confidence=0.0,
        new_supplier_name="Nonexistent Vendor",
    )
    row = decision_to_row_dict(d, ledger, "TMS-TEST-00001")

    assert row["new_supplier_name"] == "Nonexistent Vendor"
    assert row["proposed_supplier"] is None
    assert row["review_action"] == "Pending Supplier Creation"


def test_decision_to_row_dict_carries_parser_flags() -> None:
    """is_pnl_closed_zero, is_student_ledger, is_system_account come from
    the source Ledger, not the dataclass. Verify all three survive the
    persistence join."""
    ledger = _ledger(
        "System P&L",
        tally_id="999",
        parent_chain=["Equity"],
        root_type="Equity",
        is_system_account=True,
        is_pnl_closed_zero=True,
    )
    d = _decision(
        tally_name="System P&L",
        tally_id="999",
        tally_root_type="Equity",
    )
    row = decision_to_row_dict(d, ledger, "TMS-TEST-00001")

    assert row["is_system_account"] == 1
    assert row["is_pnl_closed_zero"] == 1
    assert row["is_student_ledger"] == 0


def test_decision_to_row_dict_empty_parent_chain() -> None:
    """Root-level ledgers (no parent_chain) produce empty-string tally_parent_chain."""
    ledger = _ledger("Suspense", tally_id=None, parent_chain=[])
    d = _decision(tally_name="Suspense", tally_id=None)
    row = decision_to_row_dict(d, ledger, "TMS-TEST-00001")
    assert row["tally_parent_chain"] == ""


# ---------------------------------------------------------------------------
# index_ledgers_by_identity — collision and lookup
# ---------------------------------------------------------------------------


def test_index_by_identity_collision_on_name_distinguished_by_tally_id() -> None:
    """Two ledgers can share a cleaned name if their tally_ids differ.
    Identity tuple (name, tally_id) keeps them distinct per
    CLAUDE.md 'Name collisions by tally_id'."""
    l1 = _ledger("Bank of Maharashtra", tally_id="BOM1", opening_dr=100.0)
    l2 = _ledger("Bank of Maharashtra", tally_id="BOM2", opening_dr=200.0)
    tb = _empty_tb([l1, l2])

    idx = index_ledgers_by_identity(tb)
    assert len(idx) == 2
    assert idx[("Bank of Maharashtra", "BOM1")].opening_dr == 100.0
    assert idx[("Bank of Maharashtra", "BOM2")].opening_dr == 200.0


def test_index_by_identity_none_tally_id_normalises_to_empty_string() -> None:
    """Ledgers with tally_id=None use "" in the key — avoids None unhashable
    issues and keeps collision semantics consistent."""
    l = _ledger("Ad-hoc Account", tally_id=None)
    idx = index_ledgers_by_identity(_empty_tb([l]))
    assert ("Ad-hoc Account", "") in idx


# ---------------------------------------------------------------------------
# Full pipeline smoke on committed fixture (slow but cheap — 10 MB)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# decision_from_doc_row — reviewer-preference pivot (Item 2 Commit 3)
# ---------------------------------------------------------------------------


def test_decision_from_doc_row_uses_final_account_when_present() -> None:
    """Reviewer-preference rule: final_account (if populated) is
    surfaced as d.proposed_account so generators see the override."""
    row = {
        "tally_name": "Foo",
        "tally_root_type": "Asset",
        "tier": "tier1_rule",
        "proposed_account": "ABC - CACSPU",
        "final_account": "XYZ - CACSPU",  # reviewer override
        "review_action": "Manual Override",
    }
    d = decision_from_doc_row(row)
    assert d.proposed_account == "XYZ - CACSPU"  # final wins


def test_decision_from_doc_row_falls_back_to_proposed_account() -> None:
    """When the reviewer hasn't set final_account, the mapper's
    proposed_account flows through unchanged."""
    row = {
        "tally_name": "Foo",
        "tally_root_type": "Asset",
        "tier": "tier1_rule",
        "proposed_account": "ABC - CACSPU",
        "final_account": None,
        "review_action": "Pending",
    }
    d = decision_from_doc_row(row)
    assert d.proposed_account == "ABC - CACSPU"


def test_decision_from_doc_row_supplier_preference() -> None:
    """final_supplier wins over proposed_supplier on supplier-tier rows."""
    row = {
        "tally_name": "Aarna",
        "tally_root_type": "Liability",
        "tier": "tier1_supplier_fuzzy",
        "proposed_supplier": "SUP-AARNA-FUZZY",
        "final_supplier": "SUP-AARNA-FINAL",  # reviewer override
        "supplier_match_score": 0.87,
        "review_action": "Manual Override",
    }
    d = decision_from_doc_row(row)
    assert d.proposed_supplier == "SUP-AARNA-FINAL"
    assert d.supplier_match_score == 0.87


def test_decision_from_doc_row_empty_row_defaults() -> None:
    """Minimal row (e.g. from frappe.get_all with missing fields) still
    produces a valid MappedDecision with sensible defaults."""
    d = decision_from_doc_row({})
    assert d.tally_name == ""
    assert d.tally_id is None
    assert d.tier == "unmapped"
    assert d.review_action == "Pending"
    assert d.proposed_account is None
    assert d.proposed_supplier is None


# ---------------------------------------------------------------------------
# ledger_from_doc_row + synthesize_tb_from_ledger_index
# ---------------------------------------------------------------------------


def test_ledger_from_doc_row_carries_parser_flags() -> None:
    """is_student_ledger / is_system_account / is_pnl_closed_zero from
    the persisted row survive the Ledger reconstruction."""
    row = {
        "tally_name": "System P&L",
        "tally_id": "999",
        "tally_root_type": "Equity",
        "opening_dr": 0.0,
        "opening_cr": 0.0,
        "net_amount": 0.0,
        "net_side": "Zero",
        "is_system_account": 1,
        "is_pnl_closed_zero": 1,
        "is_student_ledger": 0,
    }
    l = ledger_from_doc_row(row)
    assert l.is_system_account is True
    assert l.is_pnl_closed_zero is True
    assert l.is_student_ledger is False
    assert l.is_leaf is True  # unconditional


def test_ledger_from_doc_row_name_and_amounts() -> None:
    row = {
        "tally_name": "Bank - Maharashtra",
        "tally_id": "BOM1",
        "tally_root_type": "Asset",
        "opening_dr": 125000.0,
        "opening_cr": 0.0,
        "net_amount": -125000.0,
        "net_side": "Dr",
    }
    l = ledger_from_doc_row(row)
    assert l.name == "Bank - Maharashtra"
    assert l.tally_id == "BOM1"
    assert l.opening_dr == 125000.0
    assert l.net_side == "Dr"


def test_synthesize_tb_from_ledger_index_preserves_ledgers() -> None:
    """Generated TB exposes exactly the ledgers in the index (by identity)."""
    l1 = _ledger("A", tally_id="1", opening_dr=100.0)
    l2 = _ledger("B", tally_id="2", opening_cr=50.0)
    idx = {("A", "1"): l1, ("B", "2"): l2}
    tb = synthesize_tb_from_ledger_index(
        idx, company_name="Test Co", tb_date="2026-03-31", source_format="xml",
    )
    assert set(id(l) for l in tb.ledgers) == {id(l1), id(l2)}
    assert tb.company_name == "Test Co"
    assert tb.groups == []


def test_run_parse_and_map_full_pipeline_on_sample_fixture() -> None:
    """End-to-end: parse 10 MB CACSPU sample → run mapper → summary populated.

    This is the one test that exercises the parse step together with the
    mapper. Slow (~200-400 ms on CI) but catches integration breakage
    between parser output shape and mapper input expectations.
    """
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "sample_cacspu_masters_sample.xml"
    )
    if not fixture.exists():
        pytest.skip(f"fixture missing: {fixture}")

    result = run_parse_and_map(
        source_path=str(fixture),
        source_format="xml",
        abbr="CACSPU",
        entity_type="*",
        coa={},  # empty COA — all ledgers fall through to unmapped
        suppliers=[],
        rule_source=InMemoryRuleSource([]),
    )

    # Sample fixture has > 10 ledgers (it's the committed curated subset
    # plus 13 must-include diagnostic ledgers per tests/fixtures/README).
    assert len(result.decisions) > 10
    # Every decision must have a matching Ledger in the TB — index lookup
    # should succeed for every single one, which is the invariant
    # decision_to_row_dict relies on.
    idx = index_ledgers_by_identity(result.tb)
    for d in result.decisions:
        key = (d.tally_name, d.tally_id or "")
        assert key in idx, f"decision {d.tally_name} has no matching ledger in index"
