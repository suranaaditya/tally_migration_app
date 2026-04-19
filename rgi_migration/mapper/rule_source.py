"""Rule-source abstraction for the Tier-1 mapper.

`RuleSource` is a Protocol; the mapper consumes `Rule` dataclasses without
caring where they come from. Three concrete implementations today:

* `JsonFileRuleSource` — reads `docs/seed_plan.json` produced by
  `scripts/seed_mapping_rules.py --dry-run`. No Frappe dependency. This is
  what the Tier-1 self-test and the pre-bench demo run use.
* `InMemoryRuleSource` — a list-backed source, for hand-built rule sets in
  tests. Duck-typed against `RuleSource`.
* `FrappeRuleSource` — placeholder that reads from the `Mapping Rule`
  DocType. Lands when the Frappe bench is scaffolded; raises
  `NotImplementedError` until then.

All three produce identical `Rule` objects; the mapper is source-agnostic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


# ---------------------------------------------------------------------------
# Rule dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AlternatePattern:
    tally_pattern: str
    tally_match_mode: str


@dataclass(frozen=True)
class Rule:
    """Canonical shape consumed by the mapper. All fields are populated even
    for pure positive rules (anti-pattern fields as None) and vice-versa —
    simplifies the consumer."""

    # Identity / provenance
    source_section: str
    source_hash: str
    rule_name: str
    is_anti_pattern: bool
    status: str
    applies_to_entity_types: str

    # Tally-side matching
    tally_pattern: str
    tally_match_mode: str
    tally_pattern_alternates: tuple[AlternatePattern, ...]
    applicable_root_type: str   # "Any | Asset | Liability | Equity | Income | Expense"
    tally_parent_contains: str | None

    # Positive-rule target
    erpnext_account_template: str | None
    combine_amounts: bool

    # Anti-pattern payload
    forbidden_erpnext_template: str | None
    anti_pattern_reason: str | None
    suggested_alternative_template: str | None

    # Account-creation directive (see docs/mapper_design_notes.md §3)
    creates_erpnext_account: bool
    new_account_name_template: str | None
    new_account_parent: str | None          # entity-agnostic, no {ABBR}
    new_account_root_type: str | None
    new_account_is_group: bool


# ---------------------------------------------------------------------------
# RuleSource Protocol
# ---------------------------------------------------------------------------


class RuleSource(Protocol):
    """Anything that produces positive and anti-pattern Rules. The mapper
    calls each once at construction time and caches the result."""

    def positive_rules(self, entity_type: str = "*") -> list[Rule]: ...
    def anti_pattern_rules(self, entity_type: str = "*") -> list[Rule]: ...


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _rule_from_dict(d: dict, *, is_anti_pattern: bool) -> Rule:
    alternates = tuple(
        AlternatePattern(
            tally_pattern=a["tally_pattern"],
            tally_match_mode=a["tally_match_mode"],
        )
        for a in d.get("tally_pattern_alternates") or []
    )
    return Rule(
        source_section=d["source_section"],
        source_hash=d["source_hash"],
        rule_name=d["rule_name"],
        is_anti_pattern=bool(d.get("is_anti_pattern", 1 if is_anti_pattern else 0)),
        status=d.get("status", "confirmed"),
        applies_to_entity_types=d.get("applies_to_entity_types", "*"),
        tally_pattern=d["tally_pattern"],
        tally_match_mode=d["tally_match_mode"],
        tally_pattern_alternates=alternates,
        applicable_root_type=d.get("applicable_root_type") or "Any",
        tally_parent_contains=d.get("tally_parent_contains"),
        erpnext_account_template=d.get("erpnext_account_template"),
        combine_amounts=bool(d.get("combine_amounts", 0)),
        forbidden_erpnext_template=d.get("forbidden_erpnext_template"),
        anti_pattern_reason=d.get("anti_pattern_reason"),
        suggested_alternative_template=d.get("suggested_alternative_template"),
        creates_erpnext_account=bool(d.get("creates_erpnext_account", 0)),
        new_account_name_template=d.get("new_account_name_template"),
        new_account_parent=d.get("new_account_parent"),
        new_account_root_type=d.get("new_account_root_type"),
        new_account_is_group=bool(d.get("new_account_is_group", 0)),
    )


def _filter_by_entity(rules: list[Rule], entity_type: str) -> list[Rule]:
    """Drop non-confirmed rules, then apply the applies_to_entity_types CSV
    filter. `entity_type="*"` disables the filter (demo mode)."""
    out: list[Rule] = []
    for r in rules:
        if r.status != "confirmed":
            continue
        scope = (r.applies_to_entity_types or "*").strip()
        if entity_type == "*" or scope == "*":
            out.append(r)
            continue
        allowed = {s.strip() for s in scope.split(",") if s.strip()}
        if entity_type in allowed:
            out.append(r)
    return out


# ---------------------------------------------------------------------------
# Concrete implementations
# ---------------------------------------------------------------------------


class JsonFileRuleSource:
    """Load rules from the dry-run seed plan JSON."""

    def __init__(self, path: Path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self._positive = [
            _rule_from_dict(r, is_anti_pattern=False) for r in data["positive_rules"]
        ]
        self._anti = [
            _rule_from_dict(r, is_anti_pattern=True) for r in data["anti_pattern_rules"]
        ]

    def positive_rules(self, entity_type: str = "*") -> list[Rule]:
        return _filter_by_entity(self._positive, entity_type)

    def anti_pattern_rules(self, entity_type: str = "*") -> list[Rule]:
        return _filter_by_entity(self._anti, entity_type)


class InMemoryRuleSource:
    """Rule source backed by a hand-built list. For tests."""

    def __init__(self, rules: list[Rule]):
        self._positive = [r for r in rules if not r.is_anti_pattern]
        self._anti = [r for r in rules if r.is_anti_pattern]

    def positive_rules(self, entity_type: str = "*") -> list[Rule]:
        return _filter_by_entity(self._positive, entity_type)

    def anti_pattern_rules(self, entity_type: str = "*") -> list[Rule]:
        return _filter_by_entity(self._anti, entity_type)


class FrappeRuleSource:
    """Placeholder. Lands when the Frappe bench + `Mapping Rule` DocType
    are installed (Week 2 step 1). Until then, use `JsonFileRuleSource`."""

    def positive_rules(self, entity_type: str = "*") -> list[Rule]:
        raise NotImplementedError(
            "FrappeRuleSource requires a Frappe bench with the Mapping Rule "
            "DocType installed. Use JsonFileRuleSource against docs/seed_plan.json."
        )

    def anti_pattern_rules(self, entity_type: str = "*") -> list[Rule]:
        raise NotImplementedError(
            "FrappeRuleSource requires a Frappe bench with the Mapping Rule "
            "DocType installed. Use JsonFileRuleSource against docs/seed_plan.json."
        )
