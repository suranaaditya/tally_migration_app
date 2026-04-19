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

from typing import Any

from rapidfuzz import fuzz, process

from rgi_migration.mapper.supplier_source import Supplier, SupplierSource


# ---------------------------------------------------------------------------
# Party-ledger detection
# ---------------------------------------------------------------------------

# Parent-chain substrings that mark a ledger as a vendor/creditor party.
# Lowercased; match via `in` on each parent-chain element (also lowercased).
_VENDOR_PARENT_MARKERS = (
    "sundry creditors",
)


def is_vendor_party_ledger(ledger: Any) -> bool:
    """True if the ledger's parent chain indicates a Sundry-Creditors vendor.

    Uses only the parser-emitted parent_chain, which is authoritative.
    The legacy `-V###` / `-S###` suffix heuristic is not used here —
    parent chain is strictly more reliable and the suffix pattern
    varies across the 59 entities.
    """
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
) -> tuple[Supplier, str] | None:
    """Layer 2 — iterate Supplier Alias Rule rows and return the first
    match. Returns (supplier, rule_name) for audit provenance.

    STUB: Supplier Alias Rule table is empty on erp.jewonline.in. This
    helper returns None until the reviewer-promotion workflow starts
    populating rules in a later sprint. Placeholder left in place so
    the three-layer flow in mapper.py has the right call site already
    wired up.
    """
    return None


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

    # Layer 2 — saved Supplier Alias Rule (stub)
    alias = find_alias_rule_supplier(cleaned, supplier_source)
    if alias is not None:
        supplier, rule_name = alias
        return "tier1_supplier_alias", supplier, 1.0, rule_name

    # Layer 3 — general fuzzy
    fuzzy = find_fuzzy_supplier(cleaned, supplier_source, threshold=fuzzy_threshold)
    if fuzzy is not None:
        supplier, score = fuzzy
        return "tier1_supplier_fuzzy", supplier, score, None

    return "pending_supplier_creation", None, 0.0, None
