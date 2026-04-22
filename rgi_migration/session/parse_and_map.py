"""Frappe-free parse-and-map core for the Tally Migration Session lifecycle.

Item 2 Commit 2 — extracts the parse + map pipeline out of the per-generator
``_parse_and_map`` helpers (opening_je.py / oit_csv.py / advance_je.py) into
a single canonical entry point. The generators continue to use their own
helpers in Commit 2; Commit 3 pivots them to read persisted Mapping Decisions.

Public surface:

* :func:`parse_source` — dispatches to the XML or Excel parser.
* :func:`run_mapper_pipeline` — runs the mapper against an already-parsed
  ``ParsedTallyTB`` with caller-supplied COA, suppliers, rule source. Pure
  (no Frappe), fully testable without a bench.
* :func:`run_parse_and_map` — composes ``parse_source`` + ``run_mapper_pipeline``
  for the common "give me everything" case.
* :func:`decision_to_row_dict` — builds the ``Mapping Decision`` insert-dict
  for one ``MappedDecision`` joined against its source ``Ledger``.
* :func:`index_ledgers_by_identity` — ``{(name, tally_id or ""): Ledger}`` map
  so the persistence loop can look up metadata the dataclass doesn't carry
  (parent_chain, net_amount/side, parser-level flags).

Architecture: per mapper_design_notes §9.1, ``Mapping Decision`` DocType is
the authoritative persistence layer. Callers invoke these helpers from a
Frappe wrapper (the ``TallyMigrationSession`` controller), assemble the
insert dicts via :func:`decision_to_row_dict`, and bulk-insert.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rgi_migration.mapper.mapper import (
    CoaAccount,
    MappedDecision,
    Mapper,
    summarize,
)
from rgi_migration.mapper.rule_source import RuleSource
from rgi_migration.mapper.supplier_source import InMemorySupplierSource, Supplier
from rgi_migration.parsers.normalized_schema import Ledger, ParsedTallyTB


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParseAndMapResult:
    """Bundle of parse + map output for one session run.

    ``summary`` is the dict from :func:`rgi_migration.mapper.mapper.summarize`.
    """

    tb: ParsedTallyTB
    decisions: list[MappedDecision]
    summary: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Parse dispatch
# ---------------------------------------------------------------------------


def parse_source(source_path: str, source_format: str) -> ParsedTallyTB:
    """Dispatch to the XML or Excel parser based on ``source_format``.

    Normalises ``source_format`` to lowercase; unrecognised values fall
    through to XML (matching the generators' existing behaviour).
    """
    fmt = (source_format or "xml").strip().lower()
    if fmt == "excel":
        from rgi_migration.parsers.tally_excel_parser import parse_excel
        return parse_excel(source_path)
    from rgi_migration.parsers.tally_xml_parser import parse_xml
    return parse_xml(source_path)


# ---------------------------------------------------------------------------
# Mapper pipeline
# ---------------------------------------------------------------------------


def run_mapper_pipeline(
    *,
    tb: ParsedTallyTB,
    abbr: str,
    entity_type: str,
    coa: dict[str, CoaAccount],
    suppliers: list[Supplier],
    rule_source: RuleSource,
    supplier_fuzzy_threshold: float = 85.0,
) -> ParseAndMapResult:
    """Run the Tier-1 mapper over an already-parsed TB.

    Pure — takes every Frappe-sourced dependency (COA, suppliers, rules) as
    an argument so the caller can swap in fakes for testing. The mapper
    itself is unchanged; this is the session-lifecycle wrapper.
    """
    mapper = Mapper(
        rule_source,
        coa,
        abbr=abbr,
        entity_type=entity_type,
        supplier_source=InMemorySupplierSource(suppliers),
        supplier_fuzzy_threshold=supplier_fuzzy_threshold,
    )
    decisions = mapper.map_all(tb.ledgers)
    return ParseAndMapResult(
        tb=tb,
        decisions=decisions,
        summary=summarize(decisions),
    )


def run_parse_and_map(
    *,
    source_path: str,
    source_format: str,
    abbr: str,
    entity_type: str,
    coa: dict[str, CoaAccount],
    suppliers: list[Supplier],
    rule_source: RuleSource,
    supplier_fuzzy_threshold: float = 85.0,
) -> ParseAndMapResult:
    """Composed pipeline — parse the source, then run the mapper."""
    tb = parse_source(source_path, source_format)
    return run_mapper_pipeline(
        tb=tb,
        abbr=abbr,
        entity_type=entity_type,
        coa=coa,
        suppliers=suppliers,
        rule_source=rule_source,
        supplier_fuzzy_threshold=supplier_fuzzy_threshold,
    )


# ---------------------------------------------------------------------------
# Persistence dict-builder
# ---------------------------------------------------------------------------


def index_ledgers_by_identity(tb: ParsedTallyTB) -> dict[tuple[str, str], Ledger]:
    """Build ``{(name, tally_id or ""): Ledger}`` for MappedDecision → Ledger
    join during persistence.

    Identity is the (name, tally_id) tuple per CLAUDE.md's
    "Name collisions by tally_id" guidance — name alone is not unique.
    Uses ``tally_id or ""`` so ``None`` tally_ids don't trip the key.
    """
    return {(l.name, l.tally_id or ""): l for l in tb.ledgers}


def decision_to_row_dict(
    decision: MappedDecision,
    ledger: Ledger,
    session_name: str,
) -> dict[str, Any]:
    """Build the ``Mapping Decision`` insert dict for one decision.

    Joins the mapper's ``MappedDecision`` with its source ``Ledger`` to
    populate DocType fields that the mapper dataclass doesn't carry
    (parent_chain, net_amount, net_side, is_* flags). Caller passes the
    result to ``frappe.get_doc(row).insert(ignore_permissions=True)``.

    Invariant: ``decision.tally_name == ledger.name`` AND
    ``decision.tally_id == ledger.tally_id``. Caller is responsible for
    the join key; this helper trusts the pairing.
    """
    parent_chain = (
        " > ".join(ledger.parent_chain) if ledger.parent_chain else ""
    )
    return {
        "doctype": "Mapping Decision",
        "session": session_name,
        # -- Tally-side input
        "tally_name": decision.tally_name,
        "tally_id": decision.tally_id,
        "tally_parent_chain": parent_chain,
        "tally_root_type": decision.tally_root_type,
        "opening_dr": decision.opening_dr,
        "opening_cr": decision.opening_cr,
        "net_amount": ledger.net_amount,
        "net_side": ledger.net_side,
        "is_pnl_closed_zero": 1 if ledger.is_pnl_closed_zero else 0,
        "is_student_ledger": 1 if ledger.is_student_ledger else 0,
        "is_system_account": 1 if ledger.is_system_account else 0,
        # -- Mapper proposal
        "tier": decision.tier,
        "proposed_account": decision.proposed_account,
        "proposed_supplier": decision.proposed_supplier,
        "new_supplier_name": decision.new_supplier_name,
        "matched_rule": decision.matched_rule,
        "confidence": decision.confidence,
        "supplier_match_score": decision.supplier_match_score,
        # proposed_dr / proposed_cr left unset — review UI reads opening_dr/cr
        # and the reviewer's final_dr/cr directly; mapper's proposal is the
        # same as opening in the unedited case.
        # -- Anti-pattern provenance
        "anti_pattern_blocked": 1 if decision.anti_pattern_blocked else 0,
        "anti_pattern_rule": decision.anti_pattern_rule,
        "anti_pattern_message": decision.anti_pattern_message,
        # -- Reviewer decision (initial state from mapper)
        "review_action": decision.review_action,
        "final_account": None,
        "final_supplier": None,
        "reviewer_notes": None,
        "excluded_reason": decision.excluded_reason,
    }
