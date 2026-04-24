"""Tier-1 supplier resolution for party ledgers.

Called for ledgers whose parent chain shows vendor/creditor lineage
(currently "Sundry Creditors" and descendants). Three-layer, parallel
shape to tier1_rules.find_matching_positive_rule:

    Layer 1 — exact_ci on supplier_name (case/whitespace insensitive)
    Layer 2 — saved Supplier Alias Rule hits (exact_ci | fuzzy_85 | fuzzy_90)
              Stub for now: no alias rules are seeded on
              erp.jewonline.in yet. The layer returns None until the
              reviewer-promotion workflow starts populating rules.
    Layer 3 — general fuzzy match against the Supplier master, default
              threshold 85%. Uses rapidfuzz.fuzz.WRatio which handles
              word-order, partial matches, and whitespace reasonably
              well for Indian vendor names.

The orchestrator in mapper.py decides what to do with the result:
match -> tier1_supplier_exact/fuzzy decision; no match ->
pending_supplier_creation decision with review_action
"Pending Supplier Creation" (ready for the DocType workflow to pick
up once Session is backed by real data).

Debtor (customer) ledgers are NOT routed through here. Sundry Debtors
under this entity are student ledgers, handled by the Phase-2 student
CSV handoff per docs/dux_voucher_integration.md (out of scope for
Tier-1 supplier matching).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from rapidfuzz import fuzz, process

from rgi_migration.mapper.alias_rule_source import AliasRule
from rgi_migration.mapper.supplier_source import Supplier, SupplierSource

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Party-ledger detection
# ---------------------------------------------------------------------------

# Parent-chain substrings that mark a ledger as a vendor/creditor party.
# Lowercased; match via `in` on each parent-chain element (also lowercased).
_VENDOR_PARENT_MARKERS = (
    "sundry creditors",
)

# Control-account name patterns that short-circuit party routing even when
# a ledger lives under Sundry Creditors. These are either (a) rules-library
# candidates that haven't been seeded yet or (b) pseudo-accounts that exist
# for Tally bookkeeping mechanics (GST, TDS, rounding, provisions). They
# stay in the main JE flow — NOT routed to OIT / Supplier Creation Request.
#
# All patterns are case-insensitive and anchored (^) at the start of the
# cleaned ledger name. Word-boundary (\b) at the end prevents partial-prefix
# matches against vendor names that happen to start with similar tokens.
#
# Future work (Week 4+): make this extensible via a `Control Account
# Pattern` DocType so ops staff can add entries without code edits. For
# Week 3 scope, patterns are hardcoded here and documented in
# docs/mapper_design_notes.md sec 6.
_CONTROL_ACCOUNT_PATTERNS = [
    r"^Advances?\s+Received\b",             # Advance Received, Advances Received (For Expenses|...)
    r"^TDS\s+Payable\b",                    # TDS Payable, TDS Payable 194C, TDS Payable On Rent
    r"^(GST\s+)?Tax\s+Collected\b",         # Tax Collected at Source, GST Tax Collected (...)
    r"^Provision\s+for\b",                  # Provision for Expenses, Provision for Audit Fees
    r"^Suspense\s+A/c\b",                   # Suspense A/c (slash required — Ac alone is ambiguous)
    r"^Unadjusted\b",                       # Unadjusted Advance, Unadjusted Receipts
    r"^GST\s+(Payable|Input|Output)\b",     # GST Payable, GST Input, GST Output
    r"^Round(ing)?\s+off\b",                # Round off, Rounding off
]
_COMPILED_CONTROL_PATTERNS = [
    re.compile(pat, re.IGNORECASE) for pat in _CONTROL_ACCOUNT_PATTERNS
]


def is_control_account(ledger: Any) -> bool:
    """True if the ledger name matches any hardcoded control-account
    pattern. Control accounts stay in the main JE flow (account-level
    balances), never route to Supplier Creation Request even when
    they live under Sundry Creditors.
    """
    name = str(getattr(ledger, "name", "") or "").strip()
    if not name:
        return False
    for pat in _COMPILED_CONTROL_PATTERNS:
        if pat.search(name):
            return True
    return False


def is_vendor_party_ledger(ledger: Any) -> bool:
    """True if the ledger's parent chain indicates a Sundry-Creditors vendor
    AND the ledger name is not a control-account pattern.

    Control accounts (GST, TDS, Provisions, Rounding, etc.) live under
    Sundry Creditors in Tally but aren't vendors — they're bookkeeping
    mechanics for which no Supplier master entry should ever exist.
    Excluding them here keeps them out of the Supplier Creation Request
    pipeline and lets them flow through normal rule / exact-name matching
    in the main JE path.
    """
    if is_control_account(ledger):
        return False
    chain = getattr(ledger, "parent_chain", None) or []
    chain_lc = [str(p).lower() for p in chain]
    for marker in _VENDOR_PARENT_MARKERS:
        if any(marker in p for p in chain_lc):
            return True
    return False


# ---------------------------------------------------------------------------
# Layer helpers
# ---------------------------------------------------------------------------


def _clean(name: str) -> str:
    """Normalize a name for comparison: strip surrounding whitespace,
    collapse internal multi-space to single, drop a trailing
    Tally-party-ID suffix like '-VA0243' / '-SA0205' / '-SX0001' when
    present. The parser's cleaned-name field already strips purely
    numeric '-{digits}' IDs; party IDs carry an uppercase-letter prefix
    (V/S/P/G/...) and so survive the parser's strip.
    """
    import re

    s = (name or "").strip()
    if not s:
        return s
    # Collapse internal whitespace runs
    s = re.sub(r"\s+", " ", s)
    # Strip trailing Tally-party-ID suffix: -[UPPER][UPPER]\d{3,}
    s = re.sub(r"-[A-Z]{1,2}\d{3,}$", "", s)
    return s


def find_exact_supplier(
    ledger_name: str,
    supplier_source: SupplierSource,
) -> Supplier | None:
    """Layer 1 — case-insensitive exact match on supplier_name, with
    whitespace normalisation on both sides so the leading-space
    artefact Aditya flagged in the CACSPU data doesn't block matches."""
    cleaned = _clean(ledger_name)
    if not cleaned:
        return None

    # supplier_source.get_by_name does case-insensitive exact match on
    # whatever the CSV stored. The stored name might itself carry
    # leading whitespace (" Nilesh Traders" in the real CSV), so we
    # fall back to an iterated normalized comparison if the fast path
    # misses.
    direct = supplier_source.get_by_name(cleaned)
    if direct is not None:
        return direct

    target = cleaned.lower()
    for s in supplier_source.get_all_suppliers():
        if _clean(s.supplier_name).lower() == target:
            return s
    return None


