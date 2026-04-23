"""Tests for Item 5 Commit 2 — reviewer-promotion of approved
supplier-target Mapping Decisions into ``Supplier Alias Rule`` rows.

Coverage layers (parallels
``test_rule_promotion.py`` / ``test_acr_approval.py`` philosophy):

1. **Pure-core unit tests** — ``rgi_migration.mapper.alias_promotion``.
   ``build_alias_rule_payload``, ``compute_supplier_source_hash``,
   ``_clean`` import-identity. No Frappe dependency.
2. **Cross-namespace hash collision** — structurally parallel account
   and supplier promotion inputs produce DIFFERENT source_hash
   outputs. Catches any future refactor that accidentally merges
   the namespace prefixes.
3. **α-codification integration test** — the live
   ``find_alias_rule_supplier`` stub (Layer 2 of
   ``tier1_supplier.resolve_supplier``) still returns ``None`` after
   alias rule rows are assumed to exist. Will start failing when
   Item 8 replaces the stub with a DocType read; update to codify
   γ at that point, not silence.

The Frappe wrapper ``promote_supplier_decision_to_alias_rule``
(md_review.py) is exercised via Phase C bench programmatic smoke —
same policy as Commit 1's whitelist wrapper.
"""
from __future__ import annotations

import hashlib

import pytest

from rgi_migration.mapper.alias_promotion import (
    AliasRulePayload,
    NotPromotable,
    build_alias_rule_payload,
    compute_supplier_source_hash,
)
from rgi_migration.mapper.alias_promotion import _clean as alias_clean
from rgi_migration.mapper.tier1_supplier import _clean as tier1_clean


# ---------------------------------------------------------------------------
# _clean import-identity — S-1 enforcement
# ---------------------------------------------------------------------------


def test_clean_is_tier1_supplier_clean() -> None:
    """S-1 hard requirement: alias_promotion._clean must BE
    tier1_supplier._clean (same function object), not a reimplementation.

    Catches silent forks. If a future refactor splits these
    functions — even with identical source — the matching semantics
    can drift independently (one cleans differently than the other),
    and Item 8's future FrappeSupplierSource layer-2 matcher would
    read stored patterns that don't line up with match-time cleaning.

    This test asserts object identity, not behavioral equivalence,
    because behavioral equivalence is refactor-fragile.
    """
    assert alias_clean is tier1_clean


# ---------------------------------------------------------------------------
# compute_supplier_source_hash
# ---------------------------------------------------------------------------


def test_supplier_source_hash_key_format() -> None:
    """Key format: {source_section}|0|{cleaned_pattern}|{erpnext_supplier}.
    The constant ``|0|`` segment occupies the anti-pattern-bit slot
    that account-side uses for a supplier-irrelevant axis, but
    keeping it in the key positionally prevents a future merge from
    producing accidental collisions."""
    expected_key = "session-SAR-MD-X|0|GAJANAN FOODS|GAJANAN FOODS AND HOSPITALITY"
    expected = hashlib.sha1(expected_key.encode("utf-8")).hexdigest()
    got = compute_supplier_source_hash(
        source_section="session-SAR-MD-X",
        cleaned_tally_pattern="GAJANAN FOODS",
        erpnext_supplier="GAJANAN FOODS AND HOSPITALITY",
    )
    assert got == expected


def test_supplier_source_hash_different_suppliers_different_hashes() -> None:
    """Same (section, pattern) → different suppliers produce different
    hashes. The conflict path needs this to find pattern-matched but
    target-different rows as distinct rows rather than colliding at
    the source_hash level."""
    h1 = compute_supplier_source_hash(
        "session-SAR-MD-X", "Shree Graphics", "Shree Graphics Pvt Ltd",
    )
    h2 = compute_supplier_source_hash(
        "session-SAR-MD-X", "Shree Graphics", "Shree Graphics (Defunct Branch)",
    )
    assert h1 != h2


