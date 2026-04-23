"""Pure-core alias-rule promotion builder for Item 5 Commit 2
(Supplier Alias Rule side).

Given a reviewer-approved, supplier-target Mapping Decision and its
session context, produce the deterministic payload Frappe will persist
to a ``Supplier Alias Rule`` row. No Frappe imports; callable from
tests and the whitelist wrapper identically.

Design decisions frozen in Item 5 Commit 2 Phase A:

* **S-1 _clean reuse**: ``tally_name_pattern`` stored on the rule is
  the output of :func:`rgi_migration.mapper.tier1_supplier._clean`
  byte-for-byte — NOT a reimplementation. This guarantees Item 8's
  future ``FrappeSupplierSource`` Layer-2 matcher
  (``find_alias_rule_supplier``) reads the stored pattern and
  compares it against ``_clean``'d incoming ledger names with zero
  divergence. If ``tier1_supplier._clean`` is ever updated (e.g. new
  party-ID suffix convention), every promoted rule and every live
  match uses the new semantic together.

* **S-2 match mode**: hardcoded ``"exact_ci"`` in v1.
  ``tier1_supplier_fuzzy`` MDs DO get promoted (``apply_supplier_
  resolution`` tier-lifts them to ``tier1_supplier_exact`` at dialog
  time, so the reviewer's manual authority has already crystallised
  the exact match by the time promotion fires). Fuzzy match modes
  land alongside or after Item 8.

* **S-3 no {ABBR} substitution**: supplier master is entity-agnostic
  on this bench (0/14 existing Supplier records contain "CACSPU").
  The payload stores the ``final_supplier`` Link verbatim. No
  occurrence count, no is_literal flag.

* **source_hash namespace prefix**: ``session-SAR-{md_name}`` (NOT
  ``session-MR-`` used by account-side). Prevents cross-namespace
  hash collisions between the two Item 5 rule families even under
  hypothetical pattern coincidence. See
  :func:`compute_supplier_source_hash`.

* **α-architecture (Phase A A-0 carried forward)**: promoted rows
  sit inert in the DocType until Item 8 lands
  ``FrappeSupplierSource`` + flips ``find_alias_rule_supplier`` off
  the ``return None`` stub. The integration test in
  ``test_alias_promotion.py`` codifies this invariant identically to
  Commit 1's account-side test.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

# Direct import — S-1 resolution mandates re-use, not copy. Also asserted
# by test_alias_promotion.py::test_clean_is_tier1_supplier_clean to
# prevent silent fork on future refactor.
from rgi_migration.mapper.tier1_supplier import _clean


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AliasRulePayload:
    """Full payload for a ``Supplier Alias Rule`` row promoted from a
    session decision.

    The Frappe wrapper maps these fields 1:1 onto DocType fields:

    * ``tally_name_pattern`` → ``tally_name_pattern`` (cleaned)
    * ``tally_match_mode`` → ``tally_match_mode`` (always ``exact_ci``)
    * ``erpnext_supplier`` → ``erpnext_supplier`` (Link, verbatim)
    * ``applies_to_entity_types`` → ``applies_to_entity_types``
      (session's entity_type, defaults ``"*"``)
    * ``created_from`` = ``"session_review"`` → ``created_from``
    * ``source_hash`` → not a DocType field on Supplier Alias Rule,
      carried in-memory for idempotency checks by the whitelist.
      Intentionally NOT persisted to avoid schema churn; lookups
      use the composite ``(tally_name_pattern, tally_match_mode)``
      key, which is sufficient given no fuzzy mode split in v1.

    The payload is shape-deliberately different from
    :class:`~rgi_migration.mapper.rule_promotion.RulePayload`: no
    ``abbr_occurrences`` / ``is_literal_template`` / ``raw_final_*``
    fields because S-3 skipped {ABBR} substitution. No
    ``rule_name`` because Supplier Alias Rule uses
    ``format:SAR-{#####}`` autonaming only — no human-readable label
    slot.
    """

    tally_name_pattern: str
    tally_match_mode: str
    erpnext_supplier: str
    applies_to_entity_types: str
    created_from: str
    source_hash: str
    # Optional provenance — Supplier Alias Rule DocType has no
    # created_via_session field today (unlike Mapping Rule which added
    # one in Item 5 Commit 1). Carry the session name anyway; the
    # wrapper persists it in source_entities CSV as a combined
    # audit marker.
    session_name: str


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PromotionError(Exception):
    """Base class for alias-promotion refusals surfaced to the reviewer."""


class NotPromotable(PromotionError):
    """Raised when the Mapping Decision's state is insufficient to promote
    (wrong tier, missing final_supplier, empty tally_name, etc.).
    Reviewer-facing message goes in ``str(exc)``.
    """


# ---------------------------------------------------------------------------
# Tier validation
# ---------------------------------------------------------------------------


# The tiers that COULD carry final_supplier. tier1_supplier_fuzzy is
# included because save_supplier_resolution tier-lifts fuzzy matches to
# tier1_supplier_exact at dialog commit — but a post-hoc promotion on an
# MD whose persisted tier is still fuzzy (unusual but possible if data
# written via API bypassed the dialog) is still valid. The wrapper
# enforces review_action=Approved, which is the real gate.
_SUPPLIER_TIERS = frozenset({
    "tier1_supplier_exact",
    "tier1_supplier_alias",
    "tier1_supplier_fuzzy",
    "pending_supplier_creation",  # post-SCR-approval this tier can
                                   # persist even with final_supplier set
})


# ---------------------------------------------------------------------------
# source_hash — supplier namespace
# ---------------------------------------------------------------------------


def compute_supplier_source_hash(
    source_section: str,
    cleaned_tally_pattern: str,
    erpnext_supplier: str,
) -> str:
    """SHA1 idempotency key for a Supplier Alias Rule.

    Key format::

        {source_section}|0|{cleaned_tally_pattern}|{erpnext_supplier}

    Distinct from the account-side formula
    (``{source_section}|{int(is_anti_pattern)}|{tally_pattern}``):

    * Supplier has no anti-pattern axis; the ``|0|`` placeholder stays
      in the key for positional clarity and cross-namespace hash
      non-collision (S-7 probe: structurally parallel account /
      supplier inputs must produce DIFFERENT hashes even if the
      namespace prefix logic regresses).
    * ``erpnext_supplier`` IS in the key — two promotions with the
      same pattern to different suppliers produce different hashes,
      enabling the conflict-detect path to find them as distinct
      rows rather than colliding at source_hash level. Account-side
      hash excludes target deliberately (account conflicts surface
      via template lookup, not hash); supplier side includes target
      because there's no template-vs-raw split to key on.

    Callers always pass ``source_section`` starting with
    ``"session-SAR-"`` for session-promoted rules; seed-loaded rules
    would use whatever source markers a future seed introduces.
    """
    key = f"{source_section}|0|{cleaned_tally_pattern}|{erpnext_supplier}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------


def build_alias_rule_payload(
    *,
    md_name: str,
    tally_name: str,
    tier: str,
    final_supplier: str,
    session_name: str,
    entity_type: str | None,
) -> AliasRulePayload:
    """Assemble the ``Supplier Alias Rule`` payload from a promotion-ready
    MD.

    All inputs extracted from Frappe docs by the caller (wrapper
    responsibility); this function has no Frappe dependency.

    Args:
        md_name: Mapping Decision ``name`` field.
        tally_name: Raw ``MD.tally_name`` value; ``_clean`` is applied
            internally.
        tier: MD's persisted tier; validated against the supplier-tier
            set. tier1_supplier_exact / tier1_supplier_fuzzy /
            tier1_supplier_alias / pending_supplier_creation all accepted.
        final_supplier: The reviewer-picked Supplier doc name. Stored
            verbatim on the rule (no {ABBR} substitution).
        session_name: The promoting session's name.
        entity_type: Session's entity_type (from Company Abbreviation
            row). Falls back to ``"*"`` when missing.

    Raises:
        NotPromotable — on any missing / invalid input. Message is
            reviewer-facing and identifies the MD by name.
    """
    if not md_name:
        raise NotPromotable("Mapping Decision name is empty.")
    if tier and tier not in _SUPPLIER_TIERS:
        raise NotPromotable(
            f"Mapping Decision {md_name!r} has tier {tier!r}; supplier "
            f"alias promotion requires a supplier-target tier "
            f"({sorted(_SUPPLIER_TIERS)})."
        )
    if not final_supplier:
        raise NotPromotable(
            f"Mapping Decision {md_name!r} has no final_supplier — "
            "approve via the Supplier Resolution dialog before "
            "promoting."
        )
    if not session_name:
        raise NotPromotable(
            f"Mapping Decision {md_name!r} has no session link."
        )

    # S-1: cleaned pattern via the shared tier1_supplier helper.
    cleaned = _clean(tally_name or "")
    if not cleaned:
        raise NotPromotable(
            f"Mapping Decision {md_name!r} has no tally_name to promote "
            "from (or the cleaned form is empty)."
        )

    source_section = f"session-SAR-{md_name}"
    src_hash = compute_supplier_source_hash(
        source_section=source_section,
        cleaned_tally_pattern=cleaned,
        erpnext_supplier=final_supplier,
    )
    applies_to = (entity_type or "").strip() or "*"

    return AliasRulePayload(
        tally_name_pattern=cleaned,
        tally_match_mode="exact_ci",
        erpnext_supplier=final_supplier,
        applies_to_entity_types=applies_to,
        created_from="session_review",
        source_hash=src_hash,
        session_name=session_name,
    )


__all__ = [
    "AliasRulePayload",
    "PromotionError",
    "NotPromotable",
    "compute_supplier_source_hash",
    "build_alias_rule_payload",
]
