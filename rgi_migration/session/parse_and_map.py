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


def decision_from_doc_row(row: dict[str, Any]) -> MappedDecision:
    """Reconstruct a ``MappedDecision`` from a persisted Mapping Decision row.

    Applies the reviewer-preference rule: ``proposed_account`` on the
    returned dataclass is set to ``final_account`` if the reviewer has
    populated it, else the mapper's original ``proposed_account``. Same
    pattern for ``proposed_supplier`` vs ``final_supplier``. This means
    downstream generator code can keep reading ``d.proposed_account`` /
    ``d.proposed_supplier`` and transparently get the reviewer's final
    decision when present.

    ``row`` is a dict shaped like ``frappe.get_all("Mapping Decision",
    fields=["*"], ...)`` output. Values for missing fields default to
    dataclass defaults (0 / 0.0 / False / None).
    """
    return MappedDecision(
        tally_name=row.get("tally_name") or "",
        tally_id=row.get("tally_id"),
        tally_root_type=row.get("tally_root_type") or "",
        opening_dr=row.get("opening_dr") or 0.0,
        opening_cr=row.get("opening_cr") or 0.0,
        tier=row.get("tier") or "unmapped",
        proposed_account=(
            row.get("final_account") or row.get("proposed_account")
        ),
        review_action=row.get("review_action") or "Pending",
        matched_rule=row.get("matched_rule"),
        confidence=row.get("confidence") or 0.0,
        anti_pattern_blocked=bool(row.get("anti_pattern_blocked")),
        anti_pattern_rule=row.get("anti_pattern_rule"),
        anti_pattern_message=row.get("anti_pattern_message"),
        excluded_reason=row.get("excluded_reason"),
        # Supplier-resolution payload — reviewer's final_supplier
        # preferred over mapper's proposed_supplier so advance_je /
        # oit_csv pick up reviewer overrides transparently.
        proposed_supplier=(
            row.get("final_supplier") or row.get("proposed_supplier")
        ),
        supplier_match_score=row.get("supplier_match_score") or 0.0,
        matched_alias_rule=None,  # not persisted; audit-only if needed later
        new_supplier_name=row.get("new_supplier_name"),
    )


def ledger_from_doc_row(row: dict[str, Any]) -> Ledger:
    """Reconstruct a ``Ledger`` from a Mapping Decision row.

    Used by the Main JE generator's ``ledger_index`` lookup, which reads
    ``is_student_ledger``, ``is_system_account``, ``root_type``,
    ``opening_dr``, ``opening_cr``, and (implicitly) ``is_leaf``. All
    persisted Mapping Decisions came from leaf ledgers (the parser never
    emits groups into ``tb.ledgers``), so ``is_leaf=True`` unconditionally.

    ``parent_chain`` is returned empty — no generator reads it. If a
    downstream consumer ever needs it, split ``tally_parent_chain`` on
    " > ".
    """
    return Ledger(
        name=row.get("tally_name") or "",
        tally_id=row.get("tally_id"),
        parent_group="",
        parent_chain=[],
        root_type=row.get("tally_root_type") or "",
        opening_dr=row.get("opening_dr") or 0.0,
        opening_cr=row.get("opening_cr") or 0.0,
        net_amount=row.get("net_amount") or 0.0,
        net_side=row.get("net_side") or "Zero",
        is_leaf=True,
        is_system_account=bool(row.get("is_system_account")),
        is_student_ledger=bool(row.get("is_student_ledger")),
        is_pnl_closed_zero=bool(row.get("is_pnl_closed_zero")),
    )


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
        # Item 4 Commit 1: persist the mapper's account-creation
        # suggestion alongside the supplier one. Populated only for
        # tier=pending_account_creation rows (mapper.py:393-396); NULL
        # on every other tier including unmapped, where the dialog will
        # open with empty defaults and the reviewer fills from scratch.
        "new_account_name": decision.new_account_name,
        "new_account_parent": decision.new_account_parent,
        "new_account_root_type": decision.new_account_root_type,
        "new_account_is_group": (
            1 if decision.new_account_is_group else 0
        ),
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


# ---------------------------------------------------------------------------
# Frappe-side loader — used by generators (Item 2 Commit 3 pivot)
# ---------------------------------------------------------------------------


# Session statuses from which generators are permitted to run. Must
# have gone through `run_mapper` first (status transitions to
# Reviewing after persistence). Generating / Generated / Submitted
# are allowed because re-generation of existing artefacts is a
# legitimate reviewer action.
GENERATOR_ALLOWED_STATUSES = frozenset({
    "Reviewing",
    "Generating",
    "Generated",
    "Submitted",
})


def require_generator_status(session: Any, generator_name: str) -> None:
    """Guard generators from running on un-mapped sessions.

    Raises a descriptive ``frappe.ValidationError`` (resolved via
    ``frappe.throw``) if the session's status is not in
    :data:`GENERATOR_ALLOWED_STATUSES`. Caller is responsible for
    passing a session doc with a ``status`` attribute; the guard does
    not refetch.
    """
    import frappe

    if session.status not in GENERATOR_ALLOWED_STATUSES:
        frappe.throw(
            f"{generator_name} cannot run on session "
            f"{session.name!r} with status {session.status!r}. "
            f"Run Mapper first from the session form to populate "
            f"Mapping Decisions; allowed statuses are "
            f"{sorted(GENERATOR_ALLOWED_STATUSES)}."
        )


def load_decisions_from_session(
    session_name: str,
) -> tuple[list[MappedDecision], dict[tuple[str, str], Ledger]]:
    """Read persisted Mapping Decisions + rebuild ledger_index.

    Returns ``(decisions, ledger_index)``:
      * ``decisions`` — ``MappedDecision`` list with reviewer's
        ``final_account`` / ``final_supplier`` preferred over the
        mapper's ``proposed_*`` fields (see ``decision_from_doc_row``).
      * ``ledger_index`` — ``{(tally_name, tally_id or ""): Ledger}``
        map suitable for the main-JE generator's lookups.

    Called by generators in place of the pre-Item-2 ``_parse_and_map``
    helpers. Does not re-parse the source file.
    """
    import frappe

    rows = frappe.get_all(
        "Mapping Decision",
        filters={"session": session_name},
        fields=["*"],
        order_by="creation",
        limit_page_length=0,
    )

    decisions: list[MappedDecision] = []
    ledger_index: dict[tuple[str, str], Ledger] = {}
    for row in rows:
        d = decision_from_doc_row(row)
        l = ledger_from_doc_row(row)
        decisions.append(d)
        # Identity key matches the persistence path — see
        # index_ledgers_by_identity above.
        ledger_index[(d.tally_name, d.tally_id or "")] = l
    return decisions, ledger_index


def synthesize_tb_from_ledger_index(
    ledger_index: dict[tuple[str, str], Ledger],
    *,
    company_name: str = "",
    tb_date: str = "",
    source_format: str = "",
) -> ParsedTallyTB:
    """Build a minimal ``ParsedTallyTB`` from a reconstructed ledger_index.

    Used by ``opening_je.generate_main_opening_je`` to keep the
    ``build_je_payload(tb=...)`` signature unchanged after the pivot
    away from reparse-and-remap. Only ``ledgers`` is populated;
    other fields are placeholders since ``build_je_payload`` only
    reads ``tb.ledgers``.
    """
    return ParsedTallyTB(
        company_name=company_name,
        tb_date=tb_date,
        source_format=source_format,
        source_file="",
        ledgers=list(ledger_index.values()),
        groups=[],
        total_dr=0.0,
        total_cr=0.0,
        is_balanced=False,
    )