def test_supplier_source_hash_deterministic() -> None:
    a = compute_supplier_source_hash(
        "session-SAR-MD-001", "Pattern", "Supplier",
    )
    b = compute_supplier_source_hash(
        "session-SAR-MD-001", "Pattern", "Supplier",
    )
    assert a == b


# ---------------------------------------------------------------------------
# Cross-namespace hash collision — S-7 explicit ask
# ---------------------------------------------------------------------------


def test_cross_namespace_hash_no_collision() -> None:
    """Structurally parallel inputs across account-side and supplier-
    side promotion must produce DIFFERENT source_hash outputs.

    Catches future refactors that accidentally merge the namespace
    prefixes. The namespaces today:

    * Account:  ``session-MR-{md_name}``
    * Supplier: ``session-SAR-{md_name}``

    If a refactor drops the MR/SAR distinction (e.g. both become
    ``session-RULE-{md_name}``), this test fails loudly. The commit-1
    account-side source_hash excludes the target, while supplier side
    includes it; that's also a protection, but this test pins the
    prefix-level separation too.
    """
    from rgi_migration.mapper.rule_promotion import (
        compute_source_hash as account_hash,
    )

    md = "MD-X"
    account_h = account_hash(
        source_section=f"session-MR-{md}",
        is_anti_pattern=False,
        tally_pattern="Professional Tax",
    )
    supplier_h = compute_supplier_source_hash(
        source_section=f"session-SAR-{md}",
        cleaned_tally_pattern="Professional Tax",
        erpnext_supplier="Professional Tax",  # contrived collision target
    )
    assert account_h != supplier_h


# ---------------------------------------------------------------------------
# build_alias_rule_payload — happy path + refusals
# ---------------------------------------------------------------------------


def _happy_inputs(**overrides) -> dict:
    base = {
        "md_name": "MD-2026-00001",
        "tally_name": "GAJANAN FOODS AND HOSPITALITY-VG00104",
        "tier": "tier1_supplier_exact",
        "final_supplier": "GAJANAN FOODS AND HOSPITALITY",
        "session_name": "TMS-CACSPU--00495",
        "entity_type": "college",
    }
    base.update(overrides)
    return base


def test_build_alias_payload_happy_path() -> None:
    p = build_alias_rule_payload(**_happy_inputs())
    assert isinstance(p, AliasRulePayload)
    # tally_name_pattern is the _clean()'d form — party-ID suffix
    # stripped, whitespace normalized.
    assert p.tally_name_pattern == "GAJANAN FOODS AND HOSPITALITY"
    assert p.tally_match_mode == "exact_ci"
    assert p.erpnext_supplier == "GAJANAN FOODS AND HOSPITALITY"
    assert p.applies_to_entity_types == "college"
    assert p.created_from == "session_review"
    assert p.session_name == "TMS-CACSPU--00495"
    # source_hash composes deterministically over the cleaned pattern
    # + target + session-SAR- prefix.
    assert p.source_hash == compute_supplier_source_hash(
        source_section="session-SAR-MD-2026-00001",
        cleaned_tally_pattern="GAJANAN FOODS AND HOSPITALITY",
        erpnext_supplier="GAJANAN FOODS AND HOSPITALITY",
    )


def test_build_alias_payload_strips_party_id_suffix() -> None:
    """Verify the _clean reuse surfaces in the stored pattern."""
    p = build_alias_rule_payload(
        **_happy_inputs(tally_name="Sharma Enterprises-VS0088")
    )
    assert p.tally_name_pattern == "Sharma Enterprises"


def test_build_alias_payload_collapses_whitespace() -> None:
    p = build_alias_rule_payload(
        **_happy_inputs(tally_name="  Foo   Bar  ")
    )
    assert p.tally_name_pattern == "Foo Bar"


def test_build_alias_payload_entity_type_defaults_to_star() -> None:
    """Mirrors account-side: missing entity_type → ``"*"``."""
    for et in (None, "", "   "):
        p = build_alias_rule_payload(**_happy_inputs(entity_type=et))
        assert p.applies_to_entity_types == "*"


