"""Tier-1 rule-based mapper: Tally ledgers -> ERPNext Account proposals.

Three-layer rule resolution (first match wins):
    Layer 1 — positive rules with tally_match_mode == "exact_ci"
    Layer 2 — positive rules with pattern modes (regex, prefix_ci, suffix_ci, contains_ci)
    Layer 3 — exact-name match against the target company's ERP COA

Plus two structural validators that run alongside resolution:
    - P&L root-type exclusion (Income/Expense ledgers skipped)
    - Group-account refusal (no posting to group accounts)

Public API:
    from rgi_migration.mapper import Mapper, JsonFileRuleSource, load_coa
    rs = JsonFileRuleSource(Path("docs/seed_plan.json"))
    coa = load_coa(Path("rgi_migration/tests/fixtures/cacspu_erpnext_coa_real.csv"))
    m = Mapper(rs, coa, abbr="CACSPU")
    decisions = m.map_all(parsed_tb.ledgers)

CLI (python -m rgi_migration.mapper.mapper ...): see mapper.py module docstring.
"""

from __future__ import annotations

from rgi_migration.mapper.mapper import (
    CoaAccount,
    MappedDecision,
    Mapper,
    load_coa,
    summarize,
)
from rgi_migration.mapper.rule_source import (
    AlternatePattern,
    FrappeRuleSource,
    InMemoryRuleSource,
    JsonFileRuleSource,
    Rule,
    RuleSource,
)
from rgi_migration.mapper.supplier_source import (
    CsvFileSupplierSource,
    FrappeSupplierSource,
    InMemorySupplierSource,
    Supplier,
    SupplierSource,
)

__all__ = [
    "AlternatePattern",
    "CoaAccount",
    "CsvFileSupplierSource",
    "FrappeRuleSource",
    "FrappeSupplierSource",
    "InMemoryRuleSource",
    "InMemorySupplierSource",
    "JsonFileRuleSource",
    "MappedDecision",
    "Mapper",
    "Rule",
    "RuleSource",
    "Supplier",
    "SupplierSource",
    "load_coa",
    "summarize",
]
