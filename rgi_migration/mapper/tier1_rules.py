"""Three-layer rule resolution for Tier-1.

Resolution order (first match wins):

    Layer 1 — positive rules with tally_match_mode == "exact_ci"
    Layer 2 — positive rules with pattern modes
              (regex, prefix_ci, suffix_ci, contains_ci)
    Layer 3 — exact-name match against the target company's ERP COA

Rules run before raw exact-name match because a seeded rule is always more
specific than a name coincidence. §4.5 (Tally `Canara Bank FDR` → ERP leaf
`FDR Canara Bank`) would otherwise lose to an exact-name hit on the ERP
group `Canara Bank FDR`. See docs/mapper_design_notes.md §2(b).

Anti-pattern matching is exposed here too, but anti-patterns are not a
resolution layer — they are a *filter* applied by the orchestrator
(mapper.py) to any candidate target.

Structural validators (P&L exclusion, group-account refusal) live in
validators.py and are applied in mapper.py — not here. Keeping this module
focused on pattern matching.
"""

from __future__ import annotations

import re
from typing import Any

from rgi_migration.mapper.rule_source import Rule


# ---------------------------------------------------------------------------
# Pattern-match primitives
# ---------------------------------------------------------------------------


def _match_single(name_lc: str, pattern: str, mode: str) -> bool:
    """Case-insensitive single-pattern match.

    ``name_lc`` must already be lower-cased; ``pattern`` is lower-cased here
    for non-regex modes. Regex is passed through with re.IGNORECASE so the
    rule author can write any Tally-name expression.
    """
    if mode == "exact_ci":
        return name_lc == pattern.lower()
    if mode == "prefix_ci":
        return name_lc.startswith(pattern.lower())
    if mode == "suffix_ci":
        return name_lc.endswith(pattern.lower())
    if mode == "contains_ci":
        return pattern.lower() in name_lc
    if mode == "regex":
        return bool(re.search(pattern, name_lc, re.IGNORECASE))
    return False


def _applicability_passes(rule: Rule, ledger: Any) -> bool:
    """Root-type and parent-chain guardrails that gate ANY pattern match."""
    if rule.applicable_root_type and rule.applicable_root_type != "Any":
        if ledger.root_type != rule.applicable_root_type:
            return False
    if rule.tally_parent_contains:
        needle = rule.tally_parent_contains.lower()
        if not any(needle in (p or "").lower() for p in ledger.parent_chain):
            return False
    return True


def rule_matches_ledger(rule: Rule, ledger: Any) -> bool:
    """True iff the rule's Tally-side patterns (primary + alternates) AND its
    guardrails accept the given ledger."""
    if not _applicability_passes(rule, ledger):
        return False
    name_lc = ledger.name.lower()
    if _match_single(name_lc, rule.tally_pattern, rule.tally_match_mode):
        return True
    for alt in rule.tally_pattern_alternates:
        if _match_single(name_lc, alt.tally_pattern, alt.tally_match_mode):
            return True
    return False


# ---------------------------------------------------------------------------
# Layer resolvers
# ---------------------------------------------------------------------------


def find_matching_positive_rule(
    ledger: Any,
    positive_rules: list[Rule],
) -> tuple[Rule, str] | None:
    """Return ``(rule, sub_tier)`` for the first positive rule that accepts
    the ledger, probing Layer 1 before Layer 2.

    ``sub_tier`` is the Mapping Decision ``tier`` value:
        - ``"tier1_rule"``    when an exact_ci rule matched
        - ``"tier1_pattern"`` when a pattern-mode rule matched
    """
    # Layer 1 — exact_ci rules
    for rule in positive_rules:
        if rule.is_anti_pattern:
            continue
        if rule.tally_match_mode != "exact_ci":
            continue
        if rule_matches_ledger(rule, ledger):
            return rule, "tier1_rule"
    # Layer 2 — pattern-mode rules
    for rule in positive_rules:
        if rule.is_anti_pattern:
            continue
        if rule.tally_match_mode == "exact_ci":
            continue
        if rule_matches_ledger(rule, ledger):
            return rule, "tier1_pattern"
    return None


def find_matching_anti_pattern(
    ledger: Any,
    anti_pattern_rules: list[Rule],
) -> Rule | None:
    """Return the first anti-pattern whose Tally-side matches, else None."""
    for rule in anti_pattern_rules:
        if rule_matches_ledger(rule, ledger):
            return rule
    return None


# Whitespace collapse + case-fold normalisation for Layer-3 exact match.
# Tally users type ledger names with varied conventions (ALL CAPS, Title
# Case, stray leading/trailing whitespace, double spaces). ERPNext COA is
# usually Title Case from the initial import but can carry CSV-import
# whitespace artefacts. Layer 3 must tolerate both sides' informality while
# remaining strict enough to avoid false positives ("Cash" must not match
# "Petty Cash"). Surfaced on CACSPU ledger "STUDENT PAYABLE CYBERVIDYA"
# (all-caps Tally input) vs COA "Student Payable Cybervidya - CACSPU".
_WS_RE = re.compile(r"\s+")


def _normalize_for_match(s: str) -> str:
    if not s:
        return ""
    return _WS_RE.sub(" ", s.strip()).lower()


def resolve_exact_name(
    ledger: Any,
    coa: dict[str, Any],
    abbr: str,
) -> str | None:
    """Layer 3 — construct ``<cleaned_tally_name> - {abbr}`` and scan the COA
    with case-insensitive, whitespace-tolerant matching. Returns the COA key
    with its *original* casing (so downstream Frappe Link-field resolution
    keeps working) if a match is found, else None. The mapper applies the
    group-account refusal check separately; this function does NOT filter
    by ``is_group``.

    Linear scan over the COA is fine at ~700 accounts × ~2000 ledgers on a
    full-entity run (<2s mapping overhead). If the COA grows materially,
    build a pre-normalised index once in ``load_coa``.
    """
    if not ledger.name:
        return None
    candidate_norm = _normalize_for_match(f"{ledger.name} - {abbr}")
    if not candidate_norm:
        return None
    for coa_key in coa:
        if _normalize_for_match(coa_key) == candidate_norm:
            return coa_key
    return None
