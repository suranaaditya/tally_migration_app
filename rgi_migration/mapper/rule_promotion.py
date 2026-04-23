"""Pure-core rule-promotion builder for Item 5 Commit 1 (Account-side).

Given a reviewer-approved Mapping Decision and its session context, produce
the deterministic payload Frappe will persist to a ``Mapping Rule`` row.
No Frappe imports; callable from tests and the whitelist wrapper
identically.

Design decisions frozen in Item 5 Phase A (see
``docs/WEEK4_ITEM5_PHASE_A.md`` if/when created):

* v1 captures ``exact_ci`` match mode only. regex / contains / prefix /
  suffix are deferred.
* ``{ABBR}`` substitution is case-sensitive, whole-word, all occurrences.
  Zero-occurrence templates ("literal rules") are valid but require
  explicit reviewer confirmation at the UI layer — the payload builder
  surfaces the count via ``RulePayload.abbr_occurrences`` so the caller
  can gate on it.
* ``source_hash`` uses the same SHA1 construction as
  ``scripts/seed_mapping_rules.py:source_hash`` so seed-rule idempotency
  and session-promoted-rule idempotency live in the same key space.
* ``applies_to_entity_types`` defaults to the session's entity type, not
  ``"*"`` (Phase A A-10). Reviewers can widen a rule manually via the
  Mapping Rule form.
* ``source_section`` is ``session-MR-{md_name}`` so Mapping Rule rows
  promoted from sessions are cleanly distinguishable from §4.X-seeded
  rows in audit queries.

α-architecture note (Phase A A-0): promoted rows sit inert in the DocType
until Item 8 lands ``FrappeRuleSource``. The live mapper today reads
``docs/seed_plan.json`` via ``JsonFileRuleSource``. This is a deliberate
boundary — see the integration test in
``rgi_migration/tests/test_rule_promotion.py`` for the codified
invariant.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AbbrSubstitutionResult:
    """Outcome of a single {ABBR} substitution run.

    ``substituted`` is the transformed text; equal to the input when
    ``occurrences == 0``. ``is_literal`` is a derived convenience flag so
    callers don't have to re-check the count. A literal template is NOT
    an error — it just means the reviewer is promoting a rule whose
    target account name doesn't include the entity abbr verbatim, which
    is uncommon but valid (e.g. a shared cross-entity ledger).
    """

    substituted: str
    occurrences: int
    is_literal: bool


@dataclass(frozen=True)
class RulePayload:
    """Full payload for a ``Mapping Rule`` row promoted from a session
    decision.

    The Frappe wrapper maps these fields 1:1 onto DocType fields:

    * ``rule_name`` → ``rule_name``
    * ``tally_pattern`` → ``tally_pattern``
    * ``tally_match_mode`` → ``tally_match_mode`` (always ``exact_ci``)
    * ``applicable_root_type`` → ``applicable_root_type``
    * ``erpnext_account_template`` → ``erpnext_account_template``
    * ``raw_final_account`` → ``raw_final_account`` (field added in
      Item 5 Commit 1; see ``setup.create_doctypes.run_item5_patch``)
    * ``source_section`` / ``source_hash`` → same
    * ``applies_to_entity_types`` → same
    * ``created_from`` = ``"session_review"`` → same
    * ``created_via_session`` → Link to ``Tally Migration Session``
    * ``status`` = ``"confirmed"``
    * ``is_anti_pattern`` = False (positive rule only in v1)

    Observability counts (``abbr_occurrences``, ``is_literal_template``)
    are NOT persisted — they're surfaced in the preview step so the
    reviewer can decide whether the substitution looks right before
    committing.
    """

    rule_name: str
    tally_pattern: str
    tally_match_mode: str
    applicable_root_type: str
    erpnext_account_template: str
    raw_final_account: str
    abbr_occurrences: int
    is_literal_template: bool
    source_section: str
    source_hash: str
    applies_to_entity_types: str
    created_from: str
    created_via_session: str
    status: str
    is_anti_pattern: bool


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PromotionError(Exception):
    """Base class for promotion refusals surfaced to the reviewer."""


class NotPromotable(PromotionError):
    """Raised when the Mapping Decision's state is insufficient to
    promote (missing final_account, empty tally_name, unset abbr, etc.).
    Reviewer-facing message goes in ``str(exc)``.
    """


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_ROOT_TYPES = frozenset({"Asset", "Liability", "Equity", "Income", "Expense"})


def compute_source_hash(
    source_section: str, is_anti_pattern: bool, tally_pattern: str
) -> str:
    """SHA1 idempotency key — parallels
    ``scripts.seed_mapping_rules.source_hash`` exactly.

    Key format: ``{source_section}|{int(is_anti_pattern)}|{tally_pattern}``.
    Must remain byte-for-byte identical so seed + session-promoted rows
    collide on the same logical pattern.
    """
    key = f"{source_section}|{int(is_anti_pattern)}|{tally_pattern}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def substitute_abbr(text: str, abbr: str) -> AbbrSubstitutionResult:
    """Replace every whole-word occurrence of ``abbr`` with ``{ABBR}``.

    Rules (Phase A A-9):
      * Case-sensitive. "CACSPU" does not match "cacspu" or "Cacspu".
      * Whole-word via ``\\b`` — "CAC" would not match inside "CACAO",
        "PUNE" does not match inside "PUNEET".
      * All occurrences replaced (not just the first). Count surfaced for
        the reviewer preview dialog.
      * Empty abbr → no-op with ``is_literal=True``. Defensive; the
        wrapper refuses empty abbrs up-front, but keeping this branch
        total makes the function safe to call in tests without
        preconditions.
    """
    if not abbr:
        return AbbrSubstitutionResult(
            substituted=text, occurrences=0, is_literal=True
        )
    pattern = re.compile(rf"\b{re.escape(abbr)}\b")
    count = len(pattern.findall(text))
    substituted = pattern.sub("{ABBR}", text)
    return AbbrSubstitutionResult(
        substituted=substituted,
        occurrences=count,
        is_literal=(count == 0),
    )


def clean_tally_pattern(raw: str) -> str:
    """Normalize a Tally ledger name for use as a rule's ``tally_pattern``.

    The parser's ``Ledger.name`` field already strips the numeric
    ``-{ID}`` suffix (``Income Expenditure A/c-2127`` → ``Income
    Expenditure A/c``), so this is mostly whitespace cleanup: strip
    surrounding whitespace and collapse internal runs to single spaces
    so leading-space artefacts don't produce two distinct rules for
    the same logical pattern.
    """
    if not raw:
        return ""
    return re.sub(r"\s+", " ", raw.strip())


def derive_rule_name(tally_pattern: str, abbr: str) -> str:
    """Human-readable ``rule_name`` — format ``{abbr}: {tally_pattern}``.

    Puts the promoting entity's abbr at the front for quick scanning in
    the Mapping Rule list view (which sorts by ``modified DESC`` by
    default). Not unique — two entities promoting the same pattern with
    different final accounts will produce different rule_names (refused
    by the conflict check before they both land); two promotions of the
    same (pattern, final account) from the same entity will collide on
    source_hash well before rule_name matters.
    """
    return f"{abbr}: {tally_pattern}"


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------


def build_rule_payload(
    *,
    md_name: str,
    tally_name: str,
    tally_root_type: str | None,
    final_account: str,
    session_name: str,
    abbr: str,
    entity_type: str | None,
) -> RulePayload:
    """Assemble the ``Mapping Rule`` payload from a promotion-ready MD.

    All inputs are extracted from Frappe docs by the caller (wrapper
    responsibility); this function has no Frappe dependency.

    Raises:
        NotPromotable — if any input is missing or empty. Message is
            reviewer-facing and identifies the MD by name.
    """
    tally_pattern = clean_tally_pattern(tally_name)
    if not tally_pattern:
        raise NotPromotable(
            f"Mapping Decision {md_name!r} has no tally_name to promote from."
        )
    if not final_account:
        raise NotPromotable(
            f"Mapping Decision {md_name!r} has no final_account — approve "
            "with a target before promoting."
        )
    if not session_name:
        raise NotPromotable(
            f"Mapping Decision {md_name!r} has no session link."
        )
    if not abbr:
        raise NotPromotable(
            f"Session {session_name!r} has no company_abbr set."
        )

    subst = substitute_abbr(final_account, abbr)
    source_section = f"session-MR-{md_name}"
    src_hash = compute_source_hash(
        source_section=source_section,
        is_anti_pattern=False,
        tally_pattern=tally_pattern,
    )

    applicable_root = (tally_root_type or "").strip()
    applicable_root_type = (
        applicable_root if applicable_root in _ROOT_TYPES else "Any"
    )

    applies_to = (entity_type or "").strip() or "*"

    return RulePayload(
        rule_name=derive_rule_name(tally_pattern, abbr),
        tally_pattern=tally_pattern,
        tally_match_mode="exact_ci",
        applicable_root_type=applicable_root_type,
        erpnext_account_template=subst.substituted,
        raw_final_account=final_account,
        abbr_occurrences=subst.occurrences,
        is_literal_template=subst.is_literal,
        source_section=source_section,
        source_hash=src_hash,
        applies_to_entity_types=applies_to,
        created_from="session_review",
        created_via_session=session_name,
        status="confirmed",
        is_anti_pattern=False,
    )


__all__ = [
    "AbbrSubstitutionResult",
    "RulePayload",
    "PromotionError",
    "NotPromotable",
    "compute_source_hash",
    "substitute_abbr",
    "clean_tally_pattern",
    "derive_rule_name",
    "build_rule_payload",
]