def test_build_alias_payload_accepts_all_supplier_tiers() -> None:
    """All four supplier-target tiers should promote cleanly.
    tier1_supplier_fuzzy MDs do get promoted with match_mode=exact_ci
    per S-2 — reviewer's manual authority via the dialog has already
    crystallised the fuzzy match into a structural exact one."""
    for t in (
        "tier1_supplier_exact",
        "tier1_supplier_alias",
        "tier1_supplier_fuzzy",
        "pending_supplier_creation",
    ):
        p = build_alias_rule_payload(**_happy_inputs(tier=t))
        assert p.tally_match_mode == "exact_ci"  # hardcoded per S-2


def test_build_alias_payload_refuses_account_tier() -> None:
    with pytest.raises(NotPromotable, match="supplier-target tier"):
        build_alias_rule_payload(**_happy_inputs(tier="tier1_exact"))


def test_build_alias_payload_refuses_unknown_tier() -> None:
    with pytest.raises(NotPromotable, match="supplier-target tier"):
        build_alias_rule_payload(**_happy_inputs(tier="mystery_tier"))


def test_build_alias_payload_refuses_empty_final_supplier() -> None:
    with pytest.raises(NotPromotable, match="final_supplier"):
        build_alias_rule_payload(**_happy_inputs(final_supplier=""))


def test_build_alias_payload_refuses_empty_session_name() -> None:
    with pytest.raises(NotPromotable, match="session"):
        build_alias_rule_payload(**_happy_inputs(session_name=""))


def test_build_alias_payload_refuses_empty_md_name() -> None:
    with pytest.raises(NotPromotable, match="name is empty"):
        build_alias_rule_payload(**_happy_inputs(md_name=""))


def test_build_alias_payload_refuses_empty_tally_name_after_clean() -> None:
    """_clean may reduce certain inputs to empty (all-whitespace, or a
    name that's just a party-ID suffix). The builder must refuse."""
    for bad in ("", "   ", "-VA0001"):
        with pytest.raises(NotPromotable, match="tally_name"):
            build_alias_rule_payload(**_happy_inputs(tally_name=bad))


def test_build_alias_payload_idempotent_source_hash() -> None:
    """Two builds of the same MD → same source_hash. Foundation of
    the whitelist's duplicate-detect-and-skip semantic."""
    p1 = build_alias_rule_payload(**_happy_inputs())
    p2 = build_alias_rule_payload(**_happy_inputs())
    assert p1.source_hash == p2.source_hash


def test_build_alias_payload_different_target_different_hash() -> None:
    """Same MD / same pattern but different final_supplier → different
    hash. This is the conflict-detection foundation: two alias rules
    on the same cleaned pattern to different Suppliers live as
    distinct rows rather than colliding at source_hash."""
    p1 = build_alias_rule_payload(
        **_happy_inputs(final_supplier="Supplier A"),
    )
    p2 = build_alias_rule_payload(
        **_happy_inputs(final_supplier="Supplier B"),
    )
    assert p1.tally_name_pattern == p2.tally_name_pattern
    assert p1.source_hash != p2.source_hash


# ---------------------------------------------------------------------------
# α-codification integration — supplier side
# ---------------------------------------------------------------------------


def test_find_alias_rule_supplier_stub_still_returns_none() -> None:
    """α codification for supplier side: even with alias rules
    promoted into the DocType, the live Layer-2 matcher
    ``find_alias_rule_supplier`` still returns None because its body
    is a hardcoded stub until Item 8.

    This test should start failing when Item 8 replaces the stub
    with a DocType read. Update to codify γ (live-read) at that
    point — the test serves as a future-forcing marker, same
    pattern as test_rule_promotion's α invariant.
    """
    from rgi_migration.mapper.tier1_supplier import find_alias_rule_supplier
    from rgi_migration.mapper.supplier_source import InMemorySupplierSource

    src = InMemorySupplierSource([])
    result = find_alias_rule_supplier("GAJANAN FOODS", src)
    assert result is None