def find_alias_rule_supplier(
    ledger_name: str,
    supplier_source: SupplierSource,
    alias_rules: list[AliasRule] | None = None,
) -> tuple[Supplier, str] | None:
    """Layer 2 — iterate Supplier Alias Rule rows and return the first
    match. Returns (supplier, rule_name) for audit provenance.

    Only status="confirmed" rules reach this function (the caller's
    AliasRuleSource filters at construction time). Match mode: only
    ``exact_ci`` is supported today. Other modes (``fuzzy_85`` /
    ``fuzzy_90`` per the SAR DocType's Select options) raise
    ``NotImplementedError`` — defensive against write-path drift per
    Commit 2 S-2 deferral.

    Multi-match policy: first-match-wins in the order the caller's
    `AliasRuleSource` supplied the rules (`FrappeAliasRuleSource` loads
    ``ORDER BY creation ASC`` so oldest promotion takes precedence).
    When a second match is found on the same cleaned ledger name, emits
    a WARNING log entry surfacing both rule names for investigation —
    doesn't block the mapper run.

    When ``alias_rules`` is None or empty, returns None — preserves the
    pre-Item-8 stub contract so callers that haven't wired an
    AliasRuleSource get no-op behavior.
    """
    if not alias_rules:
        return None

    cleaned = _clean(ledger_name)
    if not cleaned:
        return None
    target = cleaned.lower()

    first_hit: tuple[AliasRule, Supplier] | None = None
    for rule in alias_rules:
        mode = rule.tally_match_mode
        if mode != "exact_ci":
            raise NotImplementedError(
                f"Supplier Alias Rule match mode {mode!r} is not "
                f"implemented. Only 'exact_ci' is supported today; "
                f"'fuzzy_85' / 'fuzzy_90' are deferred per "
                f"docs/mapper_design_notes.md §6."
            )
        if _clean(rule.tally_name_pattern).lower() != target:
            continue

        supplier = supplier_source.get_by_id(rule.erpnext_supplier)
        if supplier is None:
            # Alias rule references a Supplier that no longer exists on
            # the bench (deleted after promotion). Skip silently — the
            # alias is stale; a later rule may still match.
            continue

        if first_hit is None:
            first_hit = (rule, supplier)
            continue

        # Second match on the same cleaned ledger — duplicate promotion.
        LOG.warning(
            "Multiple Supplier Alias Rules match cleaned ledger %r: "
            "%r wins (oldest creation), later rule %r also matches — "
            "investigate duplicate promotion.",
            cleaned, first_hit[0].name, rule.name,
        )

    if first_hit is None:
        return None
    rule, supplier = first_hit
    return supplier, rule.name


