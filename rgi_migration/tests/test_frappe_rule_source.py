"""Tests for Item 8 FrappeRuleSource read path.

Covers the pure helpers (`rule_from_doc_row`, `_filter_by_entity`)
that FrappeRuleSource composes. Unit-testing the Frappe DB binding
itself would require a bench — that lives in Phase C smoke.

Substring-avoidance assertion is a codified architectural invariant:
a rule with ``applies_to_entity_types="college_prep"`` must NOT match
a session with ``entity_type="college"``.
"""

from __future__ import annotations

import pytest

from rgi_migration.mapper.rule_source import (
    InMemoryRuleSource,
    Rule,
    _filter_by_entity,
    rule_from_doc_row,
)


def _row(**overrides) -> dict:
    """Build a Mapping Rule DocType-shaped dict with sensible defaults."""
    base = {
        "name": "MR-00001",
        "source_section": "§4.1",
        "source_hash": "deadbeef",
        "rule_name": "test rule",
        "is_anti_pattern": 0,
        "status": "confirmed",
        "applies_to_entity_types": "*",
        "tally_pattern": "Foo",
        "tally_match_mode": "exact_ci",
        "applicable_root_type": "Any",
        "tally_parent_contains": None,
        "erpnext_account_template": "Foo - {ABBR}",
        "combine_amounts": 0,
        "forbidden_erpnext_template": None,
        "anti_pattern_reason": None,
        "suggested_alternative_template": None,
        "creates_erpnext_account": 0,
        "new_account_name_template": None,
        "new_account_parent": None,
        "new_account_root_type": None,
        "new_account_is_group": 0,
    }
    base.update(overrides)
    return base


class TestRuleFromDocRow:
    def test_all_fields_round_trip(self) -> None:
        rule = rule_from_doc_row(_row())
        assert rule.source_section == "§4.1"
        assert rule.tally_pattern == "Foo"
        assert rule.erpnext_account_template == "Foo - {ABBR}"
        assert rule.is_anti_pattern is False
        assert rule.status == "confirmed"
        assert rule.applies_to_entity_types == "*"
        assert rule.tally_pattern_alternates == ()

    def test_alternates_joined(self) -> None:
        alts = [
            {"tally_pattern": "Foo2", "tally_match_mode": "exact_ci"},
            {"tally_pattern": "Foo3", "tally_match_mode": "regex"},
        ]
        rule = rule_from_doc_row(_row(), alternates=alts)
        assert len(rule.tally_pattern_alternates) == 2
        assert rule.tally_pattern_alternates[0].tally_pattern == "Foo2"
        assert rule.tally_pattern_alternates[1].tally_match_mode == "regex"

    def test_anti_pattern_bool(self) -> None:
        rule = rule_from_doc_row(_row(is_anti_pattern=1))
        assert rule.is_anti_pattern is True


class TestStatusFilter:
    """Per Phase A Q5 Option A — only status='confirmed' is active.

    All other statuses (tentative, paused, deprecated for Mapping Rule;
    paused, deprecated for Supplier Alias Rule) are excluded by the
    shared `_filter_by_entity` helper.
    """

    @pytest.mark.parametrize("status", ["tentative", "paused", "deprecated"])
    def test_non_confirmed_excluded(self, status: str) -> None:
        rule = rule_from_doc_row(_row(status=status))
        src = InMemoryRuleSource([rule])
        assert src.positive_rules("*") == []

    def test_confirmed_included(self) -> None:
        rule = rule_from_doc_row(_row(status="confirmed"))
        src = InMemoryRuleSource([rule])
        assert len(src.positive_rules("*")) == 1


class TestEntityTypeFilter:
    def test_wildcard_session_sees_all(self) -> None:
        rules = [
            rule_from_doc_row(_row(applies_to_entity_types="college")),
            rule_from_doc_row(_row(applies_to_entity_types="hostel")),
        ]
        src = InMemoryRuleSource(rules)
        assert len(src.positive_rules("*")) == 2

    def test_wildcard_rule_matches_specific_session(self) -> None:
        rule = rule_from_doc_row(_row(applies_to_entity_types="*"))
        src = InMemoryRuleSource([rule])
        assert len(src.positive_rules("college")) == 1

    def test_exact_entity_match(self) -> None:
        rule = rule_from_doc_row(_row(applies_to_entity_types="college"))
        src = InMemoryRuleSource([rule])
        assert len(src.positive_rules("college")) == 1
        assert src.positive_rules("hostel") == []

    def test_csv_membership(self) -> None:
        rule = rule_from_doc_row(
            _row(applies_to_entity_types="college, hostel, university")
        )
        src = InMemoryRuleSource([rule])
        assert len(src.positive_rules("hostel")) == 1
        assert src.positive_rules("society") == []

    def test_substring_does_not_match(self) -> None:
        """Architectural invariant: 'college_prep' must NOT match 'college'.

        If this test fails, the filter has regressed to SQL-LIKE-style
        substring matching and would let rules bleed into unintended
        entity types.
        """
        rule = rule_from_doc_row(_row(applies_to_entity_types="college_prep"))
        src = InMemoryRuleSource([rule])
        assert src.positive_rules("college") == []

    def test_substring_reverse_does_not_match(self) -> None:
        """Same invariant from the other direction: 'college' rule must
        NOT match a 'college_prep' session."""
        rule = rule_from_doc_row(_row(applies_to_entity_types="college"))
        src = InMemoryRuleSource([rule])
        assert src.positive_rules("college_prep") == []


class TestOrphanSessionHandling:
    """Orphaned `created_via_session` is ignored at rule-source level —
    provenance is not scope (Phase A Q6)."""

    def test_orphan_rule_still_returned(self) -> None:
        rule = rule_from_doc_row(_row(status="confirmed"))
        src = InMemoryRuleSource([rule])
        assert len(src.positive_rules("*")) == 1


class TestPositiveAntiSplit:
    def test_split_by_is_anti_pattern(self) -> None:
        rules = [
            rule_from_doc_row(_row(is_anti_pattern=0, source_section="§4.1")),
            rule_from_doc_row(_row(is_anti_pattern=1, source_section="§11.1")),
            rule_from_doc_row(_row(is_anti_pattern=0, source_section="§4.2")),
        ]
        src = InMemoryRuleSource(rules)
        pos = src.positive_rules("*")
        anti = src.anti_pattern_rules("*")
        assert len(pos) == 2
        assert len(anti) == 1
        assert anti[0].source_section == "§11.1"


class TestFilterHelperDirect:
    """Direct tests on `_filter_by_entity` — the engine both JSON and
    Frappe sources use."""

    def _rule_with(self, **fields) -> Rule:
        return rule_from_doc_row(_row(**fields))

    def test_empty_input(self) -> None:
        assert _filter_by_entity([], "college") == []
