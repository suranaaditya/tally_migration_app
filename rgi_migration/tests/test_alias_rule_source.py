"""Tests for Item 8 supplier-alias read path.

Covers:
* `alias_rule_from_doc_row` schema translation
* `_filter_alias_rules` status + entity filter (including substring
  avoidance, mirroring FrappeRuleSource's invariant)
* `find_alias_rule_supplier` exact_ci match / miss / unsupported mode /
  multi-match warning
"""

from __future__ import annotations

import logging

import pytest

from rgi_migration.mapper.alias_rule_source import (
    AliasRule,
    InMemoryAliasRuleSource,
    _filter_alias_rules,
    alias_rule_from_doc_row,
)
from rgi_migration.mapper.supplier_source import (
    InMemorySupplierSource,
    Supplier,
)
from rgi_migration.mapper.tier1_supplier import find_alias_rule_supplier


def _sar_row(**overrides) -> dict:
    base = {
        "name": "SAR-00001",
        "tally_name_pattern": "ACME CORP",
        "tally_match_mode": "exact_ci",
        "erpnext_supplier": "ACME CORP",
        "status": "confirmed",
        "applies_to_entity_types": "*",
        "creation": "2026-04-24 10:00:00",
    }
    base.update(overrides)
    return base


def _supplier(name: str) -> Supplier:
    return Supplier(
        name=name, supplier_name=name, supplier_group="", disabled=False,
    )


class TestAliasRuleFromDocRow:
    def test_round_trip(self) -> None:
        r = alias_rule_from_doc_row(_sar_row())
        assert r.name == "SAR-00001"
        assert r.tally_name_pattern == "ACME CORP"
        assert r.tally_match_mode == "exact_ci"
        assert r.erpnext_supplier == "ACME CORP"
        assert r.status == "confirmed"


class TestStatusFilter:
    @pytest.mark.parametrize("status", ["paused", "deprecated"])
    def test_non_confirmed_excluded(self, status: str) -> None:
        rule = alias_rule_from_doc_row(_sar_row(status=status))
        src = InMemoryAliasRuleSource([rule])
        assert src.alias_rules("*") == []

    def test_confirmed_included(self) -> None:
        rule = alias_rule_from_doc_row(_sar_row(status="confirmed"))
        src = InMemoryAliasRuleSource([rule])
        assert len(src.alias_rules("*")) == 1


class TestEntityFilter:
    def test_substring_does_not_match(self) -> None:
        """Same architectural invariant as FrappeRuleSource."""
        rule = alias_rule_from_doc_row(
            _sar_row(applies_to_entity_types="college_prep")
        )
        src = InMemoryAliasRuleSource([rule])
        assert src.alias_rules("college") == []


class TestFindAliasRuleSupplierMatching:
    def test_stub_behavior_without_rules(self) -> None:
        """alias_rules=None preserves pre-Item-8 stub."""
        src = InMemorySupplierSource([_supplier("ACME CORP")])
        assert find_alias_rule_supplier("ACME CORP", src) is None
        assert find_alias_rule_supplier("ACME CORP", src, None) is None
        assert find_alias_rule_supplier("ACME CORP", src, []) is None

    def test_exact_ci_hit(self) -> None:
        rule = alias_rule_from_doc_row(_sar_row())
        supplier = _supplier("ACME CORP")
        src = InMemorySupplierSource([supplier])
        hit = find_alias_rule_supplier("acme corp", src, [rule])
        assert hit is not None
        matched_supplier, rule_name = hit
        assert matched_supplier.name == "ACME CORP"
        assert rule_name == "SAR-00001"

    def test_exact_ci_ignores_suffix_variation(self) -> None:
        """Ledger `'ACME CORP-VG0001'` should match alias pattern
        'ACME CORP' via `_clean` suffix stripping."""
        rule = alias_rule_from_doc_row(_sar_row())
        supplier = _supplier("ACME CORP")
        src = InMemorySupplierSource([supplier])
        hit = find_alias_rule_supplier("ACME CORP-VG0001", src, [rule])
        assert hit is not None

    def test_miss_returns_none(self) -> None:
        rule = alias_rule_from_doc_row(_sar_row())
        src = InMemorySupplierSource([_supplier("ACME CORP")])
        assert find_alias_rule_supplier("DIFFERENT CORP", src, [rule]) is None

    def test_stale_supplier_reference_skipped(self) -> None:
        """Alias rule points to a Supplier that no longer exists."""
        rule = alias_rule_from_doc_row(
            _sar_row(erpnext_supplier="DELETED CORP")
        )
        src = InMemorySupplierSource([_supplier("OTHER CORP")])
        assert find_alias_rule_supplier("ACME CORP", src, [rule]) is None


class TestFindAliasRuleSupplierUnsupportedModes:
    @pytest.mark.parametrize("mode", ["fuzzy_85", "fuzzy_90", "regex"])
    def test_raises_not_implemented(self, mode: str) -> None:
        rule = alias_rule_from_doc_row(_sar_row(tally_match_mode=mode))
        src = InMemorySupplierSource([_supplier("ACME CORP")])
        with pytest.raises(NotImplementedError, match=mode):
            find_alias_rule_supplier("ACME CORP", src, [rule])


class TestMultiMatchWarning:
    def test_first_match_wins_and_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        rule_a = alias_rule_from_doc_row(
            _sar_row(name="SAR-00001", erpnext_supplier="ACME CORP")
        )
        rule_b = alias_rule_from_doc_row(
            _sar_row(name="SAR-00002", erpnext_supplier="ACME CORP")
        )
        src = InMemorySupplierSource([_supplier("ACME CORP")])
        with caplog.at_level(logging.WARNING, logger="rgi_migration.mapper.tier1_supplier"):
            hit = find_alias_rule_supplier("ACME CORP", src, [rule_a, rule_b])
        assert hit is not None
        _, winning_rule = hit
        assert winning_rule == "SAR-00001"
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        msg = warnings[0].getMessage()
        assert "SAR-00001" in msg and "SAR-00002" in msg