def find_fuzzy_supplier(
    ledger_name: str,
    supplier_source: SupplierSource,
    threshold: float = 85.0,
) -> tuple[Supplier, float] | None:
    """Layer 3 — general fuzzy match over all suppliers using
    rapidfuzz.fuzz.WRatio. Returns (supplier, score_0_to_1) if best
    score >= threshold, else None. Comparison is against
    `_clean(supplier_name)` on both sides.
    """
    cleaned_ledger = _clean(ledger_name)
    if not cleaned_ledger:
        return None

    candidates = supplier_source.get_all_suppliers()
    if not candidates:
        return None

    choices = {i: _clean(s.supplier_name) for i, s in enumerate(candidates)}

    # rapidfuzz.process.extractOne returns (choice, score, key) or None
    hit = process.extractOne(
        cleaned_ledger,
        choices,
        scorer=fuzz.WRatio,
        score_cutoff=threshold,
    )
    if hit is None:
        return None
    _, score, key = hit
    return candidates[key], float(score) / 100.0


# ---------------------------------------------------------------------------
# Top-level resolver
# ---------------------------------------------------------------------------


def resolve_supplier(
    ledger: Any,
    supplier_source: SupplierSource,
    fuzzy_threshold: float = 85.0,
    alias_rules: list[AliasRule] | None = None,
) -> tuple[str, Supplier | None, float, str | None]:
    """Resolve a party ledger to a Supplier (or Supplier Creation Request).

    Returns a 4-tuple ``(tier, supplier, confidence, matched_alias_rule)``:
        tier            — "tier1_supplier_exact" | "tier1_supplier_fuzzy" |
                          "pending_supplier_creation"
        supplier        — the matched Supplier, or None for the creation case
        confidence      — 0.0-1.0; 1.0 for exact, score/100 for fuzzy,
                          0.0 for pending creation
        matched_alias_rule — name of the Supplier Alias Rule that fired,
                             or None. Always None in the current stub
                             implementation of Layer 2.
    """
    cleaned = _clean(ledger.name)

    # Layer 1 — exact_ci
    exact = find_exact_supplier(cleaned, supplier_source)
    if exact is not None:
        return "tier1_supplier_exact", exact, 1.0, None

    # Layer 2 — saved Supplier Alias Rule (Item 8 live)
    alias = find_alias_rule_supplier(cleaned, supplier_source, alias_rules)
    if alias is not None:
        supplier, rule_name = alias
        return "tier1_supplier_alias", supplier, 1.0, rule_name

    # Layer 3 — general fuzzy
    fuzzy = find_fuzzy_supplier(cleaned, supplier_source, threshold=fuzzy_threshold)
    if fuzzy is not None:
        supplier, score = fuzzy
        return "tier1_supplier_fuzzy", supplier, score, None

    return "pending_supplier_creation", None, 0.0, None
