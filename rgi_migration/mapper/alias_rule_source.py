"""Alias-rule source abstraction for Tier-1 supplier matching (Item 8).

Parallel to `rule_source.py` / `supplier_source.py`. Produces `AliasRule`
value objects for the Layer-2 branch of `tier1_supplier.resolve_supplier`.

Three concrete implementations:

* `InMemoryAliasRuleSource` — list-backed, for unit tests.
* `FrappeAliasRuleSource` — reads `Supplier Alias Rule` DocType rows from
  the bench at construction time (Item 8). Applies
  ``status="confirmed"`` + entity-type filters Python-side so SQL LIKE
  substring false-positives are avoided (e.g. ``"college_prep"`` must
  NOT match ``"college"``).

Only status="confirmed" rules are returned. paused, deprecated, and any
other statuses are treated as inactive. This matches JsonFileRuleSource's
`_filter_by_entity` semantics and treats non-confirmed states as
reviewer-UI distinctions, not mapper-behavior distinctions. If future
requirements need runtime-configurable active-statuses, refactor to a
whitelist parameter at that time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


# ---------------------------------------------------------------------------
# Value object
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AliasRule:
    """One Supplier Alias Rule — the shape consumed by
    `tier1_supplier.find_alias_rule_supplier`.
    """

    name: str                   # Doc ID, e.g. "SAR-00720"
    tally_name_pattern: str     # Cleaned tally-side name to match
    tally_match_mode: str       # exact_ci | fuzzy_85 | fuzzy_90 (only exact_ci supported today)
    erpnext_supplier: str       # Target Supplier doc ID
    status: str                 # confirmed | paused | deprecated
    applies_to_entity_types: str
    creation: str = ""          # Frappe creation timestamp, used for multi-match ordering


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class AliasRuleSource(Protocol):
    """Anything that produces AliasRule instances. The mapper calls
    once at construction time and caches the result."""

    def alias_rules(self, entity_type: str = "*") -> list[AliasRule]: ...


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _filter_alias_rules(
    rules: list[AliasRule], entity_type: str,
) -> list[AliasRule]:
    """Drop non-confirmed rules, then apply the applies_to_entity_types CSV
    filter. ``entity_type="*"`` disables the filter.

    Substring-avoidance guarantee: entity membership is decided by split-
    on-comma + exact token match, NOT SQL LIKE. A rule with
    ``applies_to_entity_types="college_prep"`` must NOT match a session
    with ``entity_type="college"``.
    """
    out: list[AliasRule] = []
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


def alias_rule_from_doc_row(row: dict[str, Any]) -> AliasRule:
    """Build an `AliasRule` from a `frappe.get_all("Supplier Alias Rule")`
    row dict. Pure function — no Frappe dependency — so unit tests can
    exercise the mapping with hand-built dicts.
    """
    return AliasRule(
        name=row["name"],
        tally_name_pattern=row["tally_name_pattern"],
        tally_match_mode=row["tally_match_mode"],
        erpnext_supplier=row["erpnext_supplier"],
        status=row.get("status") or "confirmed",
        applies_to_entity_types=row.get("applies_to_entity_types") or "*",
        creation=str(row.get("creation") or ""),
    )


# ---------------------------------------------------------------------------
# Concrete implementations
# ---------------------------------------------------------------------------


class InMemoryAliasRuleSource:
    """List-backed source. For unit tests."""

    def __init__(self, rules: list[AliasRule]):
        self._rules: list[AliasRule] = list(rules)

    def alias_rules(self, entity_type: str = "*") -> list[AliasRule]:
        return _filter_alias_rules(self._rules, entity_type)


class FrappeAliasRuleSource:
    """Reads `Supplier Alias Rule` rows from the Frappe bench at
    construction time.

    Loads all rows (not filtered at SQL level) and applies the status +
    entity-type filter Python-side per the substring-avoidance guarantee
    in `_filter_alias_rules`. For R7-class volumes (<1000 rows) this is
    negligible overhead; revisit only if a bench grows to 10k+ alias
    rules.

    Ordering: rows are loaded ``ORDER BY creation ASC`` so that
    `tier1_supplier.find_alias_rule_supplier` naturally resolves
    multi-match collisions in first-promotion-wins order (oldest rule
    takes precedence — matches Q11 resolution).
    """

    def __init__(self) -> None:
        import frappe

        rows = frappe.get_all(
            "Supplier Alias Rule",
            fields=[
                "name",
                "tally_name_pattern",
                "tally_match_mode",
                "erpnext_supplier",
                "status",
                "applies_to_entity_types",
                "creation",
            ],
            order_by="creation asc",
            limit_page_length=0,
        )
        self._rules = [alias_rule_from_doc_row(r) for r in rows]

    def alias_rules(self, entity_type: str = "*") -> list[AliasRule]:
        return _filter_alias_rules(self._rules, entity_type)
