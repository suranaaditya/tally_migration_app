"""Rule-source abstraction for the Tier-1 mapper.

`RuleSource` is a Protocol; the mapper consumes `Rule` dataclasses without
caring where they come from. Three concrete implementations today:

* `JsonFileRuleSource` — reads `docs/seed_plan.json` produced by
  `scripts/seed_mapping_rules.py --dry-run`. No Frappe dependency.
  Kept as a reference implementation and for fresh-bench seed
  bootstrapping (Item 13). Not instantiated at runtime post-Item-8 —
  `FrappeRuleSource` is the sole runtime source.
* `InMemoryRuleSource` — a list-backed source, for hand-built rule sets in
  tests. Duck-typed against `RuleSource`.
* `FrappeRuleSource` — reads `Mapping Rule` DocType rows from the Frappe
  bench at construction time (Item 8, α→γ unlock). Applies
  ``status="confirmed"`` + entity-type filters Python-side.

All three produce identical `Rule` objects; the mapper is source-agnostic.

Only status="confirmed" rules are returned. paused, tentative, and
deprecated are all treated as inactive. This matches JsonFileRuleSource
semantics exactly and treats non-confirmed states as reviewer-UI
distinctions, not mapper-behavior distinctions. If future requirements
need runtime-configurable active-statuses, refactor to a whitelist
parameter at that time.
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


def rule_from_doc_row(
    row: dict,
    alternates: list[dict] | None = None,
) -> Rule:
    """Build a `Rule` from a `frappe.get_all("Mapping Rule")` row dict plus
    optional alternate-pattern child rows. Pure function — unit-testable
    without a bench.

    ``alternates`` is the list of child-table rows for this parent, each
    shaped like ``{"tally_pattern": ..., "tally_match_mode": ...}``. Pass
    empty list (or None) for rules with no alternates.
    """
    alt_tuples = tuple(
        AlternatePattern(
            tally_pattern=a["tally_pattern"],
            tally_match_mode=a["tally_match_mode"],
        )
        for a in (alternates or [])
    )
    return Rule(
        source_section=row.get("source_section") or "",
        source_hash=row.get("source_hash") or "",
        rule_name=row.get("rule_name") or "",
        is_anti_pattern=bool(row.get("is_anti_pattern")),
        status=row.get("status") or "confirmed",
        applies_to_entity_types=row.get("applies_to_entity_types") or "*",
        tally_pattern=row.get("tally_pattern") or "",
        tally_match_mode=row.get("tally_match_mode") or "exact_ci",
        tally_pattern_alternates=alt_tuples,
        applicable_root_type=row.get("applicable_root_type") or "Any",
        tally_parent_contains=row.get("tally_parent_contains"),
        erpnext_account_template=row.get("erpnext_account_template"),
        combine_amounts=bool(row.get("combine_amounts")),
        forbidden_erpnext_template=row.get("forbidden_erpnext_template"),
        anti_pattern_reason=row.get("anti_pattern_reason"),
        suggested_alternative_template=row.get("suggested_alternative_template"),
        creates_erpnext_account=bool(row.get("creates_erpnext_account")),
        new_account_name_template=row.get("new_account_name_template"),
        new_account_parent=row.get("new_account_parent"),
        new_account_root_type=row.get("new_account_root_type"),
        new_account_is_group=bool(row.get("new_account_is_group")),
    )


class FrappeRuleSource:
    """Reads `Mapping Rule` rows from the Frappe bench at construction time
    (Item 8, α→γ unlock).

    Loads all rows (not filtered at SQL level) and applies the status +
    entity-type filter Python-side via `_filter_by_entity` — identical
    semantics to `JsonFileRuleSource`. The substring-avoidance guarantee
    (`"college_prep"` must NOT match `"college"`) comes for free because
    `_filter_by_entity` splits on comma and tests exact token membership.

    Alternate patterns are batch-loaded in a single child-table query and
    joined by `parent` in Python, avoiding N+1. For R7-class volumes
    (<1000 rules) the full-table load is sub-100 ms; revisit only if
    bench grows to 10k+ rules.
    """

    def __init__(self) -> None:
        import frappe

        parent_rows = frappe.get_all(
            "Mapping Rule",
            fields=[
                "name",
                "source_section",
                "source_hash",
                "rule_name",
                "is_anti_pattern",
                "status",
                "applies_to_entity_types",
                "tally_pattern",
                "tally_match_mode",
                "applicable_root_type",
                "tally_parent_contains",
                "erpnext_account_template",
                "combine_amounts",
                "forbidden_erpnext_template",
                "anti_pattern_reason",
                "suggested_alternative_template",
                "creates_erpnext_account",
                "new_account_name_template",
                "new_account_parent",
                "new_account_root_type",
                "new_account_is_group",
            ],
            order_by="creation asc",
            limit_page_length=0,
        )

        alt_rows = frappe.get_all(
            "Mapping Rule Alternate Pattern",
            fields=["parent", "tally_pattern", "tally_match_mode"],
            order_by="parent asc, idx asc",
            limit_page_length=0,
        )
        alternates_by_parent: dict[str, list[dict]] = {}
        for ar in alt_rows:
            alternates_by_parent.setdefault(ar["parent"], []).append(ar)

        positives: list[Rule] = []
        anti: list[Rule] = []
        for row in parent_rows:
            rule = rule_from_doc_row(
                row, alternates=alternates_by_parent.get(row["name"]),
            )
            if rule.is_anti_pattern:
                anti.append(rule)
            else:
                positives.append(rule)
        self._positive = positives
        self._anti = anti

    def positive_rules(self, entity_type: str = "*") -> list[Rule]:
        return _filter_by_entity(self._positive, entity_type)

    def anti_pattern_rules(self, entity_type: str = "*") -> list[Rule]:
        return _filter_by_entity(self._anti, entity_type)
