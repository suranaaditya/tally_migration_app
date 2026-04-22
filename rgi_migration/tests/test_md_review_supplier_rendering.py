"""Static-file guardrails for Item 3 Commit 1a — supplier rendering + enum fix.

The detail pane's Section 3 polymorphism and the SUPPLIER_TIERS /
TIER_CHIP_STATE constants all live in ``md_review.js``. Those aren't
exercised by the pytest suite directly, but static-file assertions
keep the JS constants in lock-step with the Python-side tier enum
(the regressions they exist to prevent surfaced during Item 2 as
latent bugs: Item 2's tier enum extension added
``tier1_supplier_exact`` + ``tier1_supplier_alias`` to the DocType
Select, but neither ``SUPPLIER_TIERS`` nor ``TIER_CHIP_STATE`` in
the JS had entries — so ``_isVendorRow()`` returned false for those
rows, and the tier chip rendered in grey-muted styling).

This file re-establishes those assertions: if the DocType tier enum
is extended again, the test forces matching JS changes.

Scope is narrow by design — only catches constant drift. The
rendering logic itself is exercised via browser verification in
Commit 1a Phase C.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


_JS_PATH = (
    Path(__file__).parent.parent
    / "rgi_migration"
    / "page"
    / "md_review"
    / "md_review.js"
)


@pytest.fixture(scope="module")
def js_source() -> str:
    return _JS_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# SUPPLIER_TIERS — controls _isVendorRow() classification
# ---------------------------------------------------------------------------


_SUPPLIER_TIERS_BLOCK = re.compile(
    r"static\s+SUPPLIER_TIERS\s*=\s*new\s+Set\(\s*\[([^\]]+)\]\s*\)",
    re.DOTALL,
)


def _extract_string_literals(block: str) -> set[str]:
    """Pull out every double-quoted string from an array literal block."""
    return set(re.findall(r'"([^"]+)"', block))


def test_supplier_tiers_set_covers_all_supplier_tier_enum_values(js_source: str) -> None:
    m = _SUPPLIER_TIERS_BLOCK.search(js_source)
    assert m is not None, "SUPPLIER_TIERS static field missing from md_review.js"
    values = _extract_string_literals(m.group(1))
    required = {
        "pending_supplier_creation",
        "tier1_supplier_exact",
        "tier1_supplier_alias",
        "tier1_supplier_fuzzy",
    }
    missing = required - values
    assert not missing, (
        f"SUPPLIER_TIERS is missing supplier tier enum values: {sorted(missing)}. "
        f"Any supplier tier the mapper emits must also be classified as "
        f"vendor-row by _isVendorRow() for the Request Creation dialog to "
        f"route correctly."
    )


# ---------------------------------------------------------------------------
# TIER_CHIP_STATE — colour class lookup for the detail-pane Tier chip
# ---------------------------------------------------------------------------


_TIER_CHIP_STATE_BLOCK = re.compile(
    r"static\s+TIER_CHIP_STATE\s*=\s*\{([^}]+)\}",
    re.DOTALL,
)


def _parse_object_literal(block: str) -> dict[str, str]:
    """Parse a simple `"key": "value",` object literal into a dict."""
    return {
        m.group(1): m.group(2)
        for m in re.finditer(r'"([^"]+)"\s*:\s*"([^"]+)"', block)
    }


def test_tier_chip_state_classifies_all_supplier_tiers_as_resolved(js_source: str) -> None:
    m = _TIER_CHIP_STATE_BLOCK.search(js_source)
    assert m is not None, "TIER_CHIP_STATE static map missing"
    chip_map = _parse_object_literal(m.group(1))
    for supplier_tier in ("tier1_supplier_exact", "tier1_supplier_alias", "tier1_supplier_fuzzy"):
        assert chip_map.get(supplier_tier) == "resolved", (
            f"TIER_CHIP_STATE[{supplier_tier!r}] should be 'resolved' "
            f"(blue chip — reviewer sees it as a confident match); got "
            f"{chip_map.get(supplier_tier)!r}. Without this entry the chip "
            f"falls back to grey 'muted' which reads as excluded."
        )


def test_tier_chip_state_classifies_pending_supplier_creation_as_blocking(js_source: str) -> None:
    m = _TIER_CHIP_STATE_BLOCK.search(js_source)
    chip_map = _parse_object_literal(m.group(1))
    assert chip_map.get("pending_supplier_creation") == "blocking"


# ---------------------------------------------------------------------------
# _render_supplier_proposal_rows — structural presence check
# ---------------------------------------------------------------------------


def test_supplier_proposal_rows_renderer_exists(js_source: str) -> None:
    """Section 3 polymorphism requires a dedicated supplier-proposal
    renderer. Regression guard against accidental removal during future
    refactors."""
    assert "_render_supplier_proposal_rows" in js_source, (
        "DetailPane._render_supplier_proposal_rows missing — "
        "Section 3 polymorphism will fall through to account-only rendering."
    )
    # The Section 3 top-level renderer should branch on is_supplier
    assert "DetailPane.SUPPLIER_TIERS.has(tier)" in js_source


# ---------------------------------------------------------------------------
# _match_score_class — threshold contract
# ---------------------------------------------------------------------------


_MATCH_SCORE_CLASS_BODY = re.compile(
    r"static\s+_match_score_class\s*\(\s*score\s*\)\s*\{([^}]+)\}",
    re.DOTALL,
)


def test_match_score_class_thresholds(js_source: str) -> None:
    """Thresholds must be ≥0.95 → good, ≥0.85 → warn, else muted
    (per Item 3 AMB-6). These are the reviewer-facing bucket edges; any
    change needs explicit design sign-off."""
    m = _MATCH_SCORE_CLASS_BODY.search(js_source)
    assert m is not None, "_match_score_class helper missing"
    body = m.group(1)
    # Presence of each threshold literal — lenient regex lets future
    # refactors swap the exact control-flow shape but preserves
    # contract.
    assert "0.95" in body and "good" in body, (
        f"0.95/good threshold not in _match_score_class body: {body}"
    )
    assert "0.85" in body and "warn" in body, (
        f"0.85/warn threshold not in _match_score_class body: {body}"
    )
    assert "muted" in body, (
        f"fallback 'muted' not in _match_score_class body: {body}"
    )
